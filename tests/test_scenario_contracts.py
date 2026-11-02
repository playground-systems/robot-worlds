"""Ten independent task contracts and synthetic evaluator counterexamples.

Fixtures below are semantic test data, not validated worlds or physical demos.
"""

import copy

import pytest
from scenario_contracts import acceptance_errors, scenarios

from robot_worlds.models import WorldSpec
from robot_worlds.rubric import grade, validate_rubric

SCENARIOS = scenarios()


def contract_fixture(contract):
    objects = []
    for requirement in contract["required_objects"]:
        oid = requirement["id"]
        position, size = [-5, 0, 1], [.2, .2, .2]
        if oid == "decoy":
            position = [-5, 1.5, 1]
        if oid in {"source_support", "destination_support", "process_fixture"}:
            position = {"source_support": [-5, 0, .45], "destination_support": [5, 0, .45], "process_fixture": [0, 3, .45]}[oid]
            size = [1, 1, .9]
        obj = {"id": oid, "label": oid, "kind": "box", "position": position, "size": size, "movable": requirement["movable"]}
        if oid == "cabinet_door":
            obj.update(position=[-4.9, 0, .6], size=[.1, 1, 1.2], joint={"type": "hinge", "axis": [0, 0, 1], "anchor": [-4.9, -.5, .6], "range": [0, 1.57]})
        objects.append(obj)
    criteria = []
    for requirement in contract["rubric_requirements"]:
        criterion = {k: v for k, v in requirement.items() if k not in {"min_duration", "max_tolerance"}}
        criterion.update(description=criterion["id"], duration=requirement["min_duration"], tolerance=requirement["max_tolerance"])
        criteria.append(criterion)
    process = copy.deepcopy(contract["process_requirement"])
    if process:
        process["label"] = "Timed physical loading surrogate"
    return WorldSpec.model_validate({
        "name": contract["title"], "description": "Synthetic semantic fixture, never physically validated",
        "task": contract["prompt"], "source_prompt": contract["prompt"], "robot": contract["robot"],
        "bounds": [*contract["min_bounds_xy"], 3], "robot_start": [0, -2, 0], "objects": objects,
        "regions": [{"id": "source_approach", "label": "source", "position": [-4, 0, .75], "size": [1, 1, 1.5]},
                    {"id": "destination_approach", "label": "destination", "position": [4, 0, .75], "size": [1, 1, 1.5]},
                    {"id": contract["destination_region"], "label": "output", "position": [5, 0, 1.1], "size": [1, 1, .6]}],
        "rubric": criteria, "process_cycles": [process] if process else [],
    })


def state(spec, contract, t, *, delivered=False, picked=False, processed=False, source=False, destination=False, opened=False):
    objects = {o.id: {"position": list(o.position), "size": list(o.size), "quaternion": [1, 0, 0, 0],
                      "contacts": [], "held_by": None, "process_states": {}, "joint_position": 0} for o in spec.objects}
    subject = objects[contract["subject_id"]]
    if picked:
        subject["held_by"] = "right"
    if delivered:
        subject.update(position=[5, 0, 1], contacts=["destination_support"], held_by=None)
    if processed and spec.process_cycles:
        cycle = spec.process_cycles[0]
        subject["process_states"] = {cycle.id: cycle.output_state}
    if opened and "cabinet_door" in objects:
        objects["cabinet_door"]["joint_position"] = 1.0
    robot = [4, 0, 0] if destination else [-4, 0, 0] if source else [0, -2, 0]
    return {"time": t, "objects": objects, "robot": {"position": robot}}


def witness(spec, contract):
    trace = [state(spec, contract, 0), state(spec, contract, 1, source=True, opened=True),
            state(spec, contract, 1.25, source=True, opened=True),
            state(spec, contract, 2, source=True, picked=True, opened=True),
            state(spec, contract, 3, destination=True, picked=True, opened=True)]
    part = contract["subject_id"]
    trace[-1]["objects"][part]["position"] = [-2.5, 0, 1]
    final = 4
    if spec.process_cycles:
        duration = spec.process_cycles[0].duration
        for time, processed, held, position in [(4, False, False, [0, 3, 1]),
                                              (4+duration+.1, True, False, [0, 3, 1]),
                                              (4+duration+.2, True, True, [0, 3, 1]),
                                              (4+duration+1.2, True, True, [2, 3, 1])]:
            frame = state(spec, contract, time, destination=True, processed=processed, picked=held, opened=True)
            frame["objects"][part].update(position=position, contacts=[] if held else ["process_fixture"])
            trace.append(frame)
        final = 4+duration+2
    trace.extend([state(spec, contract, final, destination=True, delivered=True, processed=True, opened=True),
                  state(spec, contract, final+1.25, destination=True, delivered=True, processed=True, opened=True)])
    return trace


