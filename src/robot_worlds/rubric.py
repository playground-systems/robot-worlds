"""Deterministic rubric evaluation against privileged, time-stamped state.

Truth is measured from state, never inferred from an actor's claimed tool result.
Durations are checked over recorded samples; reports retain sampling limits.
"""
from __future__ import annotations

import math
from typing import Any

from .models import Condition, WorldSpec

SUPPORTED_PREDICATES = frozenset({"in_region", "on_top", "near", "holding", "released", "carried_distance", "robot_in_region", "joint_at", "process_complete"})


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
        if c.predicate != "robot_in_region" and (c.object_id not in objects or not objects[c.object_id].collision):
            errors.append(f"{label}: object_id must reference a physical object")
        if c.predicate == "robot_in_region" and c.object_id not in (None, "robot"):
            errors.append(f"{label}: robot_in_region object_id must be null or robot")
        if c.predicate in {"in_region", "robot_in_region"} and c.target_id not in regions:
            errors.append(f"{label}: target_id must reference a region")
        if c.predicate in {"on_top", "near"}:
            if c.target_id not in objects or not objects[c.target_id].collision or c.target_id == c.object_id:
                errors.append(f"{label}: target_id must reference a different physical object")
        if c.predicate == "near" and (c.threshold is None or c.threshold <= 0):
            errors.append(f"{label}: near requires a positive distance threshold")
        if c.predicate in {"holding", "released"}:
            obj = objects.get(c.object_id)
            if obj is not None and not obj.movable:
                errors.append(f"{label}: {c.predicate} requires a movable workpiece; "
                              "reference the movable assembly root, not a fixed component or visual mesh")
        if c.predicate == "carried_distance":
            if c.threshold is None or c.threshold <= 0:
                errors.append(f"{label}: carried_distance requires a positive distance threshold")
            if c.target_id is not None:
                errors.append(f"{label}: carried_distance measures the subject trajectory without a target_id")
            if c.object_id in objects and not objects[c.object_id].movable:
                errors.append(f"{label}: carried_distance requires a movable workpiece")
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


def _object_bounds(obj, state):
    position, half = _vector(state.get("position")), _extents(obj, state)
    envelope_low = [position[i]-half[i] for i in range(3)]
    envelope_high = [position[i]+half[i] for i in range(3)]
    measured = state.get("geometry_bounds")
    if measured is None:
        return envelope_low, envelope_high  # Older traces retain conservative bounds.
    if (obj.kind not in {"mesh", "revolved"} or not isinstance(measured, dict)
            or measured.get("source") != "native_collision_vertices_v1"):
        raise ValueError("unsupported physical geometry measurement")
    low, high = _vector(measured.get("lower")), _vector(measured.get("upper"))
    if any(low[i] >= high[i] or low[i] < envelope_low[i]-1e-5
           or high[i] > envelope_high[i]+1e-5 for i in range(3)):
        raise ValueError("physical geometry measurement exceeds the frozen shape envelope")
    return low, high


def rigid_members(objects, identifier):
    """Include fixed child geometry; articulated children have their own motion."""
    members = {identifier}
    while True:
        expanded = members | {o.id for o in objects.values()
                              if o.parent_id in members and not o.joint and not o.movable}
        if expanded == members:
            return members
        members = expanded


def _body_bounds(objects, identifier, states):
    low, high = [math.inf] * 3, [-math.inf] * 3
    contacts = set()
    for member in rigid_members(objects, identifier):
        # Appearance meshes are not physical solids. Their rotated bounding
        # boxes can enclose large empty corners (e.g. the round bowl), inventing
        # penetration even when every source collision piece fits and is supported.
        # Keep every physical attached component; never shrink success tolerances.
        if not objects[member].collision:
            continue
        state = states.get(member)
        if not isinstance(state, dict):
            raise ValueError(f"missing attached geometry state for {member}")
        member_low, member_high = _object_bounds(objects[member], state)
        low = [min(low[i], member_low[i]) for i in range(3)]
        high = [max(high[i], member_high[i]) for i in range(3)]
        # A missing contact measurement cannot prove release or support.
        if isinstance(state.get("contacts"), list):
            contacts.update(state["contacts"])
    return [(low[i] + high[i]) / 2 for i in range(3)], [(high[i] - low[i]) / 2 for i in range(3)], contacts


