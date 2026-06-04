"""
Advisory cycle for the smart-home ILC.

Per configured home this resolves the operation scenario (which also builds and
persists the 24 h load forecast via the supervisor), then writes MPC or RBC
thermostat setpoint *advisories* to control_advisories in shadow mode. No
commands are ever sent to any device -- this layer only recommends and logs.

This is the VOLTTRON-free core of the agent's run_mpc_advisory / run_rbc_advisory
periodics, factored out so it can be driven two ways:
  - by the VOLTTRON agent's core.periodic hooks (production), and
  - by run_advisory.py as a standalone loop beside the data collector.

Nothing here imports volttron, so it runs under a plain venv python.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _modules():
    try:
        from . import mpc_data, mpc_controller, rbc_controller, hvac_supervisor
    except ImportError:  # standalone (run from the agent dir, not as a package)
        import mpc_data
        import mpc_controller
        import rbc_controller
        import hvac_supervisor
    return mpc_data, mpc_controller, rbc_controller, hvac_supervisor


def _scenario_plan_module():
    try:
        from . import scenario_plan
    except ImportError:
        import scenario_plan
    return scenario_plan


def run_mpc_advisory(cfg=None, conn=None):
    """Compute thermostat MPC setpoint advisories (shadow mode) and log them to
    control_actions.

    Iterates every configured home, resolves its operation scenario via the
    supervisor, and runs the MPC only for homes whose resolved action is 'mpc'
    (an MPC-strategy home in normal or load-peak management). Homes in
    capacity/resiliency shed are handled by run_rbc_advisory instead.

    Advisory only: recommendations land in control_advisories, nothing is sent to
    the thermostats. Homes without a fitted RC model or recent data are skipped
    with a warning. Opens its own config/conn
    when not supplied (so the standalone runner can share one across RBC+MPC)."""
    mpc_data, mpc_controller, _rbc, hvac_supervisor = _modules()
    own_conn = conn is None
    if cfg is None:
        cfg = mpc_data._load_json("mpc_config.json")
    conn = conn or mpc_data._connect()
    try:
        for home_name in cfg.get("homes", {}):
            try:
                scen = hvac_supervisor.resolve_scenario(home_name, cfg, conn)
                strat = hvac_supervisor.home_strategy(home_name, cfg)
                if hvac_supervisor.scenario_action(scen["scenario"], strat) != "mpc":
                    continue  # band_widen/baseline homes -> run_rbc_advisory
                inp = mpc_data.build_inputs(home_name, mpc_cfg=cfg, conn=conn)
                result = mpc_controller.solve_mpc(inp, mpc_cfg=cfg)
                if result.get("status") != "ok":
                    log.warning(f"MPC[{home_name}] no solution: "
                                f"{result.get('termination')}")
                    continue
                result["operation_scenario"] = scen["scenario"]
                action_id = mpc_data.write_advisory(inp, result, conn=conn)
                log.info(
                    f"MPC advisory[{home_name}] scenario={scen['scenario']} "
                    f"action_id={action_id} solver={result['solver']} "
                    f"cool_setpoint={result.get('immediate_cool_setpoint_c')}C "
                    f"cost=${result['expected_cost_usd']} "
                    f"energy={result['expected_energy_kwh']}kWh "
                    f"comfort_viol={result['comfort_violation_degC_steps']}")
            except SystemExit as e:
                # build_inputs raises SystemExit for a missing model/data.
                log.warning(f"MPC[{home_name}] skipped: {e}")
            except Exception as e:
                log.error(f"MPC[{home_name}] failed: {e}")
    finally:
        if own_conn:
            conn.close()


def run_rbc_advisory(cfg=None, conn=None):
    """Scenario-driven band-widening control (shadow mode) for every home whose
    resolved action is 'band_widen' or 'baseline'.

    Resolves each home's operation scenario via the supervisor. For band_widen
    homes (RBC home in a DR/peak event, or any home in capacity/resiliency shed)
    it widens the thermostat deadband by the per-scenario offsets so the HVAC
    coasts to idle; for baseline homes (RBC home in normal) it recommends the
    unchanged setpoints. To keep the audit log clean it only writes when the
    resolved scenario changes for a device, so the loop can run often without
    flooding control_advisories. Opens its own config/conn when not supplied."""
    mpc_data, _mpc, rbc_controller, hvac_supervisor = _modules()
    own_conn = conn is None
    if cfg is None:
        cfg = mpc_data._load_json("mpc_config.json")
    conn = conn or mpc_data._connect()
    try:
        for home_name in cfg.get("homes", {}):
            try:
                scen = hvac_supervisor.resolve_scenario(home_name, cfg, conn)
                strat = hvac_supervisor.home_strategy(home_name, cfg)
                action = hvac_supervisor.scenario_action(scen["scenario"], strat)
                if action not in ("band_widen", "baseline"):
                    continue  # 'mpc' homes -> run_mpc_advisory
                cool_off, heat_off = (
                    hvac_supervisor.scenario_offsets(scen["scenario"], cfg)
                    if action == "band_widen" else (0.0, 0.0))
                res = rbc_controller.relax_setpoints(
                    home_name, cool_off, heat_off, mpc_cfg=cfg, conn=conn,
                    scenario=scen["scenario"],
                    triggered_by=hvac_supervisor.SCENARIO_TRIGGERED_BY.get(
                        scen["scenario"], "ILC_agent"),
                    active_events_brief=None)
                res["scenario_source"] = scen["source"]
                res["scenario_reason"] = scen["reason"]
                prev = hvac_supervisor.last_logged_scenario(conn, res["device_id"])
                if prev == scen["scenario"]:
                    continue  # no scenario transition -> nothing new to log
                action_id = rbc_controller.write_rbc_advisory(res, mpc_cfg=cfg, conn=conn)
                log.info(
                    f"RBC advisory[{home_name}] scenario={scen['scenario']} "
                    f"({scen['source']}) action={action} action_id={action_id} "
                    f"cool {res['baseline_cool_setpoint_c']}->{res['recommended_cool_setpoint_c']}C "
                    f"heat {res['baseline_heat_setpoint_c']}->{res['recommended_heat_setpoint_c']}C "
                    f"idle_expected={res['hvac_expected_idle']}")
            except SystemExit as e:
                log.warning(f"RBC[{home_name}] skipped: {e}")
            except Exception as e:
                log.error(f"RBC[{home_name}] failed: {e}")
    finally:
        if own_conn:
            conn.close()


def run_scenario_advisory(cfg=None, conn=None):
    """Full-home scenario sequences (shadow mode) for every configured home.

    For each home this resolves the operation scenario (shared with the HVAC
    layer) and builds the panel battery-mode operation sequence via scenario_plan,
    logging it to control_advisories (controller='ilc'). The HVAC setpoint advisory
    itself is still written by run_mpc_advisory/run_rbc_advisory; this layer adds
    the panel battery mode (Savings + EPS backup) around it.

    Like the RBC layer it dedups on scenario transition per panel, so the
    periodic can run often without flooding control_advisories. Opens its own
    config/conn when not supplied."""
    mpc_data, _mpc, _rbc, _sup = _modules()
    scenario_plan = _scenario_plan_module()
    own_conn = conn is None
    if cfg is None:
        cfg = mpc_data._load_json("mpc_config.json")
    conn = conn or mpc_data._connect()
    try:
        for home_name in cfg.get("homes", {}):
            try:
                plan = scenario_plan.build_plan(home_name, cfg=cfg, conn=conn)
                pdev = plan["panel_device_id"]
                if pdev is None:
                    log.warning(f"ILC[{home_name}] no smart_panel device; skipped")
                    continue
                prev = scenario_plan.last_logged_ilc_scenario(conn, pdev)
                if prev == plan["operation_scenario"]:
                    continue  # no scenario transition -> nothing new to log
                advisory_id = scenario_plan.write_plan_advisory(plan, cfg=cfg, conn=conn)
                bm = plan["battery_mode"]
                log.info(
                    f"ILC plan[{home_name}] scenario={plan['operation_scenario']} "
                    f"({plan['scenario_source']}) advisory_id={advisory_id} "
                    f"savings={bm['savings_mode']} eps_backup={bm['eps_backup']}")
            except SystemExit as e:
                log.warning(f"ILC[{home_name}] skipped: {e}")
            except Exception as e:
                log.error(f"ILC[{home_name}] failed: {e}")
    finally:
        if own_conn:
            conn.close()
