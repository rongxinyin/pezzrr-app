"""
Backfill historical weather observations via the Dark Sky Time Machine API.

Usage:
    python -m data_collectors.weather_backfill --start 2026-05-01 --end 2026-05-07
    python -m data_collectors.weather_backfill --start 2026-05-01 --end 2026-05-07 \
        --location "Arcata CA"

For each location and each day in [start, end], one Time Machine request is
made (returning that day's 24 hourly observations) and rows are upserted into
weather_observations with source='timemachine'.
"""

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from .config import get_darksky_config, iter_weather_locations
from .db import DatabaseManager
from .darksky_client import DarkSkyClient
from .darksky_transformer import transform_history

log = logging.getLogger(__name__)


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _daterange(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def backfill(start_date, end_date, location_name=None, db=None):
    cfg = get_darksky_config()
    api_key = cfg.get("api_key", "")
    if not api_key or api_key == "YOUR_DARKSKY_KEY":
        raise SystemExit("Dark Sky API key not configured in config/darksky_config.json.")

    db = db or DatabaseManager()
    db.connect()
    client = DarkSkyClient(cfg)

    targets = []
    for loc in iter_weather_locations():
        if location_name and loc["location_name"] != location_name:
            continue
        home_id = db.get_home_id(loc["home_name"]) if loc.get("home_name") else None
        location_id = db.upsert_weather_location(
            location_name=loc["location_name"],
            latitude=loc["latitude"],
            longitude=loc["longitude"],
            home_id=home_id,
            timezone=loc.get("timezone", "America/Los_Angeles"),
        )
        targets.append({
            "location_id": location_id,
            "name": loc["location_name"],
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
        })

    if not targets:
        raise SystemExit(
            f"No matching location"
            + (f" '{location_name}'" if location_name else "")
            + " in config/darksky_config.json."
        )

    total = 0
    for loc in targets:
        for day in _daterange(start_date, end_date):
            # Noon UTC keeps us safely inside the target calendar day.
            unix_time = int((day + timedelta(hours=12)).timestamp())
            try:
                data = client.get_timemachine(
                    loc["latitude"], loc["longitude"], unix_time
                )
                rows = transform_history(data, loc["location_id"])
                for row in rows:
                    db.insert_weather_observation(row)
                total += len(rows)
                log.info("Backfill [%s] %s: %d hours",
                         loc["name"], day.date(), len(rows))
            except Exception as e:
                log.error("Backfill [%s] %s failed: %s",
                          loc["name"], day.date(), e)
            time.sleep(0.2)  # be gentle on the API

    log.info("Backfill complete: %d observation rows.", total)
    db.close()
    return total


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Dark Sky historical weather backfill.")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--location", help="Limit to a single location_name from config")
    args = parser.parse_args()

    backfill(_parse_date(args.start), _parse_date(args.end), args.location)


if __name__ == "__main__":
    main()
