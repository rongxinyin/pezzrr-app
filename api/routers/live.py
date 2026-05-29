"""
Live home snapshot + SSE stream (docs/DASHBOARD_DESIGN.md §8, §13.2).

`/homes/{id}/live` is a one-shot composite snapshot (panel, battery, thermostat,
active price, ranked circuits, latest control action). `/stream/homes/{id}`
emits the same shape every few seconds (poll-and-push, option 1 in §8).

EventSource cannot set an Authorization header, so the stream endpoint takes
the JWT as a `?token=` query param and validates scope manually.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import User, _has_home_scope, decode_token, require
from ..db import db

router = APIRouter(prefix="/api/v1", tags=["live"])

_scoped = require("viewer", home_param="home_id")

STREAM_INTERVAL_S = 7


def _w(v):
    return round(float(v)) if v is not None else None


def _r(v, n=2):
    return round(float(v), n) if v is not None else None


def _iso(ts):
    return ts.isoformat() if ts is not None else None


async def _ensure_home(home_id: int) -> None:
    if await db.fetchrow("SELECT 1 FROM homes WHERE home_id = $1", home_id) is None:
        raise HTTPException(status_code=404, detail="Home not found")


async def _live_snapshot(home_id: int) -> dict:
    panel = await db.fetchrow(
        """SELECT ts, home_load_w, grid_power_w, solar_power_w, battery_power_w,
                  battery_soc_pct, grid_status, eps_mode_active
           FROM smart_panel_readings WHERE home_id = $1 ORDER BY ts DESC LIMIT 1""",
        home_id,
    )
    battery = await db.fetchrow(
        """SELECT ts, soc_pct, soh_pct, status, power_w, capacity_wh
           FROM battery_readings WHERE home_id = $1 ORDER BY ts DESC LIMIT 1""",
        home_id,
    )
    thermo = await db.fetchrow(
        """SELECT ts, indoor_temp_c, indoor_humidity_pct, hvac_mode, hvac_state,
                  heat_setpoint_c, cool_setpoint_c
           FROM thermostat_readings WHERE home_id = $1 ORDER BY ts DESC LIMIT 1""",
        home_id,
    )
    price = await db.fetchrow(
        """SELECT price_per_kwh, period_type, program_name, interval_start, interval_end
           FROM openadr_events
           WHERE interval_start <= NOW() AND interval_end > NOW()
           ORDER BY ts DESC LIMIT 1""",
    )
    circuits = await db.fetch(
        """SELECT pc.circuit_id, pc.channel_num, pc.circuit_name,
                  pc.is_critical, pc.is_controllable,
                  r.power_w, r.is_enabled, r.ts
           FROM panel_circuits pc
           JOIN devices d ON d.device_id = pc.device_id
           LEFT JOIN LATERAL (
               SELECT power_w, is_enabled, ts FROM panel_circuit_readings
               WHERE circuit_id = pc.circuit_id ORDER BY ts DESC LIMIT 1
           ) r ON TRUE
           WHERE d.home_id = $1
           ORDER BY r.power_w DESC NULLS LAST""",
        home_id,
    )
    action = await db.fetchrow(
        """SELECT action_id, ts, action_type::text AS action_type, success, acknowledged_at
           FROM control_actions WHERE home_id = $1 ORDER BY ts DESC LIMIT 1""",
        home_id,
    )

    usable_kwh = None
    if battery and battery["capacity_wh"] is not None and battery["soc_pct"] is not None:
        usable_kwh = float(battery["capacity_wh"]) * float(battery["soc_pct"]) / 100 / 1000

    return {
        "home_id": home_id,
        "panel": None if panel is None else {
            "ts": _iso(panel["ts"]),
            "home_load_w": _w(panel["home_load_w"]),
            "grid_power_w": _w(panel["grid_power_w"]),
            "solar_power_w": _w(panel["solar_power_w"]),
            "battery_power_w": _w(panel["battery_power_w"]),
            "battery_soc_pct": _r(panel["battery_soc_pct"], 1),
            "grid_status": panel["grid_status"],
            "eps_mode_active": panel["eps_mode_active"],
        },
        "battery": None if battery is None else {
            "ts": _iso(battery["ts"]),
            "soc_pct": _r(battery["soc_pct"], 1),
            "soh_pct": _r(battery["soh_pct"], 1),
            "status": battery["status"],
            "power_w": _w(battery["power_w"]),
            "capacity_wh": _w(battery["capacity_wh"]),
            "usable_kwh": _r(usable_kwh, 2),
        },
        "thermostat": None if thermo is None else {
            "ts": _iso(thermo["ts"]),
            "indoor_temp_c": _r(thermo["indoor_temp_c"]),
            "indoor_humidity_pct": _r(thermo["indoor_humidity_pct"], 1),
            "hvac_mode": thermo["hvac_mode"],
            "hvac_state": thermo["hvac_state"],
            "heat_setpoint_c": _r(thermo["heat_setpoint_c"]),
            "cool_setpoint_c": _r(thermo["cool_setpoint_c"]),
        },
        "price": None if price is None else {
            "price_per_kwh": _r(price["price_per_kwh"], 5),
            "period_type": price["period_type"],
            "program_name": price["program_name"],
            "interval_start": _iso(price["interval_start"]),
            "interval_end": _iso(price["interval_end"]),
        },
        "circuits": [
            {
                "circuit_id": c["circuit_id"],
                "channel_num": c["channel_num"],
                "circuit_name": c["circuit_name"],
                "is_critical": c["is_critical"],
                "is_controllable": c["is_controllable"],
                "power_w": _w(c["power_w"]),
                "is_enabled": c["is_enabled"],
                "ts": _iso(c["ts"]),
            }
            for c in circuits
        ],
        "latest_action": None if action is None else {
            "action_id": action["action_id"],
            "ts": _iso(action["ts"]),
            "action_type": action["action_type"],
            "success": action["success"],
            "acknowledged_at": _iso(action["acknowledged_at"]),
        },
    }


@router.get("/homes/{home_id}/live")
async def live(home_id: int, user: User = Depends(_scoped)):
    await _ensure_home(home_id)
    return await _live_snapshot(home_id)


async def _event_stream(home_id: int, request: Request):
    while True:
        if await request.is_disconnected():
            break
        snap = await _live_snapshot(home_id)
        yield f"data: {json.dumps(snap)}\n\n"
        await asyncio.sleep(STREAM_INTERVAL_S)


@router.get("/stream/homes/{home_id}")
async def stream(home_id: int, request: Request, token: str = Query(...)):
    user = decode_token(token)
    if not _has_home_scope(user, home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    await _ensure_home(home_id)
    return StreamingResponse(
        _event_stream(home_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
