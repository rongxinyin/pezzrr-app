#!/usr/bin/env python3
"""
Publish the next N days (default 7) of SCP-EMTOU prices to the VTN as 24 hourly
intervals per day, so the VEN can collect a full 24-hour price profile for each
day. Reuses the existing SCP-EMTOU program and is idempotent: it first deletes
any prior SCP-7day-* events before republishing.

Two events are posted because OpenADR carries priority at the event level and
the VEN derives period_type from it (priority 1 = peak, else off_peak):
  - <prefix>-Peak     priority 1: hourly intervals for the peak hours (16:00-21:00)
  - <prefix>-OffPeak  priority 2: hourly intervals for the other 19 hours
"""

import json
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, timedelta

VTN = "http://localhost:3001"

SUMMER_PEAK = 0.46507
SUMMER_OFFPEAK = 0.35838
WINTER_PEAK = 0.39061
WINTER_OFFPEAK = 0.36303

PEAK_HOURS = set(range(16, 21))  # 16,17,18,19,20 -> 16:00-21:00


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


def get(path, token):
    req = urllib.request.Request(f"{VTN}{path}",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def post(path, payload, token):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{VTN}{path}", data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def delete(path, token):
    req = urllib.request.Request(f"{VTN}{path}", method="DELETE",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return r.status


def tz(d: date) -> str:
    """UTC offset for a California date (PDT or PST)."""
    dst_ranges = [
        (date(2026, 3, 8), date(2026, 11, 1)),
        (date(2027, 3, 14), date(2027, 11, 7)),
    ]
    for start, end in dst_ranges:
        if start <= d < end:
            return "-07:00"
    return "-08:00"


def is_summer(d: date) -> bool:
    return 6 <= d.month <= 9


def rates(d: date):
    return (SUMMER_PEAK, SUMMER_OFFPEAK) if is_summer(d) else (WINTER_PEAK, WINTER_OFFPEAK)


def resolve_program_id(token: str, name: str) -> str:
    for p in get("/programs", token):
        if p.get("programName") == name:
            return p["id"]
    raise SystemExit(f"Program {name!r} not found on VTN; run publish_tou.py first.")


def clean_prior(token: str):
    for e in get("/events", token):
        if (e.get("eventName") or "").startswith("SCP-7day-"):
            try:
                delete(f"/events/{e['id']}", token)
                print(f"  deleted prior event {e['eventName']}")
            except urllib.error.HTTPError as exc:
                print(f"  warn: could not delete {e['eventName']}: {exc}")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    start = date.today()

    token = get_token()
    program_id = resolve_program_id(token, "SCP-EMTOU")
    print(f"Program SCP-EMTOU id={program_id}")
    clean_prior(token)
    print(f"Publishing {days} days starting {start.isoformat()} as hourly intervals")

    peak_intervals = []
    off_intervals = []
    i = 0
    for n in range(days):
        d = start + timedelta(days=n)
        off = tz(d)
        peak_price, off_price = rates(d)
        for hour in range(24):
            interval = {
                "id": i,
                "intervalPeriod": {
                    "start": f"{d.isoformat()}T{hour:02d}:00:00{off}",
                    "duration": "PT1H",
                },
                "payloads": [{"type": "PRICE",
                              "values": [peak_price if hour in PEAK_HOURS else off_price]}],
            }
            (peak_intervals if hour in PEAK_HOURS else off_intervals).append(interval)
            i += 1

    descriptors = [{"objectType": "EVENT_PAYLOAD_DESCRIPTOR",
                    "payloadType": "PRICE", "units": "KWH", "currency": "USD"}]
    prefix = f"SCP-7day-{start.isoformat()}"

    events = [
        {"programID": program_id, "eventName": f"{prefix}-Peak", "priority": 1,
         "payloadDescriptors": descriptors, "intervals": peak_intervals},
        {"programID": program_id, "eventName": f"{prefix}-OffPeak", "priority": 2,
         "payloadDescriptors": descriptors, "intervals": off_intervals},
    ]
    for ev in events:
        res = post("/events", ev, token)
        print(f"  Event: {res['eventName']:<26} prio={res['priority']} intervals={len(ev['intervals'])}")

    print(f"Done. {days} days x 24 hourly intervals published.")


if __name__ == "__main__":
    main()
