#!/usr/bin/env python3
"""
Publish PG&E / Sonoma Clean Power EM-TOU rate program and price signals to the VTN.

Rates from SCP_EMTOU_rates.csv:
  Summer (Jun 1 – Sep 30): Peak $0.46507/kWh, Off-Peak $0.35838/kWh
  Winter (Oct 1 – May 31): Peak $0.39061/kWh, Off-Peak $0.36303/kWh
  Peak window: 4 PM – 9 PM every day including weekends & holidays
"""

import json, sys
from datetime import date, timedelta
import urllib.request
import urllib.parse

VTN = "http://localhost:3001"

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_token():
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": "bl-client",
        "client_secret": "bl-client",
    }).encode()
    req = urllib.request.Request(f"{VTN}/auth/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)["access_token"]

def post(path, payload, token):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{VTN}{path}", data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as r:
        return json.load(r)

# ── Interval helpers ──────────────────────────────────────────────────────────

def tz(d: date) -> str:
    """Return UTC offset string for a California date (PDT or PST)."""
    # DST 2026: Mar 8 → Nov 1.  DST 2027: Mar 14 → Nov 7.
    dst_ranges = [
        (date(2026, 3, 8), date(2026, 11, 1)),
        (date(2027, 3, 14), date(2027, 11, 7)),
    ]
    for start, end in dst_ranges:
        if start <= d < end:
            return "-07:00"  # PDT
    return "-08:00"          # PST

def off_peak_interval(start: date, end_inclusive: date, price: float, idx: int) -> dict:
    """Single interval spanning the entire off-peak season (background price)."""
    days = (end_inclusive - start).days + 1
    offset = tz(start)
    return {
        "id": idx,
        "intervalPeriod": {
            "start": f"{start.isoformat()}T00:00:00{offset}",
            "duration": f"P{days}D",
        },
        "payloads": [{"type": "PRICE", "values": [price]}],
    }

def peak_intervals(start: date, end_inclusive: date, price: float, id_offset: int) -> list:
    """One 5-hour interval per day (16:00–21:00) for the peak window."""
    intervals = []
    current = start
    i = id_offset
    while current <= end_inclusive:
        offset = tz(current)
        intervals.append({
            "id": i,
            "intervalPeriod": {
                "start": f"{current.isoformat()}T16:00:00{offset}",
                "duration": "PT5H",
            },
            "payloads": [{"type": "PRICE", "values": [price]}],
        })
        current += timedelta(days=1)
        i += 1
    return intervals

# ── Rates ─────────────────────────────────────────────────────────────────────

SUMMER_PEAK     = 0.46507
SUMMER_OFFPEAK  = 0.35838
WINTER_PEAK     = 0.39061
WINTER_OFFPEAK  = 0.36303

# Season boundaries published on VTN
# Current: Winter, May 12 → May 31, 2026
# Summer:  Jun  1 → Sep 30, 2026
# Winter:  Oct  1, 2026 → May 31, 2027

TODAY          = date(2026, 5, 12)
WINTER_END     = date(2026, 5, 31)
SUMMER_START   = date(2026, 6, 1)
SUMMER_END     = date(2026, 9, 30)
NEXT_WIN_START = date(2026, 10, 1)
NEXT_WIN_END   = date(2027, 5, 31)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    token = get_token()
    print("Token acquired.")

    # 1. Program
    program = {
        "programName": "SCP-EMTOU",
        "programLongName": "Sonoma Clean Power EM-TOU",
        "country": "US",
        "principalSubdivision": "CA",
        "bindingEvents": True,
        "localPrice": True,
        "payloadDescriptors": [
            {
                "objectType": "EVENT_PAYLOAD_DESCRIPTOR",
                "payloadType": "PRICE",
                "units": "KWH",
                "currency": "USD",
            }
        ],
    }
    p = post("/programs", program, token)
    program_id = p["id"]
    print(f"Program: {p['programName']}  id={program_id}")

    def make_event(name, priority, intervals):
        return {
            "programID": program_id,
            "eventName": name,
            "priority": priority,
            "payloadDescriptors": [
                {"objectType": "EVENT_PAYLOAD_DESCRIPTOR", "payloadType": "PRICE", "units": "KWH", "currency": "USD"}
            ],
            "intervals": intervals,
        }

    events = [
        # ── Current Winter (remaining) ─────────────────────────────────────
        make_event(
            "Winter-2026-OffPeak",
            priority=2,
            intervals=[off_peak_interval(TODAY, WINTER_END, WINTER_OFFPEAK, 0)],
        ),
        make_event(
            "Winter-2026-Peak",
            priority=1,
            intervals=peak_intervals(TODAY, WINTER_END, WINTER_PEAK, 0),
        ),
        # ── Summer 2026 ───────────────────────────────────────────────────
        make_event(
            "Summer-2026-OffPeak",
            priority=2,
            intervals=[off_peak_interval(SUMMER_START, SUMMER_END, SUMMER_OFFPEAK, 0)],
        ),
        make_event(
            "Summer-2026-Peak",
            priority=1,
            intervals=peak_intervals(SUMMER_START, SUMMER_END, SUMMER_PEAK, 0),
        ),
        # ── Winter 2026-27 ────────────────────────────────────────────────
        make_event(
            "Winter-2026-2027-OffPeak",
            priority=2,
            intervals=[off_peak_interval(NEXT_WIN_START, NEXT_WIN_END, WINTER_OFFPEAK, 0)],
        ),
        make_event(
            "Winter-2026-2027-Peak",
            priority=1,
            intervals=peak_intervals(NEXT_WIN_START, NEXT_WIN_END, WINTER_PEAK, 0),
        ),
    ]

    for ev in events:
        result = post("/events", ev, token)
        n = len(ev["intervals"])
        print(f"  Event: {result['eventName']:<30}  priority={result['priority']}  intervals={n}")

    print("\nDone. TOU rate program and signals published.")
    print(f"\nSummary")
    print(f"  Program ID : {program_id}")
    print(f"  Utility    : PG&E (delivery) + Sonoma Clean Power (CCA)")
    print(f"  Rate       : EM-TOU  (Electric Managed Time of Use)")
    print(f"  Peak window: 4 PM – 9 PM every day (incl. weekends & holidays)")
    print(f"  Seasons covered:")
    print(f"    Winter now   {TODAY} – {WINTER_END}   Peak ${WINTER_PEAK}/kWh  Off-Peak ${WINTER_OFFPEAK}/kWh")
    print(f"    Summer 2026  {SUMMER_START} – {SUMMER_END}  Peak ${SUMMER_PEAK}/kWh  Off-Peak ${SUMMER_OFFPEAK}/kWh")
    print(f"    Winter 26-27 {NEXT_WIN_START} – {NEXT_WIN_END}  Peak ${WINTER_PEAK}/kWh  Off-Peak ${WINTER_OFFPEAK}/kWh")

if __name__ == "__main__":
    main()
