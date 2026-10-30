"""Deterministic patch boundaries; no model calls or native physics."""
import copy
import json
from pathlib import Path

import pytest

from robot_worlds.editing import apply_patch, enforce_protections, scene_diff
from robot_worlds.models import WorldSpec


@pytest.fixture
def sample():
    data = json.loads((Path(__file__).parent / "fixtures/astra-scoped-edit.json").read_text())
    return WorldSpec.model_validate(data["source"]), data["patch"]


def test_actual_patch_is_scoped_and_parent_is_unchanged(sample):
    spec, patch = sample
    frozen = spec.model_dump_json()
    result = apply_patch(spec, patch, scope=["metal_workpiece"])
    changes, counts = scene_diff(spec, result)
    assert spec.model_dump_json() == frozen
    assert [(c["entity_id"], c["property"]) for c in changes] == [("metal_workpiece", "position")]
    assert counts == {"added": 0, "removed": 0, "changed": 1, "preserved": 16}
    with pytest.raises(ValueError, match="exceeds explicit scope"):
        apply_patch(spec, patch, scope=["parts_table_top"])


@pytest.mark.parametrize("field,value", [("mass", 0.001), ("friction", 0.0), ("movable", False)])
def test_physical_properties_require_permission(sample, field, value):
    spec, _ = sample
    patch = {"operations": [{"op": "update_object", "id": "metal_workpiece",
                             "fields": [field], "values": {field: value}}]}
    with pytest.raises(ValueError, match="protected"):
        apply_patch(spec, patch)
    changed = apply_patch(spec, patch, allow_physics_changes=True)
    assert getattr(next(o for o in changed.objects if o.id == "metal_workpiece"), field) == value


def test_task_cannot_be_weakened_without_permission(sample):
    spec, _ = sample
    with pytest.raises(ValueError, match="protected"):
        apply_patch(spec, {"operations": [{"op": "set_task", "task": "Do nothing"}]})


@pytest.mark.parametrize("field,op", [("objects", "remove_object"), ("regions", "remove_region")])
def test_repair_cannot_delete_the_problem(sample, field, op):
    original, _ = sample
    oid = getattr(original, field)[0].id
    candidate = apply_patch(original, {"operations": [{"op": op, "id": oid}]}, allow_task_changes=True)
    with pytest.raises(ValueError, match="cannot remove original entities"):
        enforce_protections(original, candidate, preserve_entities=True)


def test_cross_attempt_delete_readd_does_not_bypass_original_physics(sample):
    original, _ = sample
    obj = copy.deepcopy(next(o for o in original.objects if o.id == "metal_workpiece"))
    removed = apply_patch(original, {"operations": [{"op": "remove_object", "id": obj.id}]})
    obj.mass = 0.001
    readded = apply_patch(removed, {"operations": [{"op": "add_object", "object": obj.model_dump()}]})
    with pytest.raises(ValueError, match="mass is protected"):
        enforce_protections(original, readded)


def test_ignored_update_values_do_not_modify_unlisted_fields(sample):
    spec, patch = sample
    patch["operations"][0]["values"]["mass"] = 0.001
    result = apply_patch(spec, patch)
    assert next(o for o in result.objects if o.id == "metal_workpiece").mass == 2.75
