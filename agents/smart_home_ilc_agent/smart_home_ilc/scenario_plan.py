"""
Full-home operation-scenario controller for the smart-home ILC.

The HVAC supervisor (hvac_supervisor.py) resolves *one* operation scenario per
home from live signals and drives the thermostat. This module reuses that same
resolution and extends it across the rest of the home energy system -- the smart
panel circuits, the plug-in battery (source / islanding), and the optional smart
plug -- producing an ordered *sequence of operations* per scenario:

    normal                - no overrides. Grid source, all circuits on, plugs on,
                            thermostat at baseline (MPC optimizes cost separately).
    load_peak_management  - a DR / peak-price event. Shed non-priority circuits,
      (DR event)            switch plugs off, widen the thermostat band, and put
                            the panel on battery (islanding). Verify the retained
                            load stays under the battery inverter limit.
    capacity_management   - whole-home load near the main-breaker limit. Same load
      (over threshold)      shed + plugs off + band-widen, but stay grid-tied (no
                            islanding) -- this is a TOU/over-threshold trim, not a
                            backup event.
    resiliency            - grid outage / PSPS. Island on battery, hard-widen the
      (no grid / PSPS)      thermostat, and shed BOTH non-essential and essential
                            circuits, keeping only the Critical "must have" loads.

Load is always shed lowest-priority-first (non_essential, then essential); the
Critical tier is never shed. When the panel is islanded the retained load is
checked against battery.max_output_w (7200 W); if it would exceed the inverter
the controller escalates to the next shed tier until feasible (never shedding
critical) and reports the result.

Advisory / shadow mode, exactly like the HVAC layer: the full sequence is logged
to control_advisories (controller='ilc'); NO device commands are sent. A future
real-actuation path would replay the same sequence into control_actions ->
VOLTTRON. Nothing here imports volttron, so it runs under a plain venv python.

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

# Shed order, lowest priority first. 'critical' is intentionally absent: the
# Must-have tier is never shed, even under resiliency islanding.
SHED_ORDER = ["non_essential", "essential"]


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

    Power comes from the most recent panel_circuit_readings timestamp for the
    home (the collector writes all channels at one ts). Missing readings -> 0 W
    so an un-instrumented circuit is treated as no load rather than crashing the
    feasibility math."""
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


def _shed_set(circuits, shed_tiers):
    """circuit_ids to switch off: controllable circuits whose priority tier is in
    shed_tiers. Critical/non-controllable circuits are always retained."""
    tiers = set(shed_tiers)
    return {c["circuit_id"] for c in circuits
            if c["is_controllable"] and not c["is_critical"]
            and c["circuit_priority"] in tiers}


def _retained_load_w(circuits, shed_ids):
    return round(sum(c["power_w"] for c in circuits if c["circuit_id"] not in shed_ids), 1)


