"""
Control bridge (docs/DASHBOARD_DESIGN.md §10).

POST /control/dispatch validates RBAC + home scope, refuses to touch critical
or non-controllable circuits, records a pending control_actions row, and
publishes the command to the home's MQTT topic. The edge VOLTTRON agent
(Task 8) acts and publishes a result that control_bus writes back.

Dispatch is restricted to operator/admin via require_dispatch — fleet_analyst
outranks operator but must never dispatch (§9), so role-rank alone is wrong.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import User, _has_home_scope, get_current_user, require, require_dispatch
from ..control_bus import control_bus, control_topic
from ..db import CONFIG_DIR, db
from ..models import (
    ACTION_TYPES,
    PANEL_MODE_PARAMS,
    SETPOINT_CONTROLLERS,
    ControlActionRow,
    ControlAdvisoryRow,
    DispatchRequest,
    DispatchResponse,
    ForecastPoint,
    PanelModeRow,
    SetpointPlan,
    SetpointPlanPoint,
)

router = APIRouter(prefix="/api/v1", tags=["control"])


def _action_status(success: Optional[bool], acknowledged_at) -> str:
    if success is True:
        return "success"
    if success is False:
        return "failed"
    if acknowledged_at is not None:
        return "acknowledged"
    return "pending"


def _validate_panel_params(params: dict) -> None:
    """Reject unknown keys / out-of-range values before a live panel write."""
    if not params:
        raise HTTPException(status_code=422, detail="No panel-mode params given")
    for key, val in params.items():
        allowed = PANEL_MODE_PARAMS.get(key)
        if allowed is None:
            raise HTTPException(status_code=422, detail=f"Unknown panel param '{key}'")
        if allowed is bool:
            if not isinstance(val, bool):
                raise HTTPException(status_code=422, detail=f"{key} must be a boolean")
        elif val not in allowed:
            raise HTTPException(status_code=422, detail=f"{key}={val} out of range")


async def _panel_for_home(home_id: int):
    """Return (device_id, sn) of the home's EcoFlow Smart Home Panel, or 404."""
    row = await db.fetchrow(
        """SELECT device_id, api_identifier
           FROM devices
           WHERE home_id = $1 AND device_type = 'smart_panel'
           ORDER BY device_id LIMIT 1""",
        home_id,
    )
    if row is None or not row["api_identifier"]:
        raise HTTPException(status_code=404, detail="Home has no smart panel")
    return row["device_id"], row["api_identifier"]


def _ecoflow_client_for_sn(sn: str):
    """Build an EcoFlowClient with the account creds that own `sn` (or None).
    Imported lazily so the API doesn't hard-depend on data_collectors at load."""
    from data_collectors.config import iter_ecoflow_devices
    from data_collectors.ecoflow_client import EcoFlowClient

    for dev in iter_ecoflow_devices():
        if dev["device_sn"] == sn:
            return EcoFlowClient(dev)
    return None


def _ecobee_client_for_thermostat(identifier: str):
    """Build an EcobeeClient for the account that owns this thermostat id, or
    None. Imported lazily so the API doesn't hard-depend on data_collectors."""
    from data_collectors.config import iter_ecobee_accounts, iter_ecobee_devices
    from data_collectors.ecobee_client import EcobeeClient

    account_name = None
    for dev in iter_ecobee_devices():
        if str(dev.get("device_id")) == str(identifier):
            account_name = dev.get("account_name")
            break
    if account_name is None:
        return None
    for acc in iter_ecobee_accounts():
        if acc.get("name") == account_name:
            return EcobeeClient(acc)
    return None


async def _actuate_thermostat(identifier: str, params: dict) -> tuple[bool, Optional[str]]:
    """Push a setpoint hold to the Ecobee (blocking client off-loop). The
    dashboard sends setpoints in Celsius; the Ecobee API wants Fahrenheit.
    Returns (success, error_msg)."""
    client = _ecobee_client_for_thermostat(identifier)
    if client is None:
        return False, f"No Ecobee credentials for thermostat {identifier}"
    heat_c = params.get("heat_setpoint_c")
    cool_c = params.get("cool_setpoint_c")
    heat_f = heat_c * 9 / 5 + 32 if heat_c is not None else None
    cool_f = cool_c * 9 / 5 + 32 if cool_c is not None else None
    if heat_f is None and cool_f is None:
        return False, "setpoint_adjust needs heat_setpoint_c or cool_setpoint_c"
    hold_type = params.get("hold_type", "nextTransition")
    try:
        await asyncio.to_thread(
            client.set_temperature, identifier, heat_f, cool_f, hold_type
        )
    except Exception as exc:  # noqa: BLE001 — surface as a failed action, not a 500
        return False, str(exc)
    return True, None


