"""
Bucketed telemetry endpoints (docs/DASHBOARD_DESIGN.md §7).

Source routing by bucket:
  panel    1m -> raw smart_panel_readings | 5m -> panel_5m | 1h -> panel_1h
  circuits 5m -> circuit_5m | else raw panel_circuit_readings (on-the-fly bucket)
  battery / thermostat / plugs -> raw, time_bucket on the fly (no cagg defined)
The bucket-vs-range guard (api/buckets.py) bounds how much raw data a request
can scan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..buckets import resolve_window
from ..db import db

router = APIRouter(prefix="/api/v1", tags=["telemetry"])


def _w(v):
    return round(float(v)) if v is not None else None


def _r(v, n=2):
    return round(float(v), n) if v is not None else None


async def _ensure_home(home_id: int) -> None:
    row = await db.fetchrow("SELECT 1 FROM homes WHERE home_id = $1", home_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Home not found")


def _envelope(home_id, bucket, start, end, points):
    return {
        "home_id": home_id,
        "bucket": bucket,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(points),
        "points": points,
    }


# =====================================================================
# Panel
# =====================================================================
@router.get("/homes/{home_id}/panel")
async def panel(
    home_id: int,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    bucket: str = Query("5m"),
):
    await _ensure_home(home_id)
    start, end, interval = resolve_window(date_from, date_to, bucket)

    if bucket == "1m":
        rows = await db.fetch(
            """SELECT time_bucket($4, ts) AS bucket,
                      avg(home_load_w) AS home_load_w, avg(grid_power_w) AS grid_power_w,
                      avg(solar_power_w) AS solar_power_w, avg(battery_power_w) AS battery_power_w,
                      avg(battery_soc_pct) AS battery_soc_pct
               FROM smart_panel_readings
               WHERE home_id=$1 AND ts >= $2 AND ts < $3
               GROUP BY bucket ORDER BY bucket""",
            home_id, start, end, interval,
        )
    elif bucket == "5m":
        rows = await db.fetch(
            """SELECT bucket,
                      avg(home_load_w) AS home_load_w, avg(grid_power_w) AS grid_power_w,
                      avg(solar_power_w) AS solar_power_w, avg(battery_power_w) AS battery_power_w,
                      avg(battery_soc_pct) AS battery_soc_pct
               FROM panel_5m
               WHERE home_id=$1 AND bucket >= $2 AND bucket < $3
               GROUP BY bucket ORDER BY bucket""",
            home_id, start, end,
        )
    else:  # 1h
        rows = await db.fetch(
            """SELECT bucket,
                      avg(home_load_w) AS home_load_w, max(peak_load_w) AS peak_load_w,
                      avg(grid_power_w) AS grid_power_w, avg(solar_power_w) AS solar_power_w,
                      avg(battery_soc_pct) AS battery_soc_pct
               FROM panel_1h
               WHERE home_id=$1 AND bucket >= $2 AND bucket < $3
               GROUP BY bucket ORDER BY bucket""",
            home_id, start, end,
        )

    points = []
    for r in rows:
        p = {
            "bucket": r["bucket"].isoformat(),
            "home_load_w": _w(r["home_load_w"]),
            "grid_power_w": _w(r["grid_power_w"]),
            "solar_power_w": _w(r["solar_power_w"]),
            "battery_soc_pct": _r(r["battery_soc_pct"], 1),
        }
        if bucket == "1h":
            p["peak_load_w"] = _w(r["peak_load_w"])
        else:
            p["battery_power_w"] = _w(r["battery_power_w"])
        points.append(p)
    return _envelope(home_id, bucket, start, end, points)


# =====================================================================
# Circuits (per-circuit series + metadata)
# =====================================================================
@router.get("/homes/{home_id}/circuits")
async def circuits(
    home_id: int,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    bucket: str = Query("5m"),
):
    await _ensure_home(home_id)
    start, end, interval = resolve_window(date_from, date_to, bucket)

    meta_rows = await db.fetch(
        """SELECT pc.circuit_id, pc.channel_num, pc.circuit_name, pc.rated_amps,
                  pc.rated_voltage, pc.is_critical, pc.is_controllable, pc.load_description
           FROM panel_circuits pc
           JOIN devices d ON d.device_id = pc.device_id
           WHERE d.home_id = $1
           ORDER BY pc.channel_num""",
        home_id,
    )

    if bucket == "5m":
        series_rows = await db.fetch(
            """SELECT circuit_id, bucket, avg(power_w) AS power_w, max(peak_w) AS peak_w
               FROM circuit_5m
               WHERE home_id=$1 AND bucket >= $2 AND bucket < $3
               GROUP BY circuit_id, bucket ORDER BY circuit_id, bucket""",
            home_id, start, end,
        )
    else:
        series_rows = await db.fetch(
            """SELECT circuit_id, time_bucket($4, ts) AS bucket,
                      avg(power_w) AS power_w, max(power_w) AS peak_w
               FROM panel_circuit_readings
               WHERE home_id=$1 AND ts >= $2 AND ts < $3
               GROUP BY circuit_id, bucket ORDER BY circuit_id, bucket""",
            home_id, start, end, interval,
        )

    by_circuit: dict[int, list] = {m["circuit_id"]: [] for m in meta_rows}
    for r in series_rows:
        by_circuit.setdefault(r["circuit_id"], []).append({
            "bucket": r["bucket"].isoformat(),
            "power_w": _w(r["power_w"]),
            "peak_w": _w(r["peak_w"]),
        })

    circuits_out = [{
        "circuit_id": m["circuit_id"],
        "channel_num": m["channel_num"],
        "circuit_name": m["circuit_name"],
        "rated_amps": _r(m["rated_amps"]),
        "rated_voltage": _r(m["rated_voltage"]),
        "is_critical": m["is_critical"],
        "is_controllable": m["is_controllable"],
        "load_description": m["load_description"],
        "points": by_circuit.get(m["circuit_id"], []),
    } for m in meta_rows]

    env = _envelope(home_id, bucket, start, end, [])
    del env["count"], env["points"]
    env["circuits"] = circuits_out
    return env


# =====================================================================
# Battery / thermostat / plugs (raw, on-the-fly bucket)
# =====================================================================
@router.get("/homes/{home_id}/battery")
async def battery(
    home_id: int,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    bucket: str = Query("5m"),
):
    await _ensure_home(home_id)
    start, end, interval = resolve_window(date_from, date_to, bucket)
    rows = await db.fetch(
        """SELECT time_bucket($4, ts) AS bucket,
                  avg(soc_pct) AS soc_pct, avg(soh_pct) AS soh_pct,
                  avg(power_w) AS power_w, avg(capacity_wh) AS capacity_wh
           FROM battery_readings
           WHERE home_id=$1 AND ts >= $2 AND ts < $3
           GROUP BY bucket ORDER BY bucket""",
        home_id, start, end, interval,
    )
    points = [{
        "bucket": r["bucket"].isoformat(),
        "soc_pct": _r(r["soc_pct"], 1),
        "soh_pct": _r(r["soh_pct"], 1),
        "power_w": _w(r["power_w"]),
        "capacity_wh": _w(r["capacity_wh"]),
    } for r in rows]
    return _envelope(home_id, bucket, start, end, points)


@router.get("/homes/{home_id}/thermostat")
async def thermostat(
    home_id: int,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    bucket: str = Query("5m"),
):
    await _ensure_home(home_id)
    start, end, interval = resolve_window(date_from, date_to, bucket)
    rows = await db.fetch(
        """SELECT time_bucket($4, ts) AS bucket,
                  avg(indoor_temp_c) AS indoor_temp_c, avg(outdoor_temp_c) AS outdoor_temp_c,
                  avg(indoor_humidity_pct) AS indoor_humidity_pct,
                  avg(heat_setpoint_c) AS heat_setpoint_c, avg(cool_setpoint_c) AS cool_setpoint_c
           FROM thermostat_readings
           WHERE home_id=$1 AND ts >= $2 AND ts < $3
           GROUP BY bucket ORDER BY bucket""",
        home_id, start, end, interval,
    )
    points = [{
        "bucket": r["bucket"].isoformat(),
        "indoor_temp_c": _r(r["indoor_temp_c"]),
        "outdoor_temp_c": _r(r["outdoor_temp_c"]),
        "indoor_humidity_pct": _r(r["indoor_humidity_pct"], 1),
        "heat_setpoint_c": _r(r["heat_setpoint_c"]),
        "cool_setpoint_c": _r(r["cool_setpoint_c"]),
    } for r in rows]
    return _envelope(home_id, bucket, start, end, points)


@router.get("/homes/{home_id}/plugs")
async def plugs(
    home_id: int,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    bucket: str = Query("5m"),
):
    await _ensure_home(home_id)
    start, end, interval = resolve_window(date_from, date_to, bucket)
    rows = await db.fetch(
        """SELECT device_id, time_bucket($4, ts) AS bucket,
                  avg(power_w) AS power_w, avg(energy_kwh) AS energy_kwh
           FROM smart_plug_readings
           WHERE home_id=$1 AND ts >= $2 AND ts < $3
           GROUP BY device_id, bucket ORDER BY device_id, bucket""",
        home_id, start, end, interval,
    )
    points = [{
        "device_id": r["device_id"],
        "bucket": r["bucket"].isoformat(),
        "power_w": _w(r["power_w"]),
        "energy_kwh": _r(r["energy_kwh"], 3),
    } for r in rows]
    return _envelope(home_id, bucket, start, end, points)
