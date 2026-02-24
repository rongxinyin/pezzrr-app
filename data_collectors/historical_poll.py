"""
One-time historical data backfill for deploy account devices.

EcoFlow API situation
---------------------
The /iot-open/sign/device/quota/data (POST) endpoint exists but does not
return historical time-series data for SHP2 devices:
  - Deploy devices (all 6): online=0 on EcoFlow cloud → "device offline" error
  - Lab device: online=1 but returns server error (SHP2 not supported)
EcoFlow's developer API only stores/exposes history for PowerOcean devices.

Fallback: rapid-burst polling
------------------------------
Since past data is unavailable via API, this script does a configurable
burst of rapid polls (default: 60 polls × 30 s = 30 min) using the
working /quota/all endpoint for every deploy device. All readings are
inserted into the database exactly like the continuous collector does.

Usage:
    python -m data_collectors.historical_poll
    python -m data_collectors.historical_poll --polls 120 --interval 30
"""

import argparse
import logging
import time
import traceback

from .config import iter_ecoflow_devices
from .db import DatabaseManager
from .ecoflow_client import EcoFlowClient
from .ecoflow_transformer import (
    transform_panel_reading,
    transform_circuit_readings,
    transform_battery_reading,
)

log = logging.getLogger(__name__)

HISTORY_ENDPOINT = "/iot-open/sign/device/quota/data"


def _try_history_api(client: EcoFlowClient, sn: str, begin_ms: int, end_ms: int) -> str:
    """Attempt the /quota/data history endpoint. Returns status string."""
    import hashlib, hmac, random, requests

    payload = {
        "sn": sn,
        "params": {"code": "pd303_mc", "beginTime": begin_ms, "endTime": end_ms},
    }

    def flatten(d, prefix=""):
        out = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
        return out

    ts = str(int(time.time() * 1000))
    nonce = str(random.randint(100000, 999999))
    hd = {"accessKey": client.access_key, "nonce": nonce, "timestamp": ts}
    flat = flatten(payload)
    body_str = "&".join(f"{k}={v}" for k, v in sorted(flat.items()))
    hdr_str = "&".join(f"{k}={v}" for k, v in sorted(hd.items()))
    sign_str = body_str + "&" + hdr_str
    sig = hmac.new(client.secret_key.encode(), sign_str.encode(), hashlib.sha256)
    sig = "".join(format(b, "02x") for b in sig.digest())

    hdrs = {
        "Content-Type": "application/json",
        "accessKey": client.access_key,
        "timestamp": ts,
        "nonce": nonce,
        "sign": sig,
    }
    try:
        r = requests.post(
            f"{client.api_base_url}{HISTORY_ENDPOINT}",
            headers=hdrs,
            json=payload,
            timeout=10,
        )
        body = r.json()
        code = str(body.get("code", ""))
        msg = body.get("message", "")
        if code == "0":
            data = body.get("data", {})
            return f"OK — {len(data)} records returned" if isinstance(data, list) else f"OK — {data}"
        elif code == "8516":
            return "SKIP — device offline on EcoFlow cloud (online=0); no history stored"
        elif code == "1000":
            return "SKIP — server error (SHP2 history not supported by this API)"
        else:
            return f"SKIP — code={code} {msg}"
    except Exception as e:
        return f"SKIP — exception: {e}"


def run_backfill(polls: int = 60, interval: int = 30):
    """
    Try history API for each deploy device, then do a rapid-burst poll.

    Args:
        polls:    Number of rapid-poll rounds to run as fallback.
        interval: Seconds between rapid-poll rounds.
    """
    db = DatabaseManager()
    db.connect()

    now_ms = int(time.time() * 1000)
    begin_ms = now_ms - 30 * 86400 * 1000  # 30 days ago

    # Collect all deploy devices
    devices = []
    for dev_cfg in iter_ecoflow_devices():
        if dev_cfg["account_name"] != "deploy":
            continue
        home_name = dev_cfg["home_name"]
        sn = dev_cfg["device_sn"]

        home_id = db.get_home_id(home_name)
        panel_id = db.get_device_id(sn)
        bat_id = db.get_device_id(sn + "-BAT") or panel_id
        circuit_map = db.get_circuit_map(panel_id) if panel_id else {}

        if not home_id or not panel_id:
            log.warning("No seed data for %s/%s — run 'seed' first", home_name, sn)
            continue

        client = EcoFlowClient(config=dev_cfg)
        devices.append({
            "client": client,
            "home_id": home_id,
            "panel_id": panel_id,
            "bat_id": bat_id,
            "circuit_map": circuit_map,
            "label": f"{home_name}/{sn}",
            "sn": sn,
        })

    if not devices:
        log.error("No deploy devices found. Run 'seed' first.")
        return

    log.info("=" * 60)
    log.info("Step 1: Attempt EcoFlow history API (last 30 days)")
    log.info("=" * 60)
    for d in devices:
        status = _try_history_api(d["client"], d["sn"], begin_ms, now_ms)
        log.info("[%s]  %s", d["label"], status)

    log.info("")
    log.info("=" * 60)
    log.info(
        "Step 2: Rapid-burst fallback — %d polls × %ds = ~%.0f min of data",
        polls, interval, polls * interval / 60,
    )
    log.info("=" * 60)

    inserted = {d["label"]: 0 for d in devices}

    for i in range(1, polls + 1):
        log.info("--- Poll %d/%d ---", i, polls)
        for d in devices:
            try:
                data = d["client"].get_device_quota()
                if data is None:
                    log.warning("[%s] no data returned", d["label"])
                    continue

                panel_row = transform_panel_reading(data, d["panel_id"], d["home_id"])
                db.insert_smart_panel_reading(panel_row)

                circuit_rows = transform_circuit_readings(
                    data, d["panel_id"], d["home_id"], d["circuit_map"]
                )
                for row in circuit_rows:
                    db.insert_panel_circuit_reading(row)

                bat_row = transform_battery_reading(data, d["bat_id"], d["home_id"])
                db.insert_battery_reading(bat_row)

                inserted[d["label"]] += 1
                log.info(
                    "[%s] panel=%.0fW  load=%.0fW  circuits=%d",
                    d["label"],
                    panel_row.get("grid_power_w") or 0,
                    panel_row.get("home_load_w") or 0,
                    len(circuit_rows),
                )
            except Exception:
                log.error("[%s] poll error:\n%s", d["label"], traceback.format_exc())

        if i < polls:
            time.sleep(interval)

    db.close()

    log.info("")
    log.info("=" * 60)
    log.info("Backfill complete. Readings inserted per device:")
    for label, count in inserted.items():
        log.info("  %-35s  %d rows", label, count)
    log.info("=" * 60)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--polls", type=int, default=60, help="Number of rapid-poll rounds (default: 60)")
    p.add_argument("--interval", type=int, default=30, help="Seconds between polls (default: 30)")
    args = p.parse_args()
    run_backfill(polls=args.polls, interval=args.interval)


if __name__ == "__main__":
    main()
