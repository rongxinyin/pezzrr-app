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
