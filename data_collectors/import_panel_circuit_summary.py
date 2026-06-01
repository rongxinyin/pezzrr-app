"""
Import circuit metadata from config/ecoflow_panel_circult_summary.csv into
panel_circuits (the authoritative source for ILC load-shed priorities).

The CSV is the hand-maintained panel map for every EcoFlow SHP2 in the fleet.
Its `circuit_id` column is the panel CHANNEL (1-12), not the DB circuit_id PK,
so rows are matched on (device_id, channel_num). For each circuit it sets:

  * circuit_priority  <- circuit_priorities  (Critical/Essential/Non-Essential)
  * is_critical       <- TRUE only for the critical tier
  * is_controllable   <- FALSE for critical (never shed), TRUE otherwise
  * load_description  <- "<label> | <breaker> | <notes>"  (traceability;
                         common-trip / tandem ties matter to the ILC)
  * rated_amps        <- leading integer of breaker_capacity when unambiguous

Rows with a blank circuit_priorities cell (e.g. panel 3110C) are left at the
schema default and reported, so the gaps are visible rather than silently
forced to non_essential.

    venv/bin/python3 -m data_collectors.import_panel_circuit_summary [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import psycopg2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
CSV_PATH = os.path.join(CONFIG_DIR, "ecoflow_panel_circult_summary.csv")

PRIORITY_MAP = {
    "critical": "critical",
    "essential": "essential",
    "non-essential": "non_essential",
    "non essential": "non_essential",
    "nonessential": "non_essential",
}


def _dsn():
    import json
    with open(os.path.join(CONFIG_DIR, "data_analytics_config.json")) as fh:
        cfg = json.load(fh)["database"]
    return (f"host={cfg['host']} port={cfg['port']} dbname={cfg['database_name']} "
            f"user={cfg['username']} password={cfg['password']}")


def _rated_amps(breaker):
    """Leading integer of a breaker spec ('20A', '15A/30A', '-') or None.
    For tandem/handle-tie pairs ('15A/30A') we take the first (branch) rating."""
    m = re.match(r"\s*(\d+)\s*A", breaker or "")
    return int(m.group(1)) if m else None


def _circuit_name(row, channel):
    """Dashboard-facing name 'Circuit N - <label>'; None when the label is
    empty/blank so the existing circuit_name is kept (COALESCE in the UPDATE)."""
    label = (row.get("circuit_label") or "").strip()
    if not label or label == "(Empty)":
        return None
    return f"Circuit {channel} - {label}"


def _description(row):
    parts = []
    label = (row.get("circuit_label") or "").strip()
    if label and label != "(Empty)":
        parts.append(label)
    bc = (row.get("breaker_capacity") or "").strip()
    if bc and bc != "-":
        parts.append(bc)
    note = (row.get("notes") or "").strip()
    if note:
        parts.append(note)
    return " | ".join(parts) or None


def main():
    ap = argparse.ArgumentParser(description="Import panel circuit priorities from the summary CSV.")
    ap.add_argument("--dry-run", action="store_true", help="Print changes, do not write.")
    ap.add_argument("--csv", default=CSV_PATH)
    args = ap.parse_args()

    with open(args.csv, newline="") as fh:
        rows = list(csv.DictReader(fh))

    conn = psycopg2.connect(_dsn())
    updated = skipped = blanks = 0
    try:
        with conn.cursor() as cur:
            for r in rows:
                device_id = int(r["device_id"])
                channel = int(r["circuit_id"])  # CSV "circuit_id" == channel
                raw = (r.get("circuit_priorities") or "").strip().lower()
                if not raw:
                    blanks += 1
                    print(f"  [blank priority] device {device_id} ch{channel} "
                          f"({r.get('circuit_label')}) -> left at default")
                    continue
                priority = PRIORITY_MAP.get(raw)
                if priority is None:
                    print(f"  [unknown priority {raw!r}] device {device_id} ch{channel} -> skipped")
                    skipped += 1
                    continue
                is_critical = priority == "critical"
                is_controllable = not is_critical
                desc = _description(r)
                name = _circuit_name(r, channel)
                amps = _rated_amps(r.get("breaker_capacity"))
                if args.dry_run:
                    print(f"  device {device_id} ch{channel:>2} -> {priority:<13} "
                          f"crit={is_critical} ctrl={is_controllable} amps={amps} "
                          f"name={name!r} desc={desc!r}")
                    updated += 1
                    continue
                cur.execute(
                    """UPDATE panel_circuits
                       SET circuit_priority=%s, is_critical=%s, is_controllable=%s,
                           circuit_name=COALESCE(%s, circuit_name),
                           load_description=COALESCE(%s, load_description),
                           rated_amps=COALESCE(%s, rated_amps)
                       WHERE device_id=%s AND channel_num=%s""",
                    (priority, is_critical, is_controllable, name, desc, amps, device_id, channel),
                )
                if cur.rowcount == 0:
                    print(f"  [no DB row] device {device_id} ch{channel} -> not in panel_circuits")
                    skipped += 1
                else:
                    updated += 1
        if args.dry_run:
            conn.rollback()
            print(f"\nDRY RUN: {updated} would update, {skipped} skipped, {blanks} blank.")
        else:
            conn.commit()
            print(f"\nDone: {updated} updated, {skipped} skipped, {blanks} blank (left at default).")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