def escalate_shed(circuits, shed_ids, applied_tiers, max_out_w, soc_ok):
    """Under islanding, shed lower-priority tiers until the retained load clears
    the inverter limit (or only critical remains). Pure function -- no DB.

    Returns (shed_ids, applied_tiers, retained_w, escalation, feasible). The
    critical tier is never added, so a home whose critical load alone exceeds the
    inverter reports feasible=False rather than shedding a must-have circuit.
    Islanding is also infeasible when the battery SOC is below minimum (soc_ok)."""
    shed_ids = set(shed_ids)
    applied_tiers = list(applied_tiers)
    retained_w = _retained_load_w(circuits, shed_ids)
    escalation = []
    for tier in SHED_ORDER:
        if retained_w < max_out_w:
            break
        if tier in applied_tiers:
            continue
        applied_tiers.append(tier)
        shed_ids |= _shed_set(circuits, [tier])
        retained_w = _retained_load_w(circuits, shed_ids)
        escalation.append({"tier": tier, "retained_load_w": retained_w})
    feasible = retained_w < max_out_w and soc_ok
    return shed_ids, applied_tiers, retained_w, escalation, feasible


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

        policy = lm.get("scenarios", {}).get(scenario, {})
        shed_tiers = list(policy.get("shed_tiers", []))
        battery_source = policy.get("battery_source", "grid")
        plugs = policy.get("plugs", "on")
        require_feasible = bool(policy.get("require_battery_feasible", False))

        pdev = panel_device_id(conn, home_id)
        circuits = circuits_with_power(conn, pdev, home_id) if pdev else []
        batt = battery_state(conn, home_id)
        soc = float(batt["soc_pct"]) if batt and batt.get("soc_pct") is not None else None
        total_load_w = round(sum(c["power_w"] for c in circuits), 1)

        # Initial shed set from the scenario policy.
        shed_ids = _shed_set(circuits, shed_tiers)
        applied_tiers = list(shed_tiers)

        islanding = battery_source == "islanding"
        soc_ok = soc is None or soc >= min_soc
        retained_w = _retained_load_w(circuits, shed_ids)

        # Feasibility escalation: under islanding the retained load must clear the
        # inverter limit. Shed the next lower-priority tier until it does (never
        # critical).
        escalation = []
        feasible = True
        if islanding and require_feasible:
            shed_ids, applied_tiers, retained_w, escalation, feasible = escalate_shed(
                circuits, shed_ids, applied_tiers, max_out_w, soc_ok)

        shed = [c for c in circuits if c["circuit_id"] in shed_ids]
        retained = [c for c in circuits if c["circuit_id"] not in shed_ids]

        sequence = _sequence(scenario, home_name, cfg, conn, strategy,
                             shed, plugs, battery_source, islanding, soc_ok)

        return {
            "controller": "ilc",
            "home_name": home_name,
            "home_id": home_id,
            "panel_device_id": pdev,
            "now_utc": now_utc.isoformat(),
            "operation_scenario": scenario,
            "scenario_source": scen["source"],
            "scenario_reason": scen["reason"],
            "scenario_signals": scen.get("signals", {}),
            "control_strategy": strategy,
            "triggered_by": hvac_supervisor.SCENARIO_TRIGGERED_BY.get(scenario, "ILC_agent"),
            "policy": {
                "shed_tiers_configured": shed_tiers,
                "shed_tiers_applied": applied_tiers,
                "battery_source": battery_source,
                "plugs": plugs,
                "require_battery_feasible": require_feasible,
            },
            "battery": {
                "device_id": batt["device_id"] if batt else None,
                "soc_pct": soc,
                "min_soc_pct": min_soc,
                "soc_ok_for_islanding": soc_ok,
                "max_output_w": max_out_w,
            },
            "load": {
                "total_circuit_load_w": total_load_w,
                "retained_load_w": retained_w,
                "battery_headroom_w": round(max_out_w - retained_w, 1),
                "islanding": islanding,
                "battery_feasible": feasible,
                "escalation": escalation,
            },
            "shed_circuits": [
                {"circuit_id": c["circuit_id"], "channel_num": c["channel_num"],
                 "circuit_name": c["circuit_name"], "priority": c["circuit_priority"],
                 "power_w": c["power_w"], "ties": c["load_description"]}
                for c in shed
            ],
            "retained_circuits": [
                {"circuit_id": c["circuit_id"], "channel_num": c["channel_num"],
                 "circuit_name": c["circuit_name"], "priority": c["circuit_priority"],
                 "power_w": c["power_w"]}
                for c in retained
            ],
            "sequence": sequence,
        }
    finally:
        if own:
            conn.close()


def _sequence(scenario, home_name, cfg, conn, strategy, shed, plugs,
              battery_source, islanding, soc_ok):
    """Ordered, safe sequence of operations for the scenario.

    Order matters for the inverter: shed non-critical load and ease the HVAC
    BEFORE transferring the panel onto the battery, so islanding never starts
    against the full pre-event load. Restoration (returning to normal) is the
    reverse and handled by the next cycle resolving back to 'normal'."""
    steps = []
    n = 0

    if scenario == "normal":
        n += 1
        steps.append({"order": n, "target": "all",
                      "action": "hold_baseline",
                      "detail": "No override: grid source, all circuits on, "
                                "plugs on, thermostat at MPC/baseline."})
        steps.append({"order": n, **_thermostat_step(home_name, cfg, conn, scenario, strategy)})
        return steps

    # 1) Shed circuits, lowest priority first.
    if shed:
        for tier in ("non_essential", "essential"):
            tier_circuits = [c for c in shed if c["circuit_priority"] == tier]
            if not tier_circuits:
                continue
            n += 1
            steps.append({
                "order": n, "target": "panel_circuits", "action": "channel_disable",
                "priority_tier": tier,
                "channels": [c["channel_num"] for c in tier_circuits],
                "circuit_ids": [c["circuit_id"] for c in tier_circuits],
                "detail": f"Switch OFF {len(tier_circuits)} {tier} circuit(s).",
            })

    # 2) Smart plugs.
    if plugs == "off":
        n += 1
        steps.append({"order": n, "target": "plug", "action": "relay_toggle",
                      "enabled": False, "detail": "Switch OFF smart plug(s)."})

    # 3) Thermostat band-widen / setpoint reset.
    n += 1
    steps.append({"order": n, **_thermostat_step(home_name, cfg, conn, scenario, strategy)})

    # 4) Battery source transfer LAST (only after load is reduced).
    if islanding:
        n += 1
        if soc_ok:
            steps.append({"order": n, "target": "battery", "action": "eps_toggle",
                          "enabled": True,
                          "detail": "Transfer panel to islanding / EPS mode, "
                                    "battery as power source."})
        else:
            steps.append({"order": n, "target": "battery", "action": "eps_toggle",
                          "enabled": False,
                          "detail": "Battery SOC below minimum: islanding NOT "
                                    "recommended; stay grid-tied if possible."})
    return steps


