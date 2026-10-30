"""Adversarial edits must not redefine the evaluator through scene geometry."""

import copy

import pytest

from robot_worlds.editing import apply_patch, enforce_protections
from robot_worlds.models import WorldSpec


def task_world():
    return WorldSpec.model_validate({
        "name": "Task binding fixture", "description": "Pure test fixture", "task": "Carry the red part to the output support",
        "bounds": [12, 10, 3], "robot_start": [0, -2, 0],
        "objects": [
            {"id": "part", "label": "red part", "kind": "box", "position": [-3, 0, .1], "size": [.2, .2, .2], "color": [1, 0, 0], "movable": True},
            {"id": "parent", "label": "fixture mechanism", "kind": "box", "position": [3, 0, .2], "size": [.2, .2, .4],
             "joint": {"type": "hinge", "axis": [0, 0, 1], "anchor": [3, 0, .2], "range": [0, 1.57]}},
            {"id": "support", "label": "output support", "kind": "box", "position": [3.5, 0, .5], "size": [1, 1, .1], "parent_id": "parent"},
            {"id": "decoy", "label": "blue part", "kind": "box", "position": [-3, 1, .1], "size": [.2, .2, .2], "movable": True},
        ],
        "regions": [{"id": "output", "label": "output", "position": [3.5, 0, .8], "size": [1, 1, .5]}],
        "rubric": [
            {"id": "place", "description": "Same part in output", "predicate": "in_region", "object_id": "part", "target_id": "output", "duration": 1},
            {"id": "support_check", "description": "Physical support", "predicate": "on_top", "object_id": "part", "target_id": "support"},
        ],
    })


def update(oid, field, value, *, region=False):
    return {"operations": [{"op": "update_region" if region else "update_object", "id": oid,
                            "fields": [field], "values": {field: value}}]}


@pytest.mark.parametrize("field,value", [("position", [-3, 0, .1]), ("size", [12, 10, 3])])
def test_region_relocation_or_expansion_requires_task_permission(field, value):
    world = task_world()
    patch = update("output", field, value, region=True)
    with pytest.raises(ValueError, match="task-bound region"):
        apply_patch(world, patch)
    assert apply_patch(world, patch, allow_task_changes=True).regions[0] != world.regions[0]


@pytest.mark.parametrize("field,value", [("size", [.01, .01, .01]), ("label", "blue part"), ("color", [0, 0, 1]), ("kind", "sphere")])
def test_task_subject_identity_and_shape_are_protected(field, value):
    with pytest.raises(ValueError, match="task-bound object"):
        apply_patch(task_world(), update("part", field, value))


@pytest.mark.parametrize("oid", ["support", "parent"])
def test_target_or_ancestor_motion_cannot_move_goal_beside_subject(oid):
    with pytest.raises(ValueError, match="destination/ancestor pose"):
        apply_patch(task_world(), update(oid, "position", [-3, 0, .5]))


def test_joint_ancestor_motion_and_reparenting_cannot_bypass_task_guard():
    spec = task_world()
    joint = spec.objects[1].joint.model_dump()
    joint["initial"] = 1.0
    with pytest.raises(ValueError, match="destination/ancestor pose"):
        apply_patch(spec, update("parent", "joint", joint))
    joint["anchor"] = [-3, 0, .2]
    with pytest.raises(ValueError, match="destination/ancestor pose"):
        apply_patch(spec, update("parent", "joint", joint), allow_physics_changes=True)
    with pytest.raises(ValueError, match="task-bound object parent_id"):
        apply_patch(spec, update("support", "parent_id", None), allow_physics_changes=True)


def test_same_id_remove_and_replace_is_compared_to_original_values():
    spec = task_world()
    changed = spec.objects[0].model_dump()
    changed["size"] = [.01, .01, .01]
    patch = {"operations": [{"op": "remove_object", "id": "part"}, {"op": "add_object", "object": changed}]}
    with pytest.raises(ValueError, match="task-bound object size"):
        apply_patch(spec, patch)
    with pytest.raises(ValueError, match="presence"):
        apply_patch(spec, {"operations": [{"op": "remove_object", "id": "part"}]})


def test_original_parent_check_rejects_cross_attempt_region_rebinding():
    original = task_world()
    candidate = apply_patch(original, update("output", "position", [-3, 0, .1], region=True), allow_task_changes=True)
    with pytest.raises(ValueError, match="task-bound region"):
        enforce_protections(original, candidate, preserve_entities=True)


def test_initial_subject_pose_repair_is_allowed_but_robot_start_is_protected():
    spec = task_world()
    repaired = apply_patch(spec, update("part", "position", [-3, 0, .11]))
    assert repaired.objects[0].position[-1] == .11
    assert repaired.rubric == spec.rubric and repaired.regions == spec.regions
    with pytest.raises(ValueError, match="Robot start"):
        apply_patch(spec, {"operations": [{"op": "set_robot_start", "robot_start": [3, 0, 0]}]})


def test_process_loading_region_is_protected_even_without_region_rubric():
    spec = task_world()
    data = spec.model_dump()
    data["rubric"] = [{"id": "done", "description": "Processed", "predicate": "process_complete", "object_id": "part", "target_id": "cycle"}]
    data["process_cycles"] = [{"id": "cycle", "label": "Physical loading surrogate", "fixture_id": "support", "workpiece_ids": ["part"],
                               "duration": 2, "output_state": "processed", "prerequisites": [{"predicate": "in_region", "object_id": "part", "target_id": "output"}]}]
    with pytest.raises(ValueError, match="task-bound region"):
        apply_patch(WorldSpec.model_validate(data), update("output", "size", [10, 10, 3], region=True))


def test_explicit_task_changes_do_not_silently_grant_physics_permission():
    spec = task_world()
    candidate = copy.deepcopy(spec)
    candidate.objects[0].mass = .001
    with pytest.raises(ValueError, match="mass is protected"):
        enforce_protections(spec, candidate, allow_task_changes=True)
