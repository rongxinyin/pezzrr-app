"""
Thermostat model-predictive controller (advisory / shadow mode).

Formulates a finite-horizon optimal-control problem over the MPC grid built by
mpc_data.build_inputs and solves it with an open-source solver (HiGHS or SCIP,
selectable in mpc_config.json). The plant is the linear 1R1C RCModel, so the
problem is an LP (variable-speed, continuous modulation) or a MILP (single-stage
on/off, or modulating equipment with a non-zero minimum part-load ratio).

Decision: HVAC actuation per step (cooling and/or heating capacity fraction).
Objective: minimize electricity cost = sum_k price_k * P_elec_k * dt_h, plus a
large penalty on comfort-band violation (soft bounds keep the problem feasible
even when the initial temperature already sits outside the band).

Output is advisory: the optimized indoor-temperature trajectory is translated
into a recommended cool/heat setpoint schedule. Nothing is sent to the device;
write the result to control_actions via mpc_data.write_advisory.

Solver selection:
  * HiGHS via Pyomo's APPSI interface (no executable needed).
  * SCIP via an LP-file bridge to pyscipopt (Pyomo has no native pyscipopt
    interface and the `scip` CLI is not installed here), mapping the solution
    back by symbolic variable name.

Not modeled (kept out to preserve the LP/MILP structure): compressor minimum
run-time / anti-cycling, capacity derating vs. outdoor temperature, latent
loads. These can be layered on later.
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pyomo.environ as pyo

# RCModel / equipment models live in the ecobee_agent package.
_ECOBEE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ecobee_agent"))
if _ECOBEE not in sys.path:
    sys.path.insert(0, _ECOBEE)
from ecobee_agent.hvac_model import RCModel, EquipmentModel  # noqa: E402

try:
    from . import mpc_data
except ImportError:  # running as a loose script
    import mpc_data

COMFORT_PENALTY = 100.0   # $ per (deg-C * step) of comfort violation


# =====================================================================
# Solver layer
# =====================================================================
def _solve_highs(model, options):
    """Solve via highspy on a Pyomo-exported LP file, loading the solution back
    by symbolic column name. (Avoids Pyomo's APPSI/HiGHS interface, which hangs
    on this model in the current environment.)"""
    import highspy
    fd, path = tempfile.mkstemp(suffix=".lp")
    os.close(fd)
    try:
        model.write(path, io_options={"symbolic_solver_labels": True})
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        if "time_limit" in options:
            h.setOptionValue("time_limit", float(options["time_limit"]))
        if "mip_rel_gap" in options:
            h.setOptionValue("mip_rel_gap", float(options["mip_rel_gap"]))
        h.readModel(path)
        h.run()
        status = h.modelStatusToString(h.getModelStatus())
        sol = h.getSolution()
        lp = h.getLp()
        names = [lp.col_names_[i] for i in range(lp.num_col_)]
        vals = dict(zip(names, sol.col_value))
        for v in model.component_data_objects(pyo.Var, active=True):
            nm = v.name.replace("[", "(").replace("]", ")")
            if nm in vals:
                v.set_value(vals[nm], skip_validation=True)
        return status
    finally:
        os.remove(path)


def _solve_scip(model, options):
    """Bridge Pyomo -> .lp file -> pyscipopt, then load the solution back."""
    from pyscipopt import Model as SModel
    fd, path = tempfile.mkstemp(suffix=".lp")
    os.close(fd)
    try:
        model.write(path, io_options={"symbolic_solver_labels": True})
        sm = SModel()
        sm.readProblem(path)
        for k, v in options.items():
            try:
                sm.setParam(k, v)
            except Exception:
                pass
        sm.hideOutput()
        sm.optimize()
        vals = {v.name: sm.getVal(v) for v in sm.getVars()}
        for v in model.component_data_objects(pyo.Var, active=True):
            nm = v.name.replace("[", "(").replace("]", ")")
            if nm in vals:
                v.set_value(vals[nm], skip_validation=True)
        return sm.getStatus()
    finally:
        os.remove(path)


def _solve(model, solver, all_options):
    opts = all_options.get(solver, {})
    if solver == "highs":
        return _solve_highs(model, opts)
    if solver == "scip":
        return _solve_scip(model, opts)
    raise ValueError(f"Unknown solver {solver!r}")


# =====================================================================
# Model
# =====================================================================
def build_model(inputs, binary_single_stage=True):
    rc = RCModel.from_dict(inputs.rc_model)
    equip = EquipmentModel.from_dict(inputs.equipment)
    N = inputs.horizon_steps
    dt_h = inputs.dt_s / 3600.0
    Tout = np.asarray(inputs.outdoor_temp_c, dtype=float)
    price = np.asarray(inputs.price_per_kwh, dtype=float)

    do_cool = inputs.mode in ("cool", "both")
    do_heat = inputs.mode in ("heat", "both")
    c = inputs.comfort
    # The upper comfort bound is the cooling system's responsibility; the lower
    # bound is the heating system's. A cooling-only unit cannot raise the
    # temperature, so penalizing it for passive drift below cool_min would only
    # (wrongly) discourage precooling -> enforce each bound only if the matching
    # capability exists.
    band_hi = c.get("cool_max_c") if do_cool else c.get("heat_max_c")
    band_lo = c.get("heat_min_c") if do_heat else None

    m = pyo.ConcreteModel()
    m.Ks = pyo.RangeSet(0, N)          # state grid 0..N
    m.Kc = pyo.RangeSet(0, N - 1)      # control grid 0..N-1

    m.T = pyo.Var(m.Ks, within=pyo.Reals)
    m.sl_hi = pyo.Var(m.Ks, within=pyo.NonNegativeReals)   # T above band_hi
    m.sl_lo = pyo.Var(m.Ks, within=pyo.NonNegativeReals)   # T below band_lo

    binary = (not equip.modulating) and binary_single_stage
    needs_onoff = equip.modulating and equip.min_plr > 0.0

    if do_cool:
        m.uc = pyo.Var(m.Kc, bounds=(0, 1), within=pyo.Binary if binary else pyo.NonNegativeReals)
        if needs_onoff:
            m.yc = pyo.Var(m.Kc, within=pyo.Binary)
            m.uc_on = pyo.Constraint(m.Kc, rule=lambda m, k: m.uc[k] <= m.yc[k])
            m.uc_min = pyo.Constraint(m.Kc, rule=lambda m, k: m.uc[k] >= equip.min_plr * m.yc[k])
    if do_heat:
        m.uh = pyo.Var(m.Kc, bounds=(0, 1), within=pyo.Binary if binary else pyo.NonNegativeReals)
        if needs_onoff:
            m.yh = pyo.Var(m.Kc, within=pyo.Binary)
            m.uh_on = pyo.Constraint(m.Kc, rule=lambda m, k: m.uh[k] <= m.yh[k])
            m.uh_min = pyo.Constraint(m.Kc, rule=lambda m, k: m.uh[k] >= equip.min_plr * m.yh[k])

    def q_hvac(m, k):  # signed thermal power into zone (W)
        q = 0.0
        if do_cool:
            q += -equip.capacity_w * m.uc[k]
        if do_heat:
            q += equip.capacity_w * m.uh[k]
        return q

    m.init = pyo.Constraint(expr=m.T[0] == inputs.indoor_temp_c)
    m.dyn = pyo.Constraint(
        m.Kc,
        rule=lambda m, k: m.T[k + 1] == m.T[k] + rc.a * (Tout[k] - m.T[k])
        + rc.g * q_hvac(m, k) + rc.d,
    )
    # Soft comfort band on k=1..N (k=0 is the fixed measured state).
    if band_hi is not None:
        m.cb_hi = pyo.Constraint(m.Ks, rule=lambda m, k: pyo.Constraint.Skip if k == 0
                                 else m.T[k] <= band_hi + m.sl_hi[k])
    if band_lo is not None:
        m.cb_lo = pyo.Constraint(m.Ks, rule=lambda m, k: pyo.Constraint.Skip if k == 0
                                 else m.T[k] >= band_lo - m.sl_lo[k])

    def elec_kw(m, k):
        u = 0.0
        if do_cool:
            u += m.uc[k]
        if do_heat:
            u += m.uh[k]
        return equip.rated_electrical_w * u / 1000.0

    energy_cost = sum(price[k] * elec_kw(m, k) * dt_h for k in m.Kc)
    comfort_pen = COMFORT_PENALTY * sum(m.sl_hi[k] + m.sl_lo[k] for k in m.Ks)
    m.obj = pyo.Objective(expr=energy_cost + comfort_pen, sense=pyo.minimize)

    m._meta = {"band_hi": band_hi, "band_lo": band_lo, "dt_h": dt_h,
               "do_cool": do_cool, "do_heat": do_heat, "binary": binary,
               "rated_electrical_w": equip.rated_electrical_w}
    return m, rc, equip


# =====================================================================
# Solve + translate to advisory
# =====================================================================
def solve_mpc(inputs, mpc_cfg=None, binary_single_stage=True):
    mpc_cfg = mpc_cfg or mpc_data._load_json("mpc_config.json")
    defaults = mpc_cfg["defaults"]
    solver = defaults.get("solver", "highs")
    fallbacks = defaults.get("solver_fallback", [])
    solver_opts = defaults.get("solver_options", {})

    m, rc, equip = build_model(inputs, binary_single_stage=binary_single_stage)

    term, used = None, None
    for cand in [solver] + list(fallbacks):
        try:
            term = _solve(m, cand, solver_opts)
            used = cand
            if any(t in term.lower() for t in ("optimal", "feasible")):
                break
        except Exception as e:  # solver missing / failed -> try next
            term = f"error: {e}"
            continue
    if used is None or pyo.value(m.T[1], exception=False) is None:
        return {"status": "no_solution", "solver": used, "termination": term}

    meta = m._meta
    N = inputs.horizon_steps
    T = np.array([pyo.value(m.T[k]) for k in range(N + 1)])
    uc = (np.array([pyo.value(m.uc[k]) for k in range(N)]) if meta["do_cool"]
          else np.zeros(N))
    uh = (np.array([pyo.value(m.uh[k]) for k in range(N)]) if meta["do_heat"]
          else np.zeros(N))
    sl = np.array([pyo.value(m.sl_hi[k]) + pyo.value(m.sl_lo[k]) for k in range(N + 1)])

    elec_kw = meta["rated_electrical_w"] * (uc + uh) / 1000.0
    energy_kwh = float(np.sum(elec_kw) * meta["dt_h"])
    cost = float(np.sum(inputs.price_per_kwh * elec_kw) * meta["dt_h"])

    # Translate optimized temperatures into recommended setpoints (clip to band).
    pred = T[1:]
    rec_cool = (np.clip(pred, inputs.comfort.get("cool_min_c", meta["band_lo"]),
                        inputs.comfort.get("cool_max_c", meta["band_hi"])).tolist()
                if meta["do_cool"] else None)
    rec_heat = (np.clip(pred, inputs.comfort.get("heat_min_c", meta["band_lo"]),
                        inputs.comfort.get("heat_max_c", meta["band_hi"])).tolist()
                if meta["do_heat"] else None)

    return {
        "status": "ok",
        "solver": used,
        "termination": term,
        "expected_energy_kwh": round(energy_kwh, 4),
        "expected_cost_usd": round(cost, 4),
        "comfort_violation_degC_steps": round(float(np.sum(sl)), 4),
        "predicted_indoor_temp_c": [round(x, 3) for x in pred.tolist()],
        "cooling_fraction": [round(x, 3) for x in uc.tolist()] if meta["do_cool"] else None,
        "heating_fraction": [round(x, 3) for x in uh.tolist()] if meta["do_heat"] else None,
        "recommended_cool_setpoint_c": [round(x, 2) for x in rec_cool] if rec_cool else None,
        "recommended_heat_setpoint_c": [round(x, 2) for x in rec_heat] if rec_heat else None,
        "immediate_cool_setpoint_c": round(rec_cool[0], 2) if rec_cool else None,
        "immediate_heat_setpoint_c": round(rec_heat[0], 2) if rec_heat else None,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Solve the thermostat MPC for a home.")
    ap.add_argument("--home", default="test_home")
    ap.add_argument("--solver", default=None, help="override solver (highs|scip)")
    args = ap.parse_args()

    inp = mpc_data.build_inputs(args.home)
    print(inp.summary())
    cfg = None
    if args.solver:
        cfg = mpc_data._load_json("mpc_config.json")
        cfg["defaults"]["solver"] = args.solver
        cfg["defaults"]["solver_fallback"] = []
    res = solve_mpc(inp, mpc_cfg=cfg)
    print(f"solver={res.get('solver')} term={res.get('termination')} status={res['status']}")
    if res["status"] == "ok":
        print(f"  energy={res['expected_energy_kwh']} kWh  cost=${res['expected_cost_usd']}  "
              f"comfort_viol={res['comfort_violation_degC_steps']}")
        print(f"  immediate cool setpoint: {res['immediate_cool_setpoint_c']} C")
        print(f"  pred indoor (first 8): {res['predicted_indoor_temp_c'][:8]}")
        if res["cooling_fraction"]:
            print(f"  cooling frac (first 12): {res['cooling_fraction'][:12]}")


if __name__ == "__main__":
    main()
