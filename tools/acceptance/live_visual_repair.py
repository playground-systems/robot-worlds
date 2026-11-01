"""Live, scoped UV-resource repair → sensor-only evaluation → portable evidence.

No layout is authored here. The parent is a saved live-generated environment;
the replacement OBJ must retain exactly the original vertices and face topology.
"""

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path

from robot_worlds import LLMAgent, load
from robot_worlds.artifacts import public_error
from robot_worlds.codex_client import CodexClient
from robot_worlds.editing import materialize
from robot_worlds.llm import audit_value
from robot_worlds.sdk import Report


def geometry(path):
    lines = path.read_text().splitlines()
    return (
        [line for line in lines if line.startswith("v ")],
        [
            [part.split("/")[0] for part in line.split()[1:]]
            for line in lines
            if line.startswith("f ")
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True, type=Path)
    parser.add_argument("--mesh", required=True, type=Path)
    parser.add_argument("--objects", required=True, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    parent = load(args.world)
    before = materialize(parent.spec).model_dump(mode="json")
    selected = {o["id"]: o for o in before["objects"] if o["id"] in args.objects}
    assert len(selected) == len(args.objects)
    replacement = args.mesh.resolve()
    for obj in selected.values():
        assert not obj["collision"] and not obj["movable"] and obj["kind"] == "mesh"
        assert geometry(parent.path / obj["mesh_path"]) == geometry(replacement), (
            "Geometry/topology changed"
        )
    clients = {
        "edit": CodexClient(
            max_calls=2,
            max_seconds=360,
            timeout=180,
            max_input_chars=240000,
            max_total_tokens=150000,
        ),
        "policy": CodexClient(max_calls=6, max_seconds=240, timeout=120, max_total_tokens=250000),
    }
    record = {
        "workflow_completed": False,
        "task_success": False,
        "parent_world_id": parent.id,
        "scope": args.objects,
        "repair": "Complete texture coordinates on all faces of supplied architectural lettering",
        "original_vertices_and_topology_preserved": True,
        "replacement_sha256": hashlib.sha256(replacement.read_bytes()).hexdigest(),
        "transport": "codex",
        "model": "gpt-6-astra",
        "events": [],
    }

    def save():
        record.update(
            elapsed_seconds=time.monotonic() - started,
            model_usage={phase: audit_value(c.usage) for phase, c in clients.items()},
        )
        (args.output / "acceptance.json").write_text(json.dumps(record, indent=2) + "\n")

    def event(value):
        if value.get("type") in {
            "edit_started",
            "world_created",
            "validation",
            "step",
            "episode_finished",
        }:
            record["events"].append(
                {"type": value["type"], "elapsed_seconds": time.monotonic() - started}
            )
            print(value["type"], flush=True)
            save()

    try:
        world = parent.refine(
            "For exactly these visual-only objects "
            + ", ".join(args.objects)
            + ", replace only mesh_path with this explicitly supplied corrected local OBJ: "
            + str(replacement)
            + ". Its vertex positions and face vertex indices are identical; only missing UV indices were added. "
            "Preserve every other field on these objects, every unrelated entity, layout, task, rubric, physics and robot start. "
            "Do not change textures, labels, parents, sizes, colors, positions, assumptions or description. This fixes native texture mapping.",
            scope=args.objects,
            allow_physics_changes=True,
            client=clients["edit"],
            max_repairs=1,
            output=args.output / "worlds",
            on_event=event,
        )
        after = materialize(world.spec).model_dump(mode="json")
        old = {o["id"]: o for o in before["objects"]}
        new = {o["id"]: o for o in after["objects"]}
        assert old.keys() == new.keys()
        for oid, obj in old.items():
            expected = dict(obj)
            if oid in selected:
                expected["mesh_path"] = new[oid]["mesh_path"]
                assert (
                    hashlib.sha256((world.path / new[oid]["mesh_path"]).read_bytes()).hexdigest()
                    == record["replacement_sha256"]
                )
            assert new[oid] == expected, "Unexpected object change: " + oid
        for field in (
            "regions",
            "task",
            "rubric",
            "robot",
            "robot_start",
            "bounds",
            "process_cycles",
        ):
            assert before[field] == after[field], "Changed protected field: " + field
        assert world.manifest["validation"]["status"] == "ready"
        record.update(
            world_id=world.id,
            exact_scope_and_geometry_verified=True,
            validation_checks=[
                {"check": c["check"], "passed": c["passed"]}
                for c in world.manifest["validation"]["checks"]
            ],
        )
        report = world.evaluate(
            LLMAgent(client=clients["policy"], max_steps=6, max_seconds=240),
            max_steps=6,
            max_sim_seconds=60,
            max_replay_frames=12,
            output=args.output / "runs",
            on_event=event,
        )
        record.update(
            report_id=report.path.name,
            summary=report.summary,
            task_success=report.summary["successes"] > 0,
            termination=report.result["episodes"][0]["termination"],
        )
        for artifact, name in ((world, "world"), (report, "report")):
            archive = artifact.export(args.output / (name + ".zip"))
            with zipfile.ZipFile(archive) as z:
                z.extractall(args.output / ("restored-" + name))
        restored = load(args.output / "restored-world")
        saved = Report(args.output / "restored-report")
        assert restored.id == world.id and saved.summary == report.summary
        assert saved.regrade(restored, output=args.output / "regraded").summary == report.summary
        record.update(
            workflow_completed=report.summary["valid"] == 1, portable_reload_and_regrade=True
        )
        # These are verification copies of the retained archives, not originals.
        for name in ("restored-world", "restored-report", "regraded"):
            shutil.rmtree(args.output / name)
        record["temporary_verification_copies_removed"] = True
    except Exception as error:
        record["error"] = public_error(error)
    finally:
        save()
        print(
            json.dumps(
                {
                    k: record.get(k)
                    for k in ("workflow_completed", "task_success", "elapsed_seconds", "error")
                }
            ),
            flush=True,
        )
    return 0 if record["workflow_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
