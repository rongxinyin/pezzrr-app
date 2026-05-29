"""
Operation-scenario supervisor for the thermostat HVAC controllers.

`operation_scenario` is the high-level mode input for a home (Controller Memo,
design configuration #2). It sits above the per-home control_strategy (mpc|rbc)
and decides *what the home should be doing* this cycle; the strategy decides
*how*. Four live scenarios:

    normal               - no shed. MPC homes optimize cost; RBC homes hold baseline.
    load_peak_management - a DR / peak-price event is active. MPC homes optimize
                           against the event price; RBC homes widen the band.
    capacity_management  - panel load is near the 60A main-breaker limit
                           (present amperage >= capacity_trigger_pct), OR the
                           home-load forecast anticipates a breach within the
                           lookahead window. All homes widen the band.
    resiliency           - grid outage / EPS mode. All homes widen the band hard.

The scenario is either set explicitly (per-home `operation_scenario`, or the
`defaults.operation_scenario`) or, when that is "auto", resolved each cycle from
live signals (panel telemetry + active OpenADR events) by priority:
resiliency > capacity_management > load_peak_management > normal.

`scenario_action(scenario, strategy)` maps a resolved scenario to a concrete
action for one home -- "mpc", "band_widen", or "baseline" -- and the action set
partitions cleanly: "mpc" is owned by the MPC periodic, "band_widen"/"baseline"
by the RBC periodic, so each home is handled exactly once per cycle.

Everything is advisory / shadow mode: recommendations are logged to
control_advisories, no device commands are sent.

Standalone:
    venv/bin/python -m smart_home_ilc.hvac_supervisor --home test_home
    venv/bin/python -m smart_home_ilc.hvac_supervisor --all
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import psycopg2.extras

try:
    from . import mpc_data, rbc_controller
except ImportError:  # running as a plain script, not a package module
    import mpc_data
    import rbc_controller

log = logging.getLogger(__name__)


# Action a (scenario, strategy) pair resolves to for one home.
ACTION_MPC = "mpc"
ACTION_BAND_WIDEN = "band_widen"
ACTION_BASELINE = "baseline"

# trigger_source_enum value to stamp on the logged action, per scenario.
SCENARIO_TRIGGERED_BY = {
    "normal": "ILC_agent",
    "load_peak_management": "DR_event",
    "capacity_management": "ILC_agent",
    "resiliency": "safety",
}

DEFAULT_PRIORITY = ["resiliency", "capacity_management", "load_peak_management", "normal"]


# =====================================================================
# Live signals
# =====================================================================
def latest_panel_state(conn, home_id):
    """Most recent smart_panel_readings row for a home (or None)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT ts, grid_online, grid_status, eps_mode_active,
                      home_load_w, grid_voltage_v1, grid_voltage_v2,
                      battery_soc_pct
               FROM smart_panel_readings
               WHERE home_id=%s
               ORDER BY ts DESC LIMIT 1""",
            (home_id,),
        )
        return cur.fetchone()


def _event_matches(ev, period_types, keywords):
    """True if an event's period_type or name/program matches the given filters."""
    pts = [p.lower() for p in period_types]
    if ev.get("period_type") and ev["period_type"].lower() in pts:
        return True
    kw = [k.lower() for k in keywords]
    hay = " ".join(str(ev.get(f) or "") for f in ("event_name", "program_name")).lower()
    return any(k in hay for k in kw)


