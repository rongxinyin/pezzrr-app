"""
One-time historical backfill via EcoFlow user portal API.

Uses the internal portal endpoint (Bearer JWT auth) which stores 1-minute
resolution data for offline deploy devices — unlike the developer API which
only serves online devices.

Endpoint : POST https://api-a.ecoflow.com/iot-service/single/line/index
Auth     : Authorization: Bearer <JWT>  (expires ~2026-03-22)
Token exp: decoded from JWT — script warns when < 7 days remaining.

Portal codes probed per device (first day):
  PD303_Dashboard_Grid_Day      → grid_power_w    (confirmed working)
  PD303_Dashboard_HomeLoad_Day  → home_load_w     (probe)
  PD303_Dashboard_Load_Day      → home_load_w     (probe alt name)
  PD303_Dashboard_PV_Day        → solar_power_w   (probe)
  PD303_Dashboard_Battery_Day   → battery_power_w (probe)
  PD303_Dashboard_BatterySoc_Day→ battery_soc_pct (probe)

Timestamps from portal are in device local time (America/Los_Angeles).
They are converted to UTC before insertion.

Usage:
    python -m data_collectors.portal_backfill --token <JWT>
    python -m data_collectors.portal_backfill --token <JWT> --start 2026-01-24 --end 2026-02-22
"""

import argparse
import base64
import json
import logging
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .config import iter_ecoflow_devices
from .db import DatabaseManager

log = logging.getLogger(__name__)

PORTAL_URL = "https://api-a.ecoflow.com/iot-service/single/line/index"
DEVICE_TZ  = ZoneInfo("America/Los_Angeles")

# Ordered candidate codes → DB column.  First match per column wins.
CODE_MAP = [
    ("PD303_Dashboard_Grid_Day",       "grid_power_w"),
    ("PD303_Dashboard_HomeLoad_Day",   "home_load_w"),
    ("PD303_Dashboard_Load_Day",       "home_load_w"),
    ("PD303_Dashboard_PV_Day",         "solar_power_w"),
    ("PD303_Dashboard_Battery_Day",    "battery_power_w"),
    ("PD303_Dashboard_BatterySoc_Day", "battery_soc_pct"),
]


# ---------------------------------------------------------------------------
# JWT expiry check
# ---------------------------------------------------------------------------
def _warn_token_expiry(token: str):
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        exp = payload.get("exp", 0)
        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
        days_left = (exp_dt - datetime.now(timezone.utc)).days
        if days_left < 0:
            log.error("Bearer token EXPIRED on %s", exp_dt.date())
        elif days_left < 7:
            log.warning("Bearer token expires in %d days (%s) — refresh soon", days_left, exp_dt.date())
        else:
            log.info("Bearer token valid until %s (%d days)", exp_dt.date(), days_left)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Portal fetch
# ---------------------------------------------------------------------------
def _fetch_day(sn: str, code: str, day_str: str, token: str):
    """
    Fetch one day of 1-minute data for one code.
    Returns list of (datetime_utc, float) or None on API error.
    day_str: "YYYY-MM-DD"
    """
    payload = {
        "sn": sn,
        "code": code,
        "params": {
            "beginTime": f"{day_str} 00:00:00",
            "endTime":   f"{day_str} 23:59:59",
        },
    }
    try:
        r = requests.post(
            PORTAL_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
                "accept":        "application/json, text/plain, */*",
            },
            timeout=20,
        )
        body = r.json()
        if str(body.get("code", "")) != "0":
            return None  # code unsupported or auth error

        data_list = body.get("data") or []
        if not data_list or not data_list[0].get("points"):
            return []

        results = []
        for pt in data_list[0]["points"]:
            ts_str = pt.get("xdata")
            val    = pt.get("ydata")
            if ts_str is None or val is None:
                continue
            # Portal timestamps are device local time → convert to UTC
            local_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=DEVICE_TZ
            )
            utc_dt = local_dt.astimezone(timezone.utc)
            results.append((utc_dt, float(val)))
        return results

    except Exception:
        log.debug("fetch error %s/%s/%s:\n%s", sn, code, day_str, traceback.format_exc())
        return None


