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

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import User, _has_home_scope, get_current_user, require, require_dispatch
from ..control_bus import control_bus, control_topic
from ..db import db
from ..models import (
    ACTION_TYPES,
    ControlActionRow,
    ControlAdvisoryRow,
    DispatchRequest,
    DispatchResponse,
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


@router.post("/control/dispatch", response_model=DispatchResponse)
async def dispatch(req: DispatchRequest, user: User = Depends(require_dispatch())):
    if not _has_home_scope(user, req.home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    if req.action_type not in ACTION_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown action_type '{req.action_type}'")

    gateway_id = await db.fetchval("SELECT gateway_id FROM homes WHERE home_id = $1", req.home_id)
    if gateway_id is None:
        # home exists but has no gateway, or home missing — distinguish
        exists = await db.fetchval("SELECT 1 FROM homes WHERE home_id = $1", req.home_id)
        if exists is None:
            raise HTTPException(status_code=404, detail="Home not found")
        raise HTTPException(status_code=422, detail="Home has no gateway configured")

    circuit_id = req.target.circuit_id
    device_id = req.target.device_id

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
