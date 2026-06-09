"""
Operation-scenario endpoints (Scenarios dashboard page).

The smart-home ILC resolves one operation scenario per home each cycle
(normal | load_management_tou | load_management_dr | load_management_capacity |
capacity_management | resiliency) and logs it to control_advisories. This router
surfaces that current scenario, lets an operator pin a per-day scenario on a
calendar (scenario_schedule), and dispatches a scenario's operation: the panel
battery mode + the thermostat band-widen setpoints, reusing the guarded
/control/dispatch path so RBAC, home-scope, circuit-safety and the VOLTTRON bus
all behave identically.

Dispatch resolution comes from mpc_config:
  - battery mode  <- defaults.load_management.scenarios[scenario].battery_mode,
  - setpoints     <- baseline_setpoints +/- defaults.scenarios[scenario] offsets
                     (cooling raised, heating lowered).

load_management_capacity additionally sheds each non-essential circuit by capping
its max input current (PD303 setAmp) via the same guarded dispatch path; normal
restores those circuits to their breaker rating (or the panel master breaker when
unrated). Only capacity_management's grid disconnect stays out of band — it is
performed by an external switch, so that leg is reported but not actuated here.
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import User, _has_home_scope, get_current_user, require_dispatch
from ..db import db
from ..models import (
    OPERATION_SCENARIOS,
    DispatchRequest,
    DispatchTarget,
    ScenarioCurrent,
    ScenarioDispatchRequest,
    ScenarioDispatchResult,
    ScenarioDispatchStep,
    ScenarioScheduleEntry,
    ScenarioScheduleSet,
)
from . import control

router = APIRouter(prefix="/api/v1", tags=["scenarios"])


def _check_scenario(name: str) -> None:
    if name not in OPERATION_SCENARIOS:
        raise HTTPException(
            status_code=422,
            detail=f"operation_scenario must be one of {list(OPERATION_SCENARIOS)}",
        )


@router.get("/scenarios/current", response_model=list[ScenarioCurrent])
async def scenarios_current(user: User = Depends(get_current_user)):
    """Each accessible home's latest resolved scenario plus today's override."""
    from ..auth import ALL_HOMES_ROLES

    sql = """
        SELECT h.home_id, h.home_name,
               adv.operation_scenario AS adv_scenario, adv.ts AS adv_ts,
               sch.operation_scenario AS sched_scenario
        FROM homes h
        LEFT JOIN LATERAL (
            SELECT operation_scenario, ts
            FROM control_advisories ca
            WHERE ca.home_id = h.home_id AND ca.operation_scenario IS NOT NULL
            ORDER BY ts DESC LIMIT 1
        ) adv ON TRUE
        LEFT JOIN scenario_schedule sch
            ON sch.home_id = h.home_id
           AND sch.scenario_date = (NOW() AT TIME ZONE h.timezone)::date
    """
    if user.role in ALL_HOMES_ROLES:
        rows = await db.fetch(sql + " ORDER BY h.home_id")
    else:
        rows = await db.fetch(
            sql + " WHERE h.home_id = ANY($1::int[]) ORDER BY h.home_id", user.homes
        )
    return [
        ScenarioCurrent(
            home_id=r["home_id"],
            home_name=r["home_name"],
            current_scenario=r["adv_scenario"],
            source="advisory" if r["adv_scenario"] is not None else None,
            ts=r["adv_ts"],
            scheduled_scenario=r["sched_scenario"],
        )
        for r in rows
    ]


@router.get("/scenarios/schedule", response_model=list[ScenarioScheduleEntry])
async def scenarios_schedule(
    home_id: int = Query(...),
    month: str = Query(..., description="YYYY-MM"),
    user: User = Depends(get_current_user),
):
    if not _has_home_scope(user, home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    try:
        first = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    last = first.replace(day=_cal.monthrange(first.year, first.month)[1])
    rows = await db.fetch(
        """SELECT home_id, scenario_date, operation_scenario, note,
                  created_by, updated_at
           FROM scenario_schedule
           WHERE home_id = $1 AND scenario_date BETWEEN $2 AND $3
           ORDER BY scenario_date""",
        home_id, first, last,
    )
    return [ScenarioScheduleEntry(**dict(r)) for r in rows]


@router.put("/scenarios/schedule", response_model=ScenarioScheduleEntry)
async def set_scenario_schedule(
    body: ScenarioScheduleSet, user: User = Depends(require_dispatch())
):
    if not _has_home_scope(user, body.home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    _check_scenario(body.operation_scenario)
    username = await db.fetchval(
        "SELECT username FROM app_users WHERE user_id = $1", user.user_id
    )
    row = await db.fetchrow(
        """INSERT INTO scenario_schedule
             (home_id, scenario_date, operation_scenario, note, created_by)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (home_id, scenario_date) DO UPDATE
             SET operation_scenario = EXCLUDED.operation_scenario,
                 note = EXCLUDED.note,
                 created_by = EXCLUDED.created_by,
                 updated_at = NOW()
           RETURNING home_id, scenario_date, operation_scenario, note,
                     created_by, updated_at""",
        body.home_id, body.scenario_date, body.operation_scenario,
        body.note, username,
    )
    return ScenarioScheduleEntry(**dict(row))


@router.delete("/scenarios/schedule", status_code=204)
async def clear_scenario_schedule(
    home_id: int = Query(...),
    scenario_date: date = Query(...),
    user: User = Depends(require_dispatch()),
):
    if not _has_home_scope(user, home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    await db.execute(
        "DELETE FROM scenario_schedule WHERE home_id = $1 AND scenario_date = $2",
        home_id, scenario_date,
    )
    return None


async def _dr_event_active() -> bool:
    """True if a band-widening DR/outage event covers the current moment, per
    the same mpc_config defaults.rbc.trigger keywords the RBC controller uses."""
    now = datetime.now(timezone.utc)
    windows = await control._rbc_trigger_windows(now, now + timedelta(minutes=1))
    return any(w0 <= now < w1 for w0, w1 in windows)


def _battery_params(scenario: str) -> dict:
    """Panel battery params (smartBackupMode + epsModeInfo) for the scenario from
    mpc_config. Each scenario carries a single battery_mode mapping."""
    cfg = control._mpc_config()
    scn = (((cfg.get("defaults") or {}).get("load_management") or {})
           .get("scenarios") or {}).get(scenario, {})
    mode = scn.get("battery_mode") or {}
    return {
        "smartBackupMode": int(mode.get("smartBackupMode", 0)),
        "epsModeInfo": bool(mode.get("epsModeInfo", False)),
    }


def _circuit_limit_amp() -> int:
    """Max input current (A) non-essential circuits are capped to when shedding,
    from mpc_config defaults.load_management.circuit_current_limit (default 0 =
    fully off)."""
    cfg = control._mpc_config()
    ccl = (((cfg.get("defaults") or {}).get("load_management") or {})
           .get("circuit_current_limit") or {})
    return int(ccl.get("non_essential_max_input_a", 0))


def _panel_main_breaker_amp() -> int:
    """Panel master breaker amperage from mpc_config defaults.panel (default 60),
    used as the restore target for circuits with no rated_amps on record."""
    cfg = control._mpc_config()
    panel = ((cfg.get("defaults") or {}).get("panel") or {})
    return int(panel.get("main_breaker_a", 60))


def _circuit_restore_amp(rated_amps) -> int:
    """Max input current (A) to restore a non-essential circuit to when the home
    returns to normal: its breaker rating if known, else the panel master
    breaker. Clamped to the 0-60 A setAmp range the panel accepts."""
    amp = int(rated_amps) if rated_amps is not None else _panel_main_breaker_amp()
    return max(0, min(amp, 60))


def _setpoint_params(home_name: str, scenario: str) -> dict:
    """Thermostat setpoint params (deg C) = baseline +/- the scenario offsets.
    Cooling is raised and heating lowered to widen the deadband; the home's
    mode gates which side is meaningful (cool-only -> no heat setpoint)."""
    cool_c, heat_c, mode = control._baseline_setpoints(home_name)
    cfg = control._mpc_config()
    offs = ((cfg.get("defaults") or {}).get("scenarios") or {}).get(scenario, {})
    cool_off = float(offs.get("cool_offset_f", 0.0)) * control.F_TO_C_DELTA
    heat_off = float(offs.get("heat_offset_f", 0.0)) * control.F_TO_C_DELTA
    params: dict = {}
    if cool_c is not None and mode in ("cool", "both"):
        params["cool_setpoint_c"] = round(cool_c + cool_off, 2)
    if heat_c is not None and mode in ("heat", "both"):
        params["heat_setpoint_c"] = round(heat_c - heat_off, 2)
    return params


@router.post("/scenarios/dispatch", response_model=ScenarioDispatchResult)
async def dispatch_scenario(
    req: ScenarioDispatchRequest, user: User = Depends(require_dispatch())
):
    """Dispatch a scenario's full-home operation: set the panel battery mode and
    push the thermostat band-widen setpoints. Each leg goes through the guarded
    /control/dispatch path, so a home with no panel/thermostat just records that
    leg as skipped rather than failing the whole dispatch."""
    if not _has_home_scope(user, req.home_id):
        raise HTTPException(status_code=403, detail="Home not in scope")
    _check_scenario(req.operation_scenario)

    hrow = await db.fetchrow(
        "SELECT home_name FROM homes WHERE home_id = $1", req.home_id
    )
    if hrow is None:
        raise HTTPException(status_code=404, detail="Home not found")

    dr_active = await _dr_event_active()
    steps: list[ScenarioDispatchStep] = []

    # 1. Panel battery mode (Savings mode + EPS backup) for the scenario.
    battery_params = _battery_params(req.operation_scenario)
    try:
        resp = await control.dispatch(
            DispatchRequest(
                home_id=req.home_id,
                action_type="set_operating_mode",
                target=DispatchTarget(kind="battery_mode"),
                params=battery_params,
            ),
            user,
        )
        steps.append(ScenarioDispatchStep(
            kind="battery_mode", action_id=resp.action_id,
            status=resp.status, params=battery_params,
        ))
    except HTTPException as e:
        steps.append(ScenarioDispatchStep(
            kind="battery_mode", status="skipped",
            detail=str(e.detail), params=battery_params,
        ))

    # 2. Thermostat band-widen setpoints for the scenario.
    setpoint_params = _setpoint_params(hrow["home_name"], req.operation_scenario)
    device_id = await control._thermostat_device_id(req.home_id)
    if device_id is None or not setpoint_params:
        steps.append(ScenarioDispatchStep(
            kind="thermostat", status="skipped",
            detail="No thermostat or no meaningful setpoint for this home/scenario.",
            params=setpoint_params,
        ))
    else:
        try:
            resp = await control.dispatch(
                DispatchRequest(
                    home_id=req.home_id,
                    action_type="setpoint_adjust",
                    target=DispatchTarget(kind="thermostat", device_id=device_id),
                    params=setpoint_params,
                ),
                user,
            )
            steps.append(ScenarioDispatchStep(
                kind="thermostat", action_id=resp.action_id,
                status=resp.status, params=setpoint_params,
            ))
        except HTTPException as e:
            steps.append(ScenarioDispatchStep(
                kind="thermostat", status="skipped",
                detail=str(e.detail), params=setpoint_params,
            ))

    # 3a. load_management_capacity sheds non-essential circuits by capping each
    #     one's max input current so the panel trips it off. One guarded
    #     /control/dispatch curtail per circuit, so RBAC/home-scope/circuit-safety
    #     all apply and each circuit gets its own control_actions row.
    if req.operation_scenario == "load_management_capacity":
        floor_a = _circuit_limit_amp()
        circuits = await db.fetch(
            """SELECT pc.circuit_id, pc.channel_num, pc.circuit_name
               FROM panel_circuits pc
               JOIN devices d ON d.device_id = pc.device_id
               WHERE d.home_id = $1
                 AND pc.circuit_priority = 'non_essential'
                 AND pc.is_controllable = TRUE AND pc.is_critical = FALSE
               ORDER BY pc.channel_num""",
            req.home_id,
        )
        if not circuits:
            steps.append(ScenarioDispatchStep(
                kind="circuit_current_limit", status="skipped",
                detail="No non-essential controllable circuits to shed for this home.",
            ))
        for c in circuits:
            cparams = {"max_input_a": floor_a}
            label = c["circuit_name"] or f"Circuit {c['channel_num']}"
            try:
                resp = await control.dispatch(
                    DispatchRequest(
                        home_id=req.home_id,
                        action_type="curtail",
                        target=DispatchTarget(kind="circuit", circuit_id=c["circuit_id"]),
                        params=cparams,
                    ),
                    user,
                )
                steps.append(ScenarioDispatchStep(
                    kind="circuit_current_limit", action_id=resp.action_id,
                    status=resp.status, params=cparams,
                    detail=f"{label}: cap max input current at {floor_a}A.",
                ))
            except HTTPException as e:
                steps.append(ScenarioDispatchStep(
                    kind="circuit_current_limit", status="skipped",
                    detail=f"{label}: {e.detail}", params=cparams,
                ))
    # 3b. normal restores each non-essential circuit's max input current to its
    #     breaker rating (or the panel master breaker when unrated), undoing a
    #     prior load_management_capacity shed. Same guarded per-circuit dispatch.
    elif req.operation_scenario == "normal":
        circuits = await db.fetch(
            """SELECT pc.circuit_id, pc.channel_num, pc.circuit_name, pc.rated_amps
               FROM panel_circuits pc
               JOIN devices d ON d.device_id = pc.device_id
               WHERE d.home_id = $1
                 AND pc.circuit_priority = 'non_essential'
                 AND pc.is_controllable = TRUE AND pc.is_critical = FALSE
               ORDER BY pc.channel_num""",
            req.home_id,
        )
        if not circuits:
            steps.append(ScenarioDispatchStep(
                kind="circuit_current_limit", status="skipped",
                detail="No non-essential controllable circuits to restore for this home.",
            ))
        for c in circuits:
            restore_a = _circuit_restore_amp(c["rated_amps"])
            cparams = {"max_input_a": restore_a}
            label = c["circuit_name"] or f"Circuit {c['channel_num']}"
            try:
                resp = await control.dispatch(
                    DispatchRequest(
                        home_id=req.home_id,
                        action_type="release",
                        target=DispatchTarget(kind="circuit", circuit_id=c["circuit_id"]),
                        params=cparams,
                    ),
                    user,
                )
                steps.append(ScenarioDispatchStep(
                    kind="circuit_current_limit", action_id=resp.action_id,
                    status=resp.status, params=cparams,
                    detail=f"{label}: restore max input current to {restore_a}A.",
                ))
            except HTTPException as e:
                steps.append(ScenarioDispatchStep(
                    kind="circuit_current_limit", status="skipped",
                    detail=f"{label}: {e.detail}", params=cparams,
                ))
    elif req.operation_scenario == "capacity_management":
        steps.append(ScenarioDispatchStep(
            kind="grid_disconnect", status="external",
            detail="Grid disconnect is performed by the external capacity switch "
                   "(out of scope); the panel auto-islands and runs EPS-on.",
        ))

    return ScenarioDispatchResult(
        home_id=req.home_id,
        operation_scenario=req.operation_scenario,
        dr_event_active=dr_active,
        steps=steps,
    )
