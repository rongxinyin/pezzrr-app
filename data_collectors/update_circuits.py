"""
Update panel circuit channel_num from 0-indexed (0-11) to 1-indexed (1-12)
to match EcoFlow API convention, fetch latest circuit names from the API,
and regenerate ecoflow_panel_circult_summary.csv with device info.

Usage:
    venv/bin/python3 -m data_collectors.update_circuits
"""

import csv
import logging
import os
import psycopg2

from .config import iter_ecoflow_devices, CONFIG_DIR
from .db import DatabaseManager
from .ecoflow_client import EcoFlowClient

log = logging.getLogger(__name__)

CSV_PATH = os.path.join(CONFIG_DIR, "ecoflow_panel_circult_summary.csv")


def _load_existing_csv():
    """Return dict keyed by (home_name, circuit_id) -> row dict."""
    existing = {}
    if not os.path.exists(CSV_PATH):
        return existing
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["home_name"], int(row["circuit_id"]))
            existing[key] = row
    return existing


def _fix_channel_nums(db, device_id):
    """
    Shift channel_num from 0-11 to 1-12 for the given device.
    Updates in descending order to avoid UNIQUE(device_id, channel_num) conflicts.
    Skips if already 1-indexed.
    """
    cur = db._cursor()
    cur.execute(
        "SELECT channel_num FROM panel_circuits WHERE device_id = %s ORDER BY channel_num",
        (device_id,),
    )
    nums = [r[0] for r in cur.fetchall()]
    if not nums or min(nums) >= 1:
        log.info("  device_id=%d channel_nums already 1-indexed, skipping shift", device_id)
        return False  # already correct

    # Shift 11→12, 10→11, ... 0→1 (descending to avoid conflicts)
    for old in sorted(nums, reverse=True):
        new = old + 1
        cur.execute(
            "UPDATE panel_circuits SET channel_num = %s WHERE device_id = %s AND channel_num = %s",
            (new, device_id, old),
        )
    log.info("  device_id=%d shifted channel_num from 0-11 to 1-12", device_id)
    return True


def _fetch_circuit_names(dev_cfg):
    """Query EcoFlow API and return {channel_num (1-12): name} or {}."""
    client = EcoFlowClient(config=dev_cfg)
    try:
        data = client.get_device_quota()
    except Exception as e:
        log.warning("  API call failed for %s: %s", dev_cfg["device_sn"], e)
        return {}

    if not data:
        return {}

    names = {}
    for ch in range(1, 13):
        key = f"pd303_mc.loadIncreInfo.hall1IncreInfo.ch{ch}Info.chName"
        name = data.get(key, "").strip()
        if name:
            names[ch] = name
    return names


def _update_circuit_names(db, device_id, api_names):
    """Update circuit_name in DB using API-returned names (channel 1-12)."""
    if not api_names:
        return
    cur = db._cursor()
    for ch_num, name in api_names.items():
        cur.execute(
            "UPDATE panel_circuits SET circuit_name = %s WHERE device_id = %s AND channel_num = %s",
            (name, device_id, ch_num),
        )
    log.info("  Updated %d circuit names from API", len(api_names))


def _write_csv(rows):
    """Write the enriched CSV."""
    fieldnames = [
        "home_name", "serial_number", "device_id",
        "circuit_id", "circuit_name", "circuit_label",
        "breaker_capacity", "circuit_priorities", "notes",
    ]
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %d rows to %s", len(rows), CSV_PATH)


def run():
    db = DatabaseManager()
    db.connect()

    existing_csv = _load_existing_csv()

    # Collect info: device_sn -> {device_id, home_name, api_names, db_circuits}
    device_info = {}  # sn -> dict

    for dev_cfg in iter_ecoflow_devices():
        sn = dev_cfg["device_sn"]
        home_name = dev_cfg["home_name"]

        device_id = db.get_device_id(sn)
        if device_id is None:
            log.warning("No device_id found for SN=%s, skipping", sn)
            continue

        log.info("[%s] Processing %s (device_id=%d)", home_name, sn, device_id)

        # Step 1: Fix channel_num 0-11 → 1-12
        _fix_channel_nums(db, device_id)

        # Step 2: Fetch circuit names from API
        api_names = _fetch_circuit_names(dev_cfg)

        # Step 3: Update circuit names in DB
        _update_circuit_names(db, device_id, api_names)

        device_info[sn] = {
            "device_id": device_id,
            "home_name": home_name,
            "api_names": api_names,
        }

    # Step 4: Read final DB state and build CSV rows
    csv_rows = []
    for dev_cfg in iter_ecoflow_devices():
        sn = dev_cfg["device_sn"]
        info = device_info.get(sn)
        if info is None:
            continue

        device_id = info["device_id"]
        home_name = info["home_name"]

        cur = db._cursor()
        cur.execute(
            "SELECT channel_num, circuit_name FROM panel_circuits "
            "WHERE device_id = %s ORDER BY channel_num",
            (device_id,),
        )
        db_circuits = {r[0]: r[1] for r in cur.fetchall()}

        for ch_num in range(1, 13):
            existing = existing_csv.get((home_name, ch_num), {})
            db_name = db_circuits.get(ch_num, "")

            csv_rows.append({
                "home_name": home_name,
                "serial_number": sn,
                "device_id": device_id,
                "circuit_id": ch_num,
                "circuit_name": db_name,
                "circuit_label": existing.get("circuit_label", existing.get("circuit_label", "")),
                "breaker_capacity": existing.get("breaker_capacity", ""),
                "circuit_priorities": existing.get("circuit_priorities", ""),
                "notes": existing.get("notes", ""),
            })

    _write_csv(csv_rows)
    log.info("Done.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    run()
