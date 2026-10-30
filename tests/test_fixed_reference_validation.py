"""Reference imports relax only overlap checks for genuinely fixed bodies."""

import pytest

from robot_worlds.artifacts import read_json, write_json
from robot_worlds.generator import validate_spec
from robot_worlds.models import Criterion, JointSpec, Region, SceneObject, WorldSpec
from robot_worlds.sdk import World, load


def reference():
    return WorldSpec(
        name="Reference fixture", description="Fixed compound geometry", task="Visit target",
        bounds=(8, 8, 3),
        objects=[SceneObject(id=oid, label=oid, kind="box", position=(2, 0, .5), size=(1, 1, 1))
                 for oid in ("a", "b")],
        regions=[Region(id="target", label="target", position=(-2, 0, .75), size=(1, 1, 1.5))],
        rubric=[Criterion(id="visit", description="Visit", predicate="robot_in_region", target_id="target")],
    )


def test_fixed_overlap_is_explicit_and_recorded():
    spec = reference()
    assert any("overlap" in e for e in validate_spec(spec))
    skipped = []
    assert validate_spec(spec, validation_profile="fixed_reference", skipped_fixed_pairs=skipped) == []
    assert skipped == [["a", "b"]]


@pytest.mark.parametrize("dynamic", ["movable", "joint", "movable_ancestor", "joint_ancestor"])
def test_dynamic_bodies_and_fixed_descendants_are_never_exempt(dynamic):
    spec = reference()
    target = spec.objects[0]
    if dynamic.endswith("ancestor"):
        ancestor = SceneObject(id="parent", label="parent", kind="box", position=(2, 2, .5), size=(.2, .2, .2))
        spec.objects.append(ancestor)
        target.parent_id = "parent"
        target = ancestor
    if dynamic.startswith("movable"):
        target.movable = True
    else:
        target.joint = JointSpec(type="hinge", axis=(0, 0, 1), range=(-1, 1), anchor=(2, 2, .5))
    errors = validate_spec(spec, validation_profile="fixed_reference")
    assert "a/b: initial collision volumes overlap" in errors


def test_robot_clearance_bounds_and_unknown_profiles_remain_errors():
    spec = reference()
    spec.robot_start = (2, 0, 0)
    assert any("robot clearance" in e for e in validate_spec(spec, validation_profile="fixed_reference"))
    spec.objects[0].position = (4, 0, .5)
    assert any("outside world bounds" in e for e in validate_spec(spec, validation_profile="fixed_reference"))
    with pytest.raises(ValueError, match="validation_profile"):
        validate_spec(spec, validation_profile="skip_everything")


def test_profile_and_pair_evidence_are_hashed_into_immutable_version(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda robot: {"test": "asset"})
    strict = World.from_spec(reference(), tmp_path, validate=False)
    fixed = World.from_spec(reference(), tmp_path, validate=False, validation_profile="fixed_reference")
    assert strict.manifest["validation"]["status"] == "unvalidated"
    assert fixed.manifest["validation"]["status"] == "preview"
    assert strict.id != fixed.id
    assert strict.manifest["physical_id"] == fixed.manifest["physical_id"]
    validation = fixed.manifest["validation"]
    assert validation["profile"] == "fixed_reference"
    assert validation["skipped_fixed_overlap_count"] == 1
    assert validation["skipped_fixed_overlap_pairs"] == [["a", "b"]]
    manifest = read_json(fixed.path / "manifest.json")
    manifest["validation"]["skipped_fixed_overlap_pairs"] = []
    write_json(fixed.path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="validation evidence"):
        load(fixed.path)
