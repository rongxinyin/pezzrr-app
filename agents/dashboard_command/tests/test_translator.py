"""
Unit tests for CommandTranslator (docs/DASHBOARD_DESIGN.md §10, Task 8).

These run without a VOLTTRON platform or MQTT broker: rpc_call is a fake that
records calls, so we assert the dispatch payload is translated to the right
device-agent RPC and that the result dict (which control_bus writes back) is
well-formed. Acceptance: a circuit command flips the channel and yields
success=True with an ack_ts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard_command.translator import CommandTranslator  # noqa: E402


class FakeRpc:
    def __init__(self, returns=None, raises=None):
        self.calls = []
        self._returns = returns if returns is not None else {"detail": "applied"}
        self._raises = raises

    def __call__(self, identity, method, *args):
        self.calls.append((identity, method, args))
        if self._raises is not None:
            raise self._raises
        return self._returns


CIRCUIT_MAP = {"37": {"device_sn": "SN-1", "channel": 9}}
TARGETS = {"ilc": "platform.ilc", "ecoflow": "ecoflow_agent",
           "ecobee": "ecobee_agent", "kasa": "kasa_plug"}


def _translator(rpc):
    return CommandTranslator(rpc_call=rpc, circuit_map=CIRCUIT_MAP, rpc_targets=TARGETS)


def test_circuit_curtail_flips_channel_off():
    rpc = FakeRpc()
    t = _translator(rpc)
    res = t.handle({
        "action_id": 100,
        "action_type": "curtail",
        "target": {"kind": "circuit", "circuit_id": 37},
        "params": {},
    })
    assert res["success"] is True
    assert res["action_id"] == 100
    assert res["ack_ts"]
    identity, method, args = rpc.calls[0]
    assert identity == "ecoflow_agent"
    assert method == "control_device_rpc"
    assert args[0] == "SN-1"
    assert args[1] == "set_load_channel"
    assert args[2] == {"channel": 9, "enabled": False}


def test_circuit_release_flips_channel_on():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 101,
        "action_type": "release",
        "target": {"kind": "circuit", "circuit_id": 37},
        "params": {},
    })
    assert res["success"] is True
    assert rpc.calls[0][2][2] == {"channel": 9, "enabled": True}


def test_circuit_unmapped_fails_gracefully():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 102,
        "action_type": "curtail",
        "target": {"kind": "circuit", "circuit_id": 999},
        "params": {},
    })
    assert res["success"] is False
    assert "no panel mapping" in res["response"]["error"]
    assert rpc.calls == []


def test_demand_limit_calls_ilc():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 103,
        "action_type": "curtail",
        "target": {"kind": "demand_limit"},
        "params": {"kw": 4.5},
    })
    assert res["success"] is True
    assert rpc.calls[0][0] == "platform.ilc"
    assert rpc.calls[0][1] == "update_configurations"
    assert rpc.calls[0][2][0] == {"config": {"demand_limit": 4.5}}


def test_thermostat_setpoint_calls_ecobee():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 104,
        "action_type": "setpoint_adjust",
        "target": {"kind": "thermostat", "device_id": 7},
        "params": {"cool_setpoint": 78, "hold_type": "nextTransition"},
    })
    assert res["success"] is True
    identity, method, args = rpc.calls[0]
    assert identity == "ecobee_agent"
    assert method == "set_temperature"
    assert args == (None, 78, "nextTransition")


def test_plug_relay_calls_kasa():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 105,
        "action_type": "relay_toggle",
        "target": {"kind": "plug", "device_id": "kasa-3"},
        "params": {"enabled": False},
    })
    assert res["success"] is True
    identity, method, args = rpc.calls[0]
    assert identity == "kasa_plug"
    assert method == "control_device"
    assert args == ("kasa-3", "set_relay", 0)


def test_battery_mode_set_operating_mode_calls_panel_rpc():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 108,
        "action_type": "set_operating_mode",
        "target": {"kind": "battery_mode", "device_id": "HD31ZAS4HGAS0004"},
        "params": {"backupReserveSoc": 15, "smartBackupMode": 1},
    })
    assert res["success"] is True
    identity, method, args = rpc.calls[0]
    assert identity == "ecoflow_agent"
    assert method == "set_panel_mode_rpc"
    assert args[0] == "HD31ZAS4HGAS0004"
    assert args[1] == {"backupReserveSoc": 15, "smartBackupMode": 1}


def test_battery_mode_strips_device_sn_from_panel_params():
    rpc = FakeRpc()
    _translator(rpc).handle({
        "action_id": 109,
        "action_type": "set_operating_mode",
        "target": {"kind": "battery_mode"},
        "params": {"device_sn": "SN-9", "epsModeInfo": True},
    })
    identity, method, args = rpc.calls[0]
    assert method == "set_panel_mode_rpc"
    assert args[0] == "SN-9"
    assert args[1] == {"epsModeInfo": True}


def test_battery_mode_legacy_command_calls_control_rpc():
    rpc = FakeRpc()
    _translator(rpc).handle({
        "action_id": 110,
        "action_type": "battery_charge_mode",
        "target": {"kind": "battery_mode", "device_id": "SN-2"},
        "params": {"command": "set_charge_limit", "value": 90},
    })
    identity, method, args = rpc.calls[0]
    assert method == "control_device_rpc"
    assert args == ("SN-2", "set_charge_limit", 90)


def test_battery_mode_empty_params_fails():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 111,
        "action_type": "set_operating_mode",
        "target": {"kind": "battery_mode", "device_id": "SN-2"},
        "params": {},
    })
    assert res["success"] is False
    assert "panel-mode params" in res["response"]["error"]


def test_unroutable_kind_fails():
    rpc = FakeRpc()
    res = _translator(rpc).handle({
        "action_id": 106,
        "action_type": "curtail",
        "target": {"kind": "spaceship"},
        "params": {},
    })
    assert res["success"] is False
    assert "unroutable" in res["response"]["error"]


def test_rpc_exception_captured_as_failure():
    rpc = FakeRpc(raises=RuntimeError("ilc offline"))
    res = _translator(rpc).handle({
        "action_id": 107,
        "action_type": "curtail",
        "target": {"kind": "demand_limit"},
        "params": {"kw": 3.0},
    })
    assert res["success"] is False
    assert res["response"]["error"] == "ilc offline"
    assert res["action_id"] == 107


if __name__ == "__main__":
    import traceback

    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)
