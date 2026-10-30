import copy

import pytest
from pydantic import ValidationError

from robot_worlds.models import WorldSpec
from robot_worlds.rubric import grade, measure_predicate, validate_rubric


def world(rubric=None):
    return WorldSpec.model_validate({
        "name": "two stations", "description": "test geometry", "task": "Move part to output",
        "bounds": [10, 10, 3], "objects": [
            {"id": "part", "label": "part", "kind": "box", "position": [2, 0, .1], "size": [.2, .2, .2], "movable": True},
            {"id": "wrong", "label": "other part", "kind": "box", "position": [2, 1, .1], "size": [.2, .2, .2], "movable": True},
            {"id": "table", "label": "support", "kind": "box", "position": [-2, 0, .5], "size": [1, 1, 1]},
        ], "regions": [{"id": "output", "label": "output", "position": [-2, 0, 1.3], "size": [1, 1, .6]}],
        "rubric": rubric or [{"id": "place", "description": "Part at output", "predicate": "in_region", "object_id": "part", "target_id": "output"}],
    })


def frame(time, *, output=False, holding=None, missing=False):
    objects = {o.id: {"position": list(o.position), "size": list(o.size), "quaternion": [1, 0, 0, 0], "contacts": [], "held_by": None} for o in world().objects}
    objects["part"]["held_by"] = holding
    if output:
        objects["part"]["position"] = [-2, 0, 1.1]
        objects["part"]["contacts"] = ["table"]
    if missing:
        del objects["part"]
    return {"time": time, "objects": objects, "robot": {"position": [0, 0, 0]}}


def test_noop_wrong_object_and_valid_placement():
    spec = world()
    assert grade(spec, [frame(0), frame(1)])["status"] == "failed"
    wrong = frame(1)
    wrong["objects"]["wrong"]["position"] = [-2, 0, 1.1]
    assert not grade(spec, [frame(0), wrong])["success"]
    assert grade(spec, [frame(0), frame(1, output=True)])["success"]


def test_stable_duration_breaks_on_regression_and_is_not_accumulated():
    spec = world()
    spec.rubric[0].duration = 1
    assert not grade(spec, [frame(0), frame(1, output=True), frame(1.6, output=True), frame(1.8), frame(2, output=True), frame(2.6, output=True)])["success"]
    assert grade(spec, [frame(0), frame(1, output=True), frame(2.1, output=True)])["success"]
    assert not grade(spec, [frame(0), frame(1, output=True), frame(2.1, output=True), frame(3)])["success"]


def test_eventual_does_not_require_final_persistence():
    spec = world()
    spec.rubric[0].mode = "eventually"
    assert grade(spec, [frame(0), frame(1, output=True), frame(2)])["success"]


def test_order_requires_distinct_earlier_witness():
    spec = world([
        {"id": "grasp", "description": "grasp", "predicate": "holding", "object_id": "part", "mode": "eventually"},
        {"id": "place", "description": "place", "predicate": "in_region", "object_id": "part", "target_id": "output", "after": ["grasp"]},
    ])
    assert not grade(spec, [frame(0), frame(1, output=True, holding="left")])["success"]
    assert not grade(spec, [frame(0), frame(1, output=True), frame(2, holding="left")])["success"]
    assert grade(spec, [frame(0), frame(1, holding="left"), frame(2, output=True)])["success"]


def test_missing_evidence_is_unscorable_not_task_failure():
    result = grade(world(), [frame(0), frame(1, missing=True)])
    assert result["status"] == "unscorable"
    assert result["criteria"][0]["evidence"]["missing_measurements"]
    assert grade(world(), [])["status"] == "unscorable"


def test_support_requires_contacts_and_geometry():
    spec = world([{"id": "supported", "description": "support", "predicate": "on_top", "object_id": "part", "target_id": "table"}])
    state = frame(1, output=True)
    assert grade(spec, [frame(0), state])["success"]
    state["objects"]["part"]["contacts"] = []
    assert grade(spec, [state])["status"] == "failed"
    del state["objects"]["part"]["contacts"]
    assert grade(spec, [state])["status"] == "unscorable"


def test_rotated_object_must_fit_not_just_its_center():
    spec = world()
    state = frame(1, output=True)
    spec.objects[0].size = (.8, .8, .2)
    state["objects"]["part"]["size"] = [.8, .8, .2]
    state["objects"]["part"]["quaternion"] = [.9238795325, 0, 0, .3826834324]
    assert not grade(spec, [state])["success"]


