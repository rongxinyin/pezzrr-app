"""
Standalone advisory loop -- runs the smart-home ILC advisory cycle beside the
data collector, without a VOLTTRON platform.

Each tick resolves every configured home's operation scenario (which also
persists the 24 h load forecast), and writes MPC / RBC thermostat setpoint
*advisories* to control_advisories. Shadow mode only: no commands are ever sent
to any device. This drives the same advisory_cycle code the VOLTTRON agent uses; it
just supplies the scheduling the platform would otherwise provide.

RBC and MPC keep the agent's two cadences (RBC every 300 s to catch DR/outage
events quickly; MPC every 900 s, matching the control dt). RBC runs first each
tick so MPC sees an up-to-date scenario.

Usage (from the agent dir, beside the collector):
    ../../venv/bin/python3 -m smart_home_ilc.run_advisory                 # loop
    ../../venv/bin/python3 -m smart_home_ilc.run_advisory --once          # one cycle
    ../../venv/bin/python3 -m smart_home_ilc.run_advisory --mpc-interval 900 --rbc-interval 300

Detached, mirroring the collector's nohup pattern:
    nohup ../../venv/bin/python3 -m smart_home_ilc.run_advisory >> advisory.log 2>&1 &
"""

from __future__ import annotations

import argparse
import logging
import time

try:
    from . import advisory_cycle, mpc_data
except ImportError:  # run from the agent dir, not as an installed package
    import advisory_cycle
    import mpc_data

log = logging.getLogger("smart_home_ilc.run_advisory")


def run_once():
    """Run RBC then MPC once, sharing a single DB connection."""
    cfg = mpc_data._load_json("mpc_config.json")
    conn = mpc_data._connect()
    try:
        advisory_cycle.run_rbc_advisory(cfg, conn)
        advisory_cycle.run_mpc_advisory(cfg, conn)
    finally:
        conn.close()


def loop(rbc_interval, mpc_interval):
    """Fire RBC and MPC on independent cadences until interrupted."""
    log.info("Advisory loop ready: RBC every %ds, MPC every %ds (shadow mode)",
             rbc_interval, mpc_interval)
    next_rbc = next_mpc = 0.0
    while True:
        now = time.monotonic()
        if now >= next_rbc or now >= next_mpc:
            cfg = mpc_data._load_json("mpc_config.json")
            conn = mpc_data._connect()
            try:
                if now >= next_rbc:
                    advisory_cycle.run_rbc_advisory(cfg, conn)
                    next_rbc = now + rbc_interval
                if now >= next_mpc:
                    advisory_cycle.run_mpc_advisory(cfg, conn)
                    next_mpc = now + mpc_interval
            except Exception:
                log.exception("Advisory tick failed; will retry next cadence")
            finally:
                conn.close()
        time.sleep(max(1.0, min(next_rbc, next_mpc) - time.monotonic()))


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="Run a single cycle and exit.")
    ap.add_argument("--rbc-interval", type=int, default=300,
                    help="Seconds between RBC advisory cycles (default: 300).")
    ap.add_argument("--mpc-interval", type=int, default=900,
                    help="Seconds between MPC advisory cycles (default: 900).")
    args = ap.parse_args()

    if args.once:
        run_once()
    else:
        try:
            loop(args.rbc_interval, args.mpc_interval)
        except KeyboardInterrupt:
            log.info("Advisory loop stopped.")


if __name__ == "__main__":
    main()
