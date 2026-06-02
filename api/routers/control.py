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
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import User, _has_home_scope, get_current_user, require, require_dispatch
from ..control_bus import control_bus, control_topic
from ..db import db
from ..models import (
    ACTION_TYPES,
    PANEL_MODE_PARAMS,
    ControlActionRow,
    ControlAdvisoryRow,
    DispatchRequest,
    DispatchResponse,
    PanelModeRow,
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
                "params": req.params,
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
