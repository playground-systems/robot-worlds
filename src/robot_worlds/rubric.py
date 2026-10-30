"""Deterministic rubric evaluation against privileged, time-stamped state.

Truth is measured from state, never inferred from an actor's claimed tool result.
Durations are checked over recorded samples; reports retain sampling limits.
"""
from __future__ import annotations

import math
from typing import Any

from .models import Condition, WorldSpec

SUPPORTED_PREDICATES = frozenset({"in_region", "on_top", "near", "holding", "released", "robot_in_region", "joint_at", "process_complete"})


def validate_rubric(spec: WorldSpec) -> list[str]:
    errors = []
    objects = {o.id: o for o in spec.objects}
    regions = {r.id: r for r in spec.regions}
    cycles = {c.id: c for c in spec.process_cycles}
    criteria = {c.id: c for c in spec.rubric}
    if len(criteria) != len(spec.rubric):
        errors.append("criterion IDs must be unique")
    if not any(c.required for c in spec.rubric):
        errors.append("rubric needs at least one required criterion")
    conditions = [(c.id, c) for c in spec.rubric]
    for cycle in spec.process_cycles:
        conditions.extend((f"process {cycle.id}", c) for c in cycle.prerequisites)
    for label, c in conditions:
        if c.predicate not in SUPPORTED_PREDICATES:
            errors.append(f"{label}: unsupported predicate {c.predicate}")
        if c.predicate != "robot_in_region" and c.object_id not in objects:
            errors.append(f"{label}: object_id must reference a physical object")
        if c.predicate == "robot_in_region" and c.object_id not in (None, "robot"):
            errors.append(f"{label}: robot_in_region object_id must be null or robot")
        if c.predicate in {"in_region", "robot_in_region"} and c.target_id not in regions:
            errors.append(f"{label}: target_id must reference a region")
        if c.predicate in {"on_top", "near"}:
            if c.target_id not in objects or c.target_id == c.object_id:
                errors.append(f"{label}: target_id must reference a different physical object")
        if c.predicate == "near" and (c.threshold is None or c.threshold <= 0):
            errors.append(f"{label}: near requires a positive distance threshold")
        if c.predicate == "joint_at":
            obj = objects.get(c.object_id)
            if not obj or not obj.joint:
                errors.append(f"{label}: joint_at requires articulated object")
            elif c.threshold is None or not obj.joint.range[0] <= c.threshold <= obj.joint.range[1]:
                errors.append(f"{label}: joint threshold must be inside the joint range")
        if c.predicate == "process_complete":
            cycle = cycles.get(c.target_id)
            if not cycle or c.object_id not in cycle.workpiece_ids:
                errors.append(f"{label}: process must reference its declared workpiece")
            elif c.value is not None and c.value != cycle.output_state:
                errors.append(f"{label}: process value differs from its defined output state")
    for c in spec.rubric:
        if len(c.after) != len(set(c.after)) or any(x not in criteria or x == c.id for x in c.after):
            errors.append(f"{c.id}: after must reference distinct other criteria")
        if c.alternative_group and not c.required:
            errors.append(f"{c.id}: alternative groups must contain required criteria")
        if c.mode == "always" and c.after:
            errors.append(f"{c.id}: always starts at reset and cannot have after prerequisites")
        if c.mode == "always" and c.intent != "maintain":
            errors.append(f"{c.id}: always requires maintenance intent; achievement needs a transition")
    visited, active = set(), set()

    def visit(cid):
        if cid in active:
            errors.append(f"criterion ordering cycle at {cid}")
            return
        if cid in visited or cid not in criteria:
            return
        active.add(cid)
        for dep in criteria[cid].after:
            visit(dep)
        active.remove(cid)
        visited.add(cid)

    for cid in criteria:
        visit(cid)
    return errors


def _vector(value, length=3):
    if not isinstance(value, (tuple, list)) or len(value) != length:
        raise ValueError("missing or malformed vector")
    result = [float(x) for x in value]
    if not all(math.isfinite(x) for x in result):
        raise ValueError("nonfinite vector")
    return result


