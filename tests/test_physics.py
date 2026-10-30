"""Actual simulator boundaries: these tests use the licensed TIAGo++ model."""

import numpy as np
import pytest

from robot_worlds.models import Criterion, SceneObject, WorldSpec
from robot_worlds.runtime import Runtime


def world(objects=()):
    return WorldSpec(
        name="Physics fixture",
        description="Internal engineering fixture",
        task="Move and manipulate the test block",
        bounds=(6, 6, 3),
        objects=list(objects),
        rubric=[
            Criterion(id="hold", description="Hold block", predicate="holding", object_id="block")
        ],
    )


def block_and_support():
    return [
        SceneObject(
            id="table",
            label="support",
            kind="box",
            position=(0.55, -0.4, 0.56),
            size=(0.5, 0.5, 0.06),
        ),
        SceneObject(
            id="block",
            label="test block",
            kind="box",
            position=(0.55, -0.4, 0.625),
            size=(0.045, 0.045, 0.06),
            mass=0.03,
            movable=True,
        ),
    ]


def action(runtime, name, **args):
    return runtime.execute({"tool": name, "arguments": args})


def test_wheel_contact_navigation_and_replay():
    runtime = Runtime(world(), render=False)
    try:
        initial = runtime.snapshot()
        result = action(runtime, "drive", distance=0.25, turn=0.3)
        assert result["status"] == "reached"
        assert 0.18 < result["distance_traveled"] < 0.32
        assert 0.20 < result["turn_reached"] < 0.4
        assert len(runtime.trace) > 20
        assert runtime.data.ncon > 0
        runtime.restore_snapshot(initial)
        np.testing.assert_allclose(runtime.data.qpos, initial["qpos"])
    finally:
        runtime.close()


def test_contact_grasp_lift_and_release():
    runtime = Runtime(world(block_and_support()), render=False)
    try:
        initial_z = runtime.snapshot()["objects"]["block"]["position"][2]
        for point in ([0.55, -0.4, 0.78], [0.55, -0.4, 0.625]):
            assert (
                action(runtime, "move_arm", hand="right", position=point, approach="down")["status"]
                == "reached"
            )
        assert action(runtime, "gripper", hand="right", opening=0)["contact_detected"]
        action(runtime, "move_arm", hand="right", position=[0.55, -0.4, 0.78], approach="down")
        lifted = runtime.snapshot()["objects"]["block"]
        assert lifted["held_by"] == "right"
        assert lifted["position"][2] > initial_z + 0.09
        assert "table" not in lifted["contacts"]
        action(runtime, "wait", seconds=2)
        held = runtime.snapshot()["objects"]["block"]
        assert held["held_by"] == "right"
        assert held["position"][2] > initial_z + 0.1
        action(runtime, "gripper", hand="right", opening=0.045)
        action(runtime, "wait", seconds=0.8)
        released = runtime.snapshot()["objects"]["block"]
        assert released["held_by"] is None
        assert released["position"][2] < lifted["position"][2] - 0.06
        assert runtime.model.neq == 0  # No attachment/weld constraint.
    finally:
        runtime.close()


def test_invalid_action_does_not_move_physical_state():
    runtime = Runtime(world(), render=False)
    try:
        before = runtime.data.qpos.copy()
        result = action(runtime, "move_arm", hand="right", position=[float("nan"), 0, 0])
        assert result["status"] == "invalid_action"
        np.testing.assert_equal(before, runtime.data.qpos)
        assert not runtime.observe()["sensors"]["camera_available"]
        assert "objects" not in runtime.observe()
    finally:
        runtime.close()


def test_g1_uses_physical_balance_policy_for_walking_and_reaching():
    spec = world()
    spec.robot = "g1"
    runtime = Runtime(spec, render=False)
    try:
        result = action(runtime, "drive", distance=0.5, turn=0.3)
        assert result["status"] == "reached"
        assert 0.4 < result["distance_traveled"] < 0.65
        assert runtime.data.qpos[2] > 0.7
        assert runtime.model.neq == 0
        assert runtime.manifest["policy_frequency_hz"] == 50
    finally:
        runtime.close()


def test_unknown_robot_fails_explicitly():
    spec = world().model_copy(update={"robot": "unknown"})
    with pytest.raises(ValueError, match="no validated execution controller"):
        Runtime(spec, render=False)


def test_supported_shapes_and_hinge_are_real_geometry():
    from robot_worlds.models import JointSpec

    spec = world(
        [
            SceneObject(
                id="sphere",
                label="sphere",
                kind="sphere",
                position=(2, 0, 0.2),
                size=(0.2, 0.2, 0.2),
                movable=True,
            ),
            SceneObject(
                id="cylinder",
                label="cylinder",
                kind="cylinder",
                position=(2, 1, 0.2),
                size=(0.2, 0.2, 0.3),
                movable=True,
            ),
            SceneObject(
                id="door",
                label="hinged door",
                kind="box",
                position=(2, 2, 1),
                size=(0.1, 1, 2),
                joint=JointSpec(type="hinge", axis=(0, 0, 1), range=(-1, 1), anchor=(2, 1.5, 1)),
            ),
        ]
    )
    runtime = Runtime(spec, render=False)
    try:
        assert runtime.model.joint("joint_door").id >= 0
        assert "joint_position" in runtime.snapshot()["objects"]["door"]
        action(runtime, "wait", seconds=0.5)
        assert runtime.snapshot()["objects"]["sphere"]["position"][2] == pytest.approx(
            0.1, abs=0.003
        )
    finally:
        runtime.close()


