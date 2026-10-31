"""Prepared asset edits obey the same all-or-nothing and task boundaries."""
import copy

import pytest
from test_generator import valid

from robot_worlds.editing import ScenePatch, apply_patch, scene_diff
from robot_worlds.generator import validate_spec
from robot_worlds.models import WorldSpec
from robot_worlds.prepared_assets import load_prepared_asset


def asset_patch():
    return {"operations": [
        {"op": "add_prepared_asset", "asset": {"id": "bowl", "asset_id": "google-porcelain-bowl", "mass": .6, "position": [1, -2, .001]}},
        {"op": "add_prepared_asset", "asset": {"id": "oven", "asset_id": "nvidia-microwave079", "position": [-2, -2, 0]}},
    ]}


def test_asset_edit_preserves_original_entities_task_and_source_request():
    parent = WorldSpec.model_validate(valid())
    parent.source_prompt = "Original complete request"
    original = parent.model_dump_json()
    events = []
    edited = apply_patch(parent, asset_patch(), event_callback=events.append)
    assert parent.model_dump_json() == original
    assert edited.objects[0] == parent.objects[0]
    assert edited.task == parent.task and edited.rubric == parent.rubric
    assert edited.source_prompt == parent.source_prompt
    assert len(edited.objects) == 180
    assert len(edited.regions) == 2
    assert validate_spec(edited) == []
    assert scene_diff(parent, edited)[1] == {"added": 180, "removed": 0, "changed": 0, "preserved": 3}
    assert [e["type"] for e in events] == ["asset_loading", "asset_loaded"]*2
    assert any("CC-BY-4.0" in item for item in edited.assumptions)


def test_scope_checks_every_expanded_component_and_region_not_the_prefix():
    parent = WorldSpec.model_validate(valid())
    patch = {"operations": [asset_patch()["operations"][1]]}
    asset = load_prepared_asset("nvidia-microwave079", prefix="oven", position=[-2, -2, 0]).asset
    allowed = [item.id for item in [*asset.objects, *asset.regions]]
    with pytest.raises(ValueError, match="exceeds explicit scope"):
        apply_patch(parent, patch, scope=["oven"])
    with pytest.raises(ValueError, match="exceeds explicit scope"):
        apply_patch(parent, patch, scope=[o.id for o in asset.objects])  # Missing cavity region.
    assert len(apply_patch(parent, patch, scope=allowed).objects) == 21


@pytest.mark.parametrize("failure", ["budget", "duplicate", "later_operation"])
def test_rejected_multi_operation_patch_leaves_parent_untouched(failure):
    parent = WorldSpec.model_validate(valid())
    original = parent.model_dump_json()
    patch = asset_patch()
    maximum = 10000
    if failure == "budget":
        maximum = 160  # Bowl fits but adding microwave exceeds this total.
    elif failure == "duplicate":
        patch["operations"].append(copy.deepcopy(patch["operations"][0]))
    else:
        patch["operations"].append({"op": "update_object", "id": "absent", "fields": ["mass"], "values": {"mass": 1}})
    with pytest.raises(ValueError):
        apply_patch(parent, patch, max_objects=maximum)
    assert parent.model_dump_json() == original


def test_prepared_asset_cannot_replace_bound_identity_by_reusing_its_id():
    raw = valid()
    raw["objects"][0]["id"] = "bowl_body"
    raw["rubric"][0]["object_id"] = "bowl_body"
    parent = WorldSpec.model_validate(raw)
    patch = {"operations": [{"op": "remove_object", "id": "bowl_body"}, asset_patch()["operations"][0]]}
    with pytest.raises(ValueError, match="protected|task"):
        apply_patch(parent, patch)


def test_asset_addition_does_not_authorize_task_or_existing_physics_changes():
    parent = WorldSpec.model_validate(valid())
    patch = asset_patch()
    patch["operations"].append({"op": "set_task", "task": "Do nothing"})
    with pytest.raises(ValueError, match="protected"):
        apply_patch(parent, patch)
    patch["operations"][-1] = {"op": "update_object", "id": "part", "fields": ["mass"], "values": {"mass": 100}}
    with pytest.raises(ValueError, match="protected"):
        apply_patch(parent, patch)


def test_reference_operation_has_a_strict_schema():
    from robot_worlds.llm import strict_schema
    schema = strict_schema(ScenePatch.model_json_schema())
    operation = schema["$defs"]["AddPreparedAsset"]
    assert set(operation["required"]) == {"op", "asset"}
    assert operation["additionalProperties"] is False
