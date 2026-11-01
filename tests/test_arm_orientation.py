"""Arm-frame regressions without constructing a simulator or loading robot meshes."""
import math

import numpy as np
import pytest

from robot_worlds.g1_runtime import G1Runtime
from robot_worlds.runtime import Runtime


def controller(cls, rotation):
    runtime = object.__new__(cls)
    runtime._base_transform = lambda: (np.array([12., -7., .3]), rotation)
    runtime._yaw = lambda: math.atan2(rotation[1, 0], rotation[0, 0])
    return runtime


@pytest.mark.parametrize('cls', [Runtime, G1Runtime])
@pytest.mark.parametrize('hand', ['left', 'right'])
@pytest.mark.parametrize('approach', ['forward', 'down'])
def test_arm_approach_rotates_with_the_same_base_frame_as_its_position(cls, hand, approach):
    # Yaw plus tilt catches using global axes or a yaw-only orientation while
    # position/depth coordinates use the complete base-link rotation.
    yaw, pitch = 1.2, .17
    rz = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
    ry = np.array([[math.cos(pitch), 0, math.sin(pitch)], [0, 1, 0], [-math.sin(pitch), 0, math.cos(pitch)]])
    base = rz @ ry
    reference = controller(cls, np.eye(3))._orientation_target(hand, approach, np.eye(3))
    actual = controller(cls, base)._orientation_target(hand, approach, np.eye(3))
    assert np.allclose(actual, base @ reference)
    assert np.allclose(actual.T @ actual, np.eye(3))
    assert np.linalg.det(actual) == pytest.approx(1)


@pytest.mark.parametrize('cls', [Runtime, G1Runtime])
def test_keep_preserves_current_orientation_and_invalid_approach_reads_no_pose(cls):
    runtime = object.__new__(cls)
    runtime._base_transform = lambda: pytest.fail('unneeded pose read')
    runtime._yaw = lambda: pytest.fail('unneeded heading read')
    orientation = np.diag([-1., -1., 1.])
    assert np.array_equal(runtime._orientation_target('right', 'keep', orientation), orientation)
    with pytest.raises(ValueError, match='approach'):
        runtime._orientation_target('right', 'unknown', orientation)


@pytest.mark.parametrize('distance,angle,status', [(0, .4, 'blocked_or_unreached'),
    (.06, 0, 'blocked_or_unreached'), (.01, .05, 'reached'), (0, math.pi, 'blocked_or_unreached')])
def test_arm_feedback_requires_measured_position_and_orientation(distance, angle, status):
    from types import SimpleNamespace

    runtime = object.__new__(Runtime)
    runtime._sites = {'right': 0}
    rotation = np.array([[math.cos(angle), -math.sin(angle), 0],
                         [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
    runtime.data = SimpleNamespace(site_xpos=np.array([[distance, 0, 0]]), site_xmat=rotation.reshape(1, 9))
    result = runtime._arm_feedback('right', np.zeros(3), np.eye(3))
    assert result['status'] == status
    assert result['error_m'] == pytest.approx(distance)
    assert result['orientation_error_rad'] == pytest.approx(angle)
    assert result['actual_position'] == [distance, 0, 0]


def test_g1_replans_when_position_is_reached_but_orientation_is_not(monkeypatch):
    calls = []
    results = iter([
        {'status': 'blocked_or_unreached', 'error_m': .001, 'orientation_error_rad': .4},
        {'status': 'reached', 'error_m': .001, 'orientation_error_rad': .01},
    ])
    def step(*args):
        calls.append(args)
        return next(results)
    monkeypatch.setattr(Runtime, '_move_arm', step)
    result = G1Runtime._move_arm(object.__new__(G1Runtime), 'left', np.zeros(3), 'forward')
    assert len(calls) == 2
    assert result['status'] == 'reached'


def test_execute_keeps_position_and_approach_in_one_frame_and_returns_local_feedback():
    from types import SimpleNamespace

    base = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    runtime = controller(Runtime, base)
    runtime.data = SimpleNamespace(time=0.)
    runtime._closed = False
    runtime._odometry = None
    runtime.trace = [{'time': 0.}]
    captured = {}
    def move(hand, target, approach):
        captured['target'] = target
        captured['orientation'] = runtime._orientation_target(hand, approach, np.eye(3))
        return {'status': 'reached', 'actual_position': target.tolist(), 'orientation_error_rad': .01}
    runtime._move_arm = move
    result = runtime.execute({'tool': 'move_arm', 'arguments': {
        'hand': 'right', 'position': [.5, -.3, .6], 'approach': 'forward'}})
    assert captured['target'] == pytest.approx([12.3, -6.5, .9])
    # TIAGo right-hand local +Z is the grasp approach axis; it follows base +X.
    assert captured['orientation'][:, 2] == pytest.approx([0, 1, 0])
    assert result['actual_position'] == pytest.approx([.5, -.3, .6])
    assert result['frame'] == 'base' and result['orientation_error_rad'] == .01
    assert not {'orientation', 'world_position', 'objects', 'rubric'} & result.keys()


def test_ik_updates_pose_and_jacobian_without_solving_hypothetical_contacts(monkeypatch):
    import mujoco

    model = mujoco.MjModel.from_xml_string('''<mujoco><worldbody><body>
      <joint type="slide" axis="1 0 0" range="-1 1"/>
      <joint type="slide" axis="0 1 0" range="-1 1"/>
      <joint type="slide" axis="0 0 1" range="-1 1"/>
      <geom type="sphere" size=".05"/><site name="hand"/>
    </body></worldbody></mujoco>''')
    runtime = object.__new__(Runtime)
    runtime.model = model
    runtime.data, runtime._ik_data = mujoco.MjData(model), mujoco.MjData(model)
    runtime._arm_joints, runtime._sites = {'right': np.arange(3)}, {'right': 0}
    commands = []
    runtime._arm_controls = lambda hand: np.zeros(3)
    runtime._command_arm = lambda hand, target: commands.append(target.copy())
    runtime._step = lambda seconds: None  # This check isolates the IK planner.
    runtime._arm_feedback = lambda *args: {'status': 'measured separately'}
    full_forward = mujoco.mj_forward
    monkeypatch.setattr(mujoco, 'mj_forward', lambda *args: pytest.fail(
        'IK must not solve contacts on random hypothetical arm poses'))
    target = np.array([.2, .1, .1])
    runtime._move_arm('right', target)
    assert np.linalg.norm(commands[-1]-target) < .006
    assert runtime.data.qpos == pytest.approx(np.zeros(3))
    assert runtime.data.time == 0
    reference = mujoco.MjData(model)
    reference.qpos[:] = runtime._ik_data.qpos
    full_forward(model, reference)
    assert runtime._ik_data.site_xpos == pytest.approx(reference.site_xpos)
    expected, actual = np.zeros((3, model.nv)), np.zeros((3, model.nv))
    mujoco.mj_jacSite(model, reference, expected, None, 0)
    mujoco.mj_jacSite(model, runtime._ik_data, actual, None, 0)
    assert actual == pytest.approx(expected)
