"""Typed scene patches, deterministic scope checks and bounded physical repair."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field

from .artifacts import digest, public_error, utc_now, write_json
from .generator import GenerationCancelled, GenerationError
from .large_scene import MAX_EXPANDED_OBJECTS, expand_assemblies
from .llm import ResponsesClient, audit_value, strict_schema
from .models import (
    Criterion,
    Identifier,
    JointSpec,
    Model,
    Number,
    ProcessCycle,
    Region,
    RevolvedShape,
    SceneObject,
    Size3,
    Vec3,
    WorldSpec,
)
from .rubric import rigid_members


class ObjectValues(Model):
    label: str | None = None
    kind: Literal["box", "sphere", "cylinder", "mesh", "revolved"] | None = None
    position: Vec3 | None = None
    size: Size3 | None = None
    color: Vec3 | None = None
    mass: Number | None = None
    movable: bool | None = None
    friction: Number | None = None
    parent_id: Identifier | None = None
    joint: JointSpec | None = None
    mesh_path: str | None = None
    revolved: RevolvedShape | None = None


class AddObject(Model):
    op: Literal["add_object"]
    object: SceneObject


class UpdateObject(Model):
    op: Literal["update_object"]
    id: Identifier
    fields: list[Literal["label", "kind", "position", "size", "color", "mass", "movable", "friction", "parent_id", "joint", "mesh_path", "revolved"]] = Field(min_length=1, max_length=13)
    values: ObjectValues


class RemoveObject(Model):
    op: Literal["remove_object"]
    id: Identifier


class RegionValues(Model):
    interior_of: Identifier | None = None
    label: str | None = None
    position: Vec3 | None = None
    size: Size3 | None = None


class AddRegion(Model):
    op: Literal["add_region"]
    region: Region


class UpdateRegion(Model):
    op: Literal["update_region"]
    id: Identifier
    fields: list[Literal["label", "position", "size", "interior_of"]] = Field(min_length=1, max_length=4)
    values: RegionValues


class RemoveRegion(Model):
    op: Literal["remove_region"]
    id: Identifier


class SetBounds(Model):
    op: Literal["set_bounds"]
    bounds: Size3


class SetRobotStart(Model):
    op: Literal["set_robot_start"]
    robot_start: Vec3


class SetTask(Model):
    op: Literal["set_task"]
    task: str = Field(min_length=1)


class SetRubric(Model):
    op: Literal["set_rubric"]
    rubric: list[Criterion] = Field(min_length=1, max_length=200)


class SetProcesses(Model):
    op: Literal["set_processes"]
    process_cycles: list[ProcessCycle] = Field(max_length=200)


Operation = (AddObject | UpdateObject | RemoveObject | AddRegion | UpdateRegion | RemoveRegion
             | SetBounds | SetRobotStart | SetTask | SetRubric | SetProcesses)


class ScenePatch(Model):
    operations: list[Operation] = Field(min_length=1, max_length=256)


INSTRUCTIONS = """Edit the existing physical scene with a small typed patch, never a replacement world.
Use persistent IDs exactly. Return only bounded operations in the supplied schema.
For update_object/update_region, fields explicitly lists the properties to change;
values for every unlisted property are ignored. Include complete joint data when
updating a joint, preserving its other properties if only its initial pose changes.
Preserve all unmentioned entities, the original request, task, rubric, process
requirements, robot preset and existing physics. The local applier enforces scope
and permission flags. Robot replacement is unsupported. Task/rubric/process edits
need allow_task_changes; existing mass/friction/movability/parent structure and
joint definition edits need allow_physics_changes. Joint.initial is a pose edit.
Task bindings are protected too: referenced regions cannot move or resize, bound
objects cannot disappear or change identity/shape, and destination/process fixture
anchors and their ancestors cannot move without allow_task_changes. Subject initial
placement may be repaired, but this does not certify preservation of task difficulty.
When scope is supplied, only those exact entity IDs may change, including new IDs.
Do not change world-level fields in a scoped edit. Coordinates are metres, Z up,
geometry centers, full extents. Regions do not collide. Keep physical supports,
open interiors, robot clearance and connected routes. A mesh_path must name an
actual user-supplied local mesh, never an invented path. No pose teleportation
controllers or claims that internal machine physics is implemented.
Repair validation failures with the smallest geometric/pose changes that preserve
the original edit request and all existing task requirements. Never drop rubric
criteria, remove original objects or regions, relax tolerances or alter physical properties to make a repair pass.
Diagnostics describe measured failures, not permission to modify unrelated IDs.
The compact context may omit distant entities: do not invent their state or IDs.
"""


def materialize(spec: WorldSpec) -> WorldSpec:
    result = expand_assemblies(spec, max_objects=MAX_EXPANDED_OBJECTS)
    # Materialized IDs become authoritative. Otherwise from_spec expansion would
    # restore edited/deleted generated parts or reject them as stale overrides.
    result.assembly_templates = []
    result.assembly_instances = []
    return result


def _entities(spec: WorldSpec) -> dict:
    result = {}
    for field in ("objects", "regions", "rubric", "process_cycles"):
        for entity in getattr(spec, field):
            # Criterion IDs can overlap object IDs; kind keeps diff rows distinct.
            result[(field, entity.id)] = entity.model_dump(mode="json")
    return result


def scene_diff(before: WorldSpec, after: WorldSpec) -> tuple[list[dict], dict]:
    old, new = _entities(before), _entities(after)
    changes = []
    counts = {"added": 0, "removed": 0, "changed": 0, "preserved": 0}

    def fields(entity_id, kind, previous, current, prefix=""):
        if isinstance(previous, dict) and isinstance(current, dict):
            for key in sorted(previous.keys() | current.keys()):
                fields(entity_id, kind, previous.get(key), current.get(key), prefix + "." + key if prefix else key)
        elif previous != current:
            changes.append({"entity_id": entity_id, "entity_kind": kind, "property": prefix,
                            "before": previous, "after": current})

    for key in sorted(old.keys() | new.keys()):
        kind, entity_id = key
        if key not in old:
            counts["added"] += 1
            fields(entity_id, kind, None, new[key], "$entity")
        elif key not in new:
            counts["removed"] += 1
            fields(entity_id, kind, old[key], None, "$entity")
        elif old[key] != new[key]:
            counts["changed"] += 1
            fields(entity_id, kind, old[key], new[key])
        else:
            counts["preserved"] += 1
    for key in ("bounds", "robot_start", "task", "robot"):
        previous, current = getattr(before, key), getattr(after, key)
        previous = list(previous) if isinstance(previous, tuple) else previous
        current = list(current) if isinstance(current, tuple) else current
        if previous != current:
            changes.append({"entity_id": "$world", "entity_kind": "world", "property": key,
                            "before": previous, "after": current})
    if any(row["entity_id"] == "$world" for row in changes):
        counts["changed"] += 1
    return changes, counts


def apply_patch(spec: WorldSpec, patch: ScenePatch | dict, *, scope: list[str] | None = None,
                allow_task_changes=False, allow_physics_changes=False,
                max_objects=MAX_EXPANDED_OBJECTS) -> WorldSpec:
    """Apply an all-or-nothing patch and enforce protection from actual changes."""
    before = materialize(spec)
    payload = before.model_dump(mode="json")
    plan = ScenePatch.model_validate(patch)
    if scope is not None and (not scope or len(scope) != len(set(scope))
                              or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", x) for x in scope)):
        raise ValueError("scope must contain unique persistent entity IDs")
    for operation in plan.operations:
        op = operation.model_dump(mode="json")
        name = op["op"]
        if name.startswith(("add_", "update_", "remove_")):
            field = "objects" if name.endswith("object") else "regions"
            singular = "object" if field == "objects" else "region"
            entities = payload[field]
            oid = op[singular]["id"] if name.startswith("add_") else op["id"]
            index = next((i for i, entity in enumerate(entities) if entity["id"] == oid), None)
            if name.startswith("add_"):
                if index is not None:
                    raise ValueError(f"{oid}: entity already exists; use update")
                entities.append(op[singular])
            elif index is None:
                raise ValueError(f"{oid}: cannot update/remove an unknown entity")
            elif name.startswith("remove_"):
                entities.pop(index)
            else:
                if len(op["fields"]) != len(set(op["fields"])):
                    raise ValueError(f"{oid}: duplicate update fields")
                entities[index].update({key: op["values"][key] for key in op["fields"]})
        else:
            field = {"set_bounds": "bounds", "set_robot_start": "robot_start", "set_task": "task",
                     "set_rubric": "rubric", "set_processes": "process_cycles"}[name]
            payload[field] = op[field]
    if len(payload["objects"]) > max_objects:
        raise ValueError(f"Scene exceeds the {max_objects}-object resource budget")
    after = WorldSpec.model_validate(payload)
    changes, _ = scene_diff(before, after)
    if scope is not None:
        outside = sorted({row["entity_id"] for row in changes} - set(scope))
        if outside:
            raise ValueError("Patch exceeds explicit scope: " + ", ".join(outside))
    enforce_protections(before, after, allow_task_changes=allow_task_changes,
                        allow_physics_changes=allow_physics_changes)
    if not changes:
        raise ValueError("Patch made no materialized scene changes")
    return after


def enforce_protections(before: WorldSpec, after: WorldSpec, *,
                        allow_task_changes=False, allow_physics_changes=False,
                        preserve_entities=False):
    """Compare with the original parent, including across retry boundaries."""
    if preserve_entities:
        for field in ("objects", "regions"):
            missing = {item.id for item in getattr(before, field)} - {item.id for item in getattr(after, field)}
            if missing:
                raise ValueError("Repair cannot remove original entities: " + ", ".join(sorted(missing)))
    if not allow_task_changes and any(getattr(before, key) != getattr(after, key) for key in ("task", "rubric", "process_cycles")):
        raise ValueError("Task, rubric and process definitions are protected; allow_task_changes is required")
    if not allow_task_changes:
        if before.robot_start != after.robot_start:
            raise ValueError("Robot start is a protected task initial condition; allow_task_changes is required")
        _protect_task_bindings(before, after)
    if before.robot != after.robot:
        raise ValueError("Robot preset changes are unsupported")
    if not allow_physics_changes:
        previous = {obj.id: obj for obj in before.objects}
        for obj in after.objects:
            if obj.id not in previous:
                continue
            old = previous[obj.id]
            for key in ("mass", "friction", "movable", "parent_id"):
                if getattr(old, key) != getattr(obj, key):
                    raise ValueError(f"{obj.id}: {key} is protected; allow_physics_changes is required")
            old_joint = old.joint.model_dump(exclude={"initial"}) if old.joint else None
            new_joint = obj.joint.model_dump(exclude={"initial"}) if obj.joint else None
            if old_joint != new_joint:
                raise ValueError(f"{obj.id}: joint definition is protected; only joint.initial pose may change")


def _protect_task_bindings(before: WorldSpec, after: WorldSpec):
    """Keep evaluator referents fixed, not merely their IDs in rubric text."""
    old_objects = {obj.id: obj for obj in before.objects}
    new_objects = {obj.id: obj for obj in after.objects}
    old_regions = {region.id: region for region in before.regions}
    new_regions = {region.id: region for region in after.regions}
    bound_objects, anchors, bound_regions = set(), set(), set()
    conditions = list(before.rubric)
    for process in before.process_cycles:
        anchors.add(process.fixture_id)
        bound_objects.update(process.workpiece_ids)
        conditions.extend(process.prerequisites)
    for condition in conditions:
        if condition.object_id in old_objects:
            bound_objects.add(condition.object_id)
        if condition.target_id in old_objects:
            anchors.add(condition.target_id)
        if condition.target_id in old_regions:
            bound_regions.add(condition.target_id)
    for rid in bound_regions:
        container = old_regions[rid].interior_of
        if container:
            bound_objects.add(container)
            anchors.add(container)
    bound_objects.update(anchors)
    ancestors = set()
    for oid in list(bound_objects):
        current, seen = old_objects.get(oid), set()
        while current is not None and current.parent_id is not None:
            parent = current.parent_id
            if parent in seen:
                break
            seen.add(parent)
            ancestors.add(parent)
            current = old_objects.get(parent)
    bound_objects.update(ancestors)
    anchors.update(ancestors)
    bound_roots = set(bound_objects)
    bound_objects = set().union(*(rigid_members(old_objects, oid) for oid in bound_objects))
    for rid in sorted(bound_regions):
        old, new = old_regions[rid], new_regions.get(rid)
        if new is None or old.position != new.position or old.size != new.size or old.interior_of != new.interior_of:
            raise ValueError(f"{rid}: task-bound region geometry/presence is protected; allow_task_changes is required")
    for oid in sorted(bound_objects):
        old, new = old_objects.get(oid), new_objects.get(oid)
        if old is None:
            continue  # Missing references are independently rejected by validation.
        if new is None:
            raise ValueError(f"{oid}: task-bound object presence is protected; allow_task_changes is required")
        for field in ("label", "color", "kind", "size", "mesh_path", "parent_id", "revolved"):
            if getattr(old, field) != getattr(new, field):
                raise ValueError(f"{oid}: task-bound object {field} is protected; allow_task_changes is required")
        if oid in anchors:
            pose_fields = {"type", "axis", "anchor", "initial"}
            old_pose = old.joint.model_dump(include=pose_fields) if old.joint else None
            new_pose = new.joint.model_dump(include=pose_fields) if new.joint else None
            if old.position != new.position or old_pose != new_pose:
                raise ValueError(f"{oid}: task destination/ancestor pose is protected; allow_task_changes is required")

        if old.parent_id and not old.joint and not old.movable:
            old_parent, new_parent = old_objects.get(old.parent_id), new_objects.get(old.parent_id)
            if old_parent and new_parent and any(abs((old.position[i]-old_parent.position[i]) -
                                                    (new.position[i]-new_parent.position[i])) > 1e-9 for i in range(3)):
                raise ValueError(f"{oid}: task-bound attached geometry pose is protected; allow_task_changes is required")
    for oid in bound_roots:
        if rigid_members(old_objects, oid) != rigid_members(new_objects, oid):
            raise ValueError(f"{oid}: task-bound rigid assembly membership is protected; allow_task_changes is required")


class EditingContextLimit(ValueError):
    """A complete unscoped inventory cannot fit the declared model input budget."""


def _validate_scope(scope):
    if scope is not None and (
        not isinstance(scope, list) or not scope or len(scope) > 1000
        or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", value) for value in scope)
        or len(scope) != len(set(scope))
    ):
        raise ValueError("scope must contain1 to1000 unique persistent entity IDs")


def compact_context(spec: WorldSpec, prompt: str, scope: list[str] | None, errors: list,
                    *, max_chars=85000) -> dict:
    """Prioritize selected/referenced entities without repeating a huge WorldSpec."""
    words = set(re.findall(r"[a-z0-9_-]{3,}", prompt.lower()))
    diagnostics = json.dumps(errors, ensure_ascii=False)[:12000]
    entities = _entities(spec)

    def relevance(item):
        (_, oid), data = item
        text = (oid + " " + str(data.get("label", ""))).lower()
        return (oid in (scope or []), oid in diagnostics, sum(word in text for word in words))

    ranked = sorted(entities.items(), key=relevance, reverse=True)
    context = {"name": spec.name, "bounds": spec.bounds, "robot": spec.robot,
               "robot_start": spec.robot_start, "task": spec.task,
               "original_request": spec.source_prompt, "objects_total": len(spec.objects),
               "entity_count": len(entities), "entities": [], "diagnostics": diagnostics,
               "inventory_columns": ["kind", "id", "label_first24", "position_m", "size_m"],
               "inventory_note": "Inventory coordinates rounded to4decimals; detailed entities retain exact values.",
               "inventory": [], "inventory_omitted": 0, "detailed_entities_omitted": 0}
    selected = [(key, data) for key, data in ranked if key[1] in (scope or []) or key[1] in diagnostics]
    if not scope:
        selected = ranked[:16] + selected
    selected_keys = {key for key, _ in selected}
    positions = [data["position"] for _, data in selected if "position" in data]
    if positions:
        neighbors = sorted(
            [(key, data) for key, data in ranked if key not in selected_keys and "position" in data],
            key=lambda item: min(sum((item[1]["position"][i] - point[i])**2 for i in (0, 1)) for point in positions[:16]),
        )[:24]
        selected += neighbors
    seen = set()
    for (kind, oid), data in selected:
        if (kind, oid) not in seen:
            context["entities"].append({"kind": kind, "id": oid, "data": data})
            seen.add((kind, oid))
    context["detailed_entities_omitted"] = len(entities) - len(seen)
    inventory = [[kind, oid, str(data.get("label", data.get("description", "")))[:24],
                  [round(x, 4) for x in data["position"]] if "position" in data else None,
                  data.get("size")] for (kind, oid), data in ranked]
    context["inventory"] = inventory
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encode(context)) > max_chars:
        if scope is None:
            raise EditingContextLimit("Complete scene inventory exceeds the model context budget; provide a narrower explicit scope. Parent world is preserved.")
        context["inventory"] = [row for row in inventory if (row[0], row[1]) in seen]
        context["inventory_omitted"] = len(inventory) - len(context["inventory"])
        context["inventory_note"] += " Only selected entities and their neighbors are shown; omitted distant entities cannot be edited under the explicit scope."
    if len(encode(context)) > max_chars:
        raise EditingContextLimit("Selected entities and diagnostics exceed the model context budget; narrow scope or shorten the request. Parent world is preserved.")
    return context


def edit_world(world, prompt: str | None, *, kind="refine", scope=None,
               allow_task_changes=False, allow_physics_changes=False, output=None,
               model="gpt-6-astra", reasoning="medium", client=None, max_repairs=2,
               client_options=None, runtime_factory=None, on_event=None, cancelled=None,
               scene_profile="standard", max_objects=MAX_EXPANDED_OBJECTS, min_objects=0,
               asset_root=None):
    from .sdk import World, load

    if kind not in {"refine", "repair"}:
        raise ValueError("Unknown edit kind")
    if kind == "repair" and (allow_task_changes or allow_physics_changes):
        raise ValueError("Repair cannot change task requirements or physical parameters")
    if not isinstance(max_repairs, int) or isinstance(max_repairs, bool) or not 0 <= max_repairs <= 10:
        raise ValueError("max_repairs must be an integer from0 to10")
    if not isinstance(max_objects, int) or isinstance(max_objects, bool) or not 1 <= max_objects <= MAX_EXPANDED_OBJECTS:
        raise ValueError("max_objects must be from1 to10000")
    if not isinstance(min_objects, int) or isinstance(min_objects, bool) or not 0 <= min_objects <= max_objects:
        raise ValueError("min_objects must be between0 and max_objects")
    if client is not None and client_options:
        raise ValueError("Configure budgets on the supplied client, or use client_options")
    allowed_options = {"timeout", "max_seconds", "max_calls", "max_total_output_tokens", "max_output_tokens",
                       "max_cost_usd", "input_price_per_million", "output_price_per_million",
                       "cached_input_price_per_million", "cache_write_price_per_million"}
    if client_options and set(client_options) - allowed_options:
        raise ValueError("Unsupported editing client options: " + ", ".join(sorted(set(client_options) - allowed_options)))
    if not isinstance(allow_task_changes, bool) or not isinstance(allow_physics_changes, bool):
        raise ValueError("Edit permission flags must be booleans")
    _validate_scope(scope)
    original = load(world.path)
    before = materialize(original.spec)
    if prompt is None and kind == "repair":
        prompt = "Repair the measured validation failures with the smallest geometry or pose changes. Preserve all requirements."
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32000:
        raise ValueError("Edit prompt must be nonempty")
    if client is None:
        client = ResponsesClient(model=model, reasoning=reasoning,
                                 **{"max_calls": max_repairs + 1, **(client_options or {})})
    destination = Path(output or original.path.parent).resolve()
    record_path = destination / f"edit-{uuid.uuid4().hex}.json"
    record = {"kind": kind, "parent_world_id": original.id, "prompt": prompt, "scope": scope,
              "model": model, "reasoning": reasoning, "scene_profile": scene_profile,
              "started_at": utc_now(), "status": "running", "versions": [], "attempts": [], "events": []}
    current, latest, operations = before, None, []
    errors = [check for check in original.manifest["validation"]["checks"] if not check.get("passed")]

    def event(data):
        record["events"].append(audit_value({key: value for key, value in data.items() if key != "spec"}))
        write_json(record_path, audit_value(record))
        if on_event:
            try:
                on_event(data)
            except Exception as error:
                record.setdefault("observer_errors", []).append(public_error(error))

    def check_cancelled():
        if cancelled and cancelled():
            record.update(status="cancelled", finished_at=utc_now())
            event({"type": "edit_cancelled", "world_id": latest.id if latest else None})
            raise GenerationCancelled("Scene edit cancelled; latest candidate retained", candidate=latest.spec if latest else current,
                                      history=record["attempts"], errors=errors)

    event({"type": "edit_started", "kind": kind, "parent_world_id": original.id})
    event({"type": "model_budget", "budget": audit_value(getattr(client, "budget", {}))})
    for attempt in range(max_repairs + 1):
        check_cancelled()
        try:
            task_permission = allow_task_changes and not operations
            physics_permission = allow_physics_changes and not operations
            request = {"edit_request": prompt, "kind": kind, "scope": scope,
                       "allow_task_changes": task_permission, "allow_physics_changes": physics_permission,
                       "max_objects": max_objects, "min_objects": min_objects,
                       "previous_actual_changes": scene_diff(before, current)[0][:256]}
            schema = ScenePatch.model_json_schema()
            overhead = len(INSTRUCTIONS) + len(json.dumps(request)) + len(json.dumps(strict_schema(schema))) + 2000
            context_budget = min(100000, getattr(client, "max_input_chars", 120000)) - overhead
            request["context"] = compact_context(current, prompt, scope, errors, max_chars=context_budget)
            raw = client.complete_json(INSTRUCTIONS, json.dumps(request, ensure_ascii=False, separators=(",", ":")), schema)
            event({"type": "model_usage", "usage": audit_value(getattr(client, "usage", {}))})
            patch = ScenePatch.model_validate(raw)
            candidate = apply_patch(current, patch, scope=scope, allow_task_changes=task_permission,
                                    allow_physics_changes=physics_permission, max_objects=max_objects)
            enforce_protections(before, candidate,
                                allow_task_changes=allow_task_changes,
                                allow_physics_changes=allow_physics_changes,
                                preserve_entities=kind == "repair")
            if len(candidate.objects) < min_objects:
                raise ValueError(f"Edit leaves fewer than {min_objects} physical objects")
            changes, counts = scene_diff(before, candidate)
            if not changes:
                raise ValueError("Repair reverted every requested edit; no final scene changes remain")
            candidate_operations = operations + patch.model_dump(mode="json")["operations"]
            metadata = {"kind": kind, "prompt": prompt, "parent_world_id": original.id, "scope": scope,
                        "allow_task_changes": allow_task_changes, "allow_physics_changes": allow_physics_changes,
                        "operations": candidate_operations, "changes": changes, "counts": counts,
                        "attempt": attempt + 1, "assembly_materialized": bool(original.spec.assembly_instances),
                        "original_request_sha256": digest(before.source_prompt),
                        "protection_note": "Only typed operations are applied. Unmentioned data is preserved; semantic interpretation of an unscoped request is model-proposed, not independently proven."}
            common = {"output": destination, "asset_root": asset_root or original.path,
                      "parent_id": original.id, "edit_metadata": audit_value(metadata),
                      "validation_profile": original.manifest["validation"].get("profile", "strict")}
            latest = World.from_spec(candidate, validate=False, **common)
            record["versions"].append(latest.id)
            event({"type": "preview", "spec": candidate.model_dump(mode="json"), "edit": metadata,
                   "world_id": latest.id, "validation": latest.manifest["validation"]})
            check_cancelled()
            latest = World.from_spec(candidate, runtime_factory=runtime_factory, **common)
            record["versions"].append(latest.id)
            current, operations = candidate, candidate_operations
            errors = [check for check in latest.manifest["validation"]["checks"] if not check.get("passed")]
            status = latest.manifest["validation"]["status"]
            record["attempts"].append({"attempt": attempt + 1, "world_id": latest.id,
                                       "status": status, "changes": changes, "errors": errors})
            event({"type": "validation", "attempt": attempt + 1, "world_id": latest.id,
                   "status": status, "errors": errors, "edit": metadata})
            check_cancelled()
            if status == "ready":
                record.update(status="completed", world_id=latest.id, finished_at=utc_now())
                event({"type": "world_created", "world_id": latest.id, "edit": metadata,
                       "validation": latest.manifest["validation"], "edit_path": str(record_path)})
                return latest
        except GenerationCancelled:
            raise
        except Exception as error:
            errors = [public_error(error)]
            record["attempts"].append({"attempt": attempt + 1, "status": "rejected", "errors": errors})
            event({"type": "patch_rejected", "attempt": attempt + 1, "errors": errors})
            if isinstance(error, EditingContextLimit):
                break
    record.update(status="incomplete", world_id=latest.id if latest else None, finished_at=utc_now())
    event({"type": "edit_incomplete", "world_id": latest.id if latest else None, "errors": errors,
           "edit_path": str(record_path)})
    if latest is None:
        raise GenerationError("No applicable scene patch was produced; parent world is unchanged",
                              candidate=current, attempts=max_repairs + 1, history=record["attempts"], errors=errors)
    return latest
