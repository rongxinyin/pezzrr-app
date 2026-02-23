"""
Configuration loader for data collectors.
Loads JSON configs and builds DB connection string.
"""

import json
import os

# Project root: two levels up from this file
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")


def _load_json(filename):
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, "r") as f:
        return json.load(f)


def get_ecoflow_config():
    return _load_json("ecoflow_config.json")


def iter_ecoflow_devices():
    """Yield a flat config dict for each device across all accounts.

    Each dict contains: access_key, secret_key, device_sn, home_name,
    account_name, api_base_url — ready to pass directly to EcoFlowClient.
    """
    cfg = get_ecoflow_config()
    base_url = cfg.get("api_base_url", "https://api-a.ecoflow.com")
    for account in cfg.get("accounts", []):
        for device in account.get("devices", []):
            yield {
                "account_name": account["name"],
                "access_key": account["access_key"],
                "secret_key": account["secret_key"],
                "device_sn": device["serial_number"],
                "home_name": device["home_name"],
                "api_base_url": base_url,
            }


def get_ecobee_config():
    return _load_json("ecobee_config.json")


def get_db_config():
    return _load_json("data_analytics_config.json")["database"]


def get_db_dsn():
    db = get_db_config()
    return (
        f"host={db['host']} port={db['port']} "
        f"dbname={db['database_name']} "
        f"user={db['username']} password={db['password']}"
    )