def _extents(obj, state):
    """World AABB half-extents, conservatively accounting for rotation."""
    size = list(obj.size)
    if "size" in state and any(abs(a-b) > 1e-6 for a, b in zip(_vector(state["size"]), size)):
        raise ValueError("recorded rigid-object size differs from the frozen world specification")
    if obj.kind == "sphere":
        return [x / 2 for x in size]
    w, x, y, z = _vector(state.get("quaternion"), 4)
    if abs(w*w + x*x + y*y + z*z - 1) > 1e-3:
        raise ValueError("invalid quaternion")
    rotation = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
    return [sum(abs(row[i])*size[i]/2 for i in range(3)) for row in rotation]


def measure_predicate(spec: WorldSpec, condition: Condition, snapshot: dict) -> dict[str, Any]:
    """Return three-valued truth; missing measurements are never task failures."""
    c = condition
    if c.predicate not in SUPPORTED_PREDICATES:
        raise ValueError(f"Unsupported predicate: {c.predicate}")
    objects = {o.id: o for o in spec.objects}
    regions = {r.id: r for r in spec.regions}
    try:
        state = snapshot.get("robot") if c.predicate == "robot_in_region" else snapshot.get("objects", {}).get(c.object_id)
        if not isinstance(state, dict):
            raise ValueError(f"missing state for {c.object_id or 'robot'}")
        measured: Any
        if c.predicate in {"holding", "released"}:
            if "held_by" not in state:
                raise ValueError("missing measured gripper contact/holding state")
            measured = state["held_by"]
            if measured is not None and measured not in ("left", "right", "both"):
                raise ValueError("malformed measured holding state")
            satisfied = (measured is not None) == (c.predicate == "holding")
        elif c.predicate == "joint_at":
            measured = float(state["joint_position"])
            if not math.isfinite(measured):
                raise ValueError("nonfinite joint position")
            satisfied = abs(measured - c.threshold) <= c.tolerance
        elif c.predicate == "process_complete":
            states = state["process_states"]
            if not isinstance(states, dict):
                raise ValueError("missing measured process states")
            cycle = next(p for p in spec.process_cycles if p.id == c.target_id)
            measured = states.get(c.target_id)
            satisfied = measured == (c.value or cycle.output_state)
        elif c.predicate in {"in_region", "robot_in_region"}:
            position = _vector(state.get("position"))
            region = regions[c.target_id]
            half = [0, 0, 0] if c.predicate == "robot_in_region" else _extents(objects[c.object_id], state)
            margins = [region.size[i]/2 - abs(position[i]-region.position[i]) - half[i] for i in range(3)]
            measured = {"position": position, "containment_margins": margins}
            satisfied = all(x >= -c.tolerance for x in margins)
        elif c.predicate in {"near", "on_top"}:
            position = _vector(state.get("position"))
            target = snapshot.get("objects", {}).get(c.target_id)
            if not isinstance(target, dict):
                raise ValueError("missing target state")
            target_position = _vector(target.get("position"))
            if c.predicate == "near":
                measured = math.dist(position, target_position)
                satisfied = measured <= c.threshold
            else:
                half, target_half = _extents(objects[c.object_id], state), _extents(objects[c.target_id], target)
                if "contacts" not in state or not isinstance(state["contacts"], list):
                    raise ValueError("on_top requires measured object contact IDs")
                gap = position[2]-half[2] - target_position[2]-target_half[2]
                overlap = [min(position[i]+half[i], target_position[i]+target_half[i]) - max(position[i]-half[i], target_position[i]-target_half[i]) for i in (0, 1)]
                measured = {"vertical_gap": gap, "overlap": overlap, "contact": c.target_id in state["contacts"]}
                satisfied = abs(gap) <= c.tolerance and all(x > 0 for x in overlap) and measured["contact"]
        else:
            raise ValueError("unimplemented predicate")
        return {"satisfied": bool(satisfied), "measured": measured, "reason": None}
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        return {"satisfied": None, "measured": None, "reason": str(exc)}