# ---------------------------------------------------------------------------
# Duplicate guard
# ---------------------------------------------------------------------------
def _day_has_data(db: DatabaseManager, device_id: int, day_str: str) -> bool:
    cur = db._cursor()
    cur.execute(
        """
        SELECT 1 FROM smart_panel_readings
        WHERE device_id = %s
          AND ts >= %s::date
          AND ts <  %s::date + INTERVAL '1 day'
        LIMIT 1
        """,
        (device_id, day_str, day_str),
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------
def run_portal_backfill(token: str, start_date: date, end_date: date):
    _warn_token_expiry(token)

    db = DatabaseManager()
    db.connect()

    # Load deploy devices
    devices = []
    for dev_cfg in iter_ecoflow_devices():
        if dev_cfg["account_name"] != "deploy":
            continue
        sn        = dev_cfg["device_sn"]
        home_name = dev_cfg["home_name"]
        home_id   = db.get_home_id(home_name)
        panel_id  = db.get_device_id(sn)
        if not home_id or not panel_id:
            log.warning("No seed data for %s/%s — skipping", home_name, sn)
            continue
        devices.append({"sn": sn, "home_id": home_id, "panel_id": panel_id,
                        "label": f"{home_name}/{sn}"})

    if not devices:
        log.error("No deploy devices found in DB. Run seed first.")
        return

    # Probe which codes are available (first device, first day)
    probe_sn  = devices[0]["sn"]
    probe_day = start_date.strftime("%Y-%m-%d")
    log.info("Probing available portal codes with %s on %s …", probe_sn, probe_day)

    working: list[tuple[str, str]] = []  # (code, field)
    seen_fields: set[str] = set()
    for code, field in CODE_MAP:
        if field in seen_fields:
            continue
        result = _fetch_day(probe_sn, code, probe_day, token)
        if result is not None and len(result) > 0:
            working.append((code, field))
            seen_fields.add(field)
            log.info("  %-45s → %-20s  (%d pts)", code, field, len(result))
        else:
            status = "no data" if result is not None else "error/unsupported"
            log.info("  %-45s → %s", code, status)
        time.sleep(0.4)

    if not working:
        log.error("No working portal codes found — check token validity.")
        return

    log.info("")
    log.info("Fields available : %s", [f for _, f in working])
    log.info("Devices          : %d", len(devices))
    log.info("Date range       : %s → %s", start_date, end_date)
    log.info("")

    total_rows = 0
    total_days = 0
    current = start_date

    while current <= end_date:
        day_str = current.strftime("%Y-%m-%d")

        for dev in devices:
            if _day_has_data(db, dev["panel_id"], day_str):
                log.debug("[%s] %s — skipped (data exists)", dev["label"], day_str)
                continue

            # Fetch all working codes, merge by timestamp
            minute_data: dict[datetime, dict] = {}
            for code, field in working:
                points = _fetch_day(dev["sn"], code, day_str, token)
                if not points:
                    continue
                for ts, val in points:
                    minute_data.setdefault(ts, {})[field] = val
                time.sleep(0.3)

            if not minute_data:
                log.warning("[%s] %s — no data", dev["label"], day_str)
                continue

            # Bulk insert
            inserted = 0
            for ts in sorted(minute_data):
                fields = minute_data[ts]
                row = {
                    "device_id":        dev["panel_id"],
                    "home_id":          dev["home_id"],
                    "ts":               ts,
                    "grid_power_w":     fields.get("grid_power_w"),
                    "grid_frequency_hz": None,
                    "solar_power_w":    fields.get("solar_power_w"),
                    "battery_power_w":  fields.get("battery_power_w"),
                    "battery_soc_pct":  fields.get("battery_soc_pct"),
                    "home_load_w":      fields.get("home_load_w"),
                    "grid_status":      None,
                    "eps_mode_active":  None,
                }
                try:
                    db.insert_smart_panel_reading(row)
                    inserted += 1
                except Exception:
                    log.debug("insert error: %s", traceback.format_exc())

            total_rows += inserted
            total_days += 1
            log.info("[%s] %s — %d rows", dev["label"], day_str, inserted)

        current += timedelta(days=1)

    db.close()
    log.info("")
    log.info("=" * 60)
    log.info("Portal backfill complete.")
    log.info("  Days processed : %d", total_days)
    log.info("  Rows inserted  : %d", total_rows)
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--token", required=True,
        help="Bearer JWT from user-portal.ecoflow.com (Authorization header)",
    )
    p.add_argument(
        "--start", default="2026-01-24",
        help="First date to backfill YYYY-MM-DD (default: 2026-01-24)",
    )
    p.add_argument(
        "--end",
        default=(date.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Last date to backfill YYYY-MM-DD (default: yesterday)",
    )
    args = p.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    if start > end:
        p.error("--start must be <= --end")

    run_portal_backfill(token=args.token, start_date=start, end_date=end)


if __name__ == "__main__":
    main()
