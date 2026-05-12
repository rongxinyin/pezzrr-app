"""
OpenADR 3.1 VEN Agent for PEZZRR Controller
Registers with the VTN, polls for SCP-EMTOU price event signals,
and publishes the current electricity price to the VOLTTRON message bus.
"""

import sys
import json
import re
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

import gevent
from gevent import monkey
monkey.patch_all()

import requests

from volttron import utils
from volttron.client.messaging import topics, headers as headers_mod
from volttron.utils import format_timestamp, get_aware_utc_now
from volttron.client import Agent, Core, RPC

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)

__version__ = "1.0.0"


def _parse_dt(s: str) -> datetime:
    """Parse an RFC 3339 datetime string to an aware UTC datetime."""
    return datetime.fromisoformat(s).astimezone(timezone.utc)


_ISO_DUR_RE = re.compile(
    r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)

def _parse_iso_duration(d: str) -> timedelta:
    """Parse a full ISO 8601 duration string to a timedelta.
    Months are approximated as 30 days; years as 365 days.
    """
    m = _ISO_DUR_RE.match(d)
    if not m:
        raise ValueError(f"Cannot parse ISO 8601 duration: {d!r}")
    years, months, days, hours, minutes, seconds = (int(v or 0) for v in m.groups())
    return timedelta(
        days=years * 365 + months * 30 + days,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )


def _active_price(events: list, now: datetime) -> dict | None:
    """
    Given a list of OpenADR event objects, return the payload of the
    highest-priority active interval containing `now`.

    Priority: lower number = higher priority (0 is highest).
    Returns dict with keys: price, event_name, priority, period_type
    """
    candidates = []

    for event in events:
        priority = event.get("priority")  # None means UNSPECIFIED (lowest)
        event_name = event.get("eventName", "")
        intervals = event.get("intervals") or []

        for interval in intervals:
            period = interval.get("intervalPeriod") or {}
            start_str = period.get("start")
            dur_str = period.get("duration")
            if not start_str or not dur_str:
                continue

            start = _parse_dt(start_str)
            end = start + _parse_iso_duration(dur_str)

            if start <= now < end:
                for payload in interval.get("payloads", []):
                    if payload.get("type") == "PRICE":
                        candidates.append({
                            "price": payload["values"][0],
                            "event_name": event_name,
                            "priority": priority,
                        })

    if not candidates:
        return None

    # Lower priority number wins; None (UNSPECIFIED) loses to any number
    def sort_key(c):
        p = c["priority"]
        return (1, p) if p is not None else (2, 0)

    winner = min(candidates, key=sort_key)
    winner["period_type"] = "peak" if winner["priority"] == 1 else "off_peak"
    return winner


