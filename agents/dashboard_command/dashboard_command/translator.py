"""
Dispatch → device-RPC translation (docs/DASHBOARD_DESIGN.md §10).

The dashboard publishes intent (`action_type` + `target` + `params`); the edge
knows which VOLTTRON agent actuates which device. This module is the pure,
VOLTTRON-free mapping between the two, so it can be unit-tested without a
running platform or broker. The agent (agent.py) injects `vip.rpc.call` as
`rpc_call` and the panel mapping as `circuit_map`.

A command never raises out of `handle`: any failure is captured into the
result dict so the central API's control_actions row flips to failed rather
than hanging pending forever.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

# action_types (§ models.ACTION_TYPES) that mean "turn the load OFF / reduce"
_OFF_ACTIONS = {"curtail", "channel_disable"}
# ...and the ones that restore / increase it.
_ON_ACTIONS = {"release", "augment", "channel_enable"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TranslationError(Exception):
    """Raised for a malformed/unroutable command (→ result success=False)."""


class CommandTranslator:
    def __init__(
        self,
        rpc_call: Callable[..., object],
        circuit_map: Optional[dict] = None,
        rpc_targets: Optional[dict] = None,
    ) -> None:
        # rpc_call(identity, method, *args) -> response (blocking; agent wraps
        # vip.rpc.call(...).get(timeout=...)).
        self._rpc = rpc_call
        # {circuit_id (str|int): {"device_sn": str, "channel": int}}
        self._circuit_map = {str(k): v for k, v in (circuit_map or {}).items()}
        t = rpc_targets or {}
        self._ilc = t.get("ilc", "platform.ilc")
        self._ecoflow = t.get("ecoflow", "ecoflow_agent")
        self._ecobee = t.get("ecobee", "ecobee_agent")
        self._kasa = t.get("kasa", "kasa_plug")

    def handle(self, payload: dict) -> dict:
        action_id = payload.get("action_id")
        try:
            response = self._route(payload)
            return {
                "action_id": action_id,
                "success": True,
                "response": response if isinstance(response, dict) else {"result": response},
                "ack_ts": _now_iso(),
            }
        except Exception as exc:  # noqa: BLE001 — must never escape to the bus
            return {
                "action_id": action_id,
                "success": False,
                "response": {"error": str(exc)},
                "ack_ts": _now_iso(),
            }

    def _route(self, payload: dict) -> object:
        target = payload.get("target") or {}
        kind = target.get("kind")
        action = payload.get("action_type")
        params = payload.get("params") or {}

        if kind == "demand_limit":
            kw = params.get("kw")
            if kw is None:
                raise TranslationError("demand_limit requires params.kw")
            return self._rpc(self._ilc, "update_configurations", {"config": {"demand_limit": kw}})

        if kind == "circuit":
            return self._circuit(target, action, params)

        if kind == "thermostat":
            return self._rpc(
                self._ecobee,
                "set_temperature",
                params.get("heat_setpoint"),
                params.get("cool_setpoint"),
                params.get("hold_type", "nextTransition"),
            )

        if kind == "plug":
            device = params.get("device_alias") or target.get("device_id")
            if device is None:
                raise TranslationError("plug target requires device_id or params.device_alias")
            enabled = self._enabled(action, params)
            return self._rpc(self._kasa, "control_device", device, "set_relay", int(enabled))

        if kind == "battery_mode":
            device = params.get("device_sn") or target.get("device_id")
            if device is None:
                raise TranslationError("battery_mode target requires device_sn or device_id")
            command = params.get("command", action)
            return self._rpc(self._ecoflow, "control_device_rpc", device, command, params.get("value"))

        raise TranslationError(f"unroutable target.kind '{kind}'")

    def _circuit(self, target: dict, action: str, params: dict) -> object:
        circuit_id = target.get("circuit_id")
        if circuit_id is None:
            raise TranslationError("circuit target requires circuit_id")
        mapping = self._circuit_map.get(str(circuit_id))
        device_sn = params.get("device_sn") or (mapping or {}).get("device_sn")
        channel = params.get("channel")
        if channel is None and mapping is not None:
            channel = mapping.get("channel")
        if device_sn is None or channel is None:
            raise TranslationError(f"no panel mapping for circuit_id {circuit_id}")
        enabled = self._enabled(action, params)
        # The actual EcoFlow SHP2 load-channel write lives in the device agent
        # (docs §18 Q1); here we only issue the RPC intent.
        return self._rpc(
            self._ecoflow,
            "control_device_rpc",
            device_sn,
            "set_load_channel",
            {"channel": int(channel), "enabled": bool(enabled)},
        )

    @staticmethod
    def _enabled(action: str, params: dict) -> bool:
        if "enabled" in params:
            return bool(params["enabled"])
        if action in _OFF_ACTIONS:
            return False
        if action in _ON_ACTIONS:
            return True
        raise TranslationError(f"cannot derive on/off from action_type '{action}'")
