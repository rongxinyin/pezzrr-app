"""
Bucket-vs-range guard for telemetry endpoints (docs/DASHBOARD_DESIGN.md §7).

Each bucket size maps to a max queryable range so a 1-minute request can't
scan the raw hypertable across months. Ranges wider than the cap are rejected
with 422 and a hint to use a coarser bucket.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

# Ordered coarsest-last; used to suggest the next coarser bucket on overflow.
# `interval` is a timedelta so asyncpg encodes it to a PG interval for time_bucket().
BUCKETS: dict[str, dict] = {
    "1m": {"interval": timedelta(minutes=1), "max_range": timedelta(hours=48)},
    "5m": {"interval": timedelta(minutes=5), "max_range": timedelta(days=31)},
    "1h": {"interval": timedelta(hours=1), "max_range": timedelta(days=366)},
}
DEFAULT_WINDOW = timedelta(hours=24)


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC so they compare against timestamptz."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resolve_window(
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    bucket: str,
) -> tuple[datetime, datetime, timedelta]:
    """Validate the bucket + range; return (start, end, bucket_width)."""
    if bucket not in BUCKETS:
        raise HTTPException(
            status_code=422,
            detail=f"bucket must be one of {list(BUCKETS)}",
        )
    end = _aware(date_to) if date_to else datetime.now(timezone.utc)
    start = _aware(date_from) if date_from else end - DEFAULT_WINDOW
    if start >= end:
        raise HTTPException(status_code=422, detail="`from` must be before `to`")

    max_range = BUCKETS[bucket]["max_range"]
    if end - start > max_range:
        coarser = {"1m": "5m", "5m": "1h"}.get(bucket)
        hint = f" Use bucket={coarser}." if coarser else ""
        raise HTTPException(
            status_code=422,
            detail=(
                f"Range too wide for bucket={bucket} "
                f"(max {max_range}).{hint}"
            ),
        )
    return start, end, BUCKETS[bucket]["interval"]
