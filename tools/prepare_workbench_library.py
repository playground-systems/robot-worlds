#!/usr/bin/env python3
"""Prepare one pinned fixed table; no model call or native simulation."""
import argparse
import json
from pathlib import Path

from robot_worlds.workbench_library import WORKBENCH_ID, prepare_workbench_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".robot-worlds/workbench-library"))
    parser.add_argument("--cache", type=Path, default=Path(".robot-worlds/workbench-preparation"))
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    output = prepare_workbench_library(args.output, cache_dir=args.cache, timeout=args.timeout)
    print(json.dumps({"path": str(output), "asset_ids": [WORKBENCH_ID],
        "status": "geometry_checked_not_physically_validated"}))


if __name__ == "__main__":
    main()
