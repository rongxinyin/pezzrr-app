"""
Full-home operation-scenario controller for the smart-home ILC.

The HVAC supervisor (hvac_supervisor.py) resolves *one* operation scenario per
home from live signals and drives the thermostat. This module reuses that same
resolution and extends it to the EcoFlow Smart Home Panel battery mode,
producing an ordered *sequence of operations* per scenario.

Circuit shedding is NOT directly controllable through the official EcoFlow API.
On the Smart Home Panel, load shedding is a property of the 'EPS backup' battery
mode: when EPS backup is on, the panel sheds circuits by its own predefined
circuit priorities. The central controller therefore drives the panel BATTERY
MODE (Savings mode + EPS backup), not individual circuits:

    normal                   - Savings mode off, EPS backup off. Grid source,
                               thermostat at MPC/baseline.
    load_management_tou      - TOU peak-price period (no DR): Savings 'time_of_use'
      (TOU peak price)         on, EPS off; thermostat at baseline.
    load_management_dr       - DR event active: Savings 'self-powered' on, EPS off;
      (DR event)               widen the thermostat band.
    load_management_capacity - whole-home load near the main-breaker limit. Battery
      (over threshold)         untouched; shed non-essential circuits by lowering
                               their max input current; thermostat at baseline.
    capacity_management      - design_config-3 home islanded by the external
      (Config #3 islanded)     capacity switch (grid offline): EPS backup on,
                               Savings off; widen the band. The disconnect itself
                               is external (out of scope); we only react to it.
    resiliency               - grid outage / PSPS. EPS backup on (the panel sheds
      (no grid / PSPS)         non-critical circuits by predefined priority),
                               Savings off; hard-widen the thermostat.

The battery mode maps to the same dispatch contract the dashboard 'Control &
dispatch' panel-mode card uses (action_type='set_operating_mode',
target.kind='battery_mode'): smartBackupMode (0=off, 1=time_of_use,
2=self_powered, 3=timed) and epsModeInfo (EPS backup bool).

Advisory / shadow mode, exactly like the HVAC layer: the resolved mode is logged
to control_advisories (controller='ilc', action_type='set_operating_mode'); NO
device commands are sent. A future real-actuation path would replay the same
mode into control_actions -> VOLTTRON. Nothing here imports volttron, so it runs
under a plain venv python.

Standalone:
    venv/bin/python3 -m smart_home_ilc.scenario_plan --home test_home
    venv/bin/python3 -m smart_home_ilc.scenario_plan --home test_home --scenario resiliency
    venv/bin/python3 -m smart_home_ilc.scenario_plan --home test_home --write
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

import psycopg2.extras

try:
    from . import mpc_data, rbc_controller, hvac_supervisor
except ImportError:  # running as a plain script, not a package module
    import mpc_data
    import rbc_controller
    import hvac_supervisor

log = logging.getLogger(__name__)

# Panel Savings-mode (smartBackupMode) values, matching the dashboard selector.
SAVINGS_MODE_LABEL = {0: "off", 1: "time_of_use", 2: "self_powered", 3: "timed"}


# =====================================================================
# Device / telemetry reads
# =====================================================================
def panel_device_id(conn, home_id):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT device_id FROM devices
               WHERE home_id=%s AND device_type='smart_panel'
               ORDER BY device_id LIMIT 1""",
            (home_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def battery_state(conn, home_id):
    """Latest (device_id, soc_pct, power_w) for the home's battery, or None."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT br.device_id, br.soc_pct, br.power_w, br.ts
               FROM battery_readings br
               JOIN devices d ON d.device_id = br.device_id
               WHERE d.home_id=%s AND d.device_type='battery'
               ORDER BY br.ts DESC LIMIT 1""",
            (home_id,),
        )
        return cur.fetchone()


def circuits_with_power(conn, panel_dev_id, home_id):
    """Every circuit of a panel with its latest measured power (W).

    Reported for context only -- under EPS backup the panel sheds these by its
    own predefined priority; the controller does not switch channels directly.
    Power comes from the most recent panel_circuit_readings row per circuit;
    missing readings -> 0 W."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT pc.circuit_id, pc.channel_num, pc.circuit_name,
                      pc.circuit_priority, pc.is_critical, pc.is_controllable,
                      pc.load_description,
                      COALESCE(r.power_w, 0)::float AS power_w
               FROM panel_circuits pc
               LEFT JOIN LATERAL (
                   SELECT power_w FROM panel_circuit_readings r
                   WHERE r.circuit_id = pc.circuit_id
                   ORDER BY r.ts DESC LIMIT 1
               ) r ON TRUE
               WHERE pc.device_id=%s
               ORDER BY pc.channel_num""",
            (panel_dev_id,),
        )
        return cur.fetchall()


