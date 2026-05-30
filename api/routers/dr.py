"""
Demand response endpoints (docs/DASHBOARD_DESIGN.md §13.4).

dr_events / dr_event_participants are fleet-level OpenADR records; openadr_events
is the program-wide price feed (no home_id). Fleet roles see every event and
participant; viewer/operator are scoped to events their homes take part in and
see only their own homes in the participation table.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import ALL_HOMES_ROLES, User, get_current_user
from ..db import db
from ..models import DrEventRow, DrParticipantRow, OpenAdrPrice, OpenAdrPricePoint

router = APIRouter(prefix="/api/v1", tags=["dr"])


def _f(v, n=3):
    return round(float(v), n) if v is not None else None


@router.get("/dr/events", response_model=list[DrEventRow])
async def dr_events(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    fleet = user.role in ALL_HOMES_ROLES
    sql = """
        SELECT e.event_id, e.event_reference, e.ven_id, e.vtn_id,
               e.signal_name, e.signal_type, e.signal_level, e.target_load_kw,
               e.event_start, e.event_end, e.status::text AS status,
               e.priority, e.test_event,
               (e.status = 'active' AND e.event_start <= NOW() AND e.event_end > NOW()) AS active,
               COALESCE(pc.cnt, 0) AS participant_count
        FROM dr_events e
        LEFT JOIN LATERAL (
            SELECT count(*) AS cnt FROM dr_event_participants dep
            WHERE dep.event_id = e.event_id
        ) pc ON TRUE
    """
    if fleet:
        rows = await db.fetch(sql + " ORDER BY e.event_start DESC LIMIT $1", limit)
    else:
        rows = await db.fetch(
            sql + """ WHERE EXISTS (
                        SELECT 1 FROM dr_event_participants dep
                        WHERE dep.event_id = e.event_id
                          AND dep.home_id = ANY($1::int[])
                      )
                      ORDER BY e.event_start DESC LIMIT $2""",
            user.homes, limit,
        )
    return [
        DrEventRow(
            event_id=r["event_id"],
            event_reference=r["event_reference"],
            ven_id=r["ven_id"],
            vtn_id=r["vtn_id"],
            signal_name=r["signal_name"],
            signal_type=r["signal_type"],
            signal_level=_f(r["signal_level"]),
            target_load_kw=_f(r["target_load_kw"]),
            event_start=r["event_start"],
            event_end=r["event_end"],
            status=r["status"],
            priority=r["priority"],
            test_event=r["test_event"],
            active=r["active"],
            participant_count=r["participant_count"],
        )
        for r in rows
    ]


@router.get("/dr/events/{event_id}/participants", response_model=list[DrParticipantRow])
async def dr_event_participants(event_id: int, user: User = Depends(get_current_user)):
    exists = await db.fetchval("SELECT 1 FROM dr_events WHERE event_id = $1", event_id)
    if exists is None:
        raise HTTPException(status_code=404, detail="Event not found")

    fleet = user.role in ALL_HOMES_ROLES
    sql = """
        SELECT dep.id, dep.event_id, dep.home_id, h.home_name, dep.opted_in,
               dep.baseline_kw, dep.actual_reduction_kw, dep.reduction_target_kw,
               dep.settlement_kwh, dep.performance_score, dep.notes
        FROM dr_event_participants dep
        JOIN homes h ON h.home_id = dep.home_id
        WHERE dep.event_id = $1
    """
    if fleet:
        rows = await db.fetch(sql + " ORDER BY dep.home_id", event_id)
    else:
        rows = await db.fetch(
            sql + " AND dep.home_id = ANY($2::int[]) ORDER BY dep.home_id",
            event_id, user.homes,
        )
    return [
        DrParticipantRow(
            id=r["id"],
            event_id=r["event_id"],
            home_id=r["home_id"],
            home_name=r["home_name"],
            opted_in=r["opted_in"],
            baseline_kw=_f(r["baseline_kw"]),
            actual_reduction_kw=_f(r["actual_reduction_kw"]),
            reduction_target_kw=_f(r["reduction_target_kw"]),
            settlement_kwh=_f(r["settlement_kwh"], 6),
            performance_score=_f(r["performance_score"], 4),
            notes=r["notes"],
        )
        for r in rows
    ]


@router.get("/openadr/price", response_model=OpenAdrPrice)
async def openadr_price(user: User = Depends(get_current_user)):
    """Currently-active program price (the interval covering NOW)."""
    r = await db.fetchrow(
        """SELECT ts, program_name, period_type, price_per_kwh,
                  interval_start, interval_end
           FROM openadr_events
           WHERE interval_start <= NOW() AND interval_end > NOW()
           ORDER BY ts DESC LIMIT 1"""
    )
    if r is None:
        return OpenAdrPrice()
    return OpenAdrPrice(
        ts=r["ts"],
        program_name=r["program_name"],
        period_type=r["period_type"],
        price_per_kwh=_f(r["price_per_kwh"], 5),
        interval_start=r["interval_start"],
        interval_end=r["interval_end"],
    )


@router.get("/openadr/price/history", response_model=list[OpenAdrPricePoint])
async def openadr_price_history(
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    user: User = Depends(get_current_user),
):
    """Effective price curve over the window (default last 24h).

    SCP-EMTOU prices are time-of-use: a long background off-peak window with
    shorter peak windows overlapping on top. A plain interval list would render
    a single off-peak point, so we resolve the *effective* price (peak wins) at
    every interval boundary and emit a stepped series.
    """
    end = date_to or datetime.now().astimezone()
    start = date_from or (end - timedelta(hours=24))
    # All intervals overlapping the window; latest poll per interval window.
    rows = await db.fetch(
        """SELECT DISTINCT ON (interval_start, interval_end)
                  interval_start, interval_end, period_type, price_per_kwh, priority
           FROM openadr_events
           WHERE interval_start < $2 AND interval_end > $1
           ORDER BY interval_start, interval_end, ts DESC""",
        start, end,
    )
    if not rows:
        return []

    intervals = [dict(r) for r in rows]
    # Candidate step boundaries: window edges + every interval edge inside it.
    bounds = {start, end}
    for r in intervals:
        if start < r["interval_start"] < end:
            bounds.add(r["interval_start"])
        if start < r["interval_end"] < end:
            bounds.add(r["interval_end"])
    ordered = sorted(bounds)

    def _active(t: datetime):
        # Intervals covering instant t; peak (lower priority number) wins.
        covering = [r for r in intervals if r["interval_start"] <= t < r["interval_end"]]
        if not covering:
            return None
        return min(
            covering,
            key=lambda r: (0 if r["period_type"] == "peak" else 1,
                           r["priority"] if r["priority"] is not None else 99),
        )

    points: list[OpenAdrPricePoint] = []
    for i in range(len(ordered) - 1):
        seg_start, seg_end = ordered[i], ordered[i + 1]
        cur = _active(seg_start)
        if cur is None:
            continue
        points.append(OpenAdrPricePoint(
            interval_start=seg_start,
            interval_end=seg_end,
            period_type=cur["period_type"],
            price_per_kwh=_f(cur["price_per_kwh"], 5),
        ))
    return points
