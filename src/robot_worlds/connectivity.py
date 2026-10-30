"""Bounded obstruction proofs for the configured planar navigation envelope.

Free space is deliberately optimistic: uncertain geometry is omitted and rounded
box-expansion corners are omitted. Disconnection therefore establishes a failure
of this clearance contract; a route never establishes robot/task solvability.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque

import numpy as np

from .models import WorldSpec

RADIUS = 0.4
HEIGHT = 1.6
EPSILON = 1e-7
MAX_CELLS = 250_000
MAX_OBJECTS = 10_000
MAX_CRITERIA = 10_000


def _fixed_ancestry(objects):
    """Memoized, nonrecursive ancestry check; invalid chains are uncertain."""
    fixed = {}
    for oid in objects:
        path, seen, current = [], set(), oid
        while current not in fixed:
            if current in seen or current not in objects:
                result = False
                break
            seen.add(current)
            path.append(current)
            obj = objects[current]
            if obj.movable or obj.joint is not None:
                result = False
                break
            if obj.parent_id is None:
                result = True
                break
            current = obj.parent_id
        else:
            result = fixed[current]
        for member in path:
            fixed[member] = result
    return fixed


def _cell_range(axis, lower, upper):
    # Include both sides of exact boundary contact. This enlarges the possible
    # start/target set, so a numerical boundary cannot manufacture a blockage.
    return range(max(0, bisect_left(axis, lower)-1), min(len(axis)-1, bisect_right(axis, upper)))


def diagnose_connectivity(spec: WorldSpec, *, max_cells: int = MAX_CELLS) -> dict:
    """Return blocked/unknown/not_checked evidence without a solvability claim.

    The 0.8m-diameter, 1.6m-high envelope is a declared planar clearance
    requirement, not the exact robot geometry or every posture it can achieve.
    """
    if not isinstance(max_cells, int) or isinstance(max_cells, bool) or not 1 <= max_cells <= MAX_CELLS:
        raise ValueError(f"max_cells must be an integer from 1 to {MAX_CELLS}")
    report = {
        "status": "not_checked",
        "contract": {"radius_m": RADIUS, "height_m": HEIGHT, "motion": "planar"},
        "method": "optimistic rectangle arrangement with exact coordinate compression",
        "errors": [], "criteria": {}, "excluded_objects": [],
        "limits": {"max_cells": max_cells, "max_objects": MAX_OBJECTS, "max_criteria": MAX_CRITERIA},
        "statistics": {},
        "limitations": [
            "Checks the configured 0.8m-diameter/1.6m-high planar clearance contract, not physical impossibility for every robot posture.",
            "Only world-fixed boxes are blockers; movable/articulated ancestry, meshes, spheres and cylinders are omitted from obstruction proofs.",
            "Rounded box-expansion corners are omitted and collision rectangles are relaxed by 1e-7m; possible routes are optimistic and never certified feasible.",
            "Declared world bounds are not invisible walls; the exterior of all finite geometry is included.",
            "Does not verify manipulation, stairs, climbing, door operation, temporal task execution, or general policy solvability.",
        ],
    }
    nav = [c for c in spec.rubric if c.predicate == "robot_in_region"]
    if not nav:
        return report
    report["status"] = "unknown"
    for c in nav:
        report["criteria"][c.id] = {"status": "unknown", "reason": "Not analyzed"}
    if len(spec.objects) > MAX_OBJECTS or len(spec.rubric) > MAX_CRITERIA:
        report["reason"] = "Object/criterion resource budget exceeded"
        return report
    objects = {o.id: o for o in spec.objects}
    regions = {r.id: r for r in spec.regions}
    if len(objects) != len(spec.objects) or any(c.target_id not in regions for c in nav):
        report["reason"] = "Invalid or unresolved geometry/region identities"
        return report
    fixed = _fixed_ancestry(objects)
    rectangles = []
    blocker_ids = []
    for obj in spec.objects:
        reason = None
        if not fixed[obj.id]:
            reason = "movable, articulated, or invalid ancestry"
        elif obj.kind != "box":
            reason = "uncertain non-box collision footprint"
        elif obj.position[2]-obj.size[2]/2 >= HEIGHT-EPSILON or obj.position[2]+obj.size[2]/2 <= EPSILON:
            reason = "outside the navigation envelope height"
        if reason:
            report["excluded_objects"].append({"id": obj.id, "reason": reason})
            continue
        x, y, _ = obj.position
        sx, sy, _ = obj.size
        lo_x, hi_x, lo_y, hi_y = x-sx/2, x+sx/2, y-sy/2, y+sy/2
        # These two rectangles are a subset of a circular footprint's exact
        # rounded-rectangle configuration obstacle. Square corner inflation
        # would overblock diagonal gaps and could create false rejections.
        pieces = [
            (lo_x-RADIUS+EPSILON, hi_x+RADIUS-EPSILON, lo_y+EPSILON, hi_y-EPSILON),
            (lo_x+EPSILON, hi_x-EPSILON, lo_y-RADIUS+EPSILON, hi_y+RADIUS-EPSILON),
        ]
        rectangles.extend(r for r in pieces if r[0] < r[1] and r[2] < r[3])
        blocker_ids.append(obj.id)
    targets = {}
    for c in nav:
        region = regions[c.target_id]
        x, y, _ = region.position
        sx, sy, _ = region.size
        targets[c.id] = (x-sx/2-c.tolerance, x+sx/2+c.tolerance,
                         y-sy/2-c.tolerance, y+sy/2+c.tolerance)
    x, y, _ = spec.robot_start
    xs, ys = {x}, {y}
    for lo_x, hi_x, lo_y, hi_y in [*rectangles, *targets.values()]:
        xs.update((lo_x, hi_x))
        ys.update((lo_y, hi_y))
    if any(not np.isfinite(v) or abs(v) > 1e6 for v in (*xs, *ys)):
        report["reason"] = "Coordinate magnitude exceeds numerical proof budget"
        return report
    xs.update((min(xs)-1, max(xs)+1))
    ys.update((min(ys)-1, max(ys)+1))
    xs, ys = sorted(xs), sorted(ys)
    nx, ny = len(xs)-1, len(ys)-1
    report["statistics"] = {"fixed_box_count": len(blocker_ids), "rectangle_count": len(rectangles),
                            "x_intervals": nx, "y_intervals": ny, "cells": nx*ny}
    if nx*ny > max_cells:
        report["reason"] = "Exact arrangement cell budget exceeded; no coarse-grid rejection attempted"
        return report
    x_index, y_index = {v: i for i, v in enumerate(xs)}, {v: i for i, v in enumerate(ys)}
    difference = np.zeros((nx+1, ny+1), dtype=np.int32)
    for lo_x, hi_x, lo_y, hi_y in rectangles:
        a, b, c, d = x_index[lo_x], x_index[hi_x], y_index[lo_y], y_index[hi_y]
        difference[a, c] += 1
        difference[b, c] -= 1
        difference[a, d] -= 1
        difference[b, d] += 1
    blocked = (difference.cumsum(0).cumsum(1)[:-1, :-1] > 0).reshape(-1)
    reached = np.zeros(nx*ny, dtype=bool)
    queue = deque()
    for i in _cell_range(xs, x, x):
        for j in _cell_range(ys, y, y):
            index = i*ny+j
            if not blocked[index]:
                queue.append(index)
                reached[index] = True
    if not queue:
        report["reason"] = "Start is inside a known clearance obstacle; use initial clearance diagnostics"
        return report
    while queue:
        index = queue.popleft()
        i, j = divmod(index, ny)
        neighbors = []
        if i:
            neighbors.append(index-ny)
        if i+1 < nx:
            neighbors.append(index+ny)
        if j:
            neighbors.append(index-1)
        if j+1 < ny:
            neighbors.append(index+1)
        for other in neighbors:
            if not blocked[other] and not reached[other]:
                reached[other] = True
                queue.append(other)
    reached = reached.reshape(nx, ny)
    blocked_criteria = set()
    for cid, (lo_x, hi_x, lo_y, hi_y) in targets.items():
        ix, iy = _cell_range(xs, lo_x, hi_x), _cell_range(ys, lo_y, hi_y)
        possible = bool(reached[ix.start:ix.stop, iy.start:iy.stop].any())
        report["criteria"][cid] = {
            "status": "unknown" if possible else "blocked",
            "reason": "Optimistic route exists; actual feasibility is unverified" if possible else
                      "Entire tolerated target region is disconnected under the configured clearance contract",
        }
        if not possible:
            blocked_criteria.add(cid)
    # A blocked optional navigation witness can still be a mandatory 'after'
    # prerequisite. Propagate impossibility, but never infer unknown witnesses.
    dependents = {}
    for c in spec.rubric:
        for dependency in c.after:
            dependents.setdefault(dependency, []).append(c.id)
    queue = deque(blocked_criteria)
    while queue:
        cid = queue.popleft()
        for child in dependents.get(cid, []):
            if child not in blocked_criteria:
                blocked_criteria.add(child)
                report["criteria"][child] = {"status": "blocked", "reason": f"Requires blocked navigation prerequisite {cid}"}
                queue.append(child)
    groups = {}
    for c in spec.rubric:
        if not c.required:
            continue
        if c.alternative_group:
            groups.setdefault(c.alternative_group, []).append(c.id)
        elif c.id in blocked_criteria:
            report["errors"].append(f"{c.id}: fails configured 0.8m-diameter/1.6m navigation clearance contract; required destination or prerequisite is disconnected by fixed boxes")
    for group, members in groups.items():
        if all(cid in blocked_criteria for cid in members):
            report["errors"].append(f"Alternative group {group}: every branch fails configured 0.8m-diameter/1.6m navigation clearance contract")
    if report["errors"]:
        report["status"] = "blocked"
    else:
        report["reason"] = "No mandatory disconnection proved; task and route feasibility remain unverified"
    return report
