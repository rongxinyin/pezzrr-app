"""
Seed the database with homes, devices, and panel circuits.
Sources: ecoflow_config.json (accounts + devices) and hard-coded home metadata
from 'pezerr panel summary.xlsx'.
"""

import logging

from .config import iter_ecoflow_devices
from .db import DatabaseManager
from .ecoflow_client import EcoFlowClient

log = logging.getLogger(__name__)

ECOBEE_ID = "421833899027"

# Home metadata keyed by home_name.
# Lab home kept for backward compat; deploy homes from 'pezerr panel summary.xlsx'.
HOME_METADATA = {
    "test_home": {
        "address": "West Ter",
        "city": "Lafayette",
        "state": "CA",
        "zip_code": "94549",
        "utility_id": "PG&E E-TOU-C",
        "timezone": "America/Los_Angeles",
    },
    "3110A": {
        "address": "3101 Boyd Rd",
        "city": "Arcata",
        "state": "CA",
        "zip_code": "95521",
        "utility_id": "PGE E-TOU-B",
        "timezone": "America/Los_Angeles",
    },
    "3110C": {
        "address": "3101 Boyd Rd",
        "city": "Arcata",
        "state": "CA",
        "zip_code": "95521",
        "utility_id": "PGE E-TOU-B",
        "timezone": "America/Los_Angeles",
    },
    "3110D": {
        "address": "3101 Boyd Rd",
        "city": "Arcata",
        "state": "CA",
        "zip_code": "95521",
        "utility_id": "PGE E-TOU-B",
        "timezone": "America/Los_Angeles",
    },
    "3110F": {
        "address": "3101 Boyd Rd",
        "city": "Arcata",
        "state": "CA",
        "zip_code": "95521",
        "utility_id": "PGE E-TOU-B",
        "timezone": "America/Los_Angeles",
    },
    "890B": {
        "address": "3101 Boyd Rd",
        "city": "Arcata",
        "state": "CA",
        "zip_code": "95521",
        "utility_id": "PGE E-TOU-B",
        "timezone": "America/Los_Angeles",
    },
    "900H": {
        "address": "3101 Boyd Rd",
        "city": "Arcata",
        "state": "CA",
        "zip_code": "95521",
        "utility_id": "PGE E-TOU-B",
        "timezone": "America/Los_Angeles",
    },
}


def seed(db=None):
    db = db or DatabaseManager()
    db.connect()

    seeded_homes = {}    # home_name -> home_id
    seeded_panels = {}   # device_sn -> panel_device_id

    for dev_cfg in iter_ecoflow_devices():
        home_name = dev_cfg["home_name"]
        device_sn = dev_cfg["device_sn"]
        account = dev_cfg["account_name"]

        # ---- Home ----
        if home_name not in seeded_homes:
            meta = HOME_METADATA.get(home_name, {
                "address": "", "city": "", "state": "", "zip_code": "",
                "utility_id": "", "timezone": "America/Los_Angeles",
            })
            home_id = db.upsert_home(
                home_name=home_name,
                address=meta["address"],
                city=meta["city"],
                state=meta["state"],
                zip_code=meta["zip_code"],
                utility_id=meta["utility_id"],
                timezone=meta["timezone"],
            )
            seeded_homes[home_name] = home_id
            log.info("[%s] home '%s' -> home_id=%s", account, home_name, home_id)
        else:
            home_id = seeded_homes[home_name]

        # ---- Panel device ----
        panel_device_id = db.upsert_device(
            home_id=home_id,
            device_type="smart_panel",
            device_name="EcoFlow SHP2",
            manufacturer="EcoFlow",
            model="Smart Home Panel 2",
            serial_number=device_sn,
            api_identifier=device_sn,
        )
        seeded_panels[device_sn] = panel_device_id
        log.info("[%s] panel '%s' -> device_id=%s", account, device_sn, panel_device_id)

        # Battery uses a distinct serial so it gets its own device row
        bat_sn = device_sn + "-BAT"
        battery_device_id = db.upsert_device(
            home_id=home_id,
            device_type="battery",
            device_name="EcoFlow Delta Pro Ultra",
            manufacturer="EcoFlow",
            model="Delta Pro Ultra",
            serial_number=bat_sn,
            api_identifier=device_sn,
        )
        log.info("[%s] battery '%s' -> device_id=%s", account, bat_sn, battery_device_id)

        # ---- Panel circuits (12) ----
        log.info("[%s] Fetching circuit names for %s ...", account, device_sn)
        client = EcoFlowClient(config=dev_cfg)
        try:
            data = client.get_device_quota()
        except Exception as e:
            log.warning("EcoFlow API call failed for %s (%s), using default names", device_sn, e)
            data = None

        for ch in range(12):
            api_idx = ch + 1
            if data:
                name = data.get(
                    f"pd303_mc.loadIncreInfo.hall1IncreInfo.ch{api_idx}Info.chName",
                    f"Circuit {api_idx}",
                )
            else:
                name = f"Circuit {api_idx}"
            cid = db.upsert_panel_circuit(panel_device_id, ch, name)
            log.info("  ch=%d  %-20s  circuit_id=%s", ch, name, cid)

    # ---- Ecobee thermostat (lab home only) ----
    lab_home_id = seeded_homes.get("test_home")
    if lab_home_id:
        thermo_device_id = db.upsert_device(
            home_id=lab_home_id,
            device_type="thermostat",
            device_name="Ecobee Thermostat",
            manufacturer="Ecobee",
            model="SmartThermostat",
            serial_number=ECOBEE_ID,
            api_identifier=ECOBEE_ID,
        )
        log.info("thermostat '%s' -> device_id=%s", ECOBEE_ID, thermo_device_id)

    log.info("Seed complete. %d homes, %d panel devices.", len(seeded_homes), len(seeded_panels))
    return seeded_homes, seeded_panels


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    seed()
