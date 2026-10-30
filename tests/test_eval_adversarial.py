"""Adversarial evidence checks; synthetic traces are not physical rollout claims."""

import copy
from types import SimpleNamespace

import numpy as np

from robot_worlds.models import WorldSpec
from robot_worlds.rubric import grade, measure_predicate


def world(**criterion):
    check = {
        "id": "placed",
        "description": "Place the same workpiece at output",
        "predicate": "in_region",
        "object_id": "part",
        "target_id": "output",
    }
    check.update(criterion)
    return WorldSpec.model_validate(
        {
            "name": "Adversarial test fixture",
            "description": "Engineering-only grading fixture",
            "task": "Move and release the workpiece at output",
            "bounds": [12, 8, 4],
            "objects": [
                {
                    "id": "part",
                    "label": "workpiece",
                    "kind": "box",
                    "position": [2, 0, 0.15],
                    "size": [0.3, 0.3, 0.3],
                    "movable": True,
                },
                {
                    "id": "fixture",
                    "label": "machine base",
                    "kind": "box",
                    "position": [-2, 0, 0.25],
                    "size": [1, 1, 0.5],
                },
                {
                    "id": "top",
                    "label": "machine loading surface",
                    "kind": "box",
                    "position": [-2, 0, 0.55],
                    "size": [1, 1, 0.1],
                    "parent_id": "fixture",
                },
            ],
            "regions": [
                {
                    "id": "output",
                    "label": "output volume",
                    "position": [-2, 0, 0.8],
                    "size": [0.5, 0.5, 0.5],
                }
            ],
            "rubric": [check],
        }
    )


def frame(time, *, placed=False, missing=False, held_by=None):
    spec = world()
    objects = {
        obj.id: {
            "position": list(obj.position),
            "size": list(obj.size),
            "quaternion": [1, 0, 0, 0],
            "held_by": None,
            "contacts": [],
            "process_states": {},
            "velocity": [0, 0, 0],
        }
        for obj in spec.objects
    }
    objects["part"]["held_by"] = held_by
    if placed:
        objects["part"].update(position=[-2, 0, 0.75], contacts=["top"])
    if missing:
        objects.pop("part")
    return {"time": time, "objects": objects, "robot": {"position": [0, 0, 0]}}


def test_known_final_failure_is_not_erased_by_an_earlier_missing_sample():
    result = grade(world(), [frame(0), frame(1, missing=True), frame(2)])
    assert result["status"] == "failed"
    assert result["criteria"][0]["evidence"]["missing_measurements"]


def test_known_always_violation_is_failure_even_with_other_missing_evidence():
    spec = world(predicate="released", target_id=None, mode="always", intent="maintain")
    result = grade(spec, [frame(0), frame(1, held_by="left"), frame(2, missing=True)])
    assert result["status"] == "failed"


def test_always_failure_reports_the_violating_state_even_after_recovery():
    spec = world(predicate="released", target_id=None, mode="always", intent="maintain")
    result = grade(spec, [frame(0), frame(1, held_by="left"), frame(2)])
    evidence = result["criteria"][0]["evidence"]
    assert result["status"] == "failed"
    assert evidence["decisive_failure"]["time"] == 1
    assert evidence["decisive_failure"]["measured"] == "left"


def test_trace_cannot_shrink_a_frozen_rigid_object_to_earn_containment_credit():
    spec = world()
    narrow = spec.model_dump()
    narrow["regions"][0]["size"] = [0.2, 0.2, 0.5]
    spec = WorldSpec.model_validate(narrow)
    forged = frame(1, placed=True)
    forged["objects"]["part"]["size"] = [0.01, 0.01, 0.01]
    assert measure_predicate(spec, spec.rubric[0], forged)["satisfied"] is not True
    assert not grade(spec, [frame(0), forged])["success"]


def test_missing_rigid_orientation_cannot_certify_full_containment():
    spec = world()
    state = frame(1, placed=True)
    state["objects"]["part"].pop("quaternion")
    measured = measure_predicate(spec, spec.rubric[0], state)
    assert measured["satisfied"] is None
    assert measured["reason"]


def test_valid_eventual_evidence_survives_later_missing_data():
    # An existential completed-stage witness needs no invented later measurement.
    spec = world(mode="eventually")
    result = grade(spec, [frame(0), frame(1, placed=True), frame(2, missing=True)])
    assert result["success"]
    assert result["criteria"][0]["completion_time"] == 1


def test_noop_achievement_rejects_true_at_reset_but_explicit_maintenance_can_pass():
    trace = [frame(0, placed=True), frame(2, placed=True)]
    assert not grade(world(duration=1), trace)["success"]
    assert grade(world(intent="maintain", duration=1), trace)["success"]


def test_final_placement_requires_release_as_a_separate_measured_requirement():
    data = world().model_dump()
    data["rubric"].append(
        {
            "id": "release",
            "description": "Release the workpiece",
            "predicate": "released",
            "object_id": "part",
            "intent": "maintain",
        }
    )
    spec = WorldSpec.model_validate(data)
    assert not grade(spec, [frame(0), frame(1, placed=True, held_by="left")])["success"]
    assert grade(spec, [frame(0), frame(1, placed=True)])["success"]


