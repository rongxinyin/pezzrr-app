"""
Ecobee API client with OAuth2 token management.
Reuses the auth pattern from agents/ecobee_agent/ecobee_agent/ecobee_agent.py.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta

import requests

from .config import get_ecobee_config, CONFIG_DIR, PROJECT_ROOT

log = logging.getLogger(__name__)

_TOKEN_FILE = os.path.join(CONFIG_DIR, "ecobee_tokens.json")
_VOLTTRON_STORE = os.path.join(
    PROJECT_ROOT,
    ".volttron", "configuration_store", "ecobee-agent-1.0.0_1.store",
)


class EcobeeClient:
    def __init__(self, config=None):
        cfg = config or get_ecobee_config()
        self.api_key = cfg["api_key"]
        self.api_base_url = cfg.get("api_base_url", "https://api.ecobee.com/1")
        self.device_id = cfg.get("device_id")

        self.access_token = None
        self.refresh_token = None
        self.expires_at = None

        self._load_tokens()

    # ------------------------------------------------------------------
    # Token persistence
    # ------------------------------------------------------------------
    def _load_tokens(self):
        """Load tokens from ecobee_tokens.json, falling back to VOLTTRON store."""
        if os.path.exists(_TOKEN_FILE):
            with open(_TOKEN_FILE, "r") as f:
                data = json.load(f)
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            exp = data.get("expires_at")
            self.expires_at = datetime.fromisoformat(exp) if exp else None
            log.info("Loaded tokens from %s", _TOKEN_FILE)
            return

        # Bootstrap from VOLTTRON config store
        if os.path.exists(_VOLTTRON_STORE):
            log.info("Bootstrapping tokens from VOLTTRON store ...")
            with open(_VOLTTRON_STORE, "r") as f:
                store = json.load(f)
            token_data = json.loads(store["tokens"]["data"])
            self.access_token = token_data.get("access_token")
            self.refresh_token = token_data.get("refresh_token")
            exp = token_data.get("expires_at")
            self.expires_at = datetime.fromisoformat(exp) if exp else None
            self._save_tokens()
            return

        log.warning("No stored tokens found. Call start_pin_auth() first.")

    def _save_tokens(self):
        with open(_TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            }, f, indent=2)
        log.debug("Tokens saved to %s", _TOKEN_FILE)

    # ------------------------------------------------------------------
    # OAuth2 token refresh
    # ------------------------------------------------------------------
    def _refresh_access_token(self):
        """Refresh the access token using the stored refresh token."""
        log.info("Refreshing Ecobee access token ...")
        # Ecobee token endpoint requires query-string parameters
        resp = requests.post(
            "https://api.ecobee.com/token"
            f"?grant_type=refresh_token"
            f"&refresh_token={self.refresh_token}"
            f"&client_id={self.api_key}",
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"Token refresh failed: {data}")

        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        self.expires_at = datetime.now() + timedelta(seconds=data["expires_in"])
        self._save_tokens()
        log.info("Ecobee token refreshed, expires at %s", self.expires_at)

    def _ensure_valid_token(self):
        """Auto-refresh if expired or within 5 minutes of expiry."""
        if not self.refresh_token:
            raise RuntimeError("No refresh token. Run start_pin_auth() first.")
        if (
            self.access_token is None
            or self.expires_at is None
            or datetime.now() >= self.expires_at - timedelta(minutes=5)
        ):
            self._refresh_access_token()

    # ------------------------------------------------------------------
    # PIN-based auth fallback
    # ------------------------------------------------------------------
    def start_pin_auth(self):
        """Start Ecobee PIN authorization flow (interactive)."""
        url = (
            f"https://api.ecobee.com/authorize"
            f"?response_type=ecobeePin&client_id={self.api_key}&scope=smartWrite"
        )
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        pin = data.get("ecobeePin")
        code = data.get("code")
        print(f"\n  Go to https://www.ecobee.com/consumerportal")
        print(f"  Enter PIN: {pin}")
        print(f"  Then press Enter here ...\n")
        input()

        resp = requests.post(
            f"https://api.ecobee.com/token"
            f"?grant_type=ecobeePin&code={code}&client_id={self.api_key}",
            timeout=30,
        )
        resp.raise_for_status()
        token_data = resp.json()
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data["refresh_token"]
        self.expires_at = datetime.now() + timedelta(
            seconds=token_data["expires_in"]
        )
        self._save_tokens()
        print("  Authorization successful!\n")

    # ------------------------------------------------------------------
    # API call
    # ------------------------------------------------------------------
    def get_thermostat_data(self):
        """Poll thermostat data. Returns the first thermostat dict or None."""
        self._ensure_valid_token()

        params = {
            "json": json.dumps({
                "selection": {
                    "selectionType": "registered",
                    "selectionMatch": "",
                    "includeRuntime": True,
                    "includeSettings": True,
                    "includeWeather": True,
                    "includeEvents": True,
                    "includeProgram": True,
                }
            })
        }
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        resp = requests.get(
            f"{self.api_base_url}/thermostat",
            params=params,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        thermostats = data.get("thermostatList", [])
        if not thermostats:
            log.warning("No thermostats returned from Ecobee API")
            return None
        return thermostats[0]