def test_alternatives_are_or_and_ungrouped_requirements_remain_and():
    spec = world([
        {"id": "a", "description": "a", "predicate": "in_region", "object_id": "part", "target_id": "output", "alternative_group": "destination"},
        {"id": "b", "description": "b", "predicate": "in_region", "object_id": "wrong", "target_id": "output", "alternative_group": "destination"},
        {"id": "release", "description": "release", "predicate": "released", "object_id": "part", "intent": "maintain"},
    ])
    assert grade(spec, [frame(0), frame(1, output=True)])["success"]
    assert not grade(spec, [frame(0), frame(1, output=True, holding="left")])["success"]


def test_unsupported_predicate_and_cycles_rejected():
    data = world().model_dump()
    data["rubric"][0]["predicate"] = "looks_finished"
    with pytest.raises(ValidationError):
        WorldSpec.model_validate(data)
    spec = world()
    second = copy.deepcopy(spec.rubric[0])
    second.id = "second"
    second.after = ["place"]
    spec.rubric[0].after = ["second"]
    spec.rubric.append(second)
    assert any("cycle" in e for e in validate_rubric(spec))
    with pytest.raises(ValueError, match="cycle"):
        grade(spec, [])


def test_invalid_time_and_nonfinite_measurement_are_unscorable():
    assert grade(world(), [frame(1), frame(0)])["status"] == "unscorable"
    state = frame(1, output=True)
    state["objects"]["part"]["position"][0] = float("nan")
    assert measure_predicate(world(), world().rubric[0], state)["satisfied"] is None


def test_always_violation_cannot_be_repaired_by_final_success():
    spec = world([{"id": "safe", "description": "released throughout", "predicate": "released", "object_id": "part", "mode": "always", "intent": "maintain"}])
    assert not grade(spec, [frame(0), frame(1, holding="left"), frame(2)])["success"]


def test_process_completion_is_identity_specific_and_not_claimed_event():
    data = world().model_dump()
    data["process_cycles"] = [{"id": "heat", "label": "timed surrogate", "fixture_id": "table", "workpiece_ids": ["part"], "duration": 2, "output_state": "processed", "prerequisites": [{"predicate": "on_top", "object_id": "part", "target_id": "table"}]}]
    data["rubric"] = [{"id": "done", "description": "processed", "predicate": "process_complete", "object_id": "part", "target_id": "heat"}]
    spec = WorldSpec.model_validate(data)
    state = frame(1)
    state["objects"]["part"]["process_states"] = {}
    state["events"] = [{"type": "process_complete", "object_id": "part", "target_id": "heat"}]
    assert not grade(spec, [state])["success"]
    initial = copy.deepcopy(state)
    initial["time"] = 0
    state["objects"]["part"]["process_states"] = {"heat": "processed"}
    assert grade(spec, [initial, state])["success"]


def test_achievement_cannot_start_satisfied_and_wait_out_duration():
    spec = world()
    spec.rubric[0].duration = 1
    assert not grade(spec, [frame(0, output=True), frame(2, output=True)])["success"]
    assert grade(spec, [frame(0, output=True), frame(1), frame(2, output=True), frame(3.1, output=True)])["success"]


def test_already_satisfied_later_stage_cannot_gain_ordering_credit():
    spec = world([
        {"id": "grasp", "description": "grasp", "predicate": "holding", "object_id": "part", "mode": "eventually"},
        {"id": "place", "description": "place", "predicate": "in_region", "object_id": "part", "target_id": "output", "after": ["grasp"]},
    ])
    assert not grade(spec, [frame(0), frame(1, output=True), frame(2, output=True, holding="left"), frame(3, output=True)])["success"]
    assert not grade(spec, [frame(0), frame(1, output=True, holding="left"), frame(2, output=True)])["success"]


def test_independent_achievements_allow_both_valid_orders():
    spec = world([
        {"id": "one", "description": "first part", "predicate": "in_region", "object_id": "part", "target_id": "output"},
        {"id": "two", "description": "second part", "predicate": "in_region", "object_id": "wrong", "target_id": "output"},
    ])
    part_first = frame(1, output=True)
    other_first = frame(1)
    other_first["objects"]["wrong"]["position"] = [-2, 0, 1.1]
    final = frame(2, output=True)
    final["objects"]["wrong"]["position"] = [-2, 0, 1.1]
    assert grade(spec, [frame(0), part_first, final])["success"]
    assert grade(spec, [frame(0), other_first, final])["success"]


def test_unvalidated_requirement_prevents_a_misleading_pass():
    spec = world()
    spec.unvalidated_requirements = ["Measure weld strength, which this runtime does not simulate"]
    result = grade(spec, [frame(0), frame(1, output=True)])
    assert result["criteria"][0]["status"] == "passed"
    assert result["status"] == "unscorable"
    assert not result["success"]
