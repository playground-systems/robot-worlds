"""Robot-frame sensor math. No object identities, world map or evaluator inputs."""
from __future__ import annotations

import math

import numpy as np


def local_point(point, origin, rotation):
    return (np.asarray(rotation).T @ (np.asarray(point) - origin)).tolist()


def world_point(point, origin, rotation):
    point = np.asarray(point, dtype=float)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ValueError("position must contain three finite robot-base coordinates")
    return np.asarray(origin) + np.asarray(rotation) @ point


class WheelOdometry:
    """Encoder dead reckoning; slipping wheels cause drift, never map correction."""

    def __init__(self, wheel_angles, yaw):
        self.previous = np.asarray(wheel_angles).copy()
        self.initial_yaw = yaw
        self.previous_yaw = yaw
        self.position = np.zeros(2)
        self.yaw = 0.0
        self.distance = 0.0

    def update(self, wheel_angles, yaw):
        angles = np.asarray(wheel_angles)
        distance = float(np.mean(angles - self.previous)) * 0.0762
        delta = math.atan2(math.sin(yaw - self.previous_yaw), math.cos(yaw - self.previous_yaw))
        heading = self.yaw + delta / 2
        self.position += distance * np.array([math.cos(heading), math.sin(heading)])
        self.distance += distance
        self.yaw = math.atan2(math.sin(yaw - self.initial_yaw), math.cos(yaw - self.initial_yaw))
        self.previous, self.previous_yaw = angles.copy(), yaw

    def observation(self):
        return {"frame": "odom", "position_xy_m": self.position.tolist(), "yaw_rad": self.yaw,
                "source": "wheel encoders and relative IMU heading; uncorrected for slip"}


def depth_point(depth, pixel, focal, camera_position, camera_rotation):
    """Backproject a measured pixel into the base frame using calibrated extrinsics."""
    u, v = pixel
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (u, v)):
        raise ValueError("pixel must contain integer image coordinates")
    if not (0 <= u < depth.shape[1] and 0 <= v < depth.shape[0]):
        raise ValueError("pixel is outside the camera image")
    z = float(depth[v, u])
    if not math.isfinite(z) or not 0.05 <= z <= 10:
        return {"status": "no_depth", "pixel": [u, v]}
    # MuJoCo cameras look along -Z, with image-up along +Y.
    point = np.array([(u - (depth.shape[1] - 1) / 2) * z / focal,
                      -(v - (depth.shape[0] - 1) / 2) * z / focal, -z])
    return {"status": "measured", "pixel": [u, v], "depth_m": round(z, 4),
            "frame": "base", "surface_position": np.round(camera_rotation @ point + camera_position, 4).tolist()}
