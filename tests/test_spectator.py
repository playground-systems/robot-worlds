"""Collision-aware camera placement without a simulator or graphics context."""
import math

import numpy as np
import pytest

from robot_worlds.spectator import clear_camera


def wall_x(x):
    def hit(origin, direction, maximum):
        if direction[0] <= 0:
            return None
        distance = (x-origin[0])/direction[0]
        return distance if 0 <= distance <= maximum else None
    return hit


def test_camera_retracts_before_east_wall_instead_of_clipping_delivery():
    target = [3.55, .77, .8]
    angle, elevation, distance = clear_camera(target, 4, 135, -25, wall_x(5.9))
    assert angle == 135 and elevation == -25 and distance < 4
    eye_x = target[0] - distance*math.cos(math.radians(angle))*math.cos(math.radians(elevation))
    assert eye_x < 5.9-.25


def test_near_plane_probes_catch_wall_even_when_center_ray_is_clear():
    seen = []
    def obstacle(origin, direction, maximum):
        seen.append(origin.copy())
        return 3 if origin[0] > .1 else None
    _, _, distance = clear_camera([0, 0, 1], 4, 90, -25, obstacle)
    assert distance == 2.75 and len(seen) == 9


def test_camera_uses_another_angle_when_wall_would_force_an_unusable_closeup():
    angle, elevation, distance = clear_camera([5.7, 0, 1], 4, 135, -25, wall_x(5.9))
    assert angle != 135 and distance >= 2
    assert 5.7-distance*math.cos(math.radians(angle))*math.cos(math.radians(elevation)) < 5.9


def test_open_space_preserves_requested_view_and_does_not_change_target():
    point = np.array([0., 0., .8])
    assert clear_camera(point, 4, 135, -25, lambda *args: None) == (135, -25, 4)
    np.testing.assert_array_equal(point, [0, 0, .8])
    with pytest.raises(RuntimeError, match="No clear spectator"):
        clear_camera(point, 4, 135, -25, lambda *args: .2)