def grade(spec: WorldSpec, trace: list[dict]) -> dict:
    errors = validate_rubric(spec)
    if errors:
        raise ValueError("Invalid rubric: " + "; ".join(errors))
    times = []
    for frame in trace:
        try:
            t = float(frame["time"])
            if not math.isfinite(t) or t < 0 or times and t < times[-1]:
                raise ValueError("timestamps must be finite, nonnegative, and ordered")
            times.append(t)
        except (KeyError, TypeError, ValueError) as exc:
            return {"success": False, "status": "unscorable", "criteria": [], "evidence": {"error": str(exc)}}
    by_id = {c.id: c for c in spec.rubric}
    results = {}

    def evaluate(cid):
        if cid in results:
            return results[cid]
        c = by_id[cid]
        dependencies = [evaluate(dep) for dep in c.after]
        dependency_times = [d["completion_time"] for d in dependencies]
        ready = not dependencies or all(t is not None for t in dependency_times)
        ready_at = max(dependency_times) if ready and dependencies else None
        measurements = [measure_predicate(spec, c, frame) for frame in trace]
        intervals, start = [], None
        for i, (t, measurement) in enumerate(zip(times, measurements)):
            if measurement["satisfied"] is True:
                if start is None:
                    start = i
            elif start is not None:
                intervals.append((start, i-1))
                start = None
        if start is not None:
            intervals.append((start, len(times)-1))
        qualified = [(a, b) for a, b in intervals
                     if ready
                     and (ready_at is None or times[a] > ready_at + 1e-9)
                     and (c.intent == "maintain" or a > 0 and measurements[a-1]["satisfied"] is False)
                     and times[b]-times[a] + 1e-9 >= c.duration]
        if c.mode == "final":
            qualifying = [(a, b) for a, b in qualified if b == len(times)-1]
        elif c.mode == "always":
            qualifying = [(a, b) for a, b in qualified if a == 0 and b == len(times)-1]
        else:
            qualifying = qualified
        passed = bool(qualifying)
        unknown = not trace or any(m["satisfied"] is None for m in measurements) or any(d["status"] == "unscorable" for d in dependencies)
        definite_failure = (c.mode == "final" and bool(measurements) and measurements[-1]["satisfied"] is False
                            or c.mode == "always" and any(m["satisfied"] is False for m in measurements))
        decisive_failure = None
        if definite_failure:
            unknown = False
            failure_index = len(measurements)-1 if c.mode == "final" else next(i for i, m in enumerate(measurements) if m["satisfied"] is False)
            decisive_failure = {"time": times[failure_index], "measured": measurements[failure_index]["measured"],
                                "reason": "Required final condition is false" if c.mode == "final" else "Always condition was violated"}
        # Completion witnesses are historical even if a final-state predicate later
        # regresses. The regressed criterion itself still fails the overall task.
        completion = next((times[i] for a, b in qualified for i in range(a, b+1) if times[i]-times[a] + 1e-9 >= c.duration), None)
        result = {"id": cid, "description": c.description, "status": "passed" if passed else "unscorable" if unknown else "failed", "satisfied": passed,
                  "required": c.required, "alternative_group": c.alternative_group, "completion_time": completion,
                  "evidence": {"predicate": c.predicate, "object_id": c.object_id, "target_id": c.target_id,
                               "mode": c.mode, "intent": c.intent, "duration_required": c.duration, "after": c.after,
                               "decisive_failure": decisive_failure,
                               "initial": measurements[0] if measurements else None,
                               "final": measurements[-1] if measurements else None,
                               "intervals": [[times[a], times[b]] for a, b in intervals],
                               "missing_measurements": [{"time": t, "reason": m["reason"]} for t, m in zip(times, measurements) if m["satisfied"] is None]}}
        results[cid] = result
        return result

    for cid in by_id:
        evaluate(cid)
    clauses, groups = [], {}
    for c in spec.rubric:
        if not c.required:
            continue
        if c.alternative_group:
            groups.setdefault(c.alternative_group, []).append(results[c.id]["status"])
        else:
            clauses.append(results[c.id]["status"])
    for statuses in groups.values():
        clauses.append("passed" if "passed" in statuses else "unscorable" if "unscorable" in statuses else "failed")
    status = "failed" if "failed" in clauses else "unscorable" if "unscorable" in clauses else "passed"
    if spec.unvalidated_requirements:
        status = "unscorable"
    return {"success": status == "passed", "status": status,
            "criteria": [results[c.id] for c in spec.rubric],
            "evidence": {"unvalidated_requirements": spec.unvalidated_requirements, "sample_count": len(times), "duration": times[-1]-times[0] if times else 0,
                         "maximum_sample_gap": max((b-a for a, b in zip(times, times[1:])), default=0),
                         "measurement_limit": "Duration and ordering are verified at recorded samples, not between them. Holding is physical bilateral gripper contact; on_top is contact plus conservative geometric support."}}
