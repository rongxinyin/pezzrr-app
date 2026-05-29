"""
Rule-based HVAC controller (RBC) for demand-response / power-outage events.

When an OpenADR DR or outage event is active, the RBC widens the thermostat
deadband: it raises the cooling setpoint and lowers the heating setpoint by a
fixed offset (default 2 F). The enlarged band between heating and cooling lets
the indoor temperature float without calling for the compressor, so the HVAC
coasts to idle/off for the event hours. Outside an event, no relaxation is
applied (the controller recommends the baseline setpoints unchanged).

Unlike the MPC, the RBC needs no fitted RC model and no forecast/price vector --
just the current thermostat setpoints and the active OpenADR events. It runs in
advisory / shadow mode: recommendations are logged to control_actions
(action_type=setpoint_adjust, triggered_by=DR_event); no device commands sent.

Trigger criteria and offset come from mpc_config.json -> defaults.rbc.

Standalone:
    venv/bin/python -m smart_home_ilc.rbc_controller --home test_home
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import psycopg2.extras

try:
    from . import mpc_data
except ImportError:  # running as a plain script, not a package module
    import mpc_data


F_TO_C_DELTA = 5.0 / 9.0  # degrees Fahrenheit -> degrees Celsius (difference)


def f_offset_to_c(offset_f: float) -> float:
    return float(offset_f) * F_TO_C_DELTA


# =====================================================================
# Active event detection
# =====================================================================
def active_events(conn, now_utc):
    """OpenADR events whose interval covers `now` (latest poll per event)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT DISTINCT ON (event_id)
                      event_id, event_name, program_name, priority,
                      period_type, price_per_kwh, interval_start, interval_end
               FROM openadr_events
               WHERE interval_start <= %s AND interval_end > %s
               ORDER BY event_id, ts DESC""",
            (now_utc, now_utc),
        )
        return cur.fetchall()


def is_trigger_event(ev, trigger_cfg) -> bool:
    """True if an active event should trigger band-widening (DR/outage)."""
    period_types = [p.lower() for p in trigger_cfg.get("period_types", [])]
    if ev.get("period_type") and ev["period_type"].lower() in period_types:
        return True
    keywords = [k.lower() for k in trigger_cfg.get("event_name_contains", [])]
    haystack = " ".join(
        str(ev.get(f) or "") for f in ("event_name", "program_name")
    ).lower()
    return any(k in haystack for k in keywords)


def _event_brief(ev):
    return {
        "event_id": ev.get("event_id"),
        "event_name": ev.get("event_name"),
        "program_name": ev.get("program_name"),
        "period_type": ev.get("period_type"),
        "interval_start": ev["interval_start"].isoformat() if ev.get("interval_start") else None,
        "interval_end": ev["interval_end"].isoformat() if ev.get("interval_end") else None,
    }


# =====================================================================
# Compute recommendation
# =====================================================================
def compute_rbc(home_name, mpc_cfg=None, now_utc=None, conn=None):
    """Compute the RBC setpoint recommendation for one home.

    Returns a result dict (status, event_active, baseline/recommended setpoints,
    the relaxed comfort band, and whether the HVAC is expected to idle)."""
    mpc_cfg = mpc_cfg or mpc_data._load_json("mpc_config.json")
    if home_name not in mpc_cfg["homes"]:
        raise SystemExit(f"{home_name!r} not configured in mpc_config.json")
    hc = mpc_cfg["homes"][home_name]
    rbc_cfg = mpc_cfg["defaults"].get("rbc", {})
    offset_f = float(rbc_cfg.get("setpoint_offset_f", 2.0))
    offset_c = f_offset_to_c(offset_f)
    trigger_cfg = rbc_cfg.get("trigger", {})
    mode = hc.get("mode", "both")
    comfort = hc.get("comfort", {})

    now_utc = now_utc or datetime.now(timezone.utc)
    own = conn is None
    conn = conn or mpc_data._connect()
    try:
        home_id = mpc_data.get_home_id(conn, home_name)
        device_id = hc["device_id"]
        state = mpc_data.latest_indoor_state(conn, device_id)

        # Baseline setpoints: the thermostat's current setpoints, falling back to
        # the configured comfort edges when a setpoint isn't reported.
        base_cool = state.get("cool_setpoint_c")
        base_heat = state.get("heat_setpoint_c")
        if base_cool is None:
            base_cool = comfort.get("cool_max_c")
        if base_heat is None:
            base_heat = comfort.get("heat_min_c")
        base_cool = float(base_cool) if base_cool is not None else None
        base_heat = float(base_heat) if base_heat is not None else None
        indoor = float(state["indoor_temp_c"])

        evs = active_events(conn, now_utc)
        triggers = [e for e in evs if is_trigger_event(e, trigger_cfg)]
        event_active = bool(triggers)

        do_cool = mode in ("cool", "both")
        do_heat = mode in ("heat", "both")
        rec_cool, rec_heat = base_cool, base_heat
        if event_active:
            if do_cool and base_cool is not None:
                rec_cool = base_cool + offset_c   # raise cooling setpoint
            if do_heat and base_heat is not None:
                rec_heat = base_heat - offset_c   # lower heating setpoint

        # With the widened band the HVAC should idle if indoor sits inside it.
        idle = ((rec_cool is None or indoor <= rec_cool) and
                (rec_heat is None or indoor >= rec_heat))

        return {
            "status": "ok",
            "control_strategy": "rbc",
            "home_id": home_id,
            "device_id": device_id,
            "now_utc": now_utc.isoformat(),
            "event_active": event_active,
            "active_events": [_event_brief(e) for e in triggers],
            "n_events_overlapping": len(evs),
            "mode": mode,
            "offset_f": offset_f,
            "offset_c": round(offset_c, 4),
            "indoor_temp_c": round(indoor, 3),
            "baseline_cool_setpoint_c": base_cool,
            "baseline_heat_setpoint_c": base_heat,
            "recommended_cool_setpoint_c": round(rec_cool, 3) if rec_cool is not None else None,
            "recommended_heat_setpoint_c": round(rec_heat, 3) if rec_heat is not None else None,
            "relaxed_band_c": [
                round(rec_heat, 3) if rec_heat is not None else None,
                round(rec_cool, 3) if rec_cool is not None else None,
            ],
            "hvac_expected_idle": idle,
            "indoor_reading_ts": state["ts"].isoformat(),
        }
    finally:
        if own:
            conn.close()


# =====================================================================
# Advisory write + dedup
# =====================================================================
def last_rbc_event_active(conn, device_id):
    """The event_active flag of the most recent RBC action for this device, or None."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT command_payload->>'event_active'
               FROM control_actions
               WHERE device_id=%s AND triggered_by='DR_event'
                 AND command_payload->>'control_strategy'='rbc'
               ORDER BY ts DESC LIMIT 1""",
            (device_id,),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return row[0].lower() == "true"


def write_rbc_advisory(result: dict, mpc_cfg=None, conn=None):
    """Log an RBC recommendation to control_actions (shadow mode: no command)."""
    mpc_cfg = mpc_cfg or mpc_data._load_json("mpc_config.json")
    rbc_cfg = mpc_cfg["defaults"].get("rbc", {})
    payload = {"shadow_mode": mpc_cfg["defaults"]["advisory"].get("shadow_mode", True),
               **result}
    own = conn is None
    conn = conn or mpc_data._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO control_actions
                       (home_id, device_id, action_type, triggered_by,
                        command_payload, success)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING action_id""",
                (result["home_id"], result["device_id"],
                 rbc_cfg.get("action_type", "setpoint_adjust"),
                 rbc_cfg.get("triggered_by", "DR_event"),
                 json.dumps(payload), True),
            )
            action_id = cur.fetchone()[0]
        conn.commit()
        return action_id
    finally:
        if own:
            conn.close()


