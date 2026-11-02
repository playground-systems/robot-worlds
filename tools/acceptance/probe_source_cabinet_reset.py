"""Bounded cabinet-only native reset evidence; never executes a robot policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import signal
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from robot_worlds.asset_library import AssetLibrary
from robot_worlds.models import Criterion, WorldSpec
from robot_worlds.reset_diagnostics import settling_check
from robot_worlds.runtime import Runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path(".robot-worlds/living-room-library-v2"))
    parser.add_argument("--baseline", type=Path, default=Path(".robot-worlds/manipulation-presets-2026-11-02/source-reset.json"))
    parser.add_argument("--output", type=Path, default=Path(".robot-worlds/living-room-preparation/cabinet-reset-v2.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Probe output already exists; retain immutable prior evidence")
    ceiling = 2*1024**3
    # macOS rejects a portable RLIMIT_DATA cap independently of getrlimit's
    # advertised unlimited value. Bound elapsed time and check actual peak RSS,
    # instead of imposing a virtual-address limit on mapped system frameworks.
    signal.alarm(30)
    library = AssetLibrary.open(args.library)
    sourced = library.load("fixture_cabinet_b01", prefix="cab")
    cabinet = sourced.asset
    spec = WorldSpec(name="Source cabinet reset probe", description="A source-only cabinet reset/settling check",
        task="Native reset evidence only; no policy or manipulation success is claimed.",
        bounds=(8, 8, 3), robot_start=(-2, -2, 0), objects=cabinet.objects,
        regions=cabinet.regions, rubric=[Criterion(id="unexecuted_drawer_goal", description="Unexecuted source drawer goal",
            predicate="joint_at", object_id=cabinet.joint_ids["part_01_obj_00"], threshold=.30, tolerance=.01)])
    start = time.monotonic()
    runtime = Runtime(spec, render=False)
    try:
        check = settling_check(runtime.reset_diagnostics)
        xml = ET.fromstring(runtime._xml)
        record = {"kind": "native cabinet-only reset/settling, not policy or grasp success",
            "seconds": time.monotonic()-start, "check": check,
            "source_library": str(args.library.resolve()),
            "library_sha256": hashlib.sha256((args.library/"library.json").read_bytes()).hexdigest(),
            "source_joint_collide_parent": {obj.id: obj.joint.collide_parent for obj in cabinet.objects if obj.joint},
            "compiled_body_pair_exclusions": len(xml.findall("contact/exclude")),
            "native_policy_executed": False, "render_enabled": False,
            "baseline": {"path": str(args.baseline),
                "initial_penetration_issues": sum(issue["kind"] == "initial_penetration"
                    for issue in json.loads(args.baseline.read_text())["check"]["issues"])},
            "implementation_fingerprint": runtime._implementation_fingerprint,
            "model_fingerprint": runtime._model_fingerprint}
    finally:
        runtime.close()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    signal.alarm(0)
    record["peak_resident_bytes"] = peak if sys.platform == "darwin" else peak*1024
    record["memory_budget_bytes"] = ceiling
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"output": str(args.output.resolve()), "passed": check["passed"],
        "errors": check["errors"], "seconds": record["seconds"],
        "peak_resident_bytes": record["peak_resident_bytes"],
        "compiled_body_pair_exclusions": record["compiled_body_pair_exclusions"]}))
    if not check["passed"] or record["peak_resident_bytes"] > ceiling:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