def measure_predicate(spec: WorldSpec, condition: Condition, snapshot: dict) -> dict[str, Any]:
    """Return three-valued truth; missing measurements are never task failures."""
    c = condition
    if c.predicate not in SUPPORTED_PREDICATES:
        raise ValueError(f"Unsupported predicate: {c.predicate}")
    objects = {o.id: o for o in spec.objects}
    regions = {r.id: r for r in spec.regions}
    try:
        if c.predicate != "robot_in_region" and not objects[c.object_id].collision:
            raise ValueError("object_id must reference a physical object, not appearance-only geometry")
        if c.predicate in {"near", "on_top"} and not objects[c.target_id].collision:
            raise ValueError("target_id must reference a physical object, not appearance-only geometry")
        state = snapshot.get("robot") if c.predicate == "robot_in_region" else snapshot.get("objects", {}).get(c.object_id)
        if not isinstance(state, dict):
            raise ValueError(f"missing state for {c.object_id or 'robot'}")
        measured: Any
        if c.predicate in {"holding", "released"}:
            if not objects[c.object_id].movable:
                raise ValueError(f"{c.predicate} requires a movable workpiece; "
                                 "reference the movable assembly root, not a fixed component or visual mesh")
            if "held_by" not in state:
                raise ValueError("missing measured gripper contact/holding state")
            measured = state["held_by"]
            if measured is not None and measured not in ("left", "right", "both"):
                raise ValueError("malformed measured holding state")
            satisfied = (measured is not None) == (c.predicate == "holding")
        elif c.predicate == "carried_distance":
            # One snapshot can establish the start of a streak, never travel.
            _vector(state.get("position"))
            if "held_by" not in state or state["held_by"] not in (None, "left", "right", "both"):
                raise ValueError("missing or malformed measured holding state")
            measured = {"distance_m": 0.0, "measurement": "single-sample baseline; grade uses full trajectory"}
            satisfied = False
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
            if c.predicate == "robot_in_region":
                half = [0, 0, 0]
            else:
                position, half, _ = _body_bounds(objects, c.object_id, snapshot.get("objects", {}))
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
                position, half, contacts = _body_bounds(objects, c.object_id, snapshot.get("objects", {}))
                # Support names a particular surface (e.g. microwave floor), not
                # the highest point of every component attached to the appliance.
                target_low, target_high = _object_bounds(objects[c.target_id], target)
                target_position = [(target_low[i]+target_high[i])/2 for i in range(3)]
                target_half = [(target_high[i]-target_low[i])/2 for i in range(3)]
                if "contacts" not in state or not isinstance(state["contacts"], list):
                    raise ValueError("on_top requires measured object contact IDs")
                gap = position[2]-half[2] - target_position[2]-target_half[2]
                overlap = [min(position[i]+half[i], target_position[i]+target_half[i]) - max(position[i]-half[i], target_position[i]-target_half[i]) for i in (0, 1)]
                measured = {"vertical_gap": gap, "overlap": overlap, "contact": c.target_id in contacts}
                satisfied = abs(gap) <= c.tolerance and all(x > 0 for x in overlap) and measured["contact"]
        else:
            raise ValueError("unimplemented predicate")
        return {"satisfied": bool(satisfied), "measured": measured, "reason": None}
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        return {"satisfied": None, "measured": None, "reason": str(exc)}


def carried_measurements(spec: WorldSpec, condition: Condition, trace: list[dict]) -> list[dict]:
    """Net planar displacement during an uninterrupted sampled grasp streak.

    A release or unknown sample resets the origin. Back-and-forth motion does
    not accumulate distance. This measures recorded evidence, not actor claims.
    """
    origin, started = None, None
    measurements = []
    for frame in trace:
        baseline = measure_predicate(spec, condition, frame)
        if baseline["satisfied"] is None:
            origin, started = None, None
            measurements.append(baseline)
            continue
        state = frame["objects"][condition.object_id]
        position = _vector(state["position"])
        if state["held_by"] is None:
            origin, started = None, None
            distance = 0.0
        else:
            if origin is None:
                origin, started = position, frame["time"]
            distance = math.dist(origin[:2], position[:2])
        measurements.append({
            "satisfied": distance >= condition.threshold,
            "measured": {"distance_m": distance, "threshold_m": condition.threshold,
                         "grasp_start_position": origin, "position": position,
                         "continuous_since": started, "held_by": state["held_by"],
                         "measurement": "net XY displacement within continuous sampled holding"},
            "reason": None,
        })
    return measurements


class _ProgressGrader:
    """Internal cache for the built-in runtime's append-only snapshot trace.

    Reuse measurements, not verdicts: temporal intervals and ordering are still
    recomputed by the same grader. Replacement/truncation invalidates the cache.
    Do not use this for caller-mutated snapshots or arbitrary runtime adapters.
    """
    def __init__(self, spec):
        self.spec = spec.model_copy(deep=True)
        self.frames = []
        self.values = {c.id: [] for c in self.spec.rubric if c.predicate != "carried_distance"}

    def score(self, trace):
        if len(trace) < len(self.frames) or any(a is not b for a, b in zip(self.frames, trace)):
            self.frames.clear()
            for values in self.values.values():
                values.clear()
        for frame in trace[len(self.frames):]:
            for criterion in self.spec.rubric:
                if criterion.id in self.values:
                    self.values[criterion.id].append(measure_predicate(self.spec, criterion, frame))
            self.frames.append(frame)
        return grade(self.spec, trace, _measurements=self.values)


def grade(spec: WorldSpec, trace: list[dict], *, _measurements=None) -> dict:
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
        measurements = (carried_measurements(spec, c, trace) if c.predicate == "carried_distance"
                        else _measurements[cid] if _measurements is not None
                        else [measure_predicate(spec, c, frame) for frame in trace])
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
                         "measurement_limit": "Duration and ordering are verified at recorded samples, not between them. Holding is physical bilateral gripper contact; carrying measures net XY displacement during uninterrupted sampled holding, not path length or unseen motion between samples; on_top is contact plus conservative geometric support."}}
