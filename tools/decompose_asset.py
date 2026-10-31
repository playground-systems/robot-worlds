"""Prepare collision geometry with the SDK's bounded worker and verification.

Requires the preparation extra for new concave sources. Original visuals are
unchanged. Run native preparation on CI/a capable worker, not during rendering.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from robot_worlds.collision_preparation import prepare_collision_mesh


def decompose(source: Path, output: Path, *, timeout=120):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty conversion output directory")
    with tempfile.TemporaryDirectory(prefix="robot-worlds-conversion-") as temporary:
        result = prepare_collision_mesh(source, temporary, timeout=timeout)
        shutil.copytree(result.manifest_path.parent, output, dirs_exist_ok=True)
        manifest = json.loads(result.manifest_path.read_text())
        print(json.dumps({"parts": result.parts, "volume_m3": sum(part["volume_m3"] for part in manifest["parts"]),
                          "method": result.method, "output": str(output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    decompose(args.source, args.output, timeout=args.timeout)
