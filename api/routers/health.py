"""
Device health endpoints (docs/DASHBOARD_DESIGN.md §13.6).

/devices reports each device's online state and firmware. /health/coverage
derives data coverage on the fly: the spec's hourly_energy_summary is not
populated on this deployment, so we measure the fraction of 5-minute buckets in
the requested local day that contain at least one raw reading. Every device
type polls faster than 5 minutes, so a bucket-presence metric tolerates cadence
differences (panel/battery ~30s, thermostat ~100s) while still flagging real
gaps. Fleet roles see all homes; viewer/operator are scoped to theirs.
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import ALL_HOMES_ROLES, User, get_current_user
from ..db import db
from ..models import CoverageReport, CoverageRow, DeviceHealth
from .analytics import home_timezone

router = APIRouter(prefix="/api/v1", tags=["health"])

# device_type -> raw readings table (all share device_id, home_id, ts).
_READING_TABLE = {
    "smart_panel": "smart_panel_readings",
    "battery": "battery_readings",
    "thermostat": "thermostat_readings",
    "smart_plug": "smart_plug_readings",
}
_BUCKET_SECONDS = 300  # 5-minute coverage buckets


def _scope_clause(user: User, home_id: Optional[int]):
    """Return (sql_fragment, params) restricting d.home_id to the caller's scope."""
    fleet = user.role in ALL_HOMES_ROLES
    if home_id is not None:
        if not fleet and home_id not in user.homes:
            raise HTTPException(status_code=403, detail="Home not in scope")
        return "d.home_id = $1", [home_id]
    if fleet:
        return "TRUE", []
    return "d.home_id = ANY($1::int[])", [user.homes]


@router.get("/devices", response_model=list[DeviceHealth])
async def devices(
    home_id: Optional[int] = Query(None),
    online: Optional[bool] = Query(None),
    user: User = Depends(get_current_user),
):
    clause, params = _scope_clause(user, home_id)
    sql = f"""
        SELECT d.device_id, d.home_id, h.home_name, d.device_type::text AS device_type,
               d.device_name, d.manufacturer, d.model, d.firmware_version,
               d.is_online, d.online_updated_at, d.is_active
        FROM devices d
        JOIN homes h ON h.home_id = d.home_id
        WHERE {clause}
    """
    if online is not None:
        sql += f" AND d.is_online IS {'TRUE' if online else 'NOT TRUE'}"
    sql += " ORDER BY d.home_id, d.device_id"
    rows = await db.fetch(sql, *params)
    return [
        DeviceHealth(
            device_id=r["device_id"],
            home_id=r["home_id"],
            home_name=r["home_name"],
            device_type=r["device_type"],
            device_name=r["device_name"],
            manufacturer=r["manufacturer"],
            model=r["model"],
            firmware_version=r["firmware_version"] or None,
            is_online=r["is_online"],
            online_updated_at=r["online_updated_at"],
            is_active=r["is_active"],
        )
        for r in rows
    ]


@router.get("/health/coverage", response_model=CoverageReport)
async def coverage(
    home_id: int = Query(...),
    date: Optional[str] = Query(None, description="local day YYYY-MM-DD; default today"),
    user: User = Depends(get_current_user),
):
    if user.role not in ALL_HOMES_ROLES and home_id not in user.homes:
        raise HTTPException(status_code=403, detail="Home not in scope")

    home = await db.fetchrow("SELECT home_name FROM homes WHERE home_id = $1", home_id)
    if home is None:
        raise HTTPException(status_code=404, detail="Home not found")

    tz = ZoneInfo(await home_timezone(home_id))
    try:
        day = date_cls.fromisoformat(date) if date else datetime.now(tz).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    # Don't count buckets that haven't elapsed yet (today is partial).
    now = datetime.now(tz)
    window_end = min(end, now) if now > start else start
    elapsed = max((window_end - start).total_seconds(), 0)
    expected = int(elapsed // _BUCKET_SECONDS)

    dev_rows = await db.fetch(
        """SELECT device_id, device_type::text AS device_type, device_name
           FROM devices WHERE home_id = $1 ORDER BY device_id""",
        home_id,
    )

    out: list[CoverageRow] = []
    for d in dev_rows:
        table = _READING_TABLE.get(d["device_type"])
        if table is None:
            out.append(CoverageRow(
                device_id=d["device_id"], device_type=d["device_type"],
                device_name=d["device_name"], expected_buckets=expected,
            ))
            continue
        # Distinct 5-min buckets with >=1 reading, plus raw count and last ts.
        stat = await db.fetchrow(
            f"""SELECT count(*) AS reading_count,
                       count(DISTINCT to_timestamp(floor(extract(epoch FROM ts) / {_BUCKET_SECONDS}))) AS present,
                       max(ts) AS last_ts
                FROM {table}
                WHERE device_id = $1 AND ts >= $2 AND ts < $3""",
            d["device_id"], start, window_end,
        )
        present = stat["present"] or 0
        pct = round(min(present / expected, 1.0) * 100, 1) if expected > 0 else None
        out.append(CoverageRow(
            device_id=d["device_id"],
            device_type=d["device_type"],
            device_name=d["device_name"],
            reading_count=stat["reading_count"] or 0,
            present_buckets=present,
            expected_buckets=expected,
            coverage_pct=pct,
            last_reading_at=stat["last_ts"],
        ))

    return CoverageReport(
        home_id=home_id, home_name=home["home_name"], date=day.isoformat(), devices=out
    )