async def _actuate_panel_mode(sn: str, params: dict) -> tuple[bool, Optional[str]]:
    """Push panel-mode params to the EcoFlow SHP2 (blocking client off-loop).
    Returns (success, error_msg)."""
    client = _ecoflow_client_for_sn(sn)
    if client is None:
        return False, f"No EcoFlow credentials for panel {sn}"
    try:
        out = await asyncio.to_thread(client.set_panel_mode, params, sn)
    except Exception as exc:  # noqa: BLE001 — surface as a failed action, not a 500
        return False, str(exc)
    if str(out.get("code")) != "0":
        return False, out.get("message", "EcoFlow write failed")
    return True, None


def _bus_params(req: DispatchRequest) -> dict:
    """Params shaped for the VOLTTRON CommandTranslator. The dashboard sends
    thermostat setpoints in Celsius (`*_setpoint_c`); the translator and Ecobee
    agent expect Fahrenheit (`*_setpoint`), so convert before publishing. Other
    target kinds pass through unchanged (panel-mode keys match on both sides)."""
    if req.target.kind != "thermostat":
        return req.params
    out = {
        k: v for k, v in req.params.items()
        if k not in ("cool_setpoint_c", "heat_setpoint_c")
    }
    cool_c = req.params.get("cool_setpoint_c")
    heat_c = req.params.get("heat_setpoint_c")
    if cool_c is not None:
        out["cool_setpoint"] = round(cool_c * 9 / 5 + 32, 1)
    if heat_c is not None:
        out["heat_setpoint"] = round(heat_c * 9 / 5 + 32, 1)
    return out


