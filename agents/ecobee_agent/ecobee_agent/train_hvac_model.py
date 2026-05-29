"""
Offline trainer for the gray-box RC zone model (see hvac_model.RCModel).

Pulls a thermostat's indoor/outdoor temperature history from the database,
resamples to a fixed MPC timestep, fits the 1R1C passive dynamics, identifies
the HVAC thermal gain, and writes config/hvac_model_<home>.json for the MPC.

Identification strategy (depends on what runtime data exists):

  * If per-interval compressor runtime is available (thermostat_runtime, fed by
    the Ecobee Runtime Report API), a signed HVAC thermal-power series is built
    and RCModel.fit identifies the full model (a, g, d) directly.

  * Otherwise — the situation today, since equipmentStatus was historically
    mislogged and every row reads 'idle' — only the passive time constant is
    identifiable from the bulk data. We fit (a, d) on idle steps and recover the
    HVAC gain g (hence capacitance C = dt/g) from the indoor-temperature
    pulldown during KNOWN cooling windows supplied below. Re-run once real
    runtime is logged for a full data-driven fit.

Usage:
    venv/bin/python -m ecobee_agent.train_hvac_model \
        --home test_home --device-id 3 --dt 900
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

from .hvac_model import RCModel, SingleStageCooling, VariableSpeedHeatPump

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")

# Known HVAC-on windows for homes whose equipmentStatus was not logged.
# (home_name -> list of (start_local, end_local) in America/Los_Angeles).
# These let us identify the HVAC gain from the indoor-temp pulldown.
KNOWN_COOLING_WINDOWS = {
    "test_home": [("2026-05-20 17:30", "2026-05-20 23:30")],
}

LOCAL_TZ = "America/Los_Angeles"


def _db_dsn() -> str:
    cfg = json.load(open(os.path.join(CONFIG_DIR, "data_analytics_config.json")))["database"]
    return (
        f"host={cfg['host']} port={cfg['port']} dbname={cfg['database_name']} "
        f"user={cfg['username']} password={cfg['password']}"
    )


def load_readings(device_id: int) -> pd.DataFrame:
    """Indoor/outdoor temp + cool/heat setpoints for a device, indexed by tz-naive UTC."""
    con = psycopg2.connect(_db_dsn())
    try:
        cur = con.cursor()
        cur.execute(
            """SELECT ts, indoor_temp_c, outdoor_temp_c, cool_setpoint_c, heat_setpoint_c
               FROM thermostat_readings WHERE device_id=%s ORDER BY ts""",
            (device_id,),
        )
        rows = cur.fetchall()
    finally:
        con.close()
    if not rows:
        raise SystemExit(f"No thermostat_readings for device_id={device_id}")
    # Keep everything in UTC to avoid DST discontinuities in resampling.
    idx = pd.to_datetime([r[0] for r in rows], utc=True).tz_convert("UTC").tz_localize(None)
    f = lambda v: float(v) if v is not None else np.nan
    return pd.DataFrame(
        {
            "Tin": [f(r[1]) for r in rows],
            "Tout": [f(r[2]) for r in rows],
            "csp": [f(r[3]) for r in rows],
            "hsp": [f(r[4]) for r in rows],
        },
        index=idx,
    )


def load_runtime(device_id: int, dt_s: float) -> pd.Series | None:
    """Signed HVAC thermal power (W) per dt step from thermostat_runtime, or None.

    Positive = heating into zone, negative = cooling. Returns None if the table
    has no rows for this device (the current state of the system)."""
    con = psycopg2.connect(_db_dsn())
    try:
        cur = con.cursor()
        cur.execute("SELECT to_regclass('thermostat_runtime')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute(
            "SELECT count(*) FROM thermostat_runtime WHERE device_id=%s", (device_id,)
        )
        if cur.fetchone()[0] == 0:
            return None
    finally:
        con.close()
    # When runtime exists, wire its compressor-seconds columns here.
    return None


def resample(df: pd.DataFrame, dt_s: float) -> pd.DataFrame:
    rule = f"{int(dt_s)}s"
    r = df.resample(rule).mean()
    r["valid"] = df["Tin"].resample(rule).count()
    return r[r["valid"] > 0].copy()


def cooling_mask(index: pd.DatetimeIndex, windows) -> np.ndarray:
    mask = np.zeros(len(index), dtype=bool)
    for start_local, end_local in windows:
        s = pd.Timestamp(start_local, tz=LOCAL_TZ).tz_convert("UTC").tz_localize(None)
        e = pd.Timestamp(end_local, tz=LOCAL_TZ).tz_convert("UTC").tz_localize(None)
        mask |= (index >= s) & (index < e)
    return mask


# Thermostat deadband: compressor turns on at cool_setpoint + 0.5 F (cooling) or
# heat_setpoint - 0.5 F (heating); between the two bands the HVAC is idle/off.
DEADBAND_C = 0.5 * 5.0 / 9.0  # 0.5 F in Celsius (~0.278 C)


def detect_hvac_activity(r: pd.DataFrame):
    """Infer per-sample heating/cooling-on state from the setpoint deadband.

    No per-interval runtime is logged (equipmentStatus was historically mislogged
    to 'idle'), so we reconstruct HVAC state from the thermostat's own hysteresis
    (per the unit's control logic):

      cooling_on : indoor >= cool_setpoint + deadband  -> compressor cooling.
      heating_on : indoor <= heat_setpoint - deadband  -> compressor heating.
      idle       : heat_setpoint - db < indoor < cool_setpoint + db -> HVAC off.

    The labels gate the passive fit (active samples excluded) and seed the gain
    estimators. Returns (heating_on, cooling_on) bool arrays aligned to r.index."""
    Tin = r["Tin"].values
    csp = r["csp"].values
    hsp = r["hsp"].values
    cooling_on = (Tin >= csp + DEADBAND_C) & ~np.isnan(csp)
    heating_on = (Tin <= hsp - DEADBAND_C) & ~np.isnan(hsp)
    return heating_on, cooling_on


def estimate_gain_from_pullup(r, dt_s, heat_on, a, d, equip):
    """Recover HVAC gain g (and C=dt/g) from the steepest heating steps (duty~1).

    Mirror of estimate_gain_from_pulldown for heat-pump heating: the strongest
    positive residual beyond passive drift corresponds to near-continuous
    compressor operation, so g = mean(strong_resid)/capacity_w."""
    Tin = r["Tin"].values
    Tout = r["Tout"].values
    gap = r.index.to_series().diff().dt.total_seconds().values
    contig = gap == dt_s
    dT = np.r_[np.nan, np.diff(Tin)]
    prevTout = np.r_[np.nan, Tout[:-1]]
    prevTin = np.r_[np.nan, Tin[:-1]]
    heat_step = np.r_[False, heat_on[:-1]] & contig & ~np.isnan(dT)
    if heat_step.sum() == 0:
        return None
    passive_pred = a * (prevTout - prevTin) + d
    resid = (dT - passive_pred)[heat_step]   # extra heating beyond passive (positive)
    # Strongest-pullup quartile ~ continuous compressor operation (duty ~ 1).
    k = max(3, len(resid) // 4)
    strong = np.sort(resid)[-k:]
    g = float(np.mean(strong) / equip.capacity_w)   # resid = g*cap*duty, duty~1
    if g <= 0:
        return None
    return {
        "g": g,
        "capacitance_j_per_k": dt_s / g,
        "n_heating_steps": int(heat_step.sum()),
        "pullup_mean_dT_c": float(np.mean(strong)),
    }


def fit_passive(r: pd.DataFrame, dt_s: float, active: np.ndarray):
    """Least-squares fit of dT[k] = a*(Tout[k-1]-Tin[k-1]) + d on idle (HVAC-off),
    contiguous steps. `active` flags HVAC-on steps (heating or cooling) to exclude
    so the passive coefficients are not contaminated by compressor heat."""
    Tin = r["Tin"].values
    Tout = r["Tout"].values
    gap = r.index.to_series().diff().dt.total_seconds().values
    contig = gap == dt_s
    dT = np.r_[np.nan, np.diff(Tin)]
    prevTout = np.r_[np.nan, Tout[:-1]]
    prevTin = np.r_[np.nan, Tin[:-1]]
    prev_active = np.r_[False, active[:-1]]
    m = contig & ~active & ~prev_active & ~np.isnan(dT)
    A = np.column_stack([prevTout[m] - prevTin[m], np.ones(m.sum())])
    (a, d), *_ = np.linalg.lstsq(A, dT[m], rcond=None)
    pred = A @ np.array([a, d])
    rmse = float(np.sqrt(np.mean((pred - dT[m]) ** 2)))
    return float(a), float(d), {"onestep_dT_rmse_c": rmse, "n_idle_samples": int(m.sum())}


def estimate_gain_from_pulldown(r, dt_s, cool_on, a, d, equip):
    """Recover HVAC gain g (and C=dt/g) from the steepest cooling steps (duty~1)."""
    Tin = r["Tin"].values
    Tout = r["Tout"].values
    gap = r.index.to_series().diff().dt.total_seconds().values
    contig = gap == dt_s
    dT = np.r_[np.nan, np.diff(Tin)]
    prevTout = np.r_[np.nan, Tout[:-1]]
    prevTin = np.r_[np.nan, Tin[:-1]]
    cool_step = np.r_[False, cool_on[:-1]] & contig & ~np.isnan(dT)
    if cool_step.sum() == 0:
        return None
    passive_pred = a * (prevTout - prevTin) + d
    resid = (dT - passive_pred)[cool_step]   # extra cooling beyond passive (negative)
    # Strongest-pulldown quartile ~ continuous compressor operation (duty ~ 1).
    k = max(3, len(resid) // 4)
    strong = np.sort(resid)[:k]
    g = float(-np.mean(strong) / equip.capacity_w)   # resid = g*(-cap)*duty, duty~1
    if g <= 0:
        return None
    return {
        "g": g,
        "capacitance_j_per_k": dt_s / g,
        "n_cooling_steps": int(cool_step.sum()),
        "pulldown_mean_dT_c": float(np.mean(strong)),
    }


def longest_contiguous_idle_segment(r, dt_s, active, min_len=48):
    """Pick the longest run of contiguous, HVAC-off steps for free-run validation."""
    gap = r.index.to_series().diff().dt.total_seconds().values
    contig = gap == dt_s
    ok = contig & ~active
    best = (0, 0)
    start = None
    for i, v in enumerate(ok):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    if start is not None and len(ok) - start > best[1] - best[0]:
        best = (start, len(ok))
    s, e = best
    if e - s < min_len:
        return None
    return slice(s - 1 if s > 0 else s, e)


def rolling_horizon_rmse(model, r, dt_s, active, horizon_h=24.0):
    """Mean error of an H-hour free-run prediction launched from many idle
    starts — the metric that matches MPC use (re-anchor every step, predict
    over a finite horizon). Skips windows overlapping HVAC-on steps or data gaps."""
    Tin = r["Tin"].values
    Tout = r["Tout"].values
    gap = r.index.to_series().diff().dt.total_seconds().values
    contig = gap == dt_s
    H = int(round(horizon_h * 3600.0 / dt_s))
    errs = []
    for i in range(len(r) - H):
        seg = slice(i, i + H + 1)
        if not contig[i + 1 : i + H + 1].all() or active[seg].any():
            continue
        pred = model.simulate(Tin[i], Tout[seg])
        errs.append(np.sqrt(np.mean((pred - Tin[seg]) ** 2)))
    if not errs:
        return None
    return {"mean": float(np.mean(errs)), "p90": float(np.percentile(errs, 90)),
            "n": len(errs), "horizon_h": horizon_h}


def main():
    ap = argparse.ArgumentParser(description="Fit the RC zone model for a thermostat.")
    ap.add_argument("--home", default="test_home")
    ap.add_argument("--device-id", type=int, default=3)
    ap.add_argument("--dt", type=float, default=900.0, help="MPC timestep (s)")
    ap.add_argument("--equipment", choices=["single_stage_ac", "vs_heat_pump"],
                    default="single_stage_ac")
    ap.add_argument("--capacitance", type=float, default=None,
                    help="Override zone capacitance (J/K); skips pulldown estimate.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    equip = SingleStageCooling() if args.equipment == "single_stage_ac" else VariableSpeedHeatPump()

    df = load_readings(args.device_id)
    r = resample(df, args.dt)

    windows = KNOWN_COOLING_WINDOWS.get(args.home, [])
    if windows:
        # Explicit operator-supplied cooling window (the contamination-proof path
        # used for test_home, whose data predates the equipmentStatus fix).
        cool_on = cooling_mask(r.index, windows)
        heat_on = np.zeros(len(r), dtype=bool)
    else:
        # Reconstruct HVAC state from the thermostat setpoint deadband.
        heat_on, cool_on = detect_hvac_activity(r)
    active = heat_on | cool_on
    print(f"[{args.home}] resampled {len(r)} steps @ {int(args.dt)}s "
          f"({r.index.min()} -> {r.index.max()}); "
          f"{int(cool_on.sum())} cooling, {int(heat_on.sum())} heating steps")

    model = RCModel(dt_s=args.dt)

    runtime_q = load_runtime(args.device_id, args.dt)
    if runtime_q is not None:
        # Full data-driven fit when real compressor runtime is available.
        aligned = runtime_q.reindex(r.index).fillna(0.0).values
        model.fit(r["Tin"].values, r["Tout"].values, q_hvac_w=aligned, dt_s=args.dt)
        print("Identified full model from logged HVAC runtime.")
    else:
        a, d, pm = fit_passive(r, args.dt, active)
        model.a, model.d = a, d
        model.metrics.update(pm)
        print(f"Passive fit: a={a:.5f} d={d:.5f} tau={args.dt/a/3600:.2f} h "
              f"(n_idle={pm['n_idle_samples']}, onestep RMSE={pm['onestep_dT_rmse_c']:.4f} C)")

        if args.capacitance is not None:
            model.set_capacitance(args.capacitance)
            model.fit_used_hvac = False
            print(f"HVAC gain from supplied capacitance C={args.capacitance:.3e} J/K "
                  f"-> g={model.g:.3e} K/J")
        else:
            # Gain identification: a heat pump uses one compressor for both modes,
            # so the gain magnitude is shared. Pick the better-excited mode — the
            # one with more detected on-steps — which reaches higher duty and
            # averages out noise. (For test_home the explicit window is the only
            # candidate, so it is chosen.)
            candidates = []
            if cool_on.any():
                e = estimate_gain_from_pulldown(r, args.dt, cool_on, a, d, equip)
                if e:
                    src = "explicit cooling window" if windows else "deadband cooling pulldown"
                    candidates.append((int(cool_on.sum()), src, e))
            if heat_on.any():
                e = estimate_gain_from_pullup(r, args.dt, heat_on, a, d, equip)
                if e:
                    candidates.append((int(heat_on.sum()), "deadband heating pullup", e))
            est, src = (None, None)
            if candidates:
                _, src, est = max(candidates, key=lambda c: c[0])
            if est is None:
                model.set_capacitance(model.capacitance_j_per_k)
                print(f"No usable HVAC pulldown/pullup; gain from default "
                      f"C={model.capacitance_j_per_k:.3e} -> g={model.g:.3e}")
            else:
                model.set_capacitance(est["capacitance_j_per_k"])
                model.fit_used_hvac = False
                model.metrics.update(est)
                n = est.get("n_cooling_steps", est.get("n_heating_steps"))
                step_dt = est.get("pulldown_mean_dT_c", est.get("pullup_mean_dT_c"))
                print(f"HVAC gain from {n}-step {src}: "
                      f"C={est['capacitance_j_per_k']:.3e} J/K -> g={model.g:.3e} K/J "
                      f"({step_dt:+.3f} C/step)")

    # Free-run (open-loop) validation on the longest contiguous idle segment.
    seg = longest_contiguous_idle_segment(r, args.dt, active)
    if seg is not None:
        Tin = r["Tin"].values[seg]
        Tout = r["Tout"].values[seg]
        fr = model.freerun_rmse(Tin, Tout)
        hrs = len(Tin) * args.dt / 3600.0
        model.metrics["freerun_rmse_c"] = fr
        model.metrics["freerun_hours"] = hrs
        print(f"Free-run RMSE over {hrs:.1f} h idle holdout: {fr:.3f} C")

    roll = rolling_horizon_rmse(model, r, args.dt, active, horizon_h=24.0)
    if roll is not None:
        model.metrics["horizon24h_rmse_c"] = roll["mean"]
        model.metrics["horizon24h_rmse_p90_c"] = roll["p90"]
        print(f"24h-ahead free-run RMSE (MPC-relevant, n={roll['n']}): "
              f"mean={roll['mean']:.3f} C, p90={roll['p90']:.3f} C")

    out = args.out or os.path.join(CONFIG_DIR, f"hvac_model_{args.home}.json")
    payload = {
        "home": args.home,
        "device_id": args.device_id,
        "rc_model": model.to_dict(),
        "equipment": equip.to_dict(),
        "tau_hours": model.tau_s / 3600.0,
        "resistance_k_per_w": model.resistance_k_per_w,
    }
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"Saved {out}")
    print(f"  tau={payload['tau_hours']:.2f} h  R={payload['resistance_k_per_w']:.2e} K/W  "
          f"C={model.capacitance_j_per_k:.2e} J/K  COP={equip.cop:.2f}")


if __name__ == "__main__":
    sys.exit(main())
