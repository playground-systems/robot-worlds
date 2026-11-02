"""Synthetic evaluator traces using original asset bounds, never physical success.

These tests fabricate measured state to isolate rubric semantics. They do not run
or move a robot, and cannot replace native manipulation acceptance.
"""
from copy import deepcopy

import pytest
from test_sourced_household import sourced_scene

from robot_worlds.rubric import grade, rigid_members, validate_rubric


@pytest.fixture(scope="module")
def placement():
    outside, bowl, microwave = sourced_scene()
    inside, _, _ = sourced_scene(inside=True)

    def state(spec, time):
        return {"time": time, "objects": {
            o.id: {"position": list(o.position), "size": list(o.size),
                   "quaternion": [1, 0, 0, 0], "contacts": [], "held_by": None}
            for o in spec.objects}}

    initial = state(outside, 0)
    final = state(inside, 1)
    subject = bowl.body_ids["object"]
    final["objects"][subject]["contacts"] = [microwave.geom_ids["tray"]]
    last = deepcopy(final)
    last["time"] = 1.25
    return outside, subject, [initial, final, last]


def test_synthetic_complete_placement_control(placement):
    spec, _, trace = placement
    result = grade(spec, trace)
    assert result["status"] == "passed"
    assert all(c["status"] == "passed" for c in result["criteria"])


@pytest.mark.parametrize("failure,failed_criterion", [
    ("noop", "inside"), ("wrong_object", "inside"),
    ("partial_insertion", "inside"), ("holding", "released"),
    ("unstable_support", "supported"),
])
def test_original_bowl_rejects_synthetic_failure_traces(placement, failure, failed_criterion):
    spec, subject, original = placement
    trace = deepcopy(original)
    members = rigid_members({o.id: o for o in spec.objects}, subject)
    if failure == "wrong_object":
        spec = spec.model_copy(deep=True)
        for obj in list(spec.objects):
            if obj.id not in members:
                continue
            decoy = obj.model_copy(deep=True)
            decoy.id = f"decoy_{obj.id}"
            if obj.parent_id in members:
                decoy.parent_id = f"decoy_{obj.parent_id}"
            decoy.position = (obj.position[0], obj.position[1] + .5, obj.position[2])
            spec.objects.append(decoy)
            trace[0]["objects"][decoy.id] = deepcopy(trace[0]["objects"][obj.id])
            trace[0]["objects"][decoy.id]["position"] = list(decoy.position)
    if failure in {"noop", "wrong_object"}:
        for frame in trace[1:]:
            for identifier in members:
                if failure == "wrong_object":
                    # A geometrically identical decoy has no credit for the named bowl.
                    frame["objects"][f"decoy_{identifier}"] = deepcopy(frame["objects"][identifier])
                frame["objects"][identifier] = deepcopy(trace[0]["objects"][identifier])
    elif failure == "partial_insertion":
        cavity = spec.regions[0]
        half = max(trace[-1]["objects"][identifier]["position"][1]
                   + trace[-1]["objects"][identifier]["size"][1] / 2 for identifier in members) - cavity.position[1]
        # Keep the compound center inside, but extend the rim 1 cm beyond the boundary.
        shift = cavity.size[1] / 2 - half + .01
        assert 0 < shift < cavity.size[1] / 2
        for frame in trace[1:]:
            for identifier in members:
                frame["objects"][identifier]["position"][1] += shift
    elif failure == "holding":
        for frame in trace[1:]:
            frame["objects"][subject]["held_by"] = "left"
    elif failure == "unstable_support":
        interrupted = deepcopy(trace[1])
        interrupted["time"] = 1.1
        interrupted["objects"][subject]["contacts"] = []
        restored = deepcopy(trace[-1])
        restored["time"] = 1.2
        trace[2:2] = [interrupted, restored]
    result = grade(spec, trace)
    assert result["status"] == "failed"
    assert next(c for c in result["criteria"] if c["id"] == failed_criterion)["status"] == "failed"


def test_missing_bowl_holding_evidence_is_unscorable(placement):
    spec, subject, original = placement
    trace = deepcopy(original)
    del trace[-1]["objects"][subject]["held_by"]
    result = grade(spec, trace)
    assert result["status"] == "unscorable"
    assert not result["success"]


def test_original_bowl_visual_cannot_stand_in_for_released_body(placement):
    original, subject, _ = placement
    spec = original.model_copy(deep=True)
    members = rigid_members({o.id: o for o in spec.objects}, subject)
    visual = next(o for o in spec.objects if o.id in members and not o.collision)
    spec.rubric[-1].object_id = visual.id
    assert any("released requires a movable workpiece" in error for error in validate_rubric(spec))
