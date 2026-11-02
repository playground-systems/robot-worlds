"""Prepare a small source SimReady hollow dish without model calls/simulation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from robot_worlds.kitchen_dish_library import prepare_kitchen_dish_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".robot-worlds/kitchen-dish-library"))
    parser.add_argument("--cache", type=Path, default=Path(".robot-worlds/kitchen-dish-preparation"))
    args = parser.parse_args()
    path = prepare_kitchen_dish_library(args.output, cache_dir=args.cache)
    print(json.dumps({"library": str(path), "status": "prepared_no_native_physics_validation"}))


if __name__ == "__main__":
    main()