@router.post("/control/dispatch", response_model=DispatchResponse)
async def dispatch(req: DispatchRequest, user: User = Depends(require_dispatch())):
    if not _has_home_scope(user, req.home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    if req.action_type not in ACTION_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown action_type '{req.action_type}'")

    home = await db.fetchrow("SELECT gateway_id FROM homes WHERE home_id = $1", req.home_id)
    if home is None:
        raise HTTPException(status_code=404, detail="Home not found")
    gateway_id = home["gateway_id"]
    # A gateway is only needed to route the command over the bus. When the bus
    # is enabled, a missing gateway is a real misconfiguration; when it's
    # disabled (no broker, e.g. local dev) dispatch still records the action.
    if gateway_id is None and control_bus.enabled:
        raise HTTPException(status_code=422, detail="Home has no gateway configured")

    circuit_id = req.target.circuit_id
    device_id = req.target.device_id

    # Panel operating-mode dispatch (smartBackupMode / EPS / charge settings).
    # Validate params up front so a bad write never reaches a real panel, and
    # resolve the panel SN now so a misconfigured home fails before we record.
    panel_sn: Optional[str] = None
    if req.target.kind == "battery_mode":
        _validate_panel_params(req.params)
        if device_id is not None:
            row = await db.fetchrow(
                """SELECT device_id, api_identifier, home_id
                   FROM devices WHERE device_id = $1 AND device_type = 'smart_panel'""",
                device_id,
            )
            if row is None or not row["api_identifier"]:
                raise HTTPException(status_code=404, detail="No smart panel for device_id")
            if row["home_id"] != req.home_id:
                raise HTTPException(status_code=422, detail="Panel does not belong to this home")
            panel_sn = row["api_identifier"]
        else:
            device_id, panel_sn = await _panel_for_home(req.home_id)

    # Thermostat setpoint dispatch: resolve the Ecobee identifier now so a bad
    # device fails before we record, and so the bus-disabled path can actuate.
    thermo_id: Optional[str] = None
    if req.target.kind == "thermostat":
        if device_id is None:
            raise HTTPException(status_code=422, detail="thermostat target requires device_id")
        row = await db.fetchrow(
            """SELECT api_identifier, home_id
               FROM devices WHERE device_id = $1 AND device_type = 'thermostat'""",
            device_id,
        )
        if row is None or not row["api_identifier"]:
            raise HTTPException(status_code=404, detail="No thermostat for device_id")
        if row["home_id"] != req.home_id:
            raise HTTPException(status_code=422, detail="Thermostat does not belong to this home")
        thermo_id = row["api_identifier"]

    # Safety: never curtail critical / non-controllable circuits.
    if req.target.kind == "circuit":
        if circuit_id is None:
            raise HTTPException(status_code=422, detail="circuit target requires circuit_id")
        c = await db.fetchrow(
            """SELECT pc.is_critical, pc.is_controllable, d.home_id
               FROM panel_circuits pc
               JOIN devices d ON d.device_id = pc.device_id
               WHERE pc.circuit_id = $1""",
            circuit_id,
        )
        if c is None:
            raise HTTPException(status_code=404, detail="Circuit not found")
        if c["home_id"] != req.home_id:
            raise HTTPException(status_code=422, detail="Circuit does not belong to this home")
        if c["is_critical"] or not c["is_controllable"]:
            raise HTTPException(status_code=422, detail="Circuit is critical or non-controllable")

    action_id = await db.fetchval(
        """INSERT INTO control_actions
             (home_id, device_id, circuit_id, event_id, ts,
              action_type, triggered_by, command_payload)
           VALUES ($1,$2,$3,$4,NOW(),$5::action_type_enum,'manual',$6::jsonb)
           RETURNING action_id""",
        req.home_id, device_id, circuit_id, req.event_id,
        req.action_type, json.dumps(req.params),
    )

    if control_bus.enabled and gateway_id is not None:
        await control_bus.publish(
            control_topic(gateway_id),
            {
                "action_id": action_id,
                "home_id": req.home_id,
                "action_type": req.action_type,
                "target": req.target.model_dump(),
                "params": _bus_params(req),
                "event_id": req.event_id,
            },
        )
        return DispatchResponse(action_id=action_id, status="pending")

    # Bus disabled (no broker, e.g. local dev): the API actuates the panel
    # directly so the action still reaches hardware, then writes the result
    # back onto the same control_actions row the VOLTTRON path would have.
    if req.target.kind == "battery_mode" and panel_sn is not None:
        success, error_msg = await _actuate_panel_mode(panel_sn, req.params)
        await db.execute(
            """UPDATE control_actions
               SET success = $2, acknowledged_at = NOW(), error_msg = $3
               WHERE action_id = $1""",
            action_id, success, error_msg,
        )
        return DispatchResponse(
            action_id=action_id, status="success" if success else "failed"
        )

    if req.target.kind == "thermostat" and thermo_id is not None:
        success, error_msg = await _actuate_thermostat(thermo_id, req.params)
        await db.execute(
            """UPDATE control_actions
               SET success = $2, acknowledged_at = NOW(), error_msg = $3
               WHERE action_id = $1""",
            action_id, success, error_msg,
        )
        return DispatchResponse(
            action_id=action_id, status="success" if success else "failed"
        )

    return DispatchResponse(action_id=action_id, status="pending")


def _quota_lookup(quota: dict, name: str):
    """Pull a panel-mode value out of an SHP2 quota dict. EcoFlow prefixes
    panel keys (e.g. `pd303_mc.smartBackupMode`), so match on the suffix."""
    if name in quota:
        return quota[name]
    for key, val in quota.items():
        if key.split(".")[-1] == name:
            return val
    return None


@router.get("/control/panel-mode", response_model=PanelModeRow)
async def panel_mode(home_id: int = Query(...), user: User = Depends(get_current_user)):
    if not _has_home_scope(user, home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    device_id, sn = await _panel_for_home(home_id)
    client = _ecoflow_client_for_sn(sn)
    if client is None:
        raise HTTPException(status_code=404, detail=f"No EcoFlow credentials for panel {sn}")
    quota = await asyncio.to_thread(client.get_device_quota, sn)
    if quota is None:
        raise HTTPException(status_code=502, detail="Panel did not return quota")
    eps = _quota_lookup(quota, "epsModeInfo")
    return PanelModeRow(
        home_id=home_id,
        device_id=device_id,
        smartBackupMode=_quota_lookup(quota, "smartBackupMode"),
        epsModeInfo=bool(eps) if eps is not None else None,
        backupReserveSoc=_quota_lookup(quota, "backupReserveSoc"),
        chargeWattPower=_quota_lookup(quota, "chargeWattPower"),
        foceChargeHight=_quota_lookup(quota, "foceChargeHight"),
    )


# =====================================================================
# Thermostat setpoint plan — forward 24h, one series per controller
# (docs/DASHBOARD_DESIGN.md §10). baseline / rbc are synthesized from
# mpc_config; mpc is read from the latest control_advisories schedule.
# =====================================================================
_PLAN_STEPS = 96
_PLAN_DT_S = 900
F_TO_C_DELTA = 5.0 / 9.0
_W_PER_TON = 3516.85  # 1 ton cooling = 12,000 BTU/h (matches hvac_model.py)
_mpc_config_cache: Optional[dict] = None
_hvac_model_cache: dict = {}


def _day_window(tz_name: str):
    """(start_utc, end_utc) spanning the home's current local day — midnight to
    midnight, 96 quarter-hour steps. The plan covers the whole of today, so the
    elapsed hours are shown alongside the remaining ones."""
    tz = ZoneInfo(tz_name)
    start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)
    return start_utc, start_utc + timedelta(seconds=_PLAN_DT_S * _PLAN_STEPS)


async def _thermostat_device_id(home_id) -> Optional[int]:
    row = await db.fetchrow(
        """SELECT device_id FROM devices
           WHERE home_id = $1 AND device_type = 'thermostat'
           ORDER BY device_id LIMIT 1""",
        home_id,
    )
    return row["device_id"] if row else None


def _load_hvac_model(home_name: str) -> Optional[dict]:
    """1R1C RC params + equipment capacity/mode from the fitted model file
    config/hvac_model_<home>.json (gitignored). Cached; None if absent."""
    if home_name not in _hvac_model_cache:
        path = os.path.join(CONFIG_DIR, f"hvac_model_{home_name}.json")
        try:
            with open(path) as f:
                d = json.load(f)
        except FileNotFoundError:
            _hvac_model_cache[home_name] = None
            return None
        rc = d.get("rc_model") or {}
        eq = d.get("equipment") or {}
        _hvac_model_cache[home_name] = {
            "a": float(rc.get("a", 0.0)),
            "g": float(rc.get("g", 0.0)),
            "d": float(rc.get("d", 0.0)),
            "capacity_w": float(eq.get("tons", 0) or 0) * _W_PER_TON,
            "mode": eq.get("mode", "both"),
        }
    return _hvac_model_cache[home_name]


def _nearest_grid(samples, start, steps, dt_s):
    """Nearest-sample fill of (ts, value) pairs onto a regular grid. `samples`
    must be sorted by ts. Returns a list of length `steps` (None where empty)."""
    if not samples:
        return [None] * steps
    times = [s[0] for s in samples]
    out = []
    for i in range(steps):
        ts = start + timedelta(seconds=dt_s * i)
        j = bisect.bisect_left(times, ts)
        cands = []
        if j < len(samples):
            cands.append(samples[j])
        if j > 0:
            cands.append(samples[j - 1])
        out.append(min(cands, key=lambda s: abs((s[0] - ts).total_seconds()))[1])
    return out


async def _oat_grid(home_id, device_id, start, steps, dt_s):
    """Outdoor-air temp (°C) per plan step: logged actuals for the elapsed part
    of the day, forecast for the rest, nearest-filled onto the grid."""
    end = start + timedelta(seconds=dt_s * steps)
    samples = []
    if device_id is not None:
        rows = await db.fetch(
            """SELECT ts, outdoor_temp_c FROM thermostat_readings
               WHERE device_id = $1 AND ts >= $2 AND ts <= $3
                 AND outdoor_temp_c IS NOT NULL ORDER BY ts""",
            device_id, start, end,
        )
        samples += [(r["ts"], float(r["outdoor_temp_c"])) for r in rows]
    samples += [(p.ts, p.outdoor_temp_c)
                for p in await _forecast_oat(home_id, start, end)
                if p.outdoor_temp_c is not None]
    samples.sort(key=lambda x: x[0])
    return _nearest_grid(samples, start, steps, dt_s)


async def _indoor_actual_grid(device_id, start, steps, dt_s):
    """Logged indoor temp (°C) per step for the elapsed part of the window;
    None for steps still in the future (no measurement yet)."""
    if device_id is None:
        return [None] * steps
    now = datetime.now(timezone.utc)
    rows = await db.fetch(
        """SELECT ts, indoor_temp_c FROM thermostat_readings
           WHERE device_id = $1 AND ts >= $2 AND ts <= $3
             AND indoor_temp_c IS NOT NULL ORDER BY ts""",
        device_id, start, min(start + timedelta(seconds=dt_s * steps), now),
    )
    grid = _nearest_grid([(r["ts"], float(r["indoor_temp_c"])) for r in rows], start, steps, dt_s)
    return [
        v if start + timedelta(seconds=dt_s * i) <= now else None
        for i, v in enumerate(grid)
    ]


async def _initial_indoor(device_id, at_ts):
    """Indoor temp (°C) nearest `at_ts` within ±2h, or None — the prediction's
    starting state."""
    if device_id is None:
        return None
    row = await db.fetchrow(
        """SELECT indoor_temp_c FROM thermostat_readings
           WHERE device_id = $1 AND indoor_temp_c IS NOT NULL
             AND ts BETWEEN $2 AND $3
           ORDER BY abs(extract(epoch FROM (ts - $4))) LIMIT 1""",
        device_id, at_ts - timedelta(hours=2), at_ts + timedelta(hours=2), at_ts,
    )
    return float(row["indoor_temp_c"]) if row and row["indoor_temp_c"] is not None else None


async def _past_day_setpoints(device_id, start, steps, dt_s):
    """Cool/heat setpoint series (°C) lifted from the SAME time-of-day on the
    previous day, nearest-filled onto today's grid. (None lists if no device)."""
    if device_id is None:
        return [None] * steps, [None] * steps
    prev_start = start - timedelta(days=1)
    prev_end = prev_start + timedelta(seconds=dt_s * steps)
    rows = await db.fetch(
        """SELECT ts, cool_setpoint_c, heat_setpoint_c FROM thermostat_readings
           WHERE device_id = $1 AND ts >= $2 AND ts <= $3 ORDER BY ts""",
        device_id, prev_start, prev_end,
    )
    cool_s = [(r["ts"], float(r["cool_setpoint_c"])) for r in rows if r["cool_setpoint_c"] is not None]
    heat_s = [(r["ts"], float(r["heat_setpoint_c"])) for r in rows if r["heat_setpoint_c"] is not None]
    return (
        _nearest_grid(cool_s, prev_start, steps, dt_s),
        _nearest_grid(heat_s, prev_start, steps, dt_s),
    )


def _predict_indoor(model, T0, oat, cool, heat):
    """Thermostatic forward simulation of indoor temp (°C) from the planned
    setpoints. Each step free-runs the 1R1C zone, then applies just enough HVAC
    (capped at equipment capacity) to hold the active setpoint — a smooth curve
    that tracks the setpoint whenever capacity allows, and floats otherwise.
    Returns Nones if the home has no fitted model or no starting temperature."""
    if model is None or T0 is None:
        return [None] * len(oat)
    a, g, d, cap = model["a"], model["g"], model["d"], model["capacity_w"]
    can_cool = model["mode"] in ("cool", "both")
    can_heat = model["mode"] in ("heat", "both")
    out = []
    T = T0
    for i, tout in enumerate(oat):
        if tout is None:
            out.append(round(T, 3))
            continue
        passive = T + a * (tout - T) + d  # next-step temp with HVAC off
        c_sp = cool[i] if i < len(cool) else None
        h_sp = heat[i] if i < len(heat) else None
        q = 0.0
        if can_cool and c_sp is not None and passive > c_sp and g > 0:
            q = max((c_sp - passive) / g, -cap)
        elif can_heat and h_sp is not None and passive < h_sp and g > 0:
            q = min((h_sp - passive) / g, cap)
        T = passive + g * q
        out.append(round(T, 3))
    return out


def _f_to_c(f) -> Optional[float]:
    return None if f is None else round((float(f) - 32.0) * 5.0 / 9.0, 3)


def _floor_quarter(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def _mpc_config() -> dict:
    """config/mpc_config.json (gitignored). Empty dict if absent."""
    global _mpc_config_cache
    if _mpc_config_cache is None:
        try:
            with open(os.path.join(CONFIG_DIR, "mpc_config.json")) as f:
                _mpc_config_cache = json.load(f)
        except FileNotFoundError:
            _mpc_config_cache = {}
    return _mpc_config_cache


def _baseline_setpoints(home_name: str):
    """(cool_c, heat_c, mode) comfort baseline for this home from mpc_config:
    per-home baseline_setpoints overrides defaults, comfort edges are the last
    fallback. mode gates the meaningful side (cool-only homes -> heat None)."""
    cfg = _mpc_config()
    hc = (cfg.get("homes") or {}).get(home_name, {})
    mode = hc.get("mode", "both")
    base = dict((cfg.get("defaults") or {}).get("baseline_setpoints") or {})
    base.update(hc.get("baseline_setpoints") or {})
    cool_c = _f_to_c(base.get("cool_setpoint_f"))
    heat_c = _f_to_c(base.get("heat_setpoint_f"))
    comfort = hc.get("comfort") or {}
    if cool_c is None:
        cool_c = comfort.get("cool_max_c")
    if heat_c is None:
        heat_c = comfort.get("heat_min_c")
    if mode == "cool":
        heat_c = None
    elif mode == "heat":
        cool_c = None
    return (
        float(cool_c) if cool_c is not None else None,
        float(heat_c) if heat_c is not None else None,
        mode,
    )


def _rbc_offsets_c() -> tuple[float, float]:
    rbc = (_mpc_config().get("defaults") or {}).get("rbc") or {}
    sym = float(rbc.get("setpoint_offset_f", 2.0))
    return (
        float(rbc.get("cool_offset_f", sym)) * F_TO_C_DELTA,
        float(rbc.get("heat_offset_f", sym)) * F_TO_C_DELTA,
    )


async def _rbc_trigger_windows(window_start, window_end):
    """OpenADR events overlapping the window that trigger band-widening (DR /
    outage), per mpc_config defaults.rbc.trigger. Returns [(start, end), ...]."""
    trig = ((_mpc_config().get("defaults") or {}).get("rbc") or {}).get("trigger", {})
    period_types = [p.lower() for p in trig.get("period_types", [])]
    keywords = [k.lower() for k in trig.get("event_name_contains", [])]
    rows = await db.fetch(
        """SELECT DISTINCT ON (event_id)
                  event_name, program_name, period_type, interval_start, interval_end
           FROM openadr_events
           WHERE interval_start < $2 AND interval_end > $1
           ORDER BY event_id, ts DESC""",
        window_start, window_end,
    )
    windows = []
    for r in rows:
        pt = (r["period_type"] or "").lower()
        hay = f"{r['event_name'] or ''} {r['program_name'] or ''}".lower()
        if (pt and pt in period_types) or any(k in hay for k in keywords):
            windows.append((r["interval_start"], r["interval_end"]))
    return windows


async def _forecast_oat(home_id, window_start, window_end):
    """Forecast outdoor-air temperature for the window, latest forecast run."""
    rows = await db.fetch(
        """SELECT wf.forecast_ts AS ts, wf.temp_c
           FROM weather_forecast wf
           JOIN weather_locations wl ON wl.location_id = wf.location_id
           WHERE wl.home_id = $1
             AND wf.generated_at = (
                 SELECT max(wf2.generated_at)
                 FROM weather_forecast wf2
                 JOIN weather_locations wl2 ON wl2.location_id = wf2.location_id
                 WHERE wl2.home_id = $1)
             AND wf.forecast_ts >= $2 AND wf.forecast_ts <= $3
           ORDER BY wf.forecast_ts""",
        home_id, window_start, window_end,
    )
    return [
        ForecastPoint(ts=r["ts"], outdoor_temp_c=float(r["temp_c"]) if r["temp_c"] is not None else None)
        for r in rows
    ]


async def _mpc_plan(home_id):
    """Forward schedule from the latest MPC advisory's detail arrays, or None."""
    row = await db.fetchrow(
        """SELECT detail FROM control_advisories
           WHERE home_id = $1 AND controller = 'mpc'
           ORDER BY ts DESC LIMIT 1""",
        home_id,
    )
    if row is None or row["detail"] is None:
        return None
    d = row["detail"] if isinstance(row["detail"], dict) else json.loads(row["detail"])
    cool = d.get("recommended_cool_setpoint_c")
    if not isinstance(cool, list) or not d.get("start_utc"):
        return None
    heat = d.get("recommended_heat_setpoint_c")
    pred = d.get("predicted_indoor_temp_c")
    start = datetime.fromisoformat(d["start_utc"])
    dt_s = int(float(d.get("dt_s", _PLAN_DT_S)))

    def at(arr, i):
        if isinstance(arr, list) and i < len(arr) and arr[i] is not None:
            return float(arr[i])
        return None

    points = [
        SetpointPlanPoint(
            ts=start + timedelta(seconds=dt_s * i),
            cool_setpoint_c=at(cool, i),
            heat_setpoint_c=at(heat, i),
            predicted_indoor_temp_c=at(pred, i),
        )
        for i in range(len(cool))
    ]
    imm_cool = d.get("immediate_cool_setpoint_c")
    imm_heat = d.get("immediate_heat_setpoint_c")
    return (
        start, dt_s, points,
        float(imm_cool) if imm_cool is not None else None,
        float(imm_heat) if imm_heat is not None else None,
    )


@router.get("/control/setpoint-plan", response_model=SetpointPlan)
async def setpoint_plan(
    home_id: int = Query(...),
    controller: str = Query("baseline"),
    user: User = Depends(get_current_user),
):
    if not _has_home_scope(user, home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    if controller not in SETPOINT_CONTROLLERS:
        raise HTTPException(
            status_code=422,
            detail=f"controller must be one of {list(SETPOINT_CONTROLLERS)}",
        )
    hrow = await db.fetchrow(
        "SELECT home_name, timezone FROM homes WHERE home_id = $1", home_id
    )
    if hrow is None:
        raise HTTPException(status_code=404, detail="Home not found")
    cool_c, heat_c, mode = _baseline_setpoints(hrow["home_name"])

    if controller == "mpc":
        mpc = await _mpc_plan(home_id)
        if mpc is None:
            start = _floor_quarter(datetime.now(timezone.utc))
            end = start + timedelta(seconds=_PLAN_DT_S * _PLAN_STEPS)
            return SetpointPlan(
                home_id=home_id, controller=controller, mode=mode,
                start=start, dt_s=_PLAN_DT_S, available=False,
                note="No MPC advisory available for this home.",
                forecast=await _forecast_oat(home_id, start, end),
            )
        start, dt_s, points, imm_cool, imm_heat = mpc
        end = start + timedelta(seconds=dt_s * len(points))
        return SetpointPlan(
            home_id=home_id, controller=controller, mode=mode,
            start=start, dt_s=dt_s,
            immediate_cool_setpoint_c=imm_cool, immediate_heat_setpoint_c=imm_heat,
            points=points, forecast=await _forecast_oat(home_id, start, end),
        )

    # baseline | rbc: a full current-day grid (local midnight to midnight).
    #   baseline -> the previous day's actual cool/heat setpoints, replayed.
    #   rbc      -> config comfort setpoints, band-widened during DR/outage
    #               event windows.
    # Predicted indoor temp is simulated from each controller's own planned
    # setpoints via the fitted RC model.
    start, end = _day_window(hrow["timezone"])
    device_id = await _thermostat_device_id(home_id)

    cool_series = [None] * _PLAN_STEPS
    heat_series = [None] * _PLAN_STEPS
    if controller == "baseline":
        past_cool, past_heat = await _past_day_setpoints(
            device_id, start, _PLAN_STEPS, _PLAN_DT_S
        )
        # Replay both setpoints as the thermostat actually held them yesterday;
        # don't gate by equipment mode here (a cool-only home still reports a
        # heat setpoint, and the user wants both shown for the baseline).
        for i in range(_PLAN_STEPS):
            c = past_cool[i] if past_cool[i] is not None else cool_c
            h = past_heat[i] if past_heat[i] is not None else heat_c
            cool_series[i] = round(c, 3) if c is not None else None
            heat_series[i] = round(h, 3) if h is not None else None
    else:  # rbc
        windows = await _rbc_trigger_windows(start, end)
        cool_off_c, heat_off_c = _rbc_offsets_c()
        for i in range(_PLAN_STEPS):
            ts = start + timedelta(seconds=_PLAN_DT_S * i)
            relaxed = any(w0 <= ts < w1 for w0, w1 in windows)
            c, h = cool_c, heat_c
            if relaxed:
                if c is not None:
                    c = c + cool_off_c
                if h is not None:
                    h = h - heat_off_c
            cool_series[i] = round(c, 3) if c is not None and mode in ("cool", "both") else None
            heat_series[i] = round(h, 3) if h is not None and mode in ("heat", "both") else None

    oat = await _oat_grid(home_id, device_id, start, _PLAN_STEPS, _PLAN_DT_S)
    indoor = await _indoor_actual_grid(device_id, start, _PLAN_STEPS, _PLAN_DT_S)
    model = _load_hvac_model(hrow["home_name"])
    T0 = await _initial_indoor(device_id, start)
    predicted = _predict_indoor(model, T0, oat, cool_series, heat_series)

    points = [
        SetpointPlanPoint(
            ts=start + timedelta(seconds=_PLAN_DT_S * i),
            cool_setpoint_c=cool_series[i],
            heat_setpoint_c=heat_series[i],
            predicted_indoor_temp_c=predicted[i],
            indoor_temp_c=indoor[i],
        )
        for i in range(_PLAN_STEPS)
    ]
    forecast = [
        ForecastPoint(ts=start + timedelta(seconds=_PLAN_DT_S * i), outdoor_temp_c=oat[i])
        for i in range(_PLAN_STEPS)
    ]

    # "Apply" should push the setpoint for the current moment, not midnight.
    now = datetime.now(timezone.utc)
    i_now = max(0, min(_PLAN_STEPS - 1, int((now - start).total_seconds() // _PLAN_DT_S)))
    return SetpointPlan(
        home_id=home_id, controller=controller, mode=mode,
        start=start, dt_s=_PLAN_DT_S,
        immediate_cool_setpoint_c=points[i_now].cool_setpoint_c,
        immediate_heat_setpoint_c=points[i_now].heat_setpoint_c,
        points=points, forecast=forecast,
    )


@router.get("/control/actions", response_model=list[ControlActionRow])
async def control_actions(
    home_id: int = Query(...),
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    user: User = Depends(get_current_user),
):
    if not _has_home_scope(user, home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    args: list = [home_id]
    clauses = ["home_id = $1"]
    if date_from is not None:
        args.append(date_from)
        clauses.append(f"ts >= ${len(args)}")
    if date_to is not None:
        args.append(date_to)
        clauses.append(f"ts <= ${len(args)}")
    rows = await db.fetch(
        f"""SELECT action_id, home_id, device_id, circuit_id, event_id, ts,
                   action_type::text AS action_type, triggered_by::text AS triggered_by,
                   success, acknowledged_at, error_msg
            FROM control_actions
            WHERE {' AND '.join(clauses)}
            ORDER BY ts DESC LIMIT 500""",
        *args,
    )
    return [
        ControlActionRow(
            **{k: r[k] for k in (
                "action_id", "home_id", "device_id", "circuit_id", "event_id",
                "ts", "action_type", "triggered_by", "success", "acknowledged_at", "error_msg",
            )},
            status=_action_status(r["success"], r["acknowledged_at"]),
        )
        for r in rows
    ]


@router.get("/control/actions/{action_id}", response_model=ControlActionRow)
async def control_action(action_id: int, user: User = Depends(get_current_user)):
    r = await db.fetchrow(
        """SELECT action_id, home_id, device_id, circuit_id, event_id, ts,
                  action_type::text AS action_type, triggered_by::text AS triggered_by,
                  success, acknowledged_at, error_msg
           FROM control_actions WHERE action_id = $1""",
        action_id,
    )
    if r is None:
        raise HTTPException(status_code=404, detail="Action not found")
    if not _has_home_scope(user, r["home_id"]):
        raise HTTPException(status_code=403, detail="Home not in scope")
    return ControlActionRow(
        **{k: r[k] for k in (
            "action_id", "home_id", "device_id", "circuit_id", "event_id",
            "ts", "action_type", "triggered_by", "success", "acknowledged_at", "error_msg",
        )},
        status=_action_status(r["success"], r["acknowledged_at"]),
    )


@router.get("/control/advisories", response_model=list[ControlAdvisoryRow])
async def control_advisories(
    home_id: int = Query(...),
    active: bool = Query(False),
    user: User = Depends(require("viewer", home_param=None)),
):
    if not _has_home_scope(user, home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    # "active" => the most recent advisory still in shadow mode (latest per home).
    where = "home_id = $1"
    if active:
        where += " AND shadow_mode"
    limit = "LIMIT 1" if active else "LIMIT 200"
    rows = await db.fetch(
        f"""SELECT advisory_id, home_id, device_id, circuit_id, event_id, ts,
                   controller, action_type::text AS action_type,
                   triggered_by::text AS triggered_by, operation_scenario, shadow_mode,
                   baseline_cool_setpoint_c, baseline_heat_setpoint_c,
                   recommended_cool_setpoint_c, recommended_heat_setpoint_c,
                   expected_cost_usd, expected_energy_kwh
            FROM control_advisories
            WHERE {where}
            ORDER BY ts DESC {limit}""",
        home_id,
    )
    return [ControlAdvisoryRow(**dict(r)) for r in rows]
