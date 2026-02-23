"""
CLI entry point for data collectors.

Usage:
    python -m data_collectors.run seed           # Seed home + devices + circuits
    python -m data_collectors.run collect        # Start 1-minute polling loops
    python -m data_collectors.run seed-collect   # Both
"""

import logging
import sys


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  [%(threadName)s]  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd in ("seed", "seed-collect"):
        from .seed_data import seed
        seed()

    if cmd in ("collect", "seed-collect"):
        from .collector import DataCollector
        DataCollector().start()

    if cmd not in ("seed", "collect", "seed-collect"):
        print(f"Unknown command: {cmd}")
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
