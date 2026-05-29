"""
Dark Sky-compatible weather client (Pirate Weather backend).

Pirate Weather is a drop-in for the retired Dark Sky API and serves the
two request types from separate hosts:
  Forecast:     https://api.pirateweather.net/forecast/{key}/{lat},{lng}
  Time Machine: https://timemachine.pirateweather.net/forecast/{key}/{lat},{lng},{time}

`time` is a UNIX timestamp in seconds. Both share the Dark Sky response
shape (currently + hourly blocks).
"""

import logging

import requests

from .config import get_darksky_config

log = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.pirateweather.net/forecast"
_DEFAULT_TIMEMACHINE_BASE = "https://timemachine.pirateweather.net/forecast"


class DarkSkyClient:
    def __init__(self, config=None):
        cfg = config or get_darksky_config()
        self.api_key = cfg["api_key"]
        self.api_base_url = cfg.get("api_base_url", _DEFAULT_BASE).rstrip("/")
        self.timemachine_base_url = cfg.get(
            "timemachine_base_url", _DEFAULT_TIMEMACHINE_BASE
        ).rstrip("/")
        self.units = cfg.get("units", "si")
        self.exclude = cfg.get("exclude", "minutely,daily,alerts,flags")

    def _request(self, base_url, path):
        url = f"{base_url}/{self.api_key}/{path}"
        params = {"units": self.units, "exclude": self.exclude}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_forecast(self, latitude, longitude):
        """Current conditions + hourly forecast for the next 48h."""
        return self._request(self.api_base_url, f"{latitude},{longitude}")

    def get_timemachine(self, latitude, longitude, unix_time):
        """Observed conditions for the day containing `unix_time` (seconds)."""
        return self._request(
            self.timemachine_base_url, f"{latitude},{longitude},{int(unix_time)}"
        )