class OpenADRAgent(Agent):
    """
    OpenADR 3.1 VEN agent.

    Lifecycle:
      onstart  → authenticate → register VEN → start poll loop
      periodic → poll VTN for events → resolve active price → publish
    """

    def __init__(self, config_path, **kwargs):
        super().__init__(**kwargs)

        self.default_config = {
            "vtn_url": "http://localhost:3001",
            "client_id": "ven-client-client-id",
            "client_secret": "ven-client",
            "program_name": "SCP-EMTOU",
            "ven_name": "pezzrr-ven",
            "poll_interval": 60,
            "campus": "Arcata, CA",
            "building": "test",
            "device_id": "pezerr_ven",
        }

        # Runtime state
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._ven_id: str | None = None
        self._program_id: str | None = None
        self._last_price: dict | None = None
        self._poll_greenlet = None

        self.vip.config.set_default("config", self.default_config)
        self.vip.config.subscribe(self._configure, actions=["NEW", "UPDATE"])

    # ── Configuration ─────────────────────────────────────────────────────────

    def _configure(self, config_name, action, contents):
        cfg = self.default_config.copy()
        cfg.update(contents)

        self.vtn_url = cfg["vtn_url"].rstrip("/")
        self.client_id = cfg["client_id"]
        self.client_secret = cfg["client_secret"]
        self.program_name = cfg["program_name"]
        self.ven_name = cfg["ven_name"]
        self.poll_interval = int(cfg["poll_interval"])

        device_topic = f"{cfg['campus']}/{cfg['building']}/{cfg['device_id']}"
        self.publish_topic = f"devices/{device_topic}"

        _log.info("OpenADR agent configured: VTN=%s  program=%s  VEN=%s",
                  self.vtn_url, self.program_name, self.ven_name)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @Core.receiver("onstart")
    def onstart(self, sender, **kwargs):
        _log.info("OpenADR VEN agent starting...")
        try:
            self._authenticate()
            self._register_ven()
            self._resolve_program()
            self._poll_greenlet = self.core.periodic(self.poll_interval, self._poll)
            _log.info("Poll loop started (every %ds)", self.poll_interval)
        except Exception as exc:
            _log.error("Startup failed: %s", exc)

    @Core.receiver("onstop")
    def onstop(self, sender, **kwargs):
        _log.info("OpenADR VEN agent stopping.")

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self):
        """Fetch a client-credentials OAuth token from the VTN."""
        resp = requests.post(
            f"{self.vtn_url}/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expires_at = get_aware_utc_now() + timedelta(seconds=expires_in - 60)
        _log.info("Authenticated with VTN (token expires in %ds)", expires_in)

    def _ensure_token(self):
        if self._token is None or get_aware_utc_now() >= self._token_expires_at:
            _log.info("Token expired or missing — re-authenticating.")
            self._authenticate()

    def _headers(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    # ── VEN Registration ──────────────────────────────────────────────────────

    def _register_ven(self):
        """Register (or re-use) this VEN on the VTN."""
        # Check if already registered
        resp = requests.get(f"{self.vtn_url}/vens", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        for ven in resp.json():
            if ven.get("venName") == self.ven_name:
                self._ven_id = ven["id"]
                _log.info("VEN already registered: %s  id=%s", self.ven_name, self._ven_id)
                return

        # Register fresh
        resp = requests.post(
            f"{self.vtn_url}/vens",
            json={"objectType": "VEN_VEN_REQUEST", "venName": self.ven_name},
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        self._ven_id = resp.json()["id"]
        _log.info("VEN registered: %s  id=%s", self.ven_name, self._ven_id)

    # ── Program Resolution ────────────────────────────────────────────────────

    def _resolve_program(self):
        """Look up the program ID for self.program_name."""
        resp = requests.get(f"{self.vtn_url}/programs", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        for prog in resp.json():
            if prog.get("programName") == self.program_name:
                self._program_id = prog["id"]
                _log.info("Program found: %s  id=%s", self.program_name, self._program_id)
                return
        _log.warning("Program '%s' not found on VTN.", self.program_name)

    # ── Poll ──────────────────────────────────────────────────────────────────

    def _poll(self):
        """Fetch events from the VTN and publish the current active price."""
        try:
            if self._program_id is None:
                self._resolve_program()
                if self._program_id is None:
                    return

            params = {"programID": self._program_id}
            resp = requests.get(
                f"{self.vtn_url}/events",
                params=params,
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            events = resp.json()
            _log.debug("Received %d events from VTN.", len(events))

            now = get_aware_utc_now()
            active = _active_price(events, now)

            if active:
                self._publish_price(active, now)
            else:
                _log.warning("No active price interval found for current time.")

        except Exception as exc:
            _log.error("Poll error: %s", exc)

    # ── Publish ───────────────────────────────────────────────────────────────

    def _publish_price(self, active: dict, now: datetime):
        headers = {
            headers_mod.DATE: format_timestamp(now),
            headers_mod.TIMESTAMP: format_timestamp(now),
        }

        price = active["price"]
        period_type = active["period_type"]
        event_name = active["event_name"]

        data = {
            "price_per_kwh": price,
            "period_type": period_type,
            "event_name": event_name,
            "program": self.program_name,
            "ven_id": self._ven_id,
            "timestamp": format_timestamp(now),
        }

        # Publish aggregate
        self.vip.pubsub.publish(
            "pubsub",
            f"{self.publish_topic}/all",
            headers,
            [data, self._metadata()],
        ).get(timeout=5)

        # Publish individual points
        for point, value in data.items():
            self.vip.pubsub.publish(
                "pubsub",
                f"{self.publish_topic}/{point}",
                headers,
                [{point: value}, {point: {"type": type(value).__name__, "tz": "UTC"}}],
            ).get(timeout=5)

        if self._last_price != price:
            _log.info("Price signal: $%.5f/kWh  [%s]  event=%s", price, period_type, event_name)
            self._last_price = price
        else:
            _log.debug("Price unchanged: $%.5f/kWh  [%s]", price, period_type)

    def _metadata(self) -> dict:
        return {
            "price_per_kwh": {"units": "USD/kWh", "type": "float"},
            "period_type":   {"units": None, "type": "str"},
            "event_name":    {"units": None, "type": "str"},
            "program":       {"units": None, "type": "str"},
            "ven_id":        {"units": None, "type": "str"},
            "timestamp":     {"units": None, "type": "str"},
        }

    # ── RPC ───────────────────────────────────────────────────────────────────

    @RPC.export
    def get_status(self) -> dict:
        """Return current VEN registration and price status."""
        return {
            "ven_name": self.ven_name,
            "ven_id": self._ven_id,
            "program": self.program_name,
            "program_id": self._program_id,
            "last_price": self._last_price,
            "authenticated": self._token is not None,
        }

    @RPC.export
    def force_poll(self) -> str:
        """Trigger an immediate poll outside the scheduled interval."""
        self._poll()
        return "poll complete"


def main():
    utils.vip_main(OpenADRAgent,
                   description="OpenADR 3.1 VEN Agent for SCP-EMTOU price signals",
                   argv=sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
