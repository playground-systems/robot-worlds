"""Prepare curated source furniture/props without model calls or simulation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from robot_worlds.living_room_library import prepare_living_room_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".robot-worlds/living-room-library"))
    parser.add_argument("--cache", type=Path, default=Path(".robot-worlds/living-room-preparation/sources"))
    parser.add_argument("--asset", action="append", dest="assets")
    args = parser.parse_args()
    path = prepare_living_room_library(args.output, cache_dir=args.cache, asset_ids=args.assets,
        on_event=lambda event: print(json.dumps(event), flush=True))
    print(json.dumps({"library": str(path), "status": "prepared_no_native_physics_validation"}))


if __name__ == "__main__":
    main()
