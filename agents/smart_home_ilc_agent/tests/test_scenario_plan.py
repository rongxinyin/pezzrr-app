"""
Unit tests for the pure load-shed / battery-feasibility logic of scenario_plan.

These run without a database or VOLTTRON platform: they exercise the priority
shed selection and the islanding feasibility escalation on hand-built circuit
lists. The DB-bound parts (build_plan, advisory writes) are covered by the
standalone CLI run against test_home.

    ../../venv/bin/python3 -m tests.test_scenario_plan
    pytest agents/smart_home_ilc_agent/tests/test_scenario_plan.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart_home_ilc.scenario_plan import (  # noqa: E402
    _shed_set, _retained_load_w, escalate_shed, SHED_ORDER,
)


def _c(cid, ch, priority, power, critical=False, controllable=True):
    return {
        "circuit_id": cid, "channel_num": ch, "circuit_name": f"C{ch}",
        "circuit_priority": priority, "is_critical": critical,
        "is_controllable": controllable, "power_w": float(power),
    }


# test_home-like panel: critical (must-have), essential, non_essential.
def _panel():
    return [
        _c(1, 1, "critical", 100, critical=True, controllable=False),
        _c(2, 2, "critical", 200, critical=True, controllable=False),
        _c(3, 3, "essential", 300),
        _c(4, 4, "essential", 400),
        _c(5, 5, "non_essential", 500),
        _c(6, 6, "non_essential", 600),
    ]


def test_shed_order_excludes_critical():
    assert "critical" not in SHED_ORDER
    assert SHED_ORDER == ["non_essential", "essential"]


def test_shed_set_only_controllable_non_critical():
    circuits = _panel()
    # Asking to shed non_essential picks ch5+ch6 only.
    assert _shed_set(circuits, ["non_essential"]) == {5, 6}
    # Critical circuits are never shed even if named.
    assert _shed_set(circuits, ["critical"]) == set()
    # Essential + non_essential = everything controllable.
    assert _shed_set(circuits, ["essential", "non_essential"]) == {3, 4, 5, 6}


def test_retained_load_excludes_shed():
    circuits = _panel()
    shed = _shed_set(circuits, ["non_essential"])  # {5,6} = 1100W
    # total 2100W - 1100W = 1000W retained.
    assert _retained_load_w(circuits, shed) == 1000.0


def test_feasible_without_escalation():
    circuits = _panel()
    shed = _shed_set(circuits, ["non_essential"])
    shed2, tiers, retained, esc, feasible = escalate_shed(
        circuits, shed, ["non_essential"], max_out_w=7200, soc_ok=True)
    assert feasible is True
    assert esc == []                 # plenty of headroom, no escalation
    assert tiers == ["non_essential"]
    assert retained == 1000.0


def test_escalates_to_essential_when_over_limit():
    circuits = _panel()
    shed = _shed_set(circuits, ["non_essential"])  # retained 1000W
    # Inverter limit 700W: must shed essential too (retained -> 300W).
    shed2, tiers, retained, esc, feasible = escalate_shed(
        circuits, shed, ["non_essential"], max_out_w=700, soc_ok=True)
    assert tiers == ["non_essential", "essential"]
    assert {3, 4, 5, 6} <= shed2     # essential + non_essential shed
    assert retained == 300.0         # only critical (100+200) remains
    assert feasible is True
    assert esc and esc[-1]["tier"] == "essential"


def test_infeasible_when_critical_load_exceeds_inverter():
    circuits = _panel()
    shed = _shed_set(circuits, ["non_essential"])
    # Critical load alone is 300W; a 250W inverter can never be satisfied.
    _, tiers, retained, esc, feasible = escalate_shed(
        circuits, shed, ["non_essential"], max_out_w=250, soc_ok=True)
    assert feasible is False
    assert retained == 300.0         # critical can't be shed
    assert "critical" not in tiers


def test_infeasible_when_battery_soc_below_minimum():
    circuits = _panel()
    shed = _shed_set(circuits, ["non_essential"])
    # Load fits the inverter, but the battery is depleted -> can't island.
    _, _, _, _, feasible = escalate_shed(
        circuits, shed, ["non_essential"], max_out_w=7200, soc_ok=False)
    assert feasible is False


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
