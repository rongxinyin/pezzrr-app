"""
Consumer-side data layer for the home-load forecast (smart_home_ilc).

Loads the HomeLoadModel artifact trained by ecoflow_agent, assembles the live
inputs it needs -- recent gridded load/circuit history (for the seasonal lags)
and the outdoor-temperature forecast over the horizon (from weather_forecast) --
and returns the 24 h load forecast plus the latest per-circuit instantaneous
draw. The supervisor's capacity_management can use peak_forecast_w to anticipate
breaches instead of only reacting to the present amperage.

The fitted estimators are pickled with class references into the ecoflow_agent
package, so we put that agent dir on sys.path to unpickle (the artifact is a
joblib bundle, not pure JSON like the HVAC model).

Standalone:
    venv/bin/python -m smart_home_ilc.load_forecast_data --home 3110C
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import psycopg2.extras

try:
    from . import mpc_data
except ImportError:
    import mpc_data

# The pickled model references classes in the `ecoflow_agent` package.
_ECOFLOW_AGENT_DIR = os.path.join(mpc_data.REPO_ROOT, "agents", "ecoflow_agent")
if _ECOFLOW_AGENT_DIR not in sys.path:
    sys.path.insert(0, _ECOFLOW_AGENT_DIR)
from ecoflow_agent.load_model import HomeLoadModel  # noqa: E402


def _utc_naive(ts_values) -> pd.DatetimeIndex:
    return pd.to_datetime(ts_values, utc=True).tz_convert("UTC").tz_localize(None)


def _model_paths(home_name, lf_cfg):
    """(.pkl, .json) artifact paths for a home from the load_forecast config."""
    tmpl = lf_cfg.get("model_file", "config/load_model_{home}.pkl")
    pkl = tmpl.format(home=home_name)
    if not os.path.isabs(pkl):
        pkl = os.path.join(mpc_data.REPO_ROOT, pkl)
    return pkl, pkl[:-4] + ".json" if pkl.endswith(".pkl") else pkl + ".json"


# =====================================================================
# Live inputs
# =====================================================================
def recent_load_history(conn, home_id, dt_s, days):
    interval = f"{int(dt_s)} seconds"
    with conn.cursor() as cur:
        cur.execute(
            """SELECT time_bucket(%s::interval, ts) AS bucket, avg(home_load_w)
               FROM smart_panel_readings
               WHERE home_id=%s AND home_load_w IS NOT NULL
                 AND ts >= now() - %s::interval
               GROUP BY bucket ORDER BY bucket""",
            (interval, home_id, f"{int(days)} days"),
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"No recent home_load_w for home_id={home_id}")
    return pd.Series([float(r[1]) for r in rows],
                     index=_utc_naive([r[0] for r in rows]), name="home_load")


def recent_circuit_history(conn, home_id, dt_s, days):
    interval = f"{int(dt_s)} seconds"
    with conn.cursor() as cur:
        cur.execute(
            """SELECT pc.channel_num, time_bucket(%s::interval, pcr.ts) AS bucket,
                      avg(pcr.power_w)
               FROM panel_circuit_readings pcr
               JOIN panel_circuits pc ON pc.circuit_id = pcr.circuit_id
               WHERE pcr.home_id=%s AND pcr.power_w IS NOT NULL
                 AND pcr.ts >= now() - %s::interval
               GROUP BY pc.channel_num, bucket ORDER BY pc.channel_num, bucket""",
            (interval, home_id, f"{int(days)} days"),
        )
        rows = cur.fetchall()
    by_ch: dict[int, list] = {}
    for ch, bucket, val in rows:
        by_ch.setdefault(int(ch), []).append((bucket, float(val)))
    return {ch: pd.Series([v for _, v in pairs],
                          index=_utc_naive([b for b, _ in pairs]), name=f"ch{ch}")
            for ch, pairs in by_ch.items()}


def latest_circuit_draw(conn, home_id):
    """Most recent per-circuit instantaneous draw (the 'current draw')."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT DISTINCT ON (pc.channel_num)
                      pc.channel_num, pc.circuit_name, pcr.ts,
                      pcr.power_w, pcr.current_a, pcr.is_enabled
               FROM panel_circuit_readings pcr
               JOIN panel_circuits pc ON pc.circuit_id = pcr.circuit_id
               WHERE pcr.home_id=%s
               ORDER BY pc.channel_num, pcr.ts DESC""",
            (home_id,),
        )
        rows = cur.fetchall()
    return [
        {"channel_num": r["channel_num"], "name": r["circuit_name"],
         "ts": r["ts"].isoformat() if r["ts"] else None,
         "power_w": float(r["power_w"]) if r["power_w"] is not None else None,
         "current_a": float(r["current_a"]) if r["current_a"] is not None else None,
         "is_enabled": r["is_enabled"]}
        for r in rows
    ]


def temp_forecast_vector(conn, home_id, target_times, fallback_temp):
    """Outdoor temp at each target step from weather_forecast, interpolated.

    Falls back to a flat `fallback_temp` when no forecast covers the horizon."""
    loc = mpc_data.weather_location_for_home(conn, home_id)
    if loc:
        f_ts, f_temp, _ = mpc_data.latest_forecast(conn, loc["location_id"])
        if f_ts is not None and len(f_ts) >= 2:
            grid_unix = np.array([t.replace(tzinfo=timezone.utc).timestamp()
                                  for t in target_times])
            return np.interp(grid_unix, f_ts, f_temp), "weather_forecast"
    return np.full(len(target_times), float(fallback_temp)), "flat_fallback"


# =====================================================================
# Assemble forecast
# =====================================================================
def build_forecast(home_name, mpc_cfg=None, conn=None, now_utc=None):
    """Return the 24 h home-load forecast + latest per-circuit draw for a home."""
    mpc_cfg = mpc_cfg or mpc_data._load_json("mpc_config.json")
    lf_cfg = mpc_cfg["defaults"].get("load_forecast", {})
    days = int(lf_cfg.get("history_days", 8))

    pkl, js = _model_paths(home_name, lf_cfg)
    if not (os.path.exists(pkl) and os.path.exists(js)):
        raise SystemExit(
            f"Load model missing for {home_name}: {pkl} "
            f"(run train_load_model.py for this home first)")
    model = HomeLoadModel.load(pkl, js)

    own = conn is None
    conn = conn or mpc_data._connect()
    try:
        home_id = mpc_data.get_home_id(conn, home_name)
        load = recent_load_history(conn, home_id, model.dt_s, days)
        circ = recent_circuit_history(conn, home_id, model.dt_s, days)

        now_utc = now_utc or datetime.now(timezone.utc)
        start = pd.Timestamp(now_utc).tz_convert("UTC").tz_localize(None)
        target_times = pd.DatetimeIndex(
            [start + pd.Timedelta(seconds=model.dt_s * (k + 1))
             for k in range(model.horizon_steps)])
        temp, temp_src = temp_forecast_vector(
            conn, home_id, target_times, fallback_temp=20.0)

        out = model.predict_horizon(start, load, temp, circuit_histories=circ)
        out["temp_source"] = temp_src
        out["history_days"] = days
        out["latest_circuit_draw"] = latest_circuit_draw(conn, home_id)
        out["latest_load_ts"] = load.index.max().isoformat()
        return out
    finally:
        if own:
            conn.close()


def main():
    ap = argparse.ArgumentParser(description="Build the home-load forecast for a home.")
    ap.add_argument("--home", default="test_home")
    args = ap.parse_args()
    out = build_forecast(args.home)
    print(f"[{out['home']}] start={out['start_utc']} temp_src={out['temp_source']} "
          f"steps={len(out['home_load_w'])}")
    print(f"  peak_load_w={out['peak_load_w']}  "
          f"first 6 (W): {out['home_load_w'][:6]}")
    print(f"  circuits forecast: {len(out['circuits'])}")
    draws = [d for d in out["latest_circuit_draw"] if d.get("power_w")]
    top = sorted(draws, key=lambda d: d["power_w"] or 0, reverse=True)[:5]
    print("  top current draws now:")
    for d in top:
        print(f"    ch{d['channel_num']:<2d} {d['name'] or '?':<22s} "
              f"{d['power_w']:.1f}W  {d.get('current_a') or 0:.2f}A")


if __name__ == "__main__":
    main()
