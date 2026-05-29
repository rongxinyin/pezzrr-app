"""
Data layer for the thermostat MPC (advisory / shadow mode).

Pulls everything the controller needs onto a single uniform time grid:

  * current indoor temperature (MPC initial state)
  * outdoor-temperature forecast vector (Pirate Weather, hourly -> interpolated)
  * electricity price vector ($/kWh): base TOU tariff from
    config/utility_rates.json, overlaid by any active openadr_events
  * the home's comfort band, fitted RC model, and equipment spec

and writes the resulting advisory back to control_advisories (no device commands).

Reads config + DB directly (psycopg2) so it can run inside the VOLTTRON agent or
standalone for testing:

    venv/bin/python -m smart_home_ilc.mpc_data --home test_home
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import psycopg2
import psycopg2.extras

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
DEFAULT_TZ = "America/Los_Angeles"


def _load_json(name):
    with open(os.path.join(CONFIG_DIR, name)) as fh:
        return json.load(fh)


def get_db_dsn():
    cfg = _load_json("data_analytics_config.json")["database"]
    return (
        f"host={cfg['host']} port={cfg['port']} dbname={cfg['database_name']} "
        f"user={cfg['username']} password={cfg['password']}"
    )


def _connect():
    return psycopg2.connect(get_db_dsn())


# =====================================================================
# Inputs container
# =====================================================================
@dataclass
class MPCInputs:
    home_name: str
    home_id: int
    device_id: int
    dt_s: float
    horizon_steps: int
    start_utc: datetime
    times_utc: list                  # length horizon_steps+1 (state grid)
    indoor_temp_c: float             # initial state T[0]
    outdoor_temp_c: np.ndarray       # length horizon_steps+1
    price_per_kwh: np.ndarray        # length horizon_steps (per control step)
    comfort: dict
    rc_model: dict
    equipment: dict
    mode: str
    tariff: str
    meta: dict = field(default_factory=dict)

    def summary(self):
        return (
            f"[{self.home_name}] T0={self.indoor_temp_c:.1f}C  "
            f"Tout {self.outdoor_temp_c.min():.1f}..{self.outdoor_temp_c.max():.1f}C  "
            f"price {self.price_per_kwh.min():.3f}..{self.price_per_kwh.max():.3f} $/kWh  "
            f"{self.horizon_steps} steps @ {int(self.dt_s)}s"
        )


# =====================================================================
# Config helpers
# =====================================================================
def baseline_setpoints_c(mpc_cfg, home_name):
    """(cool_c, heat_c) comfort baseline for a home from defaults.baseline_setpoints,
    overridden by a per-home baseline_setpoints block. Stored in degrees F in
    config; converted to Celsius here. Either value may be None if unconfigured."""
    base = dict(mpc_cfg.get("defaults", {}).get("baseline_setpoints", {}))
    base.update(mpc_cfg.get("homes", {}).get(home_name, {}).get("baseline_setpoints", {}))

    def f_to_c(f):
        return round((float(f) - 32.0) * 5.0 / 9.0, 2) if f is not None else None

    return f_to_c(base.get("cool_setpoint_f")), f_to_c(base.get("heat_setpoint_f"))


# =====================================================================
# DB reads
# =====================================================================
def get_home_id(conn, home_name):
    with conn.cursor() as cur:
        cur.execute("SELECT home_id FROM homes WHERE home_name=%s ORDER BY home_id LIMIT 1",
                    (home_name,))
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"No home named {home_name!r}")
    return row[0]


def latest_indoor_state(conn, device_id):
    """Most recent thermostat reading for the MPC initial state."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT ts, indoor_temp_c, outdoor_temp_c, cool_setpoint_c,
                      heat_setpoint_c, hvac_mode
               FROM thermostat_readings WHERE device_id=%s
               ORDER BY ts DESC LIMIT 1""",
            (device_id,),
        )
        row = cur.fetchone()
    if not row or row["indoor_temp_c"] is None:
        raise SystemExit(f"No indoor temperature for device_id={device_id}")
    return row


def weather_location_for_home(conn, home_id):
    """Map a home to its weather_locations row (exact home_id, else nearest by zip cluster)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM weather_locations WHERE home_id=%s LIMIT 1", (home_id,))
        row = cur.fetchone()
        if row:
            return row
        # Fallback: a home in the same zip prefix may share a station.
        cur.execute(
            """SELECT wl.* FROM weather_locations wl
               JOIN homes h1 ON h1.home_id=wl.home_id
               JOIN homes h2 ON h2.home_id=%s
               WHERE left(h1.zip_code,3)=left(h2.zip_code,3)
               ORDER BY wl.location_id LIMIT 1""",
            (home_id,),
        )
        return cur.fetchone()


