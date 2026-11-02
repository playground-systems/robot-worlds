"""Recorded-sample regressions for the authored transfer task contract.

These synthetic saved traces exercise grading only; they are not physics evidence.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from robot_worlds.models import WorldSpec
from robot_worlds.rubric import grade

SCRIPT = Path(__file__).parents[1] / "tools/acceptance/astra_authored_scene_plans.py"
MODULE_SPEC = importlib.util.spec_from_file_location("astra_authored_scene_plans", SCRIPT)
AUTHOR = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(AUTHOR)
SAVED = json.loads((Path(__file__).parent / "fixtures/astra_transfer_traces.json").read_text())


def transfer_world():
    authored = AUTHOR.transfer(
        "deliver",
        "Deliver the part",
        "part",
        "table",
        "Carry the part and set it down.",
    )
    return WorldSpec.model_validate({**SAVED["world"], "rubric": authored["rubric"]})


@pytest.mark.parametrize("trace_name", ["immediate_release", "fast_carry_immediate_release"])
def test_contact_and_release_may_begin_together_after_carried_motion(trace_name):
    result = grade(transfer_world(), SAVED["traces"][trace_name])
    assert result["success"], result
    by_id = {entry["id"]: entry for entry in result["criteria"]}
    assert (
        by_id["release"]["evidence"]["intervals"][-1][0]
        == (by_id["support"]["evidence"]["intervals"][-1][0])
    )


def test_releasing_before_carry_cannot_get_credit_for_later_unheld_motion():
    result = grade(transfer_world(), SAVED["traces"]["released_before_carry"])
    assert result["status"] == "failed"
    by_id = {entry["id"]: entry for entry in result["criteria"]}
    assert by_id["grasp"]["satisfied"]
    assert not by_id["carry"]["satisfied"]
    assert not by_id["release"]["satisfied"]
