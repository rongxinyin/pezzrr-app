"""Homes & fleet endpoints (read-only)."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import User, get_current_user, require
from ..db import db
from ..models import (
    Device,
    FleetDailyRow,
    FleetStatusItem,
    HomeDetail,
    HomeSummaryItem,
    PanelSnapshot,
    StatusSnapshot,
)

router = APIRouter(prefix="/api/v1", tags=["homes"])


def _round_w(v):
    """Powers to whole watts (§7 response conventions)."""
    return round(float(v)) if v is not None else None


def _round1(v):
    """Percentages to 1 decimal (§7 response conventions)."""
    return round(float(v), 1) if v is not None else None


def _panel_snapshot(row) -> PanelSnapshot:
    return PanelSnapshot(
        ts=row["ts"],
        home_load_w=_round_w(row["home_load_w"]),
        grid_power_w=_round_w(row["grid_power_w"]),
        solar_power_w=_round_w(row["solar_power_w"]),
        battery_power_w=_round_w(row["battery_power_w"]),
        battery_soc_pct=_round1(row["battery_soc_pct"]),
        grid_status=row["grid_status"],
        eps_mode_active=row["eps_mode_active"],
    )


@router.get("/homes", response_model=list[HomeSummaryItem])
async def list_homes(user: User = Depends(get_current_user)):
    """Homes the caller can access, with a derived gateway_online flag.

    Fleet roles (fleet_analyst/admin) see all homes; viewer/operator see only
    homes in their access list.
    """
    from ..auth import ALL_HOMES_ROLES

    if user.role in ALL_HOMES_ROLES:
        rows = await db.fetch(
            """SELECT h.home_id, h.home_name, h.city, h.state, h.timezone,
                      h.enrolled_dr, h.gateway_id,
                      COALESCE(bool_or(d.is_online), FALSE) AS gateway_online
               FROM homes h
               LEFT JOIN devices d ON d.home_id = h.home_id AND d.is_active
               GROUP BY h.home_id
               ORDER BY h.home_id"""
        )
    else:
        rows = await db.fetch(
            """SELECT h.home_id, h.home_name, h.city, h.state, h.timezone,
                      h.enrolled_dr, h.gateway_id,
                      COALESCE(bool_or(d.is_online), FALSE) AS gateway_online
               FROM homes h
               LEFT JOIN devices d ON d.home_id = h.home_id AND d.is_active
               WHERE h.home_id = ANY($1::int[])
               GROUP BY h.home_id
               ORDER BY h.home_id""",
            user.homes,
        )
    return [HomeSummaryItem(**dict(r)) for r in rows]


@router.get("/homes/{home_id}", response_model=HomeDetail)
async def get_home(home_id: int, user: User = Depends(require("viewer", home_param="home_id"))):
    """Home record + its devices + the latest panel/battery status snapshot."""
    home = await db.fetchrow(
        """SELECT home_id, home_name, address, city, state, zip_code,
                  utility_id, timezone, gateway_id, enrolled_dr
           FROM homes WHERE home_id = $1""",
        home_id,
    )
    if home is None:
        raise HTTPException(status_code=404, detail="Home not found")

    device_rows = await db.fetch(
        """SELECT device_id, device_type::text AS device_type, device_name,
                  manufacturer, model, serial_number, is_online, online_updated_at
           FROM devices WHERE home_id = $1 AND is_active
           ORDER BY device_id""",
        home_id,
    )

    panel = await db.fetchrow(
        """SELECT ts, home_load_w, grid_power_w, solar_power_w, battery_power_w,
                  battery_soc_pct, grid_status, eps_mode_active
           FROM smart_panel_readings
           WHERE home_id = $1 ORDER BY ts DESC LIMIT 1""",
        home_id,
    )
    battery = await db.fetchrow(
        """SELECT soc_pct FROM battery_readings
           WHERE home_id = $1 ORDER BY ts DESC LIMIT 1""",
        home_id,
    )

    snapshot = StatusSnapshot(
        panel=_panel_snapshot(panel) if panel else None,
        battery_soc_pct=_round1(battery["soc_pct"]) if battery else None,
    )

    return HomeDetail(
        **dict(home),
        devices=[Device(**dict(r)) for r in device_rows],
        status=snapshot,
    )


_SOC_WATCH = 35.0


def _derive_status(row) -> str:
    """status ∈ {offline, act, watch, ok} per design §7."""
    if not row["gateway_online"]:
        return "offline"
    if row["eps_mode_active"] or row["grid_status"] == 0 or row["dr_active"]:
        return "act"
    soc = row["battery_soc"] if row["battery_soc"] is not None else row["panel_soc"]
    if soc is not None and float(soc) < _SOC_WATCH:
        return "watch"
    return "ok"


@router.get("/fleet/status", response_model=list[FleetStatusItem])
async def fleet_status(user: User = Depends(get_current_user)):
    """Live per-home rollup for the overview grid (design §7).

    Latest panel + battery readings, gateway online flag, and whether the
    home is in an active DR event, with a derived status. Scoped to the
    homes the caller can access.
    """
    from ..auth import ALL_HOMES_ROLES

    sql = """
        SELECT h.home_id, h.home_name, h.city, h.enrolled_dr,
               p.ts AS panel_ts, p.home_load_w, p.grid_power_w, p.solar_power_w,
               p.grid_status, p.eps_mode_active, p.battery_soc_pct AS panel_soc,
               b.soc_pct AS battery_soc,
               COALESCE(dev.gateway_online, FALSE) AS gateway_online,
               COALESCE(dr.active, FALSE) AS dr_active
        FROM homes h
        LEFT JOIN LATERAL (
            SELECT ts, home_load_w, grid_power_w, solar_power_w,
                   grid_status, eps_mode_active, battery_soc_pct
            FROM smart_panel_readings WHERE home_id = h.home_id
            ORDER BY ts DESC LIMIT 1
        ) p ON TRUE
        LEFT JOIN LATERAL (
            SELECT soc_pct FROM battery_readings WHERE home_id = h.home_id
            ORDER BY ts DESC LIMIT 1
        ) b ON TRUE
        LEFT JOIN LATERAL (
            SELECT bool_or(is_online) AS gateway_online
            FROM devices WHERE home_id = h.home_id AND is_active
        ) dev ON TRUE
        LEFT JOIN LATERAL (
            SELECT TRUE AS active FROM dr_event_participants dep
            JOIN dr_events e ON e.event_id = dep.event_id
            WHERE dep.home_id = h.home_id AND e.status = 'active'
              AND e.event_start <= NOW() AND e.event_end > NOW()
            LIMIT 1
        ) dr ON TRUE
    """
    if user.role in ALL_HOMES_ROLES:
        rows = await db.fetch(sql + " ORDER BY h.home_id")
    else:
        rows = await db.fetch(
            sql + " WHERE h.home_id = ANY($1::int[]) ORDER BY h.home_id",
            user.homes,
        )

    out = []
    for r in rows:
        soc = r["battery_soc"] if r["battery_soc"] is not None else r["panel_soc"]
        out.append(FleetStatusItem(
            home_id=r["home_id"],
            home_name=r["home_name"],
            city=r["city"],
            status=_derive_status(r),
            gateway_online=r["gateway_online"],
            enrolled_dr=r["enrolled_dr"],
            dr_active=r["dr_active"],
            home_load_w=_round_w(r["home_load_w"]),
            grid_power_w=_round_w(r["grid_power_w"]),
            solar_power_w=_round_w(r["solar_power_w"]),
            battery_soc_pct=_round1(soc),
            panel_ts=r["panel_ts"],
        ))
    return out


@router.get("/fleet/summary", response_model=list[FleetDailyRow])
async def fleet_summary(
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    user: User = Depends(require("operator")),
):
    """Rows from the fleet_daily_summary materialized view, optional date range."""
    clauses = []
    args: list = []
    if date_from is not None:
        args.append(date_from)
        clauses.append(f"date >= ${len(args)}")
    if date_to is not None:
        args.append(date_to)
        clauses.append(f"date <= ${len(args)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await db.fetch(
        f"""SELECT date, homes_reporting, total_grid_import_kwh, total_grid_export_kwh,
                   total_solar_gen_kwh, total_home_load_kwh, avg_peak_demand_kw,
                   max_peak_demand_kw, total_dr_reduction_kwh, avg_dr_performance,
                   total_dr_events, total_estimated_cost_usd, total_estimated_savings_usd,
                   avg_self_consumption_pct, avg_battery_soc_eod
            FROM fleet_daily_summary {where} ORDER BY date""",
        *args,
    )
    return [FleetDailyRow(**dict(r)) for r in rows]