def latest_forecast(conn, location_id):
    """Latest-generated hourly forecast: (unix_ts array, temp_c array)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT forecast_ts, temp_c FROM weather_forecast
               WHERE location_id=%s
                 AND generated_at=(SELECT max(generated_at) FROM weather_forecast
                                   WHERE location_id=%s)
               ORDER BY forecast_ts""",
            (location_id, location_id),
        )
        rows = cur.fetchall()
    if not rows:
        return None, None, None
    ts = np.array([r[0].timestamp() for r in rows], dtype=float)
    temp = np.array([float(r[1]) for r in rows], dtype=float)
    gen = None
    with conn.cursor() as cur:
        cur.execute("SELECT max(generated_at) FROM weather_forecast WHERE location_id=%s",
                    (location_id,))
        gen = cur.fetchone()[0]
    return ts, temp, gen


def active_openadr_events(conn, start_utc, end_utc):
    """Distinct price events overlapping the horizon, latest poll per event."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT DISTINCT ON (event_id)
                      event_id, priority, period_type, price_per_kwh,
                      interval_start, interval_end
               FROM openadr_events
               WHERE price_per_kwh IS NOT NULL
                 AND interval_start < %s AND interval_end > %s
               ORDER BY event_id, ts DESC""",
            (end_utc, start_utc),
        )
        return cur.fetchall()


# =====================================================================
# Price vector
# =====================================================================
def _hhmm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def base_price_for_time(rates, tariff_name, local_dt):
    """TOU price ($/kWh) for one local timestamp from utility_rates.json."""
    tariff = rates["tariffs"][tariff_name]
    month = local_dt.month
    season = next((s for s, v in rates["seasons"].items() if month in v["months"]), None)
    tod = local_dt.hour * 60 + local_dt.minute
    is_weekend = local_dt.weekday() >= 5
    for p in tariff["periods"]:
        if p["season"] != season:
            continue
        days = p.get("days", "all")
        if days == "weekday" and is_weekend:
            continue
        if days == "weekend" and not is_weekend:
            continue
        if _hhmm_to_min(p["start"]) <= tod < _hhmm_to_min(p["end"]):
            return float(p["price_per_kwh"]), p["name"], season
    return None, None, season


def build_price_vector(conn, rates, tariff_name, times_utc_ctrl, tz):
    """Per-control-step price: base TOU overlaid by active openadr_events."""
    zi = ZoneInfo(tz)
    prices = np.empty(len(times_utc_ctrl))
    period_names = []
    for i, t in enumerate(times_utc_ctrl):
        local = t.astimezone(zi)
        price, name, _ = base_price_for_time(rates, tariff_name, local)
        prices[i] = price if price is not None else np.nan
        period_names.append(name)

    events = active_openadr_events(conn, times_utc_ctrl[0], times_utc_ctrl[-1])
    dr_overlaid = 0
    for ev in events:
        s = ev["interval_start"]
        e = ev["interval_end"]
        ep = float(ev["price_per_kwh"])
        for i, t in enumerate(times_utc_ctrl):
            if s <= t < e:
                prices[i] = ep
                period_names[i] = f"openadr:{ev['period_type']}"
                dr_overlaid += 1
    # Any unpriced step (gap in tariff coverage) falls back to the series median.
    if np.isnan(prices).any():
        med = np.nanmedian(prices)
        prices = np.where(np.isnan(prices), med, prices)
    return prices, period_names, len(events), dr_overlaid