def test_suite_contains_ten_distinct_internal_contracts():
    assert len(SCENARIOS) == len({s["id"] for s in SCENARIOS}) == 10
    assert {s["robot"] for s in SCENARIOS} == {"tiago++", "g1"}
    assert all(s["independent_checks"] and s["physical_review_checks"] for s in SCENARIOS)


@pytest.mark.parametrize("contract", SCENARIOS, ids=lambda c: c["id"])
def test_independent_contract_and_synthetic_positive_witness(contract):
    spec = contract_fixture(contract)
    assert acceptance_errors(spec, contract) == []
    assert validate_rubric(spec) == []
    assert grade(spec, witness(spec, contract))["success"]


@pytest.mark.parametrize("contract", SCENARIOS, ids=lambda c: c["id"])
@pytest.mark.parametrize("attack", ["wrong_object", "wrong_order", "unstable_final"])
def test_required_task_cannot_pass_counterexample_trace(contract, attack):
    spec = contract_fixture(contract)
    trace = witness(spec, contract)
    subject = contract["subject_id"]
    if attack == "wrong_object":
        for frame in trace[-2:]:
            frame["objects"]["decoy"].update(position=[5, 0, 1], contacts=["destination_support"])
            frame["objects"][subject].update(position=[-5, 0, 1], contacts=[])
    elif attack == "wrong_order":
        for frame in trace[1:]:
            frame["objects"][subject].update(position=[5, 0, 1], contacts=["destination_support"])
    else:
        final = trace[-2]["time"]
        trace.insert(-1, state(spec, contract, final+.5, destination=True, delivered=True, processed=True, opened=True))
        trace.insert(-1, state(spec, contract, final+.6, destination=True, processed=True, opened=True))
    assert not grade(spec, trace)["success"]


@pytest.mark.parametrize("contract", [s for s in SCENARIOS if s["process_requirement"]], ids=lambda c: c["id"])
def test_process_credit_cannot_transfer_from_decoy_identity(contract):
    spec = contract_fixture(contract)
    trace = witness(spec, contract)
    for frame in trace:
        frame["objects"]["decoy"]["process_states"] = frame["objects"][contract["subject_id"]]["process_states"]
        frame["objects"][contract["subject_id"]]["process_states"] = {}
    assert not grade(spec, trace)["success"]


@pytest.mark.parametrize("attack", ["distance", "region_size", "optional", "alternative", "tolerance", "duration", "ordering", "wrong_subject"])
def test_predeclared_acceptance_rejects_self_authored_rubric_shortcuts(attack):
    contract = SCENARIOS[0]
    spec = contract_fixture(contract)
    criterion = next(c for c in spec.rubric if c.id == "placed")
    if attack == "distance":
        spec.objects[0].position = (4, 0, 1)
    elif attack == "region_size":
        spec.regions[-1].size = (10, 8, 3)
    elif attack == "optional":
        criterion.required = False
    elif attack == "alternative":
        criterion.alternative_group = "easy"
    elif attack == "tolerance":
        criterion.tolerance = 10
    elif attack == "duration":
        criterion.duration = 0
    elif attack == "ordering":
        criterion.after = []
    else:
        criterion.object_id = "decoy"
    assert acceptance_errors(spec, contract)


def test_grasp_review_flags_large_boxes_without_certifying_small_ones():
    from scenario_contracts import grasp_review_warnings

    contract = SCENARIOS[0]
    spec = contract_fixture(contract)
    subject = next(o for o in spec.objects if o.id == contract["subject_id"])
    subject.size = (.18, .12, .12)
    assert "0.09m" in grasp_review_warnings(spec, contract)[0]
    subject.size = (.18, .12, .04)
    assert grasp_review_warnings(spec, contract) == []  # Only the width screen.
    spec.robot = "g1"
    assert "unassessed" in grasp_review_warnings(spec, contract)[0]
