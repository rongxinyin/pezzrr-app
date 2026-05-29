"""
Energy & analytics endpoint (docs/DASHBOARD_DESIGN.md §13.3).

Computes daily load trend, peak demand, self-consumption, cost and circuit
energy ranking. The spec's summary tables (daily_home_summary,
hourly_energy_summary) are populated by an external batch job that hasn't run
on this deployment, so the load/peak/circuit figures are derived on the fly
from the continuous aggregates (panel_1h, circuit_5m), which carry real data.
daily_home_summary is still read for cost where present.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import User, require
from ..db import db
from ..models import CircuitEnergy, EnergyAnalytics, EnergyDay, EnergyTotals

router = APIRouter(prefix="/api/v1", tags=["analytics"])

DEFAULT_RANGE = timedelta(days=30)
MAX_RANGE = timedelta(days=366)


def _r(v, n=2):
    return round(float(v), n) if v is not None else None


def _self_consumption(solar_kwh: Optional[float], export_kwh: Optional[float]):
    if not solar_kwh or solar_kwh <= 0:
        return None
    used = solar_kwh - (export_kwh or 0)
    return _r(max(0.0, min(100.0, used / solar_kwh * 100)), 1)


async def home_timezone(home_id: int) -> str:
    """Home's IANA timezone (used for local-day rollups); UTC fallback."""
    tz = await db.fetchval("SELECT timezone FROM homes WHERE home_id = $1", home_id)
    return tz or "UTC"


async def energy_dataset(home_id: int, start: datetime, end: datetime) -> EnergyAnalytics:
    """Shared by the analytics endpoint and the reports router.

    Days are bucketed in the home's local timezone so a "daily" figure lines up
    with the home's calendar day rather than a UTC slice.
    """
    home = await db.fetchrow(
        "SELECT home_name, timezone FROM homes WHERE home_id = $1", home_id
    )
    if home is None:
        raise HTTPException(status_code=404, detail="Home not found")
    home_name = home["home_name"]
    tz = home["timezone"] or "UTC"

    # Daily rollup from the hourly panel aggregate. Each panel_1h row is an
    # hourly avg-power bucket, so summing avg power over a day yields kWh.
    day_rows = await db.fetch(
        """
        WITH hourly AS (
            SELECT bucket,
                   avg(home_load_w)  AS home_load_w,
                   max(peak_load_w)  AS peak_load_w,
                   avg(grid_power_w) AS grid_power_w,
                   avg(solar_power_w) AS solar_power_w
            FROM panel_1h
            WHERE home_id = $1 AND bucket >= $2 AND bucket < $3
            GROUP BY bucket
        )
        SELECT (bucket AT TIME ZONE $4)::date AS day,
               sum(home_load_w) / 1000.0              AS home_load_kwh,
               sum(GREATEST(solar_power_w, 0)) / 1000.0 AS solar_gen_kwh,
               sum(GREATEST(grid_power_w, 0)) / 1000.0  AS grid_import_kwh,
               sum(GREATEST(-grid_power_w, 0)) / 1000.0 AS grid_export_kwh,
               max(peak_load_w) / 1000.0              AS peak_demand_kw
        FROM hourly
        GROUP BY day ORDER BY day
        """,
        home_id, start, end, tz,
    )

    # Timestamp of each day's peak hour (latest poll for the winning bucket).
    peak_at_rows = await db.fetch(
        """
        SELECT DISTINCT ON ((bucket AT TIME ZONE $4)::date)
               (bucket AT TIME ZONE $4)::date AS day, bucket AS peak_at
        FROM panel_1h
        WHERE home_id = $1 AND bucket >= $2 AND bucket < $3
        ORDER BY (bucket AT TIME ZONE $4)::date, peak_load_w DESC NULLS LAST
        """,
        home_id, start, end, tz,
    )
    peak_at = {r["day"]: r["peak_at"] for r in peak_at_rows}

    # Cost (and any other reported daily fields) where the batch summary exists.
    cost_rows = await db.fetch(
        """SELECT date, estimated_cost_usd
           FROM daily_home_summary
           WHERE home_id = $1 AND date >= $2 AND date < $3""",
        home_id, start.date(), end.date(),
    )
    cost_by_day = {r["date"]: r["estimated_cost_usd"] for r in cost_rows}

    days: list[EnergyDay] = []
    for r in day_rows:
        d = r["day"]
        days.append(EnergyDay(
            date=d,
            home_load_kwh=_r(r["home_load_kwh"]),
            solar_gen_kwh=_r(r["solar_gen_kwh"]),
            grid_import_kwh=_r(r["grid_import_kwh"]),
            grid_export_kwh=_r(r["grid_export_kwh"]),
            peak_demand_kw=_r(r["peak_demand_kw"]),
            peak_demand_at=peak_at.get(d),
            self_consumption_pct=_self_consumption(
                _r(r["solar_gen_kwh"]), _r(r["grid_export_kwh"])
            ),
            estimated_cost_usd=_r(cost_by_day.get(d)),
        ))

    # Circuit energy ranking over the whole range (5-min avg power -> kWh).
    circ_rows = await db.fetch(
        """SELECT cs.circuit_id, pc.channel_num, pc.circuit_name,
                  sum(cs.power_w) * (5.0 / 60.0) / 1000.0 AS energy_kwh
           FROM circuit_5m cs
           JOIN panel_circuits pc ON pc.circuit_id = cs.circuit_id
           WHERE cs.home_id = $1 AND cs.bucket >= $2 AND cs.bucket < $3
           GROUP BY cs.circuit_id, pc.channel_num, pc.circuit_name
           ORDER BY energy_kwh DESC NULLS LAST""",
        home_id, start, end,
    )
    circuits = [
        CircuitEnergy(
            circuit_id=r["circuit_id"],
            channel_num=r["channel_num"],
            circuit_name=r["circuit_name"],
            energy_kwh=_r(r["energy_kwh"]),
        )
        for r in circ_rows
    ]

    def _sum(key):
        vals = [getattr(d, key) for d in days if getattr(d, key) is not None]
        return _r(sum(vals)) if vals else None

    solar_total = _sum("solar_gen_kwh")
    export_total = _sum("grid_export_kwh")
    peaks = [d.peak_demand_kw for d in days if d.peak_demand_kw is not None]
    totals = EnergyTotals(
        home_load_kwh=_sum("home_load_kwh"),
        solar_gen_kwh=solar_total,
        grid_import_kwh=_sum("grid_import_kwh"),
        grid_export_kwh=export_total,
        peak_demand_kw=_r(max(peaks)) if peaks else None,
        self_consumption_pct=_self_consumption(solar_total, export_total),
        estimated_cost_usd=_sum("estimated_cost_usd"),
    )

    return EnergyAnalytics(
        home_id=home_id,
        home_name=home_name,
        start=start,
        end=end,
        days=days,
        circuits=circuits,
        totals=totals,
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resolve_range(date_from: Optional[datetime], date_to: Optional[datetime]):
    end = _aware(date_to) if date_to else datetime.now(timezone.utc)
    start = _aware(date_from) if date_from else end - DEFAULT_RANGE
    if start >= end:
        raise HTTPException(status_code=422, detail="`from` must be before `to`")
    if end - start > MAX_RANGE:
        raise HTTPException(status_code=422, detail=f"Range too wide (max {MAX_RANGE})")
    return start, end


@router.get("/homes/{home_id}/energy", response_model=EnergyAnalytics)
async def home_energy(
    home_id: int,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    user: User = Depends(require("viewer", home_param="home_id")),
):
    start, end = resolve_range(date_from, date_to)
    return await energy_dataset(home_id, start, end)
