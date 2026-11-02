"""Carry scoring uses a continuous physical-contact witness, not endpoints alone."""
import pytest
from test_rubric import frame, world

from robot_worlds.models import Criterion, ProcessPrerequisite
from robot_worlds.rubric import carried_measurements, grade, measure_predicate, validate_rubric


def carry_spec(after=None):
    spec = world()
    spec.rubric = [Criterion(id="carry", description="Carry part 1.5m", predicate="carried_distance",
                            object_id="part", threshold=1.5, mode="eventually", after=after or [])]
    return spec


def samples(points, holdings=None):
    result = []
    holdings = holdings or [None] + ["right"] * (len(points)-1)
    for i, (x, held) in enumerate(zip(points, holdings)):
        snapshot = frame(i, holding=held)
        snapshot["objects"]["part"]["position"] = [x, 0, .7]
        result.append(snapshot)
    return result


def test_continuous_carry_has_distance_witness_and_release_preserves_eventual_success():
    spec = carry_spec()
    trace = samples([0, 0, .8, 1.6, 1.6], [None, "right", "right", "right", None])
    result = grade(spec, trace)
    assert result["success"]
    assert result["criteria"][0]["completion_time"] == 3
    evidence = carried_measurements(spec, spec.rubric[0], trace)
    assert evidence[3]["measured"]["distance_m"] == 1.6
    assert evidence[3]["measured"]["continuous_since"] == 1
    assert evidence[-1]["measured"]["distance_m"] == 0
    assert measure_predicate(spec, spec.rubric[0], trace[3])["satisfied"] is False


@pytest.mark.parametrize("points,holdings", [
    ([0, 0, 1, 1, 2], [None, "right", "right", None, "right"]),
    ([0, 0, .8, 0, .8, 0], None),
    ([0, 0, 2], [None, None, None]),
])
def test_release_sliding_and_backtracking_do_not_accumulate_carry(points, holdings):
    assert not grade(carry_spec(), samples(points, holdings))["success"]


def test_missing_contact_breaks_streak_and_is_unscorable():
    spec = carry_spec()
    trace = samples([0, 0, 1, 2])
    del trace[2]["objects"]["part"]["held_by"]
    result = grade(spec, trace)
    assert not result["success"]
    assert result["status"] == "unscorable"
    assert result["criteria"][0]["evidence"]["missing_measurements"]


def test_wrong_object_and_vertical_lift_do_not_count():
    spec = carry_spec()
    trace = samples([0, 0, .1])
    trace[-1]["objects"]["wrong"].update(held_by="left", position=[10, 0, .7])
    trace[-1]["objects"]["part"]["position"][2] = 5
    assert not grade(spec, trace)["success"]


def test_ordered_second_carry_requires_a_new_post_dependency_witness():
    spec = carry_spec()
    spec.rubric.append(spec.rubric[0].model_copy(update={"id": "carry_again", "after": ["carry"]}))
    assert not grade(spec, samples([0, 0, 1.6, 2.0]))["success"]
    assert grade(spec, samples([0, 0, 1.6, 1.6, 1.6, 3.2], [None, "right", "right", None, "right", "right"]))["success"]


def test_carry_contract_rejects_fixed_subject_and_invalid_threshold():
    spec = carry_spec()
    spec.rubric[0].object_id = "table"
    spec.rubric[0].threshold = 0
    assert any("movable" in e for e in validate_rubric(spec))
    assert any("positive" in e for e in validate_rubric(spec))


def test_released_is_an_executable_process_prerequisite():
    condition = ProcessPrerequisite(predicate="released", object_id="part")
    assert measure_predicate(world(), condition, frame(0))["satisfied"] is True
    assert measure_predicate(world(), condition, frame(1, holding="right"))["satisfied"] is False