def _estimate_amps(panel, service_v):
    """Whole-home current (A) from panel load and service voltage, or None."""
    if not panel or panel.get("home_load_w") is None:
        return None
    try:
        return float(panel["home_load_w"]) / float(service_v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _outage(panel):
    """True if panel telemetry indicates a grid outage / EPS operation."""
    if not panel:
        return False
    return (panel.get("grid_online") is False
            or panel.get("grid_status") == 0
            or panel.get("eps_mode_active") is True)


def _load_forecast_module():
    try:
        from . import load_forecast_data
    except ImportError:
        import load_forecast_data
    return load_forecast_data


def _build_home_forecast(home_name, cfg, conn, now_utc):
    """Build the 24 h home-load forecast, or None on any failure (model
    missing, no history, ...) so detection still works from telemetry."""
    try:
        return _load_forecast_module().build_forecast(
            home_name, mpc_cfg=cfg, conn=conn, now_utc=now_utc)
    except (SystemExit, Exception):
        return None


def _forecast_capacity_breach(fc, service_v, trip_a, window_h):
    """Anticipated capacity breach from a forecast dict: does the predicted
    peak load within the next `window_h` hours convert to >= the forecast trip
    current? Lets capacity_management fire *before* a breach instead of only
    once present amperage is already over the limit. Returns
    (breach, peak_w, peak_amps, peak_at_iso)."""
    loads = fc.get("home_load_w") or []
    times = fc.get("target_times") or []
    if not loads or not service_v:
        return False, None, None, None
    dt_s = float(fc.get("dt_s", 900))
    n = max(1, int(window_h * 3600 / dt_s))
    window = loads[:n]
    peak_w = max(window)
    i = window.index(peak_w)
    peak_amps = peak_w / service_v
    return peak_amps >= trip_a, peak_w, peak_amps, (times[i] if i < len(times) else None)


# =====================================================================
# Scenario resolution
# =====================================================================
def resolve_scenario(home_name, cfg, conn, now_utc=None):
    """Resolve the operation scenario for a home this cycle.

    Honors an explicit per-home or default `operation_scenario`; when "auto",
    detects from live panel telemetry + active OpenADR events by priority.
    Returns a dict: scenario, source ('explicit'|'auto'), reason, signals."""
    now_utc = now_utc or datetime.now(timezone.utc)
    defaults = cfg["defaults"]
    if home_name not in cfg["homes"]:
        raise SystemExit(f"{home_name!r} not configured in mpc_config.json")
    hc = cfg["homes"][home_name]
    scen_cfg = defaults.get("scenarios", {})
    auto = scen_cfg.get("auto_detection", {})

    requested = hc.get("operation_scenario",
                       defaults.get("operation_scenario", "auto"))
    if requested and requested != "auto":
        return {"scenario": requested, "source": "explicit",
                "reason": "configured", "signals": {}}

    home_id = mpc_data.get_home_id(conn, home_name)
    panel = latest_panel_state(conn, home_id)
    evs = rbc_controller.active_events(conn, now_utc)

    service_v = float(auto.get("service_voltage_v", 240))
    amps = _estimate_amps(panel, service_v)
    breaker_a = float(defaults.get("panel", {}).get("main_breaker_a", 60))
    trip_a = float(auto.get("capacity_trigger_pct", 0.80)) * breaker_a

    resil_kw = auto.get("resiliency_event_keywords", ["outage", "emergency"])
    peak_pts = auto.get("load_peak_event_period_types", ["peak"])
    peak_kw = auto.get("load_peak_event_keywords", ["shed", "curtail", "dr"])

    outage = _outage(panel)
    resil_event = any(_event_matches(e, [], resil_kw) for e in evs)
    peak_event = any(_event_matches(e, peak_pts, peak_kw) for e in evs)
    over_capacity_now = amps is not None and amps >= trip_a

    # Home-load forecast, built once per cycle, then optionally persisted and/or
    # used to anticipate a capacity breach (both config-gated).
    use_fc = auto.get("capacity_use_forecast", True)
    store_fc = auto.get("store_forecast", True)
    fc_breach = fc_peak_w = fc_peak_amps = fc_when = None
    fc_trip_a = None
    fc = _build_home_forecast(home_name, cfg, conn, now_utc) if (use_fc or store_fc) else None

    if fc is not None and store_fc:
        try:
            _load_forecast_module().store_forecast(conn, fc)
        except Exception:
            conn.rollback()  # a failed insert aborts the txn; keep conn usable
            log.warning("store_forecast failed for %s", home_name, exc_info=True)

    if fc is not None and use_fc:
        fc_trip_a = float(auto.get("capacity_forecast_trigger_pct",
                                   auto.get("capacity_trigger_pct", 0.80))) * breaker_a
        fc_window_h = float(auto.get("capacity_forecast_horizon_h", 24))
        fc_breach, fc_peak_w, fc_peak_amps, fc_when = _forecast_capacity_breach(
            fc, service_v, fc_trip_a, fc_window_h)

    over_capacity = over_capacity_now or bool(fc_breach)

    signals = {
        "panel_ts": panel["ts"].isoformat() if panel and panel.get("ts") else None,
        "home_load_w": float(panel["home_load_w"]) if panel and panel.get("home_load_w") is not None else None,
        "estimated_amps": round(amps, 2) if amps is not None else None,
        "capacity_trip_a": round(trip_a, 2),
        "forecast_peak_w": round(fc_peak_w, 1) if fc_peak_w is not None else None,
        "forecast_peak_amps": round(fc_peak_amps, 2) if fc_peak_amps is not None else None,
        "forecast_trip_a": round(fc_trip_a, 2) if fc_trip_a is not None else None,
        "forecast_peak_at": fc_when,
        "forecast_breach": bool(fc_breach),
        "grid_outage": outage,
        "resiliency_event": resil_event,
        "peak_event": peak_event,
        "n_events_overlapping": len(evs),
    }

    if over_capacity_now:
        cap_reason = f"home load {signals['estimated_amps']}A >= {signals['capacity_trip_a']}A"
    else:
        cap_reason = (f"forecast peak {signals['forecast_peak_amps']}A >= "
                      f"{signals['forecast_trip_a']}A at {fc_when}")

    detections = {
        "resiliency": (outage or resil_event,
                       "grid outage / EPS mode" if outage else "resiliency event active"),
        "capacity_management": (over_capacity, cap_reason),
        "load_peak_management": (peak_event, "DR / peak-price event active"),
        "normal": (True, "no shed signal"),
    }

    for scen in auto.get("priority", DEFAULT_PRIORITY):
        hit, reason = detections.get(scen, (False, ""))
        if hit:
            return {"scenario": scen, "source": "auto",
                    "reason": reason, "signals": signals}
    return {"scenario": "normal", "source": "auto",
            "reason": "no shed signal", "signals": signals}


# =====================================================================
# Scenario -> action / offsets
# =====================================================================
def scenario_action(scenario, strategy):
    """Concrete action for one home given its scenario and control_strategy.

    Returns 'mpc', 'band_widen', or 'baseline'. The set partitions so the MPC
    periodic owns 'mpc' and the RBC periodic owns 'band_widen'/'baseline'."""
    strategy = (strategy or "mpc").lower()
    if scenario in ("capacity_management", "resiliency"):
        return ACTION_BAND_WIDEN  # always shed, regardless of strategy
    if scenario == "load_peak_management":
        return ACTION_MPC if strategy == "mpc" else ACTION_BAND_WIDEN
    # normal
    return ACTION_MPC if strategy == "mpc" else ACTION_BASELINE


def scenario_offsets(scenario, cfg):
    """(cool_offset_f, heat_offset_f) for a scenario from defaults.scenarios."""
    scen_cfg = cfg["defaults"].get("scenarios", {}).get(scenario, {})
    return (float(scen_cfg.get("cool_offset_f", 0.0)),
            float(scen_cfg.get("heat_offset_f", 0.0)))


def home_strategy(home_name, cfg):
    """The control_strategy ('mpc'|'rbc') configured for a home."""
    default = cfg["defaults"].get("control_strategy", "mpc")
    return cfg["homes"][home_name].get("control_strategy", default).lower()


# =====================================================================
# Advisory dedup
# =====================================================================
def last_logged_scenario(conn, device_id):
    """operation_scenario of the most recent supervisor band-widen/baseline
    advisory for this device (controller='rbc'), or None."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT operation_scenario
               FROM control_advisories
               WHERE device_id=%s AND controller='rbc'
               ORDER BY ts DESC LIMIT 1""",
            (device_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


# =====================================================================
# Standalone reporting
# =====================================================================
def _report(home_name, cfg, conn, now_utc=None):
    res = resolve_scenario(home_name, cfg, conn, now_utc=now_utc)
    strat = home_strategy(home_name, cfg)
    action = scenario_action(res["scenario"], strat)
    cool_off, heat_off = scenario_offsets(res["scenario"], cfg)
    print(f"[{home_name}] strategy={strat} scenario={res['scenario']} "
          f"({res['source']}: {res['reason']}) -> action={action}")
    if action == ACTION_BAND_WIDEN:
        print(f"    offsets cool +{cool_off}F heat -{heat_off}F")
    if res["signals"]:
        print(f"    signals {res['signals']}")


def main():
    ap = argparse.ArgumentParser(description="Resolve the HVAC operation scenario for homes.")
    ap.add_argument("--home", default="test_home")
    ap.add_argument("--all", action="store_true", help="Report every configured home.")
    args = ap.parse_args()
    cfg = mpc_data._load_json("mpc_config.json")
    conn = mpc_data._connect()
    try:
        homes = list(cfg["homes"]) if args.all else [args.home]
        for h in homes:
            _report(h, cfg, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