def main():
    ap = argparse.ArgumentParser(description="Rule-based DR/outage HVAC controller.")
    ap.add_argument("--home", default="test_home")
    ap.add_argument("--write", action="store_true",
                    help="Log the advisory to control_actions (default: print only).")
    args = ap.parse_args()
    res = compute_rbc(args.home)
    print(f"[{args.home}] event_active={res['event_active']} "
          f"({res['n_events_overlapping']} overlapping) mode={res['mode']} "
          f"indoor={res['indoor_temp_c']}C")
    print(f"  baseline  cool={res['baseline_cool_setpoint_c']} "
          f"heat={res['baseline_heat_setpoint_c']} C")
    print(f"  recommend cool={res['recommended_cool_setpoint_c']} "
          f"heat={res['recommended_heat_setpoint_c']} C "
          f"(offset {res['offset_f']}F = {res['offset_c']}C)")
    print(f"  relaxed band {res['relaxed_band_c']} C  hvac_expected_idle={res['hvac_expected_idle']}")
    for e in res["active_events"]:
        print(f"  trigger event: {e['event_name']} [{e['period_type']}] "
              f"{e['interval_start']} -> {e['interval_end']}")
    if args.write:
        action_id = write_rbc_advisory(res)
        print(f"  wrote control_actions action_id={action_id}")


if __name__ == "__main__":
    main()
