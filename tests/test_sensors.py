"""Actor boundary regressions with synthetic sensor readings; no graphics or model calls."""
import math
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from robot_worlds.runtime import Runtime
from robot_worlds.sensors import WheelOdometry, depth_point, local_point, world_point


def test_robot_frame_is_invariant_to_world_translation_and_rotation():
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    origin = np.array([35., -22., .8])
    np.testing.assert_allclose(local_point([35.4, -21.5, 1.], origin, rotation), [.5, -.4, .2])
    np.testing.assert_allclose(world_point([.5, -.4, .2], origin, rotation), [35.4, -21.5, 1.])
    with pytest.raises(ValueError):
        world_point([math.nan, 0, 0], origin, rotation)


def test_encoder_odometry_does_not_correct_wheel_slip_from_ground_truth():
    odom = WheelOdometry([100.] * 4, 1.2)
    odom.update([101.] * 4, 1.2)
    assert odom.observation()["position_xy_m"] == pytest.approx([.0762, 0])
    odom.update([102.] * 4, 1.2 + math.pi / 2)
    assert odom.yaw == pytest.approx(math.pi / 2)
    assert odom.position[1] > 0
    assert "world" not in odom.observation()


def test_depth_backprojection_uses_only_measured_pixel_and_calibration():
    depth = np.full((3, 3), 2.)
    reading = depth_point(depth, [1, 1], 2., np.array([.3, 0, 1.]), np.eye(3))
    assert reading["surface_position"] == [.3, 0., -1.]
    assert reading["depth_m"] == 2
    depth[1, 1] = np.inf
    assert depth_point(depth, [1, 1], 2., np.zeros(3), np.eye(3))["status"] == "no_depth"
    for pixel in ([-1, 0], [3, 0], [1.1, 0], [True, 0]):
        with pytest.raises(ValueError):
            depth_point(depth, pixel, 2., np.zeros(3), np.eye(3))


def sensor_runtime(render=False):
    runtime = object.__new__(Runtime)
    runtime._base_transform = lambda: (np.array([20., 30., .5]), np.eye(3))
    runtime._yaw = lambda: .4
    runtime._initial_yaw = .4
    runtime._odometry = WheelOdometry([0.] * 4, .4)
    runtime._sites = {"right": 0}
    runtime._arm_joints = {"right": [0]}
    runtime.model = SimpleNamespace(jnt_qposadr=np.array([0]))
    runtime.data = SimpleNamespace(time=0., qpos=np.array([.2]), site_xpos=np.array([[20.5, 29.6, 1.2]]))
    runtime._finger_positions = lambda hand: [.04, .04]
    runtime._last_result = None
    runtime._render_enabled = render
    runtime.render = lambda: b"rgb-camera"
    runtime._depth = lambda: np.full((480, 640), 1.2)
    runtime._camera_calibration = lambda: (300., np.array([.3, 0, 1.35]), np.eye(3))
    return runtime


def test_observation_has_no_object_semantics_or_global_robot_pose():
    runtime = sensor_runtime(render=True)
    observation = runtime.observe()
    assert "visible_objects" not in observation
    assert "position" not in observation["robot"]
    assert "yaw" not in observation["robot"]
    np.testing.assert_allclose(observation["robot"]["hands"]["right"]["position"], [.5, -.4, .7])
    assert observation["image"] == b"rgb-camera"
    assert len(observation["sensors"]["depth"]["values"]) == 24
    assert len(observation["sensors"]["depth"]["values"][0]) == 32
    assert observation["robot"]["odometry"]["position_xy_m"] == [0, 0]


def test_disabled_camera_cannot_fall_back_to_simulator_perception():
    runtime = sensor_runtime()
    runtime._depth = lambda: pytest.fail("disabled depth sensor was read")
    assert "image" not in runtime.observe()
    assert runtime._query_depth([20, 20]) == {"status": "sensor_unavailable"}


def test_action_boundary_converts_arm_target_and_removes_oracle_feedback():
    runtime = sensor_runtime()
    runtime._closed = False
    runtime.trace = [{"time": 0}]
    targets = []

    def move(hand, target, approach):
        targets.append(target)
        return {"status": "reached", "actual_position": target.tolist()}

    runtime._move_arm = move
    result = runtime.execute({"tool": "move_arm", "arguments": {"hand": "right", "position": [.5, -.4, .7]}})
    np.testing.assert_allclose(targets[0], [20.5, 29.6, 1.2])
    np.testing.assert_allclose(result["actual_position"], [.5, -.4, .7])
    runtime._gripper = lambda **args: {"status": "executed", "contact_detected": True, "note": "oracle", "finger_positions": [.02, .02]}
    result = runtime.execute({"tool": "gripper", "arguments": {"hand": "right", "opening": 0}})
    assert "contact_detected" not in result and "note" not in result
    runtime._drive = lambda *args: {"status": "reached", "position": [20, 30, .5], "yaw": .4, "distance_traveled": .2, "turn_reached": 0}
    result = runtime.execute({"tool": "drive", "arguments": {"distance": .2, "turn": 0}})
    assert not {"position", "yaw", "distance_traveled", "turn_reached"} & result.keys()
    assert result["encoder_distance_m"] == 0  # No encoder motion despite oracle claim.


@pytest.mark.parametrize("warning", ["mjWARN_CONTACTFULL", "mjWARN_CNSTRFULL", "mjWARN_BADQACC"])
def test_solver_capacity_or_numerical_failure_cannot_continue_as_valid_physics(warning):
    runtime = sensor_runtime()
    runtime.data.warning = [SimpleNamespace(number=0) for _ in range(int(mujoco.mjtWarning.mjNWARNING))]
    runtime._check_physics_warnings()
    runtime.data.warning[int(getattr(mujoco.mjtWarning, warning))].number = 1
    with pytest.raises(RuntimeError, match=warning):
        runtime._check_physics_warnings()
