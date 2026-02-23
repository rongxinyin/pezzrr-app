"""
Main data collection orchestrator.
Two daemon threads poll EcoFlow and Ecobee at 1-minute intervals.
Each thread gets its own DB connection (psycopg2 is not thread-safe).
"""

import logging
import signal
import threading
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
from .ecobee_client import EcobeeClient
from .ecobee_transformer import transform_thermostat_reading, dedup_key

log = logging.getLogger(__name__)

POLL_INTERVAL = 60  # seconds


class DataCollector:
    def __init__(self):
        self._stop_event = threading.Event()
        self._last_ecobee_key = None

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

        log.info("Starting data collection (Ctrl-C to stop) ...")
        t_eco.start()
        t_bee.start()

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

        while not self._stop_event.is_set():
            for info in device_infos:
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

            self._stop_event.wait(POLL_INTERVAL)

        db.close()

    # ------------------------------------------------------------------
    # Ecobee polling loop
    # ------------------------------------------------------------------
    def _ecobee_loop(self):
        db = DatabaseManager()
        client = EcobeeClient()

        db.connect()
        home_id = db.get_home_id("test_home")
        device_id = db.get_device_id(client.device_id)
        if device_id is None:
            device_id = db.get_device_id_by_api_id(client.device_id)

        if not home_id or not device_id:
            log.error("Seed data missing for ecobee. Run 'seed' first.")
            return

        log.info("Ecobee loop ready: home_id=%s device_id=%s", home_id, device_id)

        while not self._stop_event.is_set():
            try:
                thermostat = client.get_thermostat_data()
                if thermostat is None:
                    log.warning("Ecobee poll returned no data")
                    self._stop_event.wait(POLL_INTERVAL)
                    continue

                # Dedup: only insert if readings changed
                key = dedup_key(thermostat)
                if key == self._last_ecobee_key:
                    log.debug("Ecobee: no change, skipping insert")
                    self._stop_event.wait(POLL_INTERVAL)
                    continue

                row = transform_thermostat_reading(thermostat, device_id, home_id)
                db.insert_thermostat_reading(row)
                self._last_ecobee_key = key

                log.info(
                    "Ecobee: indoor=%.1f°C  humidity=%s%%  mode=%s  state=%s",
                    row.get("indoor_temp_c") or 0,
                    row.get("indoor_humidity_pct") or "?",
                    row.get("hvac_mode") or "?",
                    row.get("hvac_state") or "?",
                )

            except Exception:
                log.error("Ecobee poll error:\n%s", traceback.format_exc())

            self._stop_event.wait(POLL_INTERVAL)

        db.close()
