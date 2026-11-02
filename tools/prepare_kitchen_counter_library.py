#!/usr/bin/env python3
"""Prepare the source kitchen counter without model calls or native simulation."""
import argparse
import json
from pathlib import Path

from robot_worlds.kitchen_counter_library import KITCHEN_COUNTER_ID, prepare_kitchen_counter_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".robot-worlds/kitchen-counter-library"))
    parser.add_argument("--cache", type=Path, default=Path(".robot-worlds/kitchen-counter-preparation"))
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    output = prepare_kitchen_counter_library(args.output, cache_dir=args.cache, timeout=args.timeout)
    print(json.dumps({"path": str(output), "asset_ids": [KITCHEN_COUNTER_ID],
        "status": "geometry_checked_not_physically_validated"}))


if __name__ == "__main__":
    main()
