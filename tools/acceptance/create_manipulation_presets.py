"""Author a sourced scene and its task suite in one bounded Astra workflow.

The model owns spatial composition and task rubrics. This script records original
responses and feeds static/native failures back through the existing generator.
It does not run a robot policy or assert task solvability.
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

from robot_worlds.artifacts import file_digest, public_error, write_json
from robot_worlds.asset_library import AssetLibrary
from robot_worlds.codex_client import CodexClient
from robot_worlds.generator import generate_spec
from robot_worlds.sdk import World
from robot_worlds.task_catalog import TaskCatalog, TaskVariant


class SceneAuthor:
    def __init__(self, output: Path):
        self.client = CodexClient(model="gpt-6-astra", reasoning="high", max_calls=4,
                                  max_seconds=1800, timeout=480,
                                  max_input_chars=700000, max_total_tokens=600000)
        self.output = output
        self.tasks = []
        self.semantic_plan = ""

    @property
    def usage(self):
        return self.client.usage

    @property
    def budget(self):
        return self.client.budget

    def complete_json(self, instructions, prompt, schema):
        world_schema = copy.deepcopy(schema)
        task_schema = TaskVariant.model_json_schema()
        definitions = world_schema.pop("$defs", {})
        for name, definition in task_schema.pop("$defs", {}).items():
            if name in definitions and definitions[name] != definition:
                raise ValueError("Conflicting task/world schema definition: " + name)
            definitions[name] = definition
        combined = {"type": "object", "properties": {
            "world": world_schema,
            "tasks": {"type": "array", "minItems": 12, "maxItems": 32,
                      "items": task_schema},
            "semantic_plan": {"type": "string"}},
            "required": ["world", "tasks", "semantic_plan"],
            "additionalProperties": False, "$defs": definitions}
        if self.tasks:
            prompt += "\nPrevious task variants (repair their errors too):\n" + json.dumps(self.tasks)
        instructions += (
            "\nReturn world, tasks and semantic_plan together. Author at least 12 DISTINCT useful "
            "task variants over exactly this world's unchanged initial physical configuration. "
            "Each task has an instruction and executable rubric using actual materialized IDs. "
            "Include separate grasp/place, transport, physical hinge or slide operation and "
            "ordered multi-step tasks wherever the supplied mechanisms support them. Include "
            "pulling and pushing an actual available drawer/door, not moving fixed scenery. "
            "Vary operations and destinations, not merely task titles. Avoid goals already true "
            "at reset. For a close task starting closed, require open then close in sequence. "
            "Do not invent predicates, object IDs, joints, processes or task-specific reset poses. "
            "Task text must describe ordinary operator instructions without coordinates or hidden "
            "state. semantic_plan explains furniture orientation, object supports, accessible "
            "handles, door/drawer sweeps and connected walking routes. Be honest about unsupported "
            "operations. No cutting, liquids, deformable objects, pretend buttons or powered "
            "transport on passive conveyors. The world.task/rubric is one flagship mission; "
            "other tasks may reuse its regions and physical objects."
        )
        response = self.client.complete_json(instructions, prompt, combined)
        number = self.client.usage["calls"]
        write_json(self.output / f"astra-response-{number}.json", response)
        self.tasks, self.semantic_plan = response["tasks"], response["semantic_plan"]
        return response["world"]


class SavedSceneAuthor:
    """Replay an Astra-authored plan after deterministic source/code repairs."""
    def __init__(self, plan: Path, tasks: Path, semantic_plan: Path | None):
        self.plan = json.loads(plan.read_text())
        self.tasks = json.loads(tasks.read_text())["tasks"]
        self.semantic_plan = semantic_plan.read_text() if semantic_plan else ""
        self.usage = {"calls": 0, "validation_model_calls": 0,
                      "authorship": "gpt-6-astra Codex subagent; separate authoring context"}
        self.budget = {"max_calls": 0, "mode": "saved Astra plan; real native validation"}

    def complete_json(self, *args):
        return copy.deepcopy(self.plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-objects", type=int, default=1800)
    parser.add_argument("--saved-plan", type=Path)
    parser.add_argument("--task-suite", type=Path)
    parser.add_argument("--semantic-plan", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    if bool(args.saved_plan) != bool(args.task_suite):
        parser.error("--saved-plan and --task-suite must be supplied together")
    author = (SavedSceneAuthor(args.saved_plan, args.task_suite, args.semantic_plan)
              if args.saved_plan else SceneAuthor(args.output))
    library = AssetLibrary.open(args.library)
    for asset_id in library.asset_ids:
        if not library.load(asset_id, prefix="source_check").selected.id.startswith("simready:"):
            raise ValueError("Only sourced SimReady library entries are permitted: " + asset_id)
    record = {"status": "running", "model": "gpt-6-astra",
              "transport": "saved Astra subagent plan" if args.saved_plan else "codex",
              "prompt": args.prompt.read_text(), "library": str(args.library.resolve()),
              "policy_runs": 0, "autonomous_task_success_verified": False, "events": []}
    if args.saved_plan:
        record["authoring_input_sha256"] = {
            "generation-plan.json": file_digest(args.saved_plan),
            "task-suite.json": file_digest(args.task_suite),
            "semantic-plan.md": file_digest(args.semantic_plan) if args.semantic_plan else None,
            "asset-library/library.json": file_digest(args.library / "library.json"),
        }
    latest = None

    def save():
        record.update(elapsed_seconds=time.monotonic()-started, usage=author.usage,
                      budget=author.budget)
        write_json(args.output / "creation.json", record)

    def event(value):
        # Candidates and original responses already have their own files.
        compact = {key: value[key] for key in ("type", "attempt", "errors", "phase") if key in value}
        record["events"].append(compact)
        print(json.dumps(compact), flush=True)
        save()

    def candidate(spec):
        nonlocal latest
        latest = World.from_spec(spec, args.output / "worlds", validate=False)
        record["preview_world_id"] = latest.id
        save()

    def validate(spec):
        nonlocal latest
        try:
            TaskCatalog(tasks=author.tasks).validate_for(spec)
        except ValueError as error:
            return ["Task catalog: " + str(error)]
        reset = {}
        def runtime_factory(*positional, **kwargs):
            from robot_worlds.runtime import Runtime
            runtime = Runtime(*positional, **kwargs)
            reset.update(runtime.snapshot())
            return runtime
        latest = World.from_spec(spec, args.output / "worlds", runtime_factory=runtime_factory,
                                 task_catalog=TaskCatalog(tasks=author.tasks))
        record["world_id"] = latest.id
        save()
        errors = []
        for check in latest.manifest["validation"]["checks"]:
            if not check["passed"]:
                errors.extend(check.get("errors", []))
                if check.get("error"):
                    errors.append(check["error"]["message"])
                if not check.get("errors") and not check.get("error"):
                    errors.append("Validation failed: " + check["check"])
        if reset:
            from robot_worlds.rubric import measure_predicate
            task_checks = []
            for task in TaskCatalog(tasks=author.tasks).tasks:
                task_spec = spec.model_copy(deep=True)
                task_spec.task, task_spec.rubric = task.task, task.rubric
                initial_errors = []
                for criterion in task.rubric:
                    measured = measure_predicate(task_spec, criterion, reset)["satisfied"]
                    if measured is None:
                        initial_errors.append("No runtime measurement: " + criterion.id)
                    if criterion.required and criterion.intent == "achieve" and not criterion.after and measured is True:
                        initial_errors.append("Required achievement is already true at reset: " + criterion.id)
                task_checks.append({"task_id": task.id, "passed": not initial_errors, "errors": initial_errors})
                errors.extend("Task " + task.id + ": " + error for error in initial_errors)
            record["task_reset_checks"] = task_checks
            save()
        return errors

    save()
    try:
        generate_spec(record["prompt"], client=author, max_repairs=0 if args.saved_plan else 2,
                      asset_library=library, online_assets=False,
                      allowed_asset_ids=list(library.asset_ids),
                      max_objects=args.max_objects, on_candidate=candidate,
                      validator=validate, event_callback=event)
        if latest.manifest["validation"]["status"] != "ready":
            raise ValueError("Generated scene has unresolved native checks")
        world = latest.with_tasks(author.tasks, output=args.output / "worlds")
        archive = world.export(args.output / "world.zip")
        write_json(args.output / "task-suite.json", {"tasks": author.tasks})
        (args.output / "semantic-plan.txt").write_text(author.semantic_plan + "\n")
        record.update(status="completed", world_id=world.id, world_path=str(world.path),
                      archive=str(archive), tasks=len(author.tasks),
                      objects=len(world.spec.objects), validation=world.manifest["validation"])
    except Exception as error:
        record.update(status="incomplete", error=public_error(error))
        if latest is not None:
            record.update(world_path=str(latest.path), validation=latest.manifest["validation"])
        raise
    finally:
        save()
        print(json.dumps({key: record.get(key) for key in
                          ("status", "world_id", "world_path", "tasks", "error")}), flush=True)


if __name__ == "__main__":
    main()
