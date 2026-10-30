"""Settling acceptance and saved repair evidence without a simulator or renderer."""

import copy

import pytest

from robot_worlds.models import WorldSpec
from robot_worlds.reset_diagnostics import settling_check
from robot_worlds.sdk import World


def evidence():
    return {"version": 1, "complete": True, "finite_state": True,
            "duration_seconds": .6, "sample_count": 301, "physics_timestep": .002,
            "scene_contacts": [], "scene_robot_contacts": [], "warnings": {},
            "objects": {"part": {"initial_position": [2, 0, .1], "final_position": [2, 0, .1],
                                 "peak_excursion_m": 0, "peak_excursion_time": .2,
                                 "peak_linear_speed_m_s": 0, "free_body": True}}}


def test_round_trip_ejection_fails_even_when_final_position_returns():
    data = evidence()
    data["objects"]["part"]["peak_excursion_m"] = .2
    check = settling_check(data)
    assert not check["passed"]
    issue = check["issues"][0]
    assert issue["object_id"] == "part"
    assert issue["measured_m"] == .2 and issue["limit_m"] == .05
    assert issue["initial_position"] == issue["final_position"]


def test_initial_penetration_has_ids_and_explicit_tolerance():
    data = evidence()
    data["scene_contacts"] = [{"entities": [{"kind": "scene_object", "id": "part"},
                                            {"kind": "scene_object", "id": "fixture"}],
                               "initial_penetration_m": .42, "peak_penetration_m": .42}]
    check = settling_check(data)
    assert not check["passed"]
    assert check["issues"][0]["measured_m"] == .42
    assert check["issues"][0]["limit_m"] == .001
    assert "part/fixture" in check["errors"][0]
    data["scene_contacts"][0]["initial_penetration_m"] = .0005
    assert settling_check(data)["passed"]  # transient compliance alone is not rejection


def test_robot_contacts_joint_motion_and_peak_speed_are_informational():
    data = evidence()
    data["scene_robot_contacts"] = [{"initial_penetration_m": .02}]
    data["objects"]["part"].update(free_body=False, peak_excursion_m=.3, peak_linear_speed_m_s=2)
    assert settling_check(data)["passed"]


@pytest.mark.parametrize("field", ["complete", "finite_state"])
def test_incomplete_or_nonfinite_observations_cannot_pass(field):
    data = evidence()
    data[field] = False
    assert not settling_check(data)["passed"]


def test_custom_runtime_absence_is_explicitly_unavailable():
    assert settling_check(None)["status"] == "unavailable"


def test_numerical_recovery_warning_cannot_be_hidden_by_a_finite_final_state():
    data = evidence()
    data["warnings"] = {"mjWARN_BADQACC": 1}
    check = settling_check(data)
    assert not check["passed"]
    assert check["issues"][0] == {"kind": "simulator_warning", "warning": "mjWARN_BADQACC", "count": 1}


def test_sdk_persists_entity_evidence_and_repaired_version(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda robot: {"test_asset": "frozen"})
    data = evidence()
    data["objects"]["part"]["peak_excursion_m"] = .2
    spec = WorldSpec.model_validate({
        "name": "Reset evidence fixture", "description": "Unit-test fake runtime",
        "task": "Move part", "bounds": [8, 8, 3],
        "objects": [{"id": "part", "label": "part", "kind": "box", "position": [2, 0, .1],
                     "size": [.2, .2, .2], "movable": True}],
        "regions": [{"id": "output", "label": "output", "position": [-2, 0, .2], "size": [1, 1, .4]}],
        "rubric": [{"id": "move", "description": "Move", "predicate": "in_region", "object_id": "part", "target_id": "output"}],
    })

    class Stub:
        manifest = {"engine": "FAKE_UNIT_TEST_ONLY"}

        def __init__(self, *args, **kwargs):
            self.reset_diagnostics = copy.deepcopy(data)

        def snapshot(self):
            return {"time": 0, "robot": {"position": [0, 0, 0]},
                    "objects": {"part": {"position": [2, 0, .1], "size": [.2, .2, .2],
                                         "quaternion": [1, 0, 0, 0], "velocity": [0, 0, 0]}}}

        def render(self):
            return b"fake-unit-test-image"

        def close(self):
            pass

    rejected = World.from_spec(spec, tmp_path, runtime_factory=Stub)
    checks = {c["check"]: c for c in rejected.manifest["validation"]["checks"]}
    assert checks["physics_reset"]["passed"]  # The final state alone hides the excursion.
    assert not checks["physics_settling"]["passed"]
    assert checks["physics_settling"]["issues"][0]["object_id"] == "part"
    assert rejected.manifest["validation"]["status"] == "unvalidated"
    data["objects"]["part"]["peak_excursion_m"] = .001
    repaired = World.from_spec(spec, tmp_path, runtime_factory=Stub, parent_id=rejected.id)
    assert repaired.manifest["validation"]["status"] == "ready"
    assert repaired.id != rejected.id
