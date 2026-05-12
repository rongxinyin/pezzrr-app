"""
Main data collection orchestrator.
Daemon threads poll EcoFlow, Ecobee, and OpenADR VTN at configured intervals.
Each thread gets its own DB connection (psycopg2 is not thread-safe).
"""

import logging
import signal
import threading
import time
import traceback

from .config import iter_ecoflow_devices, iter_ecobee_accounts, get_openadr_config
from .db import DatabaseManager
from .ecoflow_client import EcoFlowClient
from .ecoflow_transformer import (
    transform_panel_reading,
    transform_circuit_readings,
    transform_battery_reading,
)
from .ecobee_client import EcobeeClient
from .ecobee_transformer import transform_thermostat_reading, dedup_key
from .openadr_client import OpenADRClient

log = logging.getLogger(__name__)

POLL_INTERVAL = 60          # seconds
STATUS_CHECK_INTERVAL = 10  # poll cycles (~10 minutes)


class DataCollector:
    def __init__(self):
        self._stop_event = threading.Event()
        self._last_ecobee_keys = {}  # keyed by device_id

    def start(self):
        """Start both polling threads and block until SIGINT."""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        t_eco = threading.Thread(
            target=self._ecoflow_loop, name="ecoflow-poll", daemon=True
        )
        t_bee = threading.Thread(
            target=self._ecobee_loop, name="ecobee-poll", daemon=True
        )
        t_adr = threading.Thread(
            target=self._openadr_loop, name="openadr-poll", daemon=True
        )

        log.info("Starting data collection (Ctrl-C to stop) ...")
        t_eco.start()
        t_bee.start()
        t_adr.start()

        # Block main thread until stop signal
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1)

        log.info("Shutting down.")

    def _handle_signal(self, signum, frame):
        log.info("Received signal %s, stopping ...", signum)
        self._stop_event.set()

    # ------------------------------------------------------------------
    # EcoFlow polling loop  (covers all accounts + devices from config)
    # ------------------------------------------------------------------
    def _ecoflow_loop(self):
        db = DatabaseManager()
        db.connect()

        # Build per-device context once at startup
        device_infos = []
        for dev_cfg in iter_ecoflow_devices():
            home_name = dev_cfg["home_name"]
            device_sn = dev_cfg["device_sn"]

            home_id = db.get_home_id(home_name)
            panel_device_id = db.get_device_id(device_sn)
            battery_device_id = db.get_device_id(device_sn + "-BAT")
            if battery_device_id is None:
                battery_device_id = panel_device_id
            circuit_map = db.get_circuit_map(panel_device_id) if panel_device_id else {}

            if not home_id or not panel_device_id:
                log.warning(
                    "Seed data missing for home='%s' sn='%s' — skipping. "
                    "Run 'seed' first.",
                    home_name, device_sn,
                )
                continue

            client = EcoFlowClient(config=dev_cfg)
            device_infos.append({
                "client": client,
                "home_id": home_id,
                "panel_device_id": panel_device_id,
                "battery_device_id": battery_device_id,
                "circuit_map": circuit_map,
                "label": f"{home_name}/{device_sn}",
            })

        if not device_infos:
            log.error("No EcoFlow devices available. Run 'seed' first.")
            db.close()
            return

        log.info("EcoFlow loop ready: %d device(s)", len(device_infos))

        cycle = 0
        while not self._stop_event.is_set():
            if cycle % STATUS_CHECK_INTERVAL == 0:
                self._refresh_online_status(device_infos, db)

            for info in device_infos:
                if not info.get("is_online", True):
                    log.warning("EcoFlow [%s]: offline, skipping poll", info["label"])
                    continue

                try:
                    data = info["client"].get_device_quota()
                    if data is None:
                        log.warning("EcoFlow poll returned no data for %s", info["label"])
                        continue

                    panel_row = transform_panel_reading(
                        data, info["panel_device_id"], info["home_id"]
                    )
                    db.insert_smart_panel_reading(panel_row)

                    circuit_rows = transform_circuit_readings(
                        data, info["panel_device_id"], info["home_id"], info["circuit_map"]
                    )
                    for row in circuit_rows:
                        db.insert_panel_circuit_reading(row)

                    bat_row = transform_battery_reading(
                        data, info["battery_device_id"], info["home_id"]
                    )
                    db.insert_battery_reading(bat_row)

                    log.info(
                        "EcoFlow [%s]: panel=%.0fW  load=%.0fW  battery=%s%%  circuits=%d",
                        info["label"],
                        panel_row.get("grid_power_w") or 0,
                        panel_row.get("home_load_w") or 0,
                        panel_row.get("battery_soc_pct") or "?",
                        len(circuit_rows),
                    )

                except Exception:
                    log.error(
                        "EcoFlow poll error for %s:\n%s",
                        info["label"], traceback.format_exc(),
                    )

            cycle += 1
            self._stop_event.wait(POLL_INTERVAL)

        db.close()

    def _refresh_online_status(self, device_infos, db):
        """Call device/list once per unique account and update is_online in-memory + DB."""
        seen_access_keys = {}  # access_key -> {sn: bool}
        for info in device_infos:
            ak = info["client"].access_key
            if ak not in seen_access_keys:
                try:
                    seen_access_keys[ak] = info["client"].get_device_list()
                except Exception:
                    log.error("Failed to fetch device list for account with key ...%s:\n%s",
                              ak[-6:], traceback.format_exc())
                    seen_access_keys[ak] = {}

            sn = info["client"].device_sn
            status_map = seen_access_keys[ak]
            if sn not in status_map:
                continue

            new_online = status_map[sn]
            prev_online = info.get("is_online")
            info["is_online"] = new_online

            if new_online != prev_online:
                state = "online" if new_online else "offline"
                log.info("EcoFlow [%s]: status changed -> %s", info["label"], state)

            db.update_device_online_status(sn, new_online)

    # ------------------------------------------------------------------
    # Ecobee polling loop  (covers all accounts + devices from config)
    # ------------------------------------------------------------------
    def _ecobee_loop(self):
        db = DatabaseManager()
        db.connect()

        # Build per-account context at startup
        account_infos = []
        for acc_cfg in iter_ecobee_accounts():
            client = EcobeeClient(config=acc_cfg)
            device_infos = []
            for device in acc_cfg.get("devices", []):
                home_name = device["home_name"]
                ecobee_id = device["device_id"]

                home_id = db.get_home_id(home_name)
                device_id = db.get_device_id_by_api_id(ecobee_id)

                if not home_id or not device_id:
                    log.warning(
                        "Seed data missing for ecobee home='%s' device_id='%s' — skipping. "
                        "Run 'seed' first.",
                        home_name, ecobee_id,
                    )
                    continue

                device_infos.append({
                    "home_name": home_name,
                    "ecobee_id": ecobee_id,
                    "home_id": home_id,
                    "device_id": device_id,
                })

            if device_infos:
                account_infos.append({
                    "client": client,
                    "account_name": acc_cfg["name"],
                    "devices": device_infos,
                })

        if not account_infos:
            log.error("No Ecobee devices available. Run 'seed' first.")
            db.close()
            return

        total_devices = sum(len(a["devices"]) for a in account_infos)
        log.info("Ecobee loop ready: %d account(s), %d device(s)",
                 len(account_infos), total_devices)

        while not self._stop_event.is_set():
            for acc in account_infos:
                try:
                    thermostats = acc["client"].get_all_thermostats()
                    if not thermostats:
                        log.warning("Ecobee account '%s': no data returned",
                                    acc["account_name"])
                        continue

                    for dev in acc["devices"]:
                        thermostat = thermostats.get(dev["ecobee_id"])
                        if thermostat is None:
                            log.warning(
                                "Ecobee account '%s': thermostat '%s' not in response",
                                acc["account_name"], dev["ecobee_id"],
                            )
                            continue

                        # Dedup: only insert if readings changed
                        key = dedup_key(thermostat)
                        if key == self._last_ecobee_keys.get(dev["ecobee_id"]):
                            log.debug("Ecobee [%s/%s]: no change, skipping insert",
                                      acc["account_name"], dev["home_name"])
                            continue

                        row = transform_thermostat_reading(
                            thermostat, dev["device_id"], dev["home_id"]
                        )
                        db.insert_thermostat_reading(row)
                        self._last_ecobee_keys[dev["ecobee_id"]] = key

                        log.info(
                            "Ecobee [%s/%s]: indoor=%.1f°C  humidity=%s%%"
                            "  mode=%s  state=%s  hold=%s",
                            acc["account_name"], dev["home_name"],
                            row.get("indoor_temp_c") or 0,
                            row.get("indoor_humidity_pct") or "?",
                            row.get("hvac_mode") or "?",
                            row.get("hvac_state") or "?",
                            row.get("hold_type") or "none",
                        )

                except Exception:
                    log.error("Ecobee poll error for account '%s':\n%s",
                              acc["account_name"], traceback.format_exc())

            self._stop_event.wait(POLL_INTERVAL)

        db.close()

    # ------------------------------------------------------------------
    # OpenADR polling loop
    # ------------------------------------------------------------------
    def _openadr_loop(self):
        db = DatabaseManager()
        db.connect()

        cfg = get_openadr_config()
        poll_interval = int(cfg.get("poll_interval", POLL_INTERVAL))
        client = OpenADRClient(cfg)

        try:
            client.connect()
        except Exception:
            log.error("OpenADR connect failed:\n%s", traceback.format_exc())
            db.close()
            return

        log.info("OpenADR loop ready: VEN=%s  program=%s  interval=%ds",
                 client.ven_name, cfg["program_name"], poll_interval)

        while not self._stop_event.is_set():
            try:
                result = client.poll()
                if result is None:
                    log.warning("OpenADR: no active price interval at current time.")
                else:
                    row = {
                        "ts":             result["polled_at"],
                        "program_name":   result["program_name"],
                        "program_id":     result["program_id"],
                        "event_name":     result["event_name"],
                        "event_id":       result["event_id"],
                        "priority":       result["priority"],
                        "period_type":    result["period_type"],
                        "price_per_kwh":  result["price_per_kwh"],
                        "interval_start": result["interval_start"],
                        "interval_end":   result["interval_end"],
                        "ven_id":         result["ven_id"],
                        "ven_name":       result["ven_name"],
                    }
                    db.insert_openadr_event(row)
                    log.info(
                        "OpenADR [%s]: $%.5f/kWh  [%s]  event=%s",
                        result["program_name"],
                        result["price_per_kwh"],
                        result["period_type"],
                        result["event_name"],
                    )
            except Exception:
                log.error("OpenADR poll error:\n%s", traceback.format_exc())

            self._stop_event.wait(poll_interval)

        db.close()