def test_simulation_time_budget_interrupts_long_tool():
    runtime = Runtime(world(), render=False, max_sim_seconds=0.1)
    try:
        result = action(runtime, "drive", distance=2, turn=0)
        assert result["status"] == "time_limit"
        assert runtime.data.time == pytest.approx(0.1)
        assert runtime.trace[-1]["time"] == pytest.approx(0.1)
    finally:
        runtime.close()


def test_g1_dex1_contact_grasp_carry_and_release():
    spec = world(
        [
            SceneObject(
                id="table",
                label="support",
                kind="box",
                position=(0.43, -0.25, 0.74),
                size=(0.25, 0.2, 0.06),
            ),
            SceneObject(
                id="block",
                label="workpiece",
                kind="box",
                position=(0.43, -0.25, 0.83),
                size=(0.04, 0.04, 0.12),
                mass=0.03,
                movable=True,
            ),
        ]
    )
    spec.robot = "g1"
    runtime = Runtime(spec, render=False)
    try:
        for point in ([0.18, -0.3, 1.0], [0.43, -0.25, 0.98], [0.43, -0.25, 0.85]):
            result = action(runtime, "move_arm", hand="right", position=point, approach="forward")
            assert result["status"] == "reached", result
        assert action(runtime, "gripper", hand="right", closure=0.8)["contact_detected"]
        action(runtime, "move_arm", hand="right", position=[0.43, -0.25, 0.98], approach="forward")
        action(runtime, "wait", seconds=2)
        held = runtime.snapshot()["objects"]["block"]
        assert held["held_by"] == "right"
        assert held["position"][2] > 0.92
        assert "table" not in held["contacts"]
        before = runtime.data.qpos[:2].copy()
        action(runtime, "drive", distance=-0.3, turn=0.15)
        assert np.linalg.norm(runtime.data.qpos[:2] - before) > 0.1
        assert runtime.data.qpos[2] > 0.7
        assert runtime.snapshot()["objects"]["block"]["held_by"] == "right"
        action(runtime, "gripper", hand="right", closure=0)
        action(runtime, "wait", seconds=1)
        assert runtime.snapshot()["objects"]["block"]["held_by"] is None
        assert runtime.model.neq == 0
    finally:
        runtime.close()


def test_process_requires_continuous_physical_loading_on_fixture_child():
    from robot_worlds.models import ProcessCycle, ProcessPrerequisite

    spec = world(
        [
            SceneObject(
                id="machine",
                label="machine base",
                kind="box",
                position=(2, 0, 0.3),
                size=(0.4, 0.4, 0.6),
            ),
            SceneObject(
                id="tray",
                label="loading tray",
                kind="box",
                position=(2, 0, 0.625),
                size=(0.4, 0.4, 0.05),
                parent_id="machine",
            ),
            SceneObject(
                id="block",
                label="workpiece",
                kind="box",
                position=(2, 0, 0.68),
                size=(0.06, 0.06, 0.06),
                mass=0.03,
                movable=True,
            ),
        ]
    )
    spec.process_cycles = [
        ProcessCycle(
            id="inspection",
            label="timed inspection",
            fixture_id="machine",
            workpiece_ids=["block"],
            duration=0.2,
            output_state="inspected",
            prerequisites=[
                ProcessPrerequisite(predicate="on_top", object_id="block", target_id="tray")
            ],
        )
    ]
    runtime = Runtime(spec, render=False)
    try:
        action(runtime, "wait", seconds=0.1)
        assert "tray" in runtime.snapshot()["objects"]["block"]["contacts"]
        # A brief real upward disturbance removes loading between replay samples.
        bid = runtime.model.body("object_block").id
        runtime.data.xfrc_applied[bid, 2] = 1.8
        runtime._step(0.002)
        runtime.data.xfrc_applied[bid, 2] = 0
        runtime._step(0.04)
        action(runtime, "wait", seconds=0.07)
        assert "inspection" not in runtime.snapshot()["objects"]["block"]["process_states"]
        action(runtime, "wait", seconds=0.2)
        assert runtime.snapshot()["objects"]["block"]["process_states"]["inspection"] == "inspected"
    finally:
        runtime.close()


def test_unactuated_hinged_door_opens_from_robot_contact():
    from robot_worlds.models import JointSpec

    spec = world(
        [
            SceneObject(
                id="door",
                label="cabinet door",
                kind="box",
                position=(0.7, -0.4, 0.8),
                size=(0.04, 0.4, 0.45),
                mass=0.4,
                joint=JointSpec(
                    type="hinge",
                    axis=(0, 0, 1),
                    range=(-1.5, 0),
                    anchor=(0.7, -0.6, 0.8),
                    damping=0.2,
                ),
            ),
        ]
    )
    runtime = Runtime(spec, render=False)
    try:
        door_joint = runtime.model.joint("joint_door").id
        assert door_joint not in runtime.model.actuator_trnid[:, 0]
        assert runtime.snapshot()["objects"]["door"]["joint_position"] == 0
        action(runtime, "gripper", hand="right", opening=0)
        action(runtime, "move_arm", hand="right", position=[0.5, -0.4, 0.8], approach="forward")
        action(runtime, "move_arm", hand="right", position=[0.8, -0.4, 0.8], approach="forward")
        assert runtime.snapshot()["objects"]["door"]["joint_position"] < -0.5
        np.testing.assert_equal(runtime.data.xfrc_applied, 0)
        assert runtime.model.neq == 0
    finally:
        runtime.close()