# =====================================================================
# Advisory write (shadow mode)
# =====================================================================
def write_plan_advisory(plan, cfg=None, conn=None):
    """Log the ILC plan to control_advisories (shadow mode, controller='ilc').

    Writes one whole-home summary row (device_id=panel, circuit_id NULL) carrying
    the full sequence in `detail`, plus one row per shed circuit (channel_disable)
    so the per-circuit intent is queryable and a future real-actuation path can
    fan the same rows into control_actions. Returns the summary advisory_id."""
    cfg = cfg or mpc_data._load_json("mpc_config.json")
    shadow = cfg["defaults"]["advisory"].get("shadow_mode", True)
    triggered_by = plan["triggered_by"]
    scenario = plan["operation_scenario"]
    summary_action = "release" if scenario == "normal" else "curtail"

    own = conn is None
    conn = conn or mpc_data._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO control_advisories
                       (home_id, device_id, circuit_id, controller, action_type,
                        triggered_by, operation_scenario, scenario_source,
                        shadow_mode, detail)
                   VALUES (%s, %s, NULL, 'ilc', %s, %s, %s, %s, %s, %s)
                   RETURNING advisory_id""",
                (plan["home_id"], plan["panel_device_id"], summary_action,
                 triggered_by, scenario, plan["scenario_source"],
                 shadow, json.dumps({"shadow_mode": shadow, **plan})),
            )
            summary_id = cur.fetchone()[0]

            for c in plan["shed_circuits"]:
                cur.execute(
                    """INSERT INTO control_advisories
                           (home_id, device_id, circuit_id, controller, action_type,
                            triggered_by, operation_scenario, scenario_source,
                            shadow_mode, detail)
                       VALUES (%s, %s, %s, 'ilc', 'channel_disable', %s, %s, %s, %s, %s)""",
                    (plan["home_id"], plan["panel_device_id"], c["circuit_id"],
                     triggered_by, scenario, plan["scenario_source"], shadow,
                     json.dumps({"shadow_mode": shadow, "summary_advisory_id": summary_id,
                                 **c})),
                )
        conn.commit()
        return summary_id
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
    l = p["load"]
    print(f"  battery soc={b['soc_pct']}% (min {b['min_soc_pct']}%) max_out={b['max_output_w']}W "
          f"soc_ok_island={b['soc_ok_for_islanding']}")
    print(f"  load total={l['total_circuit_load_w']}W retained={l['retained_load_w']}W "
          f"headroom={l['battery_headroom_w']}W islanding={l['islanding']} "
          f"feasible={l['battery_feasible']}")
    if l["escalation"]:
        print(f"  feasibility escalation: {l['escalation']}")
    if p["shed_circuits"]:
        chans = ", ".join(f"ch{c['channel_num']}({c['priority'][:3]},{c['power_w']}W)"
                          for c in p["shed_circuits"])
        print(f"  shed: {chans}")
    else:
        print("  shed: none")
    print("  sequence:")
    for s in p["sequence"]:
        extra = ""
        if s.get("channels"):
            extra = f" channels={s['channels']}"
        elif s.get("target") == "thermostat":
            extra = (f" cool->{s.get('recommended_cool_setpoint_c')}C "
                     f"heat->{s.get('recommended_heat_setpoint_c')}C"
                     if s.get("action") == "band_widen" else "")
        print(f"    [{s['order']}] {s['target']}/{s['action']}: "
              f"{s.get('detail','')}{extra}")


def main():
    ap = argparse.ArgumentParser(description="Build the full-home ILC operation sequence for a home.")
    ap.add_argument("--home", default="test_home")
    ap.add_argument("--scenario", default=None,
                    choices=["normal", "load_peak_management", "capacity_management", "resiliency"],
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
            print(f"  wrote control_advisories summary advisory_id={advisory_id} "
                  f"(+{len(plan['shed_circuits'])} circuit rows)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