def process_world():
    data = world(predicate="process_complete", target_id="cycle").model_dump()
    data["process_cycles"] = [
        {
            "id": "cycle",
            "label": "Timed processing abstraction",
            "fixture_id": "fixture",
            "workpiece_ids": ["part"],
            "duration": 0.1,
            "output_state": "processed",
            "prerequisites": [{"predicate": "on_top", "object_id": "part", "target_id": "top"}],
        }
    ]
    return WorldSpec.model_validate(data)


def process_monitor(spec, state):
    """Use the real process monitor, with explicit measured-state injection only."""
    from robot_worlds.runtime import Runtime

    monitor = object.__new__(Runtime)
    monitor.spec = spec
    monitor.data = SimpleNamespace(time=0, qpos=np.zeros(1), qvel=np.zeros(1))
    monitor._process_timers = {}
    monitor._process_states = {}
    monitor._events = []
    monitor._fixture_members = {"fixture": {"fixture", "top"}}
    monitor._process_snapshot = lambda: copy.deepcopy(state)
    return monitor


def test_fixture_child_contact_is_a_supported_loading_contact():
    state = frame(0, placed=True)
    monitor = process_monitor(process_world(), state)
    monitor._advance_processes()
    monitor.data.time = 0.11
    monitor._advance_processes()
    assert monitor._process_states.get("part", {}).get("cycle") == "processed"


def test_lost_process_loading_resets_duration_instead_of_accumulating_credit():
    state = frame(0, placed=True)
    # Direct fixture support isolates timer semantics from the child-contact case.
    spec = process_world()
    spec.process_cycles[0].prerequisites[0].target_id = "fixture"
    state["objects"]["part"].update(position=[-2, 0, 0.65], contacts=["fixture"])
    monitor = process_monitor(spec, state)
    monitor._advance_processes()
    monitor.data.time = 0.06
    state["objects"]["part"]["contacts"] = []
    monitor._advance_processes()
    monitor.data.time = 0.07
    state["objects"]["part"]["contacts"] = ["fixture"]
    monitor._advance_processes()
    monitor.data.time = 0.12
    monitor._advance_processes()
    assert "cycle" not in monitor._process_states.get("part", {})


def test_process_monitor_observes_loading_breaks_between_recorded_samples(monkeypatch):
    """Check real step scheduling; injected states do not establish contact physics."""
    import robot_worlds.runtime as runtime_module

    state = frame(0, placed=True)
    monitor = process_monitor(process_world(), state)
    monitor.model = SimpleNamespace(opt=SimpleNamespace(timestep=0.01))
    monitor._time_limit = None
    monitor._next_sample = 0.05
    monitor._before_step = lambda: None

    def sample():
        monitor._next_sample = monitor.data.time + 0.05

    def measured_state():
        measured = copy.deepcopy(state)
        if 0.019 < monitor.data.time < 0.039:
            measured["objects"]["part"]["contacts"] = []
        return measured

    monitor._sample = sample
    monitor._process_snapshot = measured_state
    monkeypatch.setattr(
        runtime_module.mujoco,
        "mj_step",
        lambda model, data: setattr(data, "time", data.time + 0.01),
    )
    monitor._advance_processes()
    monitor._step(0.11)
    assert "cycle" not in monitor._process_states.get("part", {})
    monitor._step(0.05)
    assert monitor._process_states["part"]["cycle"] == "processed"


def test_temporal_and_geometric_tolerances_do_not_hide_nan_measurements():
    spec = world(duration=1)
    state = frame(1, placed=True)
    state["objects"]["part"]["quaternion"][0] = float("nan")
    result = grade(spec, [frame(0), state, frame(3, placed=True)])
    assert result["status"] == "unscorable"


def test_ordered_alternative_sequences_do_not_require_both_valid_branches():
    data = world().model_dump()
    # Duplicate the continuation for each branch: (left→place) OR (right→place).
    # Both choice criteria are genuine achievements of the same workpiece.
    data["rubric"] = [
        {
            "id": "grasp_a",
            "description": "First valid grasp stage",
            "predicate": "holding",
            "object_id": "part",
            "mode": "eventually",
            "alternative_group": "grasp",
        },
        {
            "id": "grasp_b",
            "description": "Alternative stage",
            "predicate": "near",
            "object_id": "part",
            "target_id": "fixture",
            "threshold": 0.5,
            "mode": "eventually",
            "alternative_group": "grasp",
        },
        {
            **data["rubric"][0],
            "id": "place_a",
            "after": ["grasp_a"],
            "alternative_group": "delivery",
        },
        {
            **data["rubric"][0],
            "id": "place_b",
            "after": ["grasp_b"],
            "alternative_group": "delivery",
        },
    ]
    spec = WorldSpec.model_validate(data)
    assert grade(spec, [frame(0), frame(1, held_by="left"), frame(2, placed=True)])["success"]
    assert not grade(spec, [frame(0), frame(1, placed=True)])["success"]
