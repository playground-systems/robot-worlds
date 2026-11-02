"""Package a native-checked Astra scene and render an inspection thumbnail.

No model calls or policy actions. The thumbnail is a disclosed aerial view of
the native reset; it is separate from policy observations and scene validation.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import shutil
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from robot_worlds.artifacts import file_digest, write_json
from robot_worlds.asset_library import AssetLibrary
from robot_worlds.sdk import load


def overview(runtime) -> bytes:
    import mujoco

    renderer = runtime._get_renderer(overview=True)
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = [0, 0, .6]
    width, depth, height = runtime.spec.bounds
    radius = math.sqrt((width/2)**2 + (depth/2)**2 + height**2)
    camera.distance = radius / math.sin(math.radians(runtime.model.vis.global_.fovy)/2)
    camera.azimuth, camera.elevation = 110, -65
    renderer.update_scene(runtime.data, camera=camera)
    output = io.BytesIO()
    Image.fromarray(renderer.render().copy()).save(output, format="PNG")
    return output.getvalue()


def freeze(creation: Path, authored: Path, identifier: str, destination: Path) -> dict:
    from robot_worlds.runtime import Runtime

    required_checks = {"schema", "geometry_and_rubric", "physics_settling",
                       "physics_reset", "initial_rubric", "render"}

    record = json.loads((creation / "creation.json").read_text())
    if record["status"] != "completed" or record["policy_runs"] != 0:
        raise ValueError("Expected completed scene-only preparation evidence")
    world = load(record["world_path"])
    if record["world_id"] != world.id:
        raise ValueError("Creation evidence identifies a different world")
    expected_inputs = record.get("authoring_input_sha256", {})
    required_inputs = {"generation-plan.json", "task-suite.json", "semantic-plan.md",
                       "asset-library/library.json"}
    if set(expected_inputs) != required_inputs or any(
            file_digest(authored / name) != expected_inputs[name] for name in required_inputs):
        raise ValueError("Authored inputs do not match native-checked creation evidence")
    from robot_worlds.task_catalog import TaskCatalog
    authored_tasks = TaskCatalog.model_validate_json((authored / "task-suite.json").read_text())
    if authored_tasks.tasks != world.tasks:
        raise ValueError("Task sidecar differs from the validated world catalog")
    validation = world.manifest["validation"]
    checks = {check["check"]: check for check in validation["checks"]}
    if (validation["status"] != "ready" or validation["profile"] != "strict"
            or not required_checks <= checks.keys()
            or not all(check["passed"] for check in checks.values())
            or not checks["physics_settling"]["evidence"]["complete"]):
        raise ValueError("Preset requires successful strict native checks")
    if (len(world.tasks) < 12 or len(record["task_reset_checks"]) != len(world.tasks)
            or {check["task_id"] for check in record["task_reset_checks"]} != {task.id for task in world.tasks}
            or not all(check["passed"] for check in record["task_reset_checks"])):
        raise ValueError("Preset requires at least twelve checked task variants")
    library = AssetLibrary.open(authored / "asset-library")
    sources = []
    for asset_id in library.asset_ids:
        source = library.load(asset_id, prefix="source_check").selected
        if not source.id.startswith("simready:"):
            raise ValueError("Non-SimReady source: " + asset_id)
        sources.append({"asset_id": asset_id, "source": asdict(source)})
    destination.mkdir(parents=True, exist_ok=True)
    # This additional reset produces an inspection image, never task success.
    runtime = Runtime(world.spec, asset_root=world.path)
    try:
        current = runtime.manifest
        current = current() if callable(current) else current
        if any(current.get(key) != world.manifest["runtime"].get(key)
               for key in ("model_fingerprint", "implementation_fingerprint")):
            raise ValueError("Runtime changed since native validation; revalidate before freezing")
        (destination / "thumbnail.png").write_bytes(overview(runtime))
    finally:
        runtime.close()
    world.export(destination / "world.zip")
    for name in ("generation-plan.json", "task-suite.json", "semantic-plan.md"):
        shutil.copyfile(authored / name, destination / name)
    evidence = {
        "id": identifier, "world_id": world.id,
        "authorship": "gpt-6-astra via Codex subagent",
        "validation": validation, "runtime": world.manifest["runtime"],
        "task_reset_checks": record["task_reset_checks"], "sources": sources,
        "source_library_sha256": file_digest(authored / "asset-library/library.json"),
        "policy_runs": 0, "autonomous_task_success_verified": False,
        "thumbnail": "Native MuJoCo aerial reset inspection. No geometry hidden; policy camera and world physics unchanged.",
    }
    write_json(destination / "preparation-evidence.json", evidence)
    entry = {
        "id": identifier, "name": world.spec.name,
        "description": world.spec.description,
        "repo": "nvidia/simready-assets",
        "revision": "4763fbfaa4f8e97658e68b57d01b4c86659e08b5",
        "optimized_revision": "e52172290393dde80e8d383861db94b8d0e92ec3",
        "world": "generation-plan.json", "license": "CC BY-4.0",
        "source_url": "https://huggingface.co/datasets/nvidia/simready-assets",
        "world_id": world.id, "bundle_sha256": file_digest(destination / "world.zip"),
        "bundle_bytes": (destination / "world.zip").stat().st_size,
        "bounds": list(world.spec.bounds), "components": len(world.spec.objects),
        "source_instances": len(json.loads((authored / "generation-plan.json").read_text())["prepared_assets"]),
        "task_count": len(world.tasks), "authored_by": "gpt-6-astra",
        "native_engine_version": world.manifest["runtime"]["engine_version"],
        "thumbnail_view": evidence["thumbnail"],
        "thumbnail_hidden_object_ids": [], "thumbnail_hidden_runtime_geoms": [],
        "interaction_evidence": "Native reset and task-reference checks; policy completion unverified",
    }
    write_json(destination / "release.json", entry)
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", required=True, type=Path)
    parser.add_argument("--authored", required=True, type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(freeze(args.creation, args.authored, args.id, args.destination)))


if __name__ == "__main__":
    main()
