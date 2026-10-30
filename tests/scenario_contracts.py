"""Independent, predeclared acceptance oracle for internal generated-scene trials.

This is deliberately outside the distributed package and does not claim that
matching descriptors proves physical feasibility or natural-language fidelity.
"""

import json
import math
from pathlib import Path


def scenarios():
    path = Path(__file__).parent / "scenarios" / "representative_tasks.json"
    return json.loads(path.read_text())["scenarios"]


def grasp_review_warnings(spec, contract):
    """Descriptor screening only, not a reachability or grasp-planning proof."""
    subject = next((o for o in spec.objects if o.id == contract["subject_id"]), None)
    if subject is None:
        return ["Required manipulation subject is absent; grasp feasibility unassessed"]
    if spec.robot != "tiago++":
        return ["G1/Dex1 grasp aperture and object approach feasibility are unassessed"]
    if subject.kind != "box":
        return ["Non-box grasp geometry is unassessed by this descriptor screen"]
    # Runtime's TIAGo action contract permits 0.045m opening per finger.
    # A passing width is not sufficient: collision geometry, pose, reach and
    # friction remain untested. Flag oversized whole-box grasps, not every
    # possible alternative interaction with a physical object.
    if min(subject.size) > .09:
        return [f"{subject.id}: every box dimension exceeds the nominal 0.09m TIAGo "
                "finger aperture; a whole-box parallel grasp is not established"]
    return []


def acceptance_errors(spec, contract):
    errors = []
    objects = {o.id: o for o in spec.objects}
    regions = {r.id: r for r in spec.regions}
    criteria = {c.id: c for c in spec.rubric}
    if spec.robot != contract["robot"]:
        errors.append("wrong robot preset")
    if spec.source_prompt != contract["prompt"]:
        errors.append("original scenario prompt was not retained")
    for identifier in contract.get("required_task_mentions", []):
        if identifier not in spec.task:
            errors.append(f"policy-facing task omitted required identifier {identifier}")
    if any(spec.bounds[i] < minimum for i, minimum in enumerate(contract["min_bounds_xy"])):
        errors.append("environment dimensions below independent minimum")
    for requirement in contract["required_objects"]:
        obj = objects.get(requirement["id"])
        if obj is None:
            errors.append(f"missing required object {requirement['id']}")
        elif obj.movable != requirement["movable"]:
            errors.append(f"{obj.id}: wrong movability")
    for rid in contract["required_regions"]:
        if rid not in regions:
            errors.append(f"missing required region {rid}")
    subject, target = objects.get(contract["subject_id"]), regions.get(contract["destination_region"])
    if subject is not None and target is not None:
        if math.dist(subject.position[:2], target.position[:2]) < contract["min_transfer_distance_m"]:
            errors.append("source-to-destination transfer distance was shortened")
        if max(target.size) > contract["max_destination_extent_m"]:
            errors.append("delivery region was oversized")
        if all(abs(subject.position[i]-target.position[i])+subject.size[i]/2 <= target.size[i]/2+.03 for i in range(3)):
            errors.append("subject already satisfies delivery containment at reset")
    source, destination = regions.get("source_approach"), regions.get("destination_approach")
    if source is not None and destination is not None:
        if math.dist(source.position[:2], destination.position[:2]) < contract["min_approach_separation_m"]:
            errors.append("navigation stages were collapsed into the same area")
    for requirement in contract["rubric_requirements"]:
        criterion = criteria.get(requirement["id"])
        if criterion is None:
            errors.append(f"missing criterion {requirement['id']}")
            continue
        for field in ("predicate", "object_id", "target_id", "mode", "intent"):
            if getattr(criterion, field) != requirement[field]:
                errors.append(f"{criterion.id}: wrong {field}")
        if not criterion.required or criterion.alternative_group is not None:
            errors.append(f"{criterion.id}: mandatory AND requirement weakened")
        if not set(requirement["after"]) <= set(criterion.after):
            errors.append(f"{criterion.id}: required ordering dependency omitted")
        if criterion.duration < requirement["min_duration"]:
            errors.append(f"{criterion.id}: stability requirement shortened")
        if criterion.tolerance > requirement["max_tolerance"]:
            errors.append(f"{criterion.id}: tolerance weakened")
        if "threshold" in requirement and criterion.threshold != requirement["threshold"]:
            errors.append(f"{criterion.id}: threshold changed")
    requirement = contract["process_requirement"]
    if requirement:
        process = next((p for p in spec.process_cycles if p.id == requirement["id"]), None)
        if process is None:
            errors.append("required physically loaded timed process omitted")
        else:
            for field in ("fixture_id", "workpiece_ids", "output_state"):
                if getattr(process, field) != requirement[field]:
                    errors.append(f"{process.id}: process {field} changed")
            if process.duration < requirement["duration"]:
                errors.append("process duration shortened")
            for condition in requirement["prerequisites"]:
                if not any(all(getattr(p, field) == value for field, value in condition.items())
                           for p in process.prerequisites):
                    errors.append("physical process loading condition omitted")
            fixture = objects.get(requirement["fixture_id"])
            if fixture and subject and target:
                minimum = contract["min_process_station_separation_m"]
                if min(math.dist(fixture.position[:2], point.position[:2]) for point in (subject, target)) < minimum:
                    errors.append("separate process station was collapsed into another task location")
    for requirement in contract["joint_requirements"]:
        obj = objects.get(requirement["object_id"])
        if obj is None or obj.joint is None or obj.joint.type != requirement["type"]:
            errors.append("required physical articulation omitted")
        elif not obj.joint.range[0] <= requirement["threshold"] <= obj.joint.range[1]:
            errors.append("required articulated pose outside joint range")
    if spec.unvalidated_requirements:
        errors.append("required capability remains explicitly unvalidated")
    return errors
