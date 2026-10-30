"""Native initialization evidence, with no renderer or model API."""

import math

from robot_worlds.generator import validate_spec
from robot_worlds.models import Criterion, JointSpec, Region, SceneObject, WorldSpec
from robot_worlds.reset_diagnostics import settling_check
from robot_worlds.runtime import Runtime


def articulated_initial_scene(initial):
    return WorldSpec(
        name="Articulated reset fixture", description="Engineering fixture for initial-pose validation",
        task="Move the floor part to output", bounds=(8, 8, 3),
        objects=[
            SceneObject(id="door", label="door", kind="box", position=(2.6, 0, .4),
                        size=(.8, .1, .8), mass=1,
                        joint=JointSpec(type="hinge", axis=(0, 0, 1), anchor=(2, 0, .4),
                                        range=(0, math.pi/2), initial=initial)),
            SceneObject(id="part", label="part", kind="box", position=(2, .6, .05),
                        size=(.1, .1, .1), mass=.05, movable=True),
        ],
        regions=[Region(id="output", label="output", position=(-2, 0, .2), size=(1, 1, .4))],
        rubric=[Criterion(id="move", description="Move part", predicate="in_region", object_id="part", target_id="output")],
    )


def test_joint_initial_pose_penetration_is_captured_before_solver_ejection():
    bad = articulated_initial_scene(math.pi/2)
    assert validate_spec(bad) == []  # Reference-pose AABBs do not see the rotated door.
    runtime = Runtime(bad, render=False)
    try:
        evidence = runtime.reset_diagnostics
        pairs = [c for c in evidence["scene_contacts"]
                 if {e["id"] for e in c["entities"]} == {"door", "part"}]
        assert pairs and pairs[0]["initial_penetration_m"] > .01
        assert evidence["sample_count"] == 301
        assert abs(evidence["duration_seconds"]-.6) < 1e-9
        assert not settling_check(evidence)["passed"]
        assert runtime.data.time == 0
        assert "reset_diagnostics" not in runtime.observe()
    finally:
        runtime.close()
    repaired = Runtime(articulated_initial_scene(0), render=False)
    try:
        assert settling_check(repaired.reset_diagnostics)["passed"]
        assert repaired.reset_diagnostics["objects"]["part"]["peak_excursion_m"] < .005
    finally:
        repaired.close()


def test_world_welded_reference_overlap_is_not_a_dynamic_reset_failure():
    spec = articulated_initial_scene(0)
    spec.objects = [SceneObject(id=oid, label=oid, kind="box", position=(2, 0, .5), size=(1, 1, 1))
                    for oid in ("a", "b")]
    spec.rubric = [Criterion(id="visit", description="Visit", predicate="robot_in_region", target_id="output")]
    assert validate_spec(spec, validation_profile="fixed_reference") == []
    runtime = Runtime(spec, render=False)
    try:
        assert settling_check(runtime.reset_diagnostics)["passed"]
        assert not runtime.reset_diagnostics["scene_contacts"]
    finally:
        runtime.close()
