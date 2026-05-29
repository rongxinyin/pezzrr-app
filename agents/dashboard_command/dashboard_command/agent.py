"""
Dashboard command agent (docs/DASHBOARD_DESIGN.md §10, Task 8).

The central API cannot RPC each home's VOLTTRON directly, so it publishes
commands to `cmd/home/<gateway_id>/control` over MQTT. This edge agent
subscribes to that topic, translates the command to the right device-agent RPC
(via CommandTranslator), and publishes the outcome to
`cmd/home/<gateway_id>/result`, which the API's control_bus writes back onto
the originating control_actions row.

paho-mqtt runs its network loop on a background thread; RPC calls are
dispatched onto VOLTTRON's gevent loop via self.core.spawn so the paho thread
is never blocked on VIP.
"""

# Import gevent first so requests/urllib3 used by device agents are patched.
import gevent
from gevent import monkey
monkey.patch_all()

import json
import logging
import sys

import paho.mqtt.client as mqtt

from volttron import utils
from volttron.client import Agent, Core, RPC
from volttron.utils import format_timestamp, get_aware_utc_now

from .translator import CommandTranslator

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)


def control_topic(gateway_id: str) -> str:
    return f"cmd/home/{gateway_id}/control"


def result_topic(gateway_id: str) -> str:
    return f"cmd/home/{gateway_id}/result"


class DashboardCommandAgent(Agent):
    def __init__(self, config_path, **kwargs):
        super().__init__(**kwargs)
        self.default_config = {
            "gateway_id": "",
            "mqtt": {"host": "localhost", "port": 1883, "user": None, "pass": None},
            "rpc_timeout": 30,
            "rpc_targets": {
                "ilc": "platform.ilc",
                "ecoflow": "ecoflow_agent",
                "ecobee": "ecobee_agent",
                "kasa": "kasa_plug",
            },
            "circuit_map": {},
        }
        self.gateway_id = ""
        self.mqtt_cfg = {}
        self.rpc_timeout = 30
        self.rpc_targets = {}
        self.circuit_map = {}
        self._mqtt = None
        self._translator = None

        self.vip.config.set_default("config", self.default_config)
        self.vip.config.subscribe(self._configure, actions=["NEW", "UPDATE"], pattern="config")

    def _configure(self, config_name, action, contents):
        cfg = dict(self.default_config)
        cfg.update(contents or {})
        self.gateway_id = cfg["gateway_id"]
        self.mqtt_cfg = cfg.get("mqtt") or {}
        self.rpc_timeout = int(cfg.get("rpc_timeout", 30))
        self.rpc_targets = cfg.get("rpc_targets") or {}
        self.circuit_map = cfg.get("circuit_map") or {}
        self._translator = CommandTranslator(
            rpc_call=self._rpc_call,
            circuit_map=self.circuit_map,
            rpc_targets=self.rpc_targets,
        )
        _log.info("dashboard_command configured for gateway_id=%s", self.gateway_id)
        if self._mqtt is not None:
            self._reconnect_mqtt()

    def _rpc_call(self, identity, method, *args):
        """Blocking VIP RPC, run on the gevent loop with a timeout."""
        return self.vip.rpc.call(identity, method, *args).get(timeout=self.rpc_timeout)

    @Core.receiver("onstart")
    def _onstart(self, sender, **kwargs):
        if not self.gateway_id:
            _log.warning("no gateway_id configured; not connecting to MQTT")
            return
        self._connect_mqtt()

    @Core.receiver("onstop")
    def _onstop(self, sender, **kwargs):
        if self._mqtt is not None:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
            self._mqtt = None

    def _connect_mqtt(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if self.mqtt_cfg.get("user"):
            client.username_pw_set(self.mqtt_cfg["user"], self.mqtt_cfg.get("pass"))
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect_async(
            self.mqtt_cfg.get("host", "localhost"),
            int(self.mqtt_cfg.get("port", 1883)),
            keepalive=60,
        )
        client.loop_start()
        self._mqtt = client
        _log.info("connecting to MQTT %s:%s", self.mqtt_cfg.get("host"), self.mqtt_cfg.get("port"))

    def _reconnect_mqtt(self):
        try:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._mqtt = None
        self._connect_mqtt()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        topic = control_topic(self.gateway_id)
        client.subscribe(topic, qos=1)
        _log.info("subscribed to %s", topic)

    def _on_message(self, client, userdata, msg):
        # paho network thread — parse here, run RPC on the gevent loop.
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            _log.warning("dropping malformed command on %s", msg.topic)
            return
        self.core.spawn(self._process, payload)

    def _process(self, payload):
        result = self._translator.handle(payload)
        # translator stamps ack_ts itself; prefer VOLTTRON's formatter for parity
        result["ack_ts"] = format_timestamp(get_aware_utc_now())
        self._publish_result(result)
        _log.info("action %s -> success=%s", result.get("action_id"), result.get("success"))

    def _publish_result(self, result):
        if self._mqtt is None:
            _log.warning("MQTT not connected; cannot publish result for %s", result.get("action_id"))
            return
        self._mqtt.publish(result_topic(self.gateway_id), json.dumps(result), qos=1)


def main():
    try:
        utils.vip_main(DashboardCommandAgent, version="1.0.0")
    except Exception:  # noqa: BLE001
        _log.exception("unhandled exception in dashboard_command main")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
