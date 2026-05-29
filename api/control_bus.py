"""
MQTT control bus (docs/DASHBOARD_DESIGN.md §10).

The central API cannot RPC each home's VOLTTRON directly, so dispatch is
asynchronous over a broker: the API publishes commands to
`cmd/home/<gateway_id>/control` and listens on `cmd/home/+/result` to write
acknowledgements back onto the originating `control_actions` row.

paho-mqtt runs its network loop on a background thread, so result callbacks
hop back onto the API's asyncio loop via run_coroutine_threadsafe.

If `config/api_config.json` has no `mqtt` block (e.g. local dev with no
broker), the bus stays disabled: publish is a logged no-op and dispatch still
records the pending action.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt

from .auth import _config
from .db import db

log = logging.getLogger("control_bus")

RESULT_TOPIC = "cmd/home/+/result"


def control_topic(gateway_id: str) -> str:
    return f"cmd/home/{gateway_id}/control"


class ControlBus:
    def __init__(self) -> None:
        self._client: Optional[mqtt.Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.enabled = False

    async def connect(self) -> None:
        cfg = _config().get("mqtt")
        if not cfg:
            log.warning("no mqtt config; control bus disabled (dispatch will still record actions)")
            return

        self._loop = asyncio.get_running_loop()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if cfg.get("user"):
            client.username_pw_set(cfg["user"], cfg.get("pass"))
        client.on_message = self._on_message
        client.on_connect = self._on_connect
        client.connect_async(cfg["host"], int(cfg.get("port", 1883)), keepalive=60)
        client.loop_start()
        self._client = client
        self.enabled = True
        log.info("control bus connecting to %s:%s", cfg["host"], cfg.get("port", 1883))

    async def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self.enabled = False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(RESULT_TOPIC, qos=1)
        log.info("control bus subscribed to %s", RESULT_TOPIC)

    def _on_message(self, client, userdata, msg):
        # Runs on paho's network thread — marshal the DB write onto the loop.
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            log.warning("dropping malformed result on %s", msg.topic)
            return
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._handle_result(payload), self._loop)

    async def _handle_result(self, payload: dict) -> None:
        action_id = payload.get("action_id")
        if action_id is None:
            return
        ack_ts = _parse_ts(payload.get("ack_ts"))
        response = payload.get("response")
        await db.execute(
            """UPDATE control_actions
               SET success = $2,
                   response_payload = $3,
                   acknowledged_at = COALESCE($4, NOW())
               WHERE action_id = $1""",
            int(action_id),
            payload.get("success"),
            json.dumps(response) if response is not None else None,
            ack_ts,
        )
        log.info("control action %s acked success=%s", action_id, payload.get("success"))

    async def publish(self, topic: str, payload: dict) -> bool:
        if not self.enabled or self._client is None:
            log.warning("control bus disabled; not publishing to %s", topic)
            return False
        self._client.publish(topic, json.dumps(payload), qos=1)
        return True


def _parse_ts(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


control_bus = ControlBus()
