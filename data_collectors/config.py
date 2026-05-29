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


def iter_ecobee_accounts():
    """Yield an account-level config dict for each Ecobee account.

    Top-level keys (poll_interval, api_base_url, etc.) are merged in as
    defaults; per-account keys take precedence.
    """
    cfg = get_ecobee_config()
    shared = {k: v for k, v in cfg.items() if k != "accounts"}
    for account in cfg.get("accounts", []):
        yield {**shared, **account}


def iter_ecobee_devices():
    """Yield a flat config dict for each device across all Ecobee accounts.

    Each dict contains: api_key, app_id, account_name, device_id, home_name,
    plus shared fields — ready to pass directly to helpers.
    """
    cfg = get_ecobee_config()
    shared = {k: v for k, v in cfg.items() if k != "accounts"}
    for account in cfg.get("accounts", []):
        acc_cfg = {**shared, **account}
        for device in account.get("devices", []):
            yield {**acc_cfg, "account_name": account["name"], **device}


def get_openadr_config():
    return _load_json("openadr_config.json")


def get_darksky_config():
    return _load_json("darksky_config.json")


def iter_weather_locations():
    """Yield a flat config dict for each weather location.

    Shared top-level keys (api_key, api_base_url, units, exclude) are
    merged in; per-location keys (location_name, latitude, longitude,
    home_name, timezone) take precedence — ready to pass to DarkSkyClient.
    """
    cfg = get_darksky_config()
    shared = {k: v for k, v in cfg.items() if k != "locations"}
    for location in cfg.get("locations", []):
        yield {**shared, **location}


def get_db_config():
    return _load_json("data_analytics_config.json")["database"]


def get_db_dsn():
    db = get_db_config()
    return (
        f"host={db['host']} port={db['port']} "
        f"dbname={db['database_name']} "
        f"user={db['username']} password={db['password']}"
    )
