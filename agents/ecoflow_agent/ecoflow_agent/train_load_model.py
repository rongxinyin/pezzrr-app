"""
Offline trainer for the home-load forecast model (see load_model.HomeLoadModel).

Pulls a home's whole-home load (smart_panel_readings.home_load_w) and per-circuit
power (panel_circuit_readings), plus the outdoor-temperature history from the
home's thermostat (thermostat_readings.outdoor_temp_c) as the exogenous driver,
resamples everything to the MPC timestep with TimescaleDB time_bucket, fits the
aggregate + per-circuit direct multi-horizon regressors, and writes:

  config/load_model_<home>.pkl   -- fitted estimators (joblib)
  config/load_model_<home>.json  -- metadata + holdout metrics

Usage:
    venv/bin/python -m ecoflow_agent.train_load_model --home test_home
    venv/bin/python -m ecoflow_agent.train_load_model --home 3110C --dt 900 --holdout-days 7
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

from .load_model import HomeLoadModel

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")


def _db_dsn() -> str:
    import json
    cfg = json.load(open(os.path.join(CONFIG_DIR, "data_analytics_config.json")))["database"]
    return (
        f"host={cfg['host']} port={cfg['port']} dbname={cfg['database_name']} "
        f"user={cfg['username']} password={cfg['password']}"
    )


def _utc_naive(ts_values) -> pd.DatetimeIndex:
    """timestamptz -> tz-naive UTC index (avoids DST gaps when resampling)."""
    return pd.to_datetime(ts_values, utc=True).tz_convert("UTC").tz_localize(None)


def resolve_home_id(con, home_name: int) -> int:
    cur = con.cursor()
    cur.execute("SELECT home_id FROM homes WHERE home_name=%s ORDER BY home_id LIMIT 1",
                (home_name,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"No home named {home_name!r}")
    return row[0]


def load_home_load(con, home_id: int, dt_s: float) -> pd.Series:
    interval = f"{int(dt_s)} seconds"
    cur = con.cursor()
    cur.execute(
        """SELECT time_bucket(%s::interval, ts) AS bucket, avg(home_load_w)
           FROM smart_panel_readings
           WHERE home_id=%s AND home_load_w IS NOT NULL
           GROUP BY bucket ORDER BY bucket""",
        (interval, home_id),
    )
    rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"No smart_panel_readings.home_load_w for home_id={home_id}")
    return pd.Series([float(r[1]) for r in rows],
                     index=_utc_naive([r[0] for r in rows]), name="home_load")


def load_outdoor_temp(con, home_id: int, dt_s: float) -> pd.Series:
    """Outdoor-temperature driver on the dt grid.

    Prefers the home's own thermostat, but falls back to a site-wide average
    across all thermostats when the home's coverage is sparse: the units are
    co-located (same weather), and several thermostats report outdoor_temp_c
    only intermittently, so pooling gives a denser, equally valid signal."""
    interval = f"{int(dt_s)} seconds"

    def _query(home_filter):
        cur = con.cursor()
        sql = """SELECT time_bucket(%s::interval, tr.ts) AS bucket,
                        avg(tr.outdoor_temp_c)
                 FROM thermostat_readings tr
                 JOIN devices d ON d.device_id = tr.device_id
                 WHERE tr.outdoor_temp_c IS NOT NULL {flt}
                 GROUP BY bucket ORDER BY bucket"""
        params = [interval]
        if home_filter:
            sql = sql.format(flt="AND d.home_id=%s")
            params.append(home_id)
        else:
            sql = sql.format(flt="")
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        if not rows:
            return None
        return pd.Series([float(r[1]) for r in rows],
                         index=_utc_naive([r[0] for r in rows]), name="temp")

    home_temp = _query(home_filter=True)
    site_temp = _query(home_filter=False)
    if site_temp is None:
        raise SystemExit("No thermostat outdoor_temp_c anywhere to build the temp driver")
    if home_temp is None or len(home_temp) < 0.8 * len(site_temp):
        return site_temp
    return home_temp


def load_circuits(con, home_id: int, dt_s: float):
    """Return ({channel_num: gridded power series}, {channel_num: name})."""
    interval = f"{int(dt_s)} seconds"
    cur = con.cursor()
    cur.execute(
        """SELECT pc.channel_num,
                  time_bucket(%s::interval, pcr.ts) AS bucket,
                  avg(pcr.power_w)
           FROM panel_circuit_readings pcr
           JOIN panel_circuits pc ON pc.circuit_id = pcr.circuit_id
           WHERE pcr.home_id=%s AND pcr.power_w IS NOT NULL
           GROUP BY pc.channel_num, bucket ORDER BY pc.channel_num, bucket""",
        (interval, home_id),
    )
    rows = cur.fetchall()
    cur.execute(
        """SELECT DISTINCT pc.channel_num, pc.circuit_name
           FROM panel_circuits pc
           JOIN devices d ON d.device_id = pc.device_id
           WHERE d.home_id=%s""",
        (home_id,),
    )
    names = {int(ch): nm for ch, nm in cur.fetchall()}
    series = {}
    by_ch: dict[int, list] = {}
    for ch, bucket, val in rows:
        by_ch.setdefault(int(ch), []).append((bucket, float(val)))
    for ch, pairs in by_ch.items():
        series[ch] = pd.Series([v for _, v in pairs],
                               index=_utc_naive([b for b, _ in pairs]),
                               name=f"ch{ch}")
    return series, names


def align_temp(load: pd.Series, temp: pd.Series) -> pd.Series:
    """Reindex temp onto the load grid, interpolating short gaps in time."""
    t = temp.reindex(load.index.union(temp.index)).interpolate(method="time")
    return t.reindex(load.index)


def main():
    ap = argparse.ArgumentParser(description="Fit the home-load forecast model.")
    ap.add_argument("--home", default="test_home")
    ap.add_argument("--dt", type=float, default=900.0, help="forecast timestep (s)")
    ap.add_argument("--horizon-hours", type=float, default=24.0)
    ap.add_argument("--holdout-days", type=float, default=7.0)
    ap.add_argument("--no-circuits", action="store_true",
                    help="Fit only the aggregate home-load model.")
    ap.add_argument("--out-dir", default=CONFIG_DIR)
    args = ap.parse_args()

    con = psycopg2.connect(_db_dsn())
    try:
        home_id = resolve_home_id(con, args.home)
        load = load_home_load(con, home_id, args.dt)
        temp = align_temp(load, load_outdoor_temp(con, home_id, args.dt))
        circuits, names = ({}, {}) if args.no_circuits else load_circuits(con, home_id, args.dt)
    finally:
        con.close()

    print(f"[{args.home}] home_id={home_id}  load {len(load)} steps @ {int(args.dt)}s "
          f"({load.index.min()} -> {load.index.max()})  "
          f"temp coverage {temp.notna().mean()*100:.0f}%  circuits={len(circuits)}")

    model = HomeLoadModel(args.home, home_id, dt_s=args.dt,
                          horizon_h=args.horizon_hours)
    model.circuit_names = names

    model.aggregate.fit(load, temp, holdout_days=args.holdout_days)
    m = model.aggregate.metrics
    print(f"  aggregate: n_train={m['n_train']} "
          + (f"holdout MAE={m.get('holdout_mae_w', float('nan')):.1f}W "
             f"RMSE={m.get('holdout_rmse_w', float('nan')):.1f}W "
             f"(mean load {m.get('mean_load_w', float('nan')):.0f}W)"
             if "holdout_mae_w" in m else "(holdout too small)"))

    for ch in sorted(circuits):
        from .load_model import TargetForecaster
        fc = TargetForecaster(f"ch{ch}", args.dt, model.lags_h)
        try:
            fc.fit(circuits[ch], temp, holdout_days=args.holdout_days)
            model.circuits[ch] = fc
            cm = fc.metrics
            print(f"  ch{ch:<2d} {names.get(ch, '?'):<22s} "
                  + (f"MAE={cm.get('holdout_mae_w', float('nan')):.1f}W "
                     f"(mean {cm.get('mean_load_w', float('nan')):.0f}W)"
                     if "holdout_mae_w" in cm else "(holdout too small)"))
        except ValueError as e:
            print(f"  ch{ch}: skipped ({e})")

    base = os.path.join(args.out_dir, f"load_model_{args.home}")
    model.save(base + ".pkl", base + ".json")
    print(f"Saved {base}.pkl + {base}.json")


if __name__ == "__main__":
    sys.exit(main())