def last_logged_ilc_scenario(conn, device_id):
    """operation_scenario of the most recent ILC plan advisory for this panel,
    or None. Used to dedup: only re-log when the scenario transitions."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT operation_scenario FROM control_advisories
               WHERE device_id=%s AND controller='ilc'
               ORDER BY ts DESC LIMIT 1""",
            (device_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


# =====================================================================
# Plan construction
# =====================================================================
def _thermostat_step(home_name, cfg, conn, scenario, strategy):
    """Describe the thermostat action for the scenario, reusing the HVAC layer.

    For an MPC home in normal/load-peak the action is 'mpc' (the MPC periodic
    owns the real setpoint advisory); we only report it here. For any band-widen
    case we compute the recommended (widened) setpoints from the scenario offsets
    via the same primitive the RBC uses, so the sequence shows concrete numbers."""
    action = hvac_supervisor.scenario_action(scenario, strategy)
    cool_off, heat_off = hvac_supervisor.scenario_offsets(scenario, cfg)
    step = {
        "target": "thermostat",
        "action": action,
        "cool_offset_f": cool_off,
        "heat_offset_f": heat_off,
    }
    if action == "band_widen":
        try:
            r = rbc_controller.relax_setpoints(
                home_name, cool_off, heat_off, mpc_cfg=cfg, conn=conn,
                scenario=scenario)
            step.update({
                "device_id": r["device_id"],
                "baseline_cool_setpoint_c": r["baseline_cool_setpoint_c"],
                "baseline_heat_setpoint_c": r["baseline_heat_setpoint_c"],
                "recommended_cool_setpoint_c": r["recommended_cool_setpoint_c"],
                "recommended_heat_setpoint_c": r["recommended_heat_setpoint_c"],
                "hvac_expected_idle": r["hvac_expected_idle"],
            })
        except SystemExit as e:
            step["note"] = f"setpoint detail unavailable: {e}"
    return step


def _battery_mode(scenario, policy, signals):
    """Resolve the panel battery mode (smartBackupMode + epsModeInfo) for the
    scenario from the load_management policy. Each scenario now carries a single
    `battery_mode` mapping (the old load_peak DR sub-case became its own
    load_management_dr scenario). Returns the chosen mode plus readable labels."""
    mode = dict(policy.get("battery_mode", {}))
    sbm = int(mode.get("smartBackupMode", 0))
    eps = bool(mode.get("epsModeInfo", False))
    return {
        "smartBackupMode": sbm,
        "savings_mode": SAVINGS_MODE_LABEL.get(sbm, str(sbm)),
        "epsModeInfo": eps,
        "eps_backup": "on" if eps else "off",
        "dr_event": bool(signals.get("dr_event")),
        "policy_used": "battery_mode",
    }


def build_plan(home_name, cfg=None, conn=None, now_utc=None, scenario_override=None):
    """Resolve the scenario and build the full-home operation sequence.

    scenario_override forces a specific scenario (for testing / explicit drills),
    bypassing live detection. Returns the plan dict; does not write anything."""
    cfg = cfg or mpc_data._load_json("mpc_config.json")
    if home_name not in cfg["homes"]:
        raise SystemExit(f"{home_name!r} not configured in mpc_config.json")
    lm = cfg["defaults"].get("load_management", {})
    batt_cfg = lm.get("battery", {})
    max_out_w = float(batt_cfg.get("max_output_w", 7200))
    min_soc = float(batt_cfg.get("min_soc_pct", 20))
    reserve_soc = float(batt_cfg.get("reserve_soc_pct", 10))

    now_utc = now_utc or datetime.now(timezone.utc)
    own = conn is None
    conn = conn or mpc_data._connect()
    try:
        home_id = mpc_data.get_home_id(conn, home_name)
        strategy = hvac_supervisor.home_strategy(home_name, cfg)

        if scenario_override:
            scen = {"scenario": scenario_override, "source": "explicit",
                    "reason": "forced via scenario_override", "signals": {}}
        else:
            scen = hvac_supervisor.resolve_scenario(home_name, cfg, conn, now_utc=now_utc)
        scenario = scen["scenario"]
        signals = scen.get("signals", {})

        policy = lm.get("scenarios", {}).get(scenario, {})
        bm = _battery_mode(scenario, policy, signals)

        pdev = panel_device_id(conn, home_id)
        circuits = circuits_with_power(conn, pdev, home_id) if pdev else []
        batt = battery_state(conn, home_id)
        soc = float(batt["soc_pct"]) if batt and batt.get("soc_pct") is not None else None
        total_load_w = round(sum(c["power_w"] for c in circuits), 1)
        soc_ok = soc is None or soc >= min_soc

        sequence = _sequence(scenario, home_name, cfg, conn, strategy, bm, pdev, circuits)

        return {
            "controller": "ilc",
            "home_name": home_name,
            "home_id": home_id,
            "panel_device_id": pdev,
            "now_utc": now_utc.isoformat(),
            "operation_scenario": scenario,
            "scenario_source": scen["source"],
            "scenario_reason": scen["reason"],
            "scenario_signals": signals,
            "control_strategy": strategy,
            "triggered_by": hvac_supervisor.SCENARIO_TRIGGERED_BY.get(scenario, "ILC_agent"),
            "battery_mode": bm,
            "battery": {
                "device_id": batt["device_id"] if batt else None,
                "soc_pct": soc,
                "min_soc_pct": min_soc,
                "reserve_soc_pct": reserve_soc,
                "soc_ok": soc_ok,
                "max_output_w": max_out_w,
            },
            "load": {"total_circuit_load_w": total_load_w},
            "circuits": [
                {"circuit_id": c["circuit_id"], "channel_num": c["channel_num"],
                 "circuit_name": c["circuit_name"], "priority": c["circuit_priority"],
                 "is_critical": c["is_critical"], "power_w": c["power_w"]}
                for c in circuits
            ],
            "sequence": sequence,
        }
    finally:
        if own:
            conn.close()


def _circuit_current_limit_step(order, cfg, circuits):
    """Advisory step: shed non-essential circuits by lowering their max input
    current (PD303 setAmp) so the panel trips them off. The agent only logs the
    advisory; the actual per-circuit write is actuated by the dashboard's guarded
    /control/dispatch path. Battery mode stays off for this scenario."""
    lm = cfg["defaults"].get("load_management", {})
    ccl = lm.get("circuit_current_limit", {})
    floor_a = float(ccl.get("non_essential_max_input_a", 0))
    targets = [
        {"circuit_id": c["circuit_id"], "channel_num": c["channel_num"],
         "circuit_name": c["circuit_name"], "power_w": c["power_w"],
         "max_input_a": floor_a}
        for c in circuits if c["circuit_priority"] == "non_essential"
        and c["is_controllable"] and not c["is_critical"]
    ]
    shed_w = round(sum(t["power_w"] for t in targets), 1)
    return {
        "order": order,
        "target": "circuit_current_limit",
        "action": "limit_input_current",
        "params": {"non_essential_max_input_a": floor_a, "circuits": targets},
        "shadow": True,
        "detail": (f"Shed {len(targets)} non-essential circuit(s) (~{shed_w}W) by "
                   f"capping max input current at {floor_a}A."),
    }


def _sequence(scenario, home_name, cfg, conn, strategy, bm, panel_device_id, circuits):
    """Ordered, safe sequence of operations for the scenario.

    Order matters for the inverter: ease the HVAC BEFORE the panel transfers to
    EPS backup, so the panel never starts shedding against the full pre-event
    load. The battery-mode step is always emitted (it also restores Savings/EPS
    to off when the home resolves back to 'normal')."""
    steps = [{"order": 1, **_thermostat_step(home_name, cfg, conn, scenario, strategy)}]

    detail = (f"Set panel battery mode: Savings '{bm['savings_mode']}', "
              f"EPS backup {bm['eps_backup']}.")
    if scenario == "capacity_management":
        detail += (" Grid disconnect is performed by the external capacity switch "
                   "(out of scope); the panel auto-islands and runs EPS-on.")
    elif bm["epsModeInfo"]:
        detail += (" Under EPS backup the panel sheds non-critical circuits by "
                   "its predefined priority (shed is not directly controllable).")
    steps.append({
        "order": 2,
        "target": "battery_mode",
        "action": "set_operating_mode",
        "device_id": panel_device_id,
        "params": {"smartBackupMode": bm["smartBackupMode"],
                   "epsModeInfo": bm["epsModeInfo"]},
        "detail": detail,
    })

    # load_management_capacity sheds circuits instead of touching the battery.
    if scenario == "load_management_capacity":
        steps.append(_circuit_current_limit_step(len(steps) + 1, cfg, circuits))
    return steps


# =====================================================================
# Advisory write (shadow mode)
# =====================================================================
def write_plan_advisory(plan, cfg=None, conn=None):
    """Log the ILC plan to control_advisories (shadow mode, controller='ilc').

    Writes one whole-home row (device_id=panel, circuit_id NULL,
    action_type='set_operating_mode') carrying the resolved battery mode and the
    full plan in `detail`. A future real-actuation path replays the battery_mode
    params into control_actions -> VOLTTRON. Returns the advisory_id."""
    cfg = cfg or mpc_data._load_json("mpc_config.json")
    shadow = cfg["defaults"]["advisory"].get("shadow_mode", True)
    triggered_by = plan["triggered_by"]
    scenario = plan["operation_scenario"]

    own = conn is None
    conn = conn or mpc_data._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO control_advisories
                       (home_id, device_id, circuit_id, controller, action_type,
                        triggered_by, operation_scenario, scenario_source,
                        shadow_mode, detail)
                   VALUES (%s, %s, NULL, 'ilc', 'set_operating_mode', %s, %s, %s, %s, %s)
                   RETURNING advisory_id""",
                (plan["home_id"], plan["panel_device_id"], triggered_by, scenario,
                 plan["scenario_source"], shadow,
                 json.dumps({"shadow_mode": shadow, **plan})),
            )
            advisory_id = cur.fetchone()[0]
        conn.commit()
        return advisory_id
    finally:
        if own:
            conn.close()


# =====================================================================
# Standalone reporting
# =====================================================================
def _print_plan(plan):
    p = plan
    print(f"[{p['home_name']}] scenario={p['operation_scenario']} "
          f"({p['scenario_source']}: {p['scenario_reason']}) strategy={p['control_strategy']}")
    b = p["battery"]
    bm = p["battery_mode"]
    print(f"  battery soc={b['soc_pct']}% (min {b['min_soc_pct']}%) max_out={b['max_output_w']}W "
          f"soc_ok={b['soc_ok']}")
    print(f"  battery mode -> Savings '{bm['savings_mode']}' "
          f"(smartBackupMode={bm['smartBackupMode']}), EPS backup {bm['eps_backup']} "
          f"[{bm['policy_used']}, dr_event={bm['dr_event']}]")
    print(f"  total circuit load={p['load']['total_circuit_load_w']}W "
          f"({len(p['circuits'])} circuits)")
    print("  sequence:")
    for s in p["sequence"]:
        extra = ""
        if s.get("target") == "thermostat" and s.get("action") == "band_widen":
            extra = (f" cool->{s.get('recommended_cool_setpoint_c')}C "
                     f"heat->{s.get('recommended_heat_setpoint_c')}C")
        elif s.get("target") == "battery_mode":
            extra = f" params={s['params']}"
        print(f"    [{s['order']}] {s['target']}/{s['action']}: "
              f"{s.get('detail','')}{extra}")


def main():
    ap = argparse.ArgumentParser(description="Build the full-home ILC operation sequence for a home.")
    ap.add_argument("--home", default="test_home")
    ap.add_argument("--scenario", default=None,
                    choices=["normal", "load_management_tou", "load_management_dr",
                             "load_management_capacity", "capacity_management", "resiliency"],
                    help="Force a scenario (default: auto-detect from live signals).")
    ap.add_argument("--write", action="store_true",
                    help="Log the plan to control_advisories (default: print only).")
    args = ap.parse_args()
    cfg = mpc_data._load_json("mpc_config.json")
    conn = mpc_data._connect()
    try:
        plan = build_plan(args.home, cfg=cfg, conn=conn, scenario_override=args.scenario)
        _print_plan(plan)
        if args.write:
            advisory_id = write_plan_advisory(plan, cfg=cfg, conn=conn)
            print(f"  wrote control_advisories advisory_id={advisory_id}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
