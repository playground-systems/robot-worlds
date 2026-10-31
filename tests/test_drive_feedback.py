"""Drive primitives cannot consult hidden global position to terminate motion."""
import math

import numpy as np
import pytest

from robot_worlds.g1_runtime import G1Runtime
from robot_worlds.runtime import Runtime
from robot_worlds.sensors import WheelOdometry


class NoWorldPose:
    time = 0.

    @property
    def qpos(self):
        pytest.fail("Drive controller read hidden global position")


def wheel_controller(*, stalled=False, yaw=1.1):
    runtime = object.__new__(Runtime)
    runtime.data = NoWorldPose()
    runtime._odometry = WheelOdometry(np.zeros(4), yaw)
    state = {"angles": np.zeros(4), "yaw": yaw, "commands": []}
    runtime._yaw = lambda: state["yaw"]

    def command(wheels, seconds):
        state["commands"].append((wheels.copy(), seconds))
        runtime.data.time += seconds
        if not stalled:
            state["angles"] += wheels*seconds
            state["yaw"] += (wheels[0]-wheels[1])*.0762/(2*.223175)*seconds
        runtime._odometry.update(state["angles"], state["yaw"])

    runtime._wheel_command = command
    return runtime, state


def test_wheel_controller_terminates_from_encoders_and_relative_heading_only():
    runtime, state = wheel_controller()
    result = runtime._drive(.25, .3)
    assert result["status"] == "executed"
    assert result["translation_feedback"] == "wheel_odometry"
    assert result["estimated_distance_m"] == pytest.approx(.25, abs=.025)
    assert runtime._odometry.yaw == pytest.approx(.3, abs=.03)
    assert state["commands"]
    assert not {"position", "yaw", "distance_traveled"} & result.keys()


def test_stalled_encoder_controller_is_bounded_without_oracle_recovery():
    runtime, state = wheel_controller(stalled=True)
    result = runtime._drive(.2, 0)
    assert result["estimated_distance_m"] == 0
    assert runtime.data.time < 6
    assert np.all(state["commands"][-1][0] == 0)


def test_g1_translation_is_a_timed_velocity_command_with_no_position_measurement():
    runtime = object.__new__(G1Runtime)
    runtime.data = NoWorldPose()
    runtime._command = np.zeros(3)
    state = {"yaw": -.7, "commands": []}
    runtime._yaw = lambda: state["yaw"]

    def step(seconds):
        state["commands"].append((runtime._command.copy(), seconds))
        runtime.data.time += seconds
        state["yaw"] += runtime._command[2]*seconds

    runtime._step = step
    result = runtime._drive(-.5, -.3)
    assert result["status"] == "executed"
    assert result["translation_feedback"] == "unavailable"
    assert sum(command[0]*seconds for command, seconds in state["commands"]) == pytest.approx(-.5)
    assert result["commanded_translation_seconds"] == pytest.approx(2.)
    assert state["yaw"] == pytest.approx(-1., abs=.04)
    assert np.all(runtime._command == 0)
    assert not {"position", "yaw", "estimated_distance_m", "distance_traveled"} & result.keys()


@pytest.mark.parametrize("distance,turn", [(math.nan, 0), (0, math.inf), (2.1, 0), (0, 3.2)])
def test_invalid_drive_requests_do_not_reach_sensors(distance, turn):
    for cls in (Runtime, G1Runtime):
        with pytest.raises(ValueError):
            cls._drive(object.__new__(cls), distance, turn)


def test_drive_feedback_does_not_depend_on_initial_global_heading():
    samples = []
    for yaw in (0., 1.7, -2.9):
        runtime, _ = wheel_controller(yaw=yaw)
        samples.append(runtime._drive(-.2, -.3))
    assert all(s["estimated_distance_m"] == pytest.approx(samples[0]["estimated_distance_m"]) for s in samples)