# =====================================================================
# Assemble
# =====================================================================
def build_inputs(home_name, mpc_cfg=None, rates=None, now_utc=None, conn=None):
    mpc_cfg = mpc_cfg or _load_json("mpc_config.json")
    rates = rates or _load_json("utility_rates.json")
    defaults = mpc_cfg["defaults"]
    if home_name not in mpc_cfg["homes"]:
        raise SystemExit(f"{home_name!r} not configured in mpc_config.json")
    hc = mpc_cfg["homes"][home_name]

    dt_s = float(defaults["dt_s"])
    horizon_steps = int(round(defaults["horizon_hours"] * 3600.0 / dt_s))
    tariff = hc.get("tariff", rates.get("default_tariff"))

    own = conn is None
    conn = conn or _connect()
    try:
        home_id = get_home_id(conn, home_name)
        device_id = hc["device_id"]
        state = latest_indoor_state(conn, device_id)

        # State grid: floor "now" to the dt boundary, horizon_steps+1 points.
        now_utc = now_utc or datetime.now(timezone.utc)
        floor = math.floor(now_utc.timestamp() / dt_s) * dt_s
        times_utc = [datetime.fromtimestamp(floor + k * dt_s, tz=timezone.utc)
                     for k in range(horizon_steps + 1)]
        times_ctrl = times_utc[:-1]

        # Outdoor forecast -> interpolate hourly forecast onto the grid.
        loc = weather_location_for_home(conn, home_id)
        tz = (loc["timezone"] if loc and loc.get("timezone") else DEFAULT_TZ)
        fc_meta = {"forecast_location_id": loc["location_id"] if loc else None}
        if loc:
            f_ts, f_temp, gen = latest_forecast(conn, loc["location_id"])
        else:
            f_ts = None
        grid_unix = np.array([t.timestamp() for t in times_utc])
        if f_ts is not None and len(f_ts) >= 2:
            outdoor = np.interp(grid_unix, f_ts, f_temp)
            fc_meta["forecast_generated_at"] = gen.isoformat() if gen else None
        else:
            # No forecast: hold the last observed outdoor temperature flat.
            base = float(state["outdoor_temp_c"]) if state["outdoor_temp_c"] is not None else 20.0
            outdoor = np.full(len(times_utc), base)
            fc_meta["forecast_generated_at"] = None
            fc_meta["forecast_fallback"] = "flat_last_observed"

        prices, period_names, n_ev, n_dr = build_price_vector(
            conn, rates, tariff, times_ctrl, tz)

        model_path = os.path.join(REPO_ROOT, hc["model_file"]) \
            if not os.path.isabs(hc["model_file"]) else hc["model_file"]
        if not os.path.exists(model_path):
            raise SystemExit(
                f"RC model file missing for {home_name}: {hc['model_file']} "
                f"(run train_hvac_model.py for this home first)")
        model_json = _load_json(os.path.relpath(model_path, CONFIG_DIR)) \
            if model_path.startswith(CONFIG_DIR) else json.load(open(model_path))

        return MPCInputs(
            home_name=home_name, home_id=home_id, device_id=device_id,
            dt_s=dt_s, horizon_steps=horizon_steps,
            start_utc=times_utc[0], times_utc=times_utc,
            indoor_temp_c=float(state["indoor_temp_c"]),
            outdoor_temp_c=outdoor,
            price_per_kwh=prices,
            comfort=hc["comfort"], rc_model=model_json["rc_model"],
            equipment=model_json["equipment"], mode=hc.get("mode", "cool"),
            tariff=tariff,
            meta={
                "timezone": tz,
                "indoor_reading_ts": state["ts"].isoformat(),
                "hvac_mode": state["hvac_mode"],
                "tou_periods": period_names,
                "openadr_events_active": n_ev,
                "openadr_steps_overlaid": n_dr,
                **fc_meta,
            },
        )
    finally:
        if own:
            conn.close()


# =====================================================================
# Advisory write
# =====================================================================
def write_advisory(inputs: MPCInputs, result: dict, conn=None):
    """Log the MPC advisory to control_advisories (shadow mode: no device command)."""
    cfg = _load_json("mpc_config.json")
    defaults = cfg["defaults"]["advisory"]
    shadow = defaults.get("shadow_mode", True)
    base_cool, base_heat = baseline_setpoints_c(cfg, inputs.home_name)
    payload = {
        "shadow_mode": shadow,
        "horizon_steps": inputs.horizon_steps,
        "dt_s": inputs.dt_s,
        "start_utc": inputs.start_utc.isoformat(),
        "tariff": inputs.tariff,
        "mode": inputs.mode,
        **result,
    }
    own = conn is None
    conn = conn or _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO control_advisories
                       (home_id, device_id, controller, action_type, triggered_by,
                        operation_scenario, shadow_mode,
                        baseline_cool_setpoint_c, baseline_heat_setpoint_c,
                        recommended_cool_setpoint_c, recommended_heat_setpoint_c,
                        expected_cost_usd, expected_energy_kwh,
                        comfort_violation_degc_steps, solver,
                        horizon_steps, dt_s, detail)
                   VALUES (%s, %s, 'mpc', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING advisory_id""",
                (inputs.home_id, inputs.device_id,
                 defaults.get("action_type", "setpoint_adjust"),
                 defaults.get("triggered_by", "ILC_agent"),
                 result.get("operation_scenario"), shadow,
                 base_cool, base_heat,
                 result.get("immediate_cool_setpoint_c"),
                 result.get("immediate_heat_setpoint_c"),
                 result.get("expected_cost_usd"),
                 result.get("expected_energy_kwh"),
                 result.get("comfort_violation_degC_steps"),
                 result.get("solver"),
                 inputs.horizon_steps, inputs.dt_s,
                 json.dumps(payload)),
            )
            advisory_id = cur.fetchone()[0]
        conn.commit()
        return advisory_id
    finally:
        if own:
            conn.close()


def main():
    ap = argparse.ArgumentParser(description="Assemble + print MPC inputs for a home.")
    ap.add_argument("--home", default="test_home")
    args = ap.parse_args()
    inp = build_inputs(args.home)
    print(inp.summary())
    print("  forecast:", inp.meta.get("forecast_generated_at"),
          "loc", inp.meta.get("forecast_location_id"))
    print("  openadr:", inp.meta["openadr_events_active"], "events,",
          inp.meta["openadr_steps_overlaid"], "steps overlaid")
    print("  first 8 prices:", np.round(inp.price_per_kwh[:8], 3).tolist())
    print("  first 8 Tout:", np.round(inp.outdoor_temp_c[:8], 1).tolist())


if __name__ == "__main__":
    main()
