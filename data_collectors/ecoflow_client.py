"""
EcoFlow API client with HMAC-SHA256 authentication.
Auth pattern copied from agents/ecoflow_agent/simple_test_ecoflow.py:157-200.
"""

import hashlib
import hmac
import logging
import random
import time

import requests

from .config import get_ecoflow_config

log = logging.getLogger(__name__)

# Quota endpoint only works on api-a.ecoflow.com
_QUOTA_BASE = "https://api-a.ecoflow.com"


class EcoFlowClient:
    def __init__(self, config=None):
        cfg = config or get_ecoflow_config()
        self.access_key = cfg["access_key"]
        self.secret_key = cfg["secret_key"]
        self.device_sn = cfg["device_sn"]
        # Force api-a for quota endpoint
        base = cfg.get("api_base_url", _QUOTA_BASE)
        if "api.ecoflow.com" in base and "api-a" not in base:
            base = _QUOTA_BASE
        self.api_base_url = base

    # ------------------------------------------------------------------
    # Auth helpers (copied from simple_test_ecoflow.py:157-200)
    # ------------------------------------------------------------------
    @staticmethod
    def _get_qstring(params):
        if not params:
            return ""
        sorted_params = sorted(params.items())
        return "&".join(f"{k}={v}" for k, v in sorted_params)

    @staticmethod
    def _hmac_sha256(data, key):
        hashed = hmac.new(
            key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
        ).digest()
        return "".join(format(byte, "02x") for byte in hashed)

    def _generate_signature(self, params=None):
        timestamp = str(int(time.time() * 1000))
        nonce = str(random.randint(100000, 999999))

        headers_dict = {
            "accessKey": self.access_key,
            "nonce": nonce,
            "timestamp": timestamp,
        }

        sign_parts = []
        if params:
            sign_parts.append(self._get_qstring(params))
        sign_parts.append(self._get_qstring(headers_dict))
        sign_str = "&".join(sign_parts)

        signature = self._hmac_sha256(sign_str, self.secret_key)

        return {
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature,
        }

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def make_request(self, endpoint, params=None):
        """Authenticated GET returning the `data` dict (or None)."""
        auth = self._generate_signature(params)
        headers = {
            "Content-Type": "application/json",
            "accessKey": self.access_key,
            "timestamp": auth["timestamp"],
            "nonce": auth["nonce"],
            "sign": auth["signature"],
        }
        url = f"{self.api_base_url}{endpoint}"
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        code = body.get("code")
        if str(code) != "0":
            log.error("EcoFlow API error: %s", body.get("message", body))
            return None
        return body.get("data", {})

    def get_device_quota(self, sn=None):
        """Fetch all quota data for the SHP2."""
        sn = sn or self.device_sn
        # sn is in the URL query string but NOT in the signature
        # (matches simple_test_ecoflow.py behavior)
        return self.make_request(
            f"/iot-open/sign/device/quota/all?sn={sn}",
        )
