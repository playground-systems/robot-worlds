"""Camera-only clearance selection; never modify scene geometry or actor sensors."""
import math

import numpy as np


def near_plane_clearance(near, vertical_fov, aspect=4/3):
    """Cover the actual near-plane diagonal plus 1 cm of clearance.

    A fixed 25 cm camera radius cannot inspect an appliance cavity. This is a
    spectator-only geometric margin; actor camera calibration is unchanged.
    """
    if (not all(math.isfinite(v) for v in (near, vertical_fov, aspect))
            or near <= 0 or not 0 < vertical_fov < 180 or aspect <= 0):
        raise ValueError("Camera frustum must have positive near/aspect and FOV in (0, 180)")
    diagonal = near*math.tan(math.radians(vertical_fov)/2)*math.sqrt(1+aspect*aspect)
    return max(.02, diagonal+.01)


def clear_camera(lookat, distance, azimuth, elevation, obstruction, *, clearance=.25):
    """Sweep a small camera volume from the subject toward each candidate eye.

    obstruction(origin, unit_direction, maximum) returns the distance to the
    first scene surface, or None. Nine parallel probes cover the camera center
    and near-plane perimeter. Keep the requested angle when a useful closer
    view fits; otherwise try other directions before reporting no clear view.
    """
    lookat = np.asarray(lookat, dtype=float)
    if lookat.shape != (3,) or not np.isfinite(lookat).all():
        raise ValueError("Spectator target must be a finite 3D point")
    if not all(math.isfinite(v) for v in (distance, azimuth, elevation, clearance)) or distance <= 0 or clearance <= 0:
        raise ValueError("Spectator camera parameters must be finite with positive distance and clearance")

    def available(angle, tilt):
        a, e = math.radians(angle), math.radians(tilt)
        direction = np.array([-math.cos(a)*math.cos(e), -math.sin(a)*math.cos(e), -math.sin(e)])
        right = np.array([math.sin(a), -math.cos(a), 0])
        up = np.cross(right, -direction)
        limit = distance
        for x in (-1, 0, 1):
            for y in (-1, 0, 1):
                origin = lookat + clearance*(x*right + y*up)
                hit = obstruction(origin, direction, distance+clearance)
                if hit is not None:
                    limit = min(limit, hit-clearance)
        return max(0, limit)

    best = (available(azimuth, elevation), azimuth, elevation)
    if best[0] >= min(2, distance):
        return best[1], best[2], best[0]
    for offset in (-30, 30, -60, 60, -90, 90, -120, 120, 180):
        candidate = (available(azimuth+offset, elevation), azimuth+offset, elevation)
        if candidate[0] > best[0]:
            best = candidate
        if best[0] >= min(2, distance):
            break
    if best[0] < min(1, distance):
        raise RuntimeError("No clear spectator camera position around the robot")
    return best[1], best[2], best[0]
