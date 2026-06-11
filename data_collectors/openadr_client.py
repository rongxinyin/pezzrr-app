"""
OpenADR 3.1 VTN polling client.
Authenticates as a VEN, registers with the VTN, fetches events for a given
program, and resolves the active price interval for the current moment.
"""

import re
import logging
from datetime import datetime, timezone, timedelta

import requests

log = logging.getLogger(__name__)

_ISO_DUR_RE = re.compile(
    r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)


def _parse_iso_duration(d: str) -> timedelta:
    """Parse a full ISO 8601 duration string. Months ≈ 30 d, years ≈ 365 d."""
    m = _ISO_DUR_RE.match(d)
    if not m:
        raise ValueError(f"Cannot parse ISO 8601 duration: {d!r}")
    y, mo, da, h, mi, s = (int(v or 0) for v in m.groups())
    return timedelta(days=y * 365 + mo * 30 + da, hours=h, minutes=mi, seconds=s)


class OpenADRClient:
    """
    Thin OpenADR 3.1 VTN client for a single VEN / program pair.

    Usage:
        client = OpenADRClient(cfg)
        client.connect()           # auth + VEN registration
        result = client.poll()     # returns active price dict or None
    """

    def __init__(self, cfg: dict):
        self._vtn = cfg["vtn_url"].rstrip("/")
        self._client_id = cfg["client_id"]
        self._client_secret = cfg["client_secret"]
        self._program_name = cfg["program_name"]
        self._ven_name = cfg["ven_name"]

        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self.ven_id: str | None = None
        self.ven_name: str = self._ven_name
        self.program_id: str | None = None

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _authenticate(self):
        resp = requests.post(
            f"{self._vtn}/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
        log.debug("Authenticated with VTN (expires in %ds)", expires_in)

    def _ensure_token(self):
        if self._token is None or datetime.now(timezone.utc) >= self._token_expires_at:
            self._authenticate()

    def _headers(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    # ── VEN registration ─────────────────────────────────────────────────────

    def _register_ven(self):
        resp = requests.get(f"{self._vtn}/vens", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        for ven in resp.json():
            if ven.get("venName") == self._ven_name:
                self.ven_id = ven["id"]
                log.info("VEN already registered: %s  id=%s", self._ven_name, self.ven_id)
                return
        resp = requests.post(
            f"{self._vtn}/vens",
            json={"objectType": "VEN_VEN_REQUEST", "venName": self._ven_name},
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        self.ven_id = resp.json()["id"]
        log.info("VEN registered: %s  id=%s", self._ven_name, self.ven_id)

    # ── Program lookup ────────────────────────────────────────────────────────

    def _resolve_program(self):
        resp = requests.get(f"{self._vtn}/programs", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        for prog in resp.json():
            if prog.get("programName") == self._program_name:
                self.program_id = prog["id"]
                log.info("Program found: %s  id=%s", self._program_name, self.program_id)
                return
        raise RuntimeError(f"Program '{self._program_name}' not found on VTN.")

    # ── Connect (call once at startup) ────────────────────────────────────────

    def connect(self):
        self._authenticate()
        self._register_ven()
        self._resolve_program()

    # ── Poll ─────────────────────────────────────────────────────────────────

    def poll(self) -> dict | None:
        """
        Fetch events and return the active price for now, or None.

        Returned dict keys:
            price_per_kwh, period_type, priority,
            event_name, event_id,
            program_name, program_id,
            interval_start, interval_end,
            ven_id, ven_name, polled_at
        """
        self._ensure_token()
        resp = requests.get(
            f"{self._vtn}/events",
            params={"programID": self.program_id},
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json()
        log.debug("Fetched %d events from VTN.", len(events))

        now = datetime.now(timezone.utc)
        return self._active_price(events, now)

    # ── Full-day curve ─────────────────────────────────────────────────────────

    def day_curve(self, now: datetime | None = None,
                  tz_name: str = "America/Los_Angeles") -> list[dict]:
        """Effective price segments covering the local day that contains `now`.

        Fetches the program's events and steps at every interval boundary inside
        the day, emitting one segment per boundary span with the winning price
        (peak overrides off-peak). With hourly intervals on the VTN this yields
        the full 24-hour profile for the current day.
        """
        from zoneinfo import ZoneInfo

        self._ensure_token()
        resp = requests.get(
            f"{self._vtn}/events",
            params={"programID": self.program_id},
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json()

        if now is None:
            now = datetime.now(timezone.utc)
        local = ZoneInfo(tz_name)
        d = now.astimezone(local).date()
        day_start = datetime(d.year, d.month, d.day, tzinfo=local).astimezone(timezone.utc)
        day_end = day_start + timedelta(days=1)

        ivs = []
        for event in events:
            priority = event.get("priority")
            for interval in (event.get("intervals") or []):
                period = interval.get("intervalPeriod") or {}
                start_str = period.get("start")
                dur_str = period.get("duration")
                if not start_str or not dur_str:
                    continue
                start = datetime.fromisoformat(start_str).astimezone(timezone.utc)
                end = start + _parse_iso_duration(dur_str)
                if end <= day_start or start >= day_end:
                    continue
                price = None
                for payload in interval.get("payloads", []):
                    if payload.get("type") == "PRICE":
                        price = payload["values"][0]
                        break
                if price is None:
                    continue
                ivs.append({
                    "start": start, "end": end, "price": price,
                    "priority": priority,
                    "period_type": "peak" if priority == 1 else "off_peak",
                    "event_name": event.get("eventName", ""),
                    "event_id": event.get("id", ""),
                })
        if not ivs:
            return []

        bounds = {day_start, day_end}
        for r in ivs:
            if day_start < r["start"] < day_end:
                bounds.add(r["start"])
            if day_start < r["end"] < day_end:
                bounds.add(r["end"])
        ordered = sorted(bounds)

        def winner_at(t: datetime):
            covering = [r for r in ivs if r["start"] <= t < r["end"]]
            if not covering:
                return None
            return min(covering, key=lambda r: (
                0 if r["period_type"] == "peak" else 1,
                r["priority"] if r["priority"] is not None else 99,
            ))

        segments = []
        for i in range(len(ordered) - 1):
            t0, t1 = ordered[i], ordered[i + 1]
            w = winner_at(t0)
            if w is None:
                continue
            segments.append({
                "interval_start": t0,
                "interval_end": t1,
                "price_per_kwh": w["price"],
                "period_type": w["period_type"],
                "priority": w["priority"],
                "event_name": w["event_name"],
                "event_id": w["event_id"],
                "program_name": self._program_name,
                "program_id": self.program_id,
                "ven_id": self.ven_id,
                "ven_name": self.ven_name,
            })
        return segments

    # ── Price resolution ──────────────────────────────────────────────────────

    def _active_price(self, events: list, now: datetime) -> dict | None:
        candidates = []
        for event in events:
            for interval in (event.get("intervals") or []):
                period = interval.get("intervalPeriod") or {}
                start_str = period.get("start")
                dur_str = period.get("duration")
                if not start_str or not dur_str:
                    continue
                start = datetime.fromisoformat(start_str).astimezone(timezone.utc)
                end = start + _parse_iso_duration(dur_str)
                if not (start <= now < end):
                    continue
                for payload in interval.get("payloads", []):
                    if payload.get("type") != "PRICE":
                        continue
                    priority = event.get("priority")
                    candidates.append({
                        "price_per_kwh":  payload["values"][0],
                        "priority":       priority,
                        "event_name":     event.get("eventName", ""),
                        "event_id":       event.get("id", ""),
                        "interval_start": start,
                        "interval_end":   end,
                    })

        if not candidates:
            return None

        # Lower priority number = higher precedence; None (UNSPECIFIED) loses to any number
        winner = min(candidates, key=lambda c: (1, c["priority"]) if c["priority"] is not None else (2, 0))
        winner["period_type"]  = "peak" if winner["priority"] == 1 else "off_peak"
        winner["program_name"] = self._program_name
        winner["program_id"]   = self.program_id
        winner["ven_id"]       = self.ven_id
        winner["ven_name"]     = self.ven_name
        winner["polled_at"]    = now
        return winner
