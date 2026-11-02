"""Cheap release-gate and foundation checks; native evidence is produced on Linux."""
import importlib.util
import io
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from robot_worlds.artifacts import file_digest
from robot_worlds.generator import validate_spec
from robot_worlds.models import WorldSpec
from robot_worlds.sdk import World

MODULE = Path(__file__).resolve().parents[1] / "tools/starters/freeze.py"
specification = importlib.util.spec_from_file_location("starter_freeze", MODULE)
freeze = importlib.util.module_from_spec(specification)
specification.loader.exec_module(freeze)


def source_spec():
    return WorldSpec.model_validate({
        "name": "Source foundation", "description": "Test-only static source", "task": "Drive ahead",
        "bounds": [8, 8, 3], "objects": [
            {"id": "foundation", "label": "Source floor", "kind": "box",
             "position": [2, 0, -.1], "size": [1, 1, .2]},
        ],
        "regions": [{"id": "arrival", "label": "Arrival zone", "position": [-2, 0, .5], "size": [1, 1, 1]}],
        "rubric": [{"id": "arrive", "description": "Arrive", "predicate": "robot_in_region",
                    "target_id": "arrival", "tolerance": .005}],
    })


def test_fixed_reference_foundation_exception_keeps_other_bounds():
    plan = source_spec()
    assert validate_spec(plan, validation_profile="fixed_reference") == []
    assert any("outside world bounds" in e for e in validate_spec(plan))
    for position in ([4, 0, -.1], [2, 0, 3.]):
        changed = plan.model_copy(deep=True)
        changed.objects[0].position = position
        assert any("outside world bounds" in e for e in validate_spec(changed, validation_profile="fixed_reference"))
    changed = plan.model_copy(deep=True)
    changed.regions[0].position = [-2, 0, .4]
    assert any("arrival: geometry/region extends outside" in e
               for e in validate_spec(changed, validation_profile="fixed_reference"))


@pytest.mark.parametrize("ancestry", ["movable", "joint", "missing", "cycle"])
def test_moving_or_unresolved_ancestry_cannot_be_a_foundation(ancestry):
    data = source_spec().model_dump()
    child = data["objects"][0]
    if ancestry == "movable":
        data["objects"].append({"id": "moving", "label": "Moving parent", "kind": "box",
                                "position": [2, 0, .6], "size": [.1, .1, .1], "movable": True})
        child["parent_id"] = "moving"
    elif ancestry == "joint":
        child["joint"] = {"type": "hinge", "axis": [0, 0, 1], "anchor": [2, 0, 0], "range": [0, 1]}
    else:
        child["parent_id"] = "absent" if ancestry == "missing" else "foundation"
    errors = validate_spec(WorldSpec.model_validate(data), validation_profile="fixed_reference")
    assert any("foundation: geometry/region extends outside" in e for e in errors)


def test_only_fixed_reference_ground_backing_can_meet_initial_robot_envelope():
    plan = source_spec()
    plan.objects[0].position = [0, 0, 0]
    plan.objects[0].size = [2, 2, .0005]
    assert validate_spec(plan, validation_profile="fixed_reference") == []
    assert any("initial robot clearance" in e for e in validate_spec(plan))
    for change in ("raised", "movable", "articulated"):
        changed = plan.model_copy(deep=True)
        if change == "raised":
            changed.objects[0].position = [0, 0, .001]
        elif change == "movable":
            changed.objects[0].movable = True
        else:
            data = changed.model_dump()
            data["objects"][0]["joint"] = {"type": "hinge", "axis": [0, 0, 1],
                                           "anchor": [0, 0, 0], "range": [0, 1]}
            changed = WorldSpec.model_validate(data)
        assert any("initial robot clearance" in e
                   for e in validate_spec(changed, validation_profile="fixed_reference"))


def test_hidden_assembly_visibility_uses_welded_groups_not_articulated_children(monkeypatch):
    data = source_spec().model_dump()
    data["objects"][0].update(position=[2, 0, .2], visible=False)
    data["objects"].append({"id": "visible_child", "label": "Visible articulated child", "kind": "box",
                            "position": [2, 0, 1], "size": [.1, .1, .1], "parent_id": "foundation",
                            "joint": {"type": "hinge", "axis": [0, 0, 1],
                                      "anchor": [2, 0, 1], "range": [0, 1]}})
    assert any("hidden collision geometry" in e for e in validate_spec(WorldSpec.model_validate(data)))
    data["objects"][1].pop("joint")
    # Hidden-part checks must not rescan all objects per part in a large source
    # assembly. No interior regions in this fixture need rigid_members either.
    def no_global_rescan(*args):
        raise AssertionError("Hidden parts must use the precomputed welded-root visibility set")
    monkeypatch.setattr("robot_worlds.generator.rigid_members", no_global_rescan)
    assert validate_spec(WorldSpec.model_validate(data)) == []


def test_native_reset_bounds_mirror_fixed_foundation_exception(tmp_path, monkeypatch):
    class FixtureRuntime:
        """Bounds orchestration fixture only; cannot pass the native release gate."""
        def __init__(self, spec, **kwargs):
            self.spec = spec
        def snapshot(self):
            return {"time": 0, "robot": {"position": [0, 0, 0]}, "objects": {
                obj.id: {"position": list(obj.position), "size": list(obj.size),
                         "quaternion": [1, 0, 0, 0], "velocity": [0, 0, 0]}
                for obj in self.spec.objects}}
        def render(self):
            return b"fixture image"
        def close(self):
            pass

    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda robot: {"test": "fixture"})
    world = World.from_spec(source_spec(), tmp_path, validation_profile="fixed_reference",
                            runtime_factory=FixtureRuntime)
    assert world.manifest["validation"]["status"] == "ready"
    assert world.manifest["validation"]["below_floor_fixed_object_ids"] == ["foundation"]
    assert world.manifest["validation"]["initial_ground_clearance_tolerance_m"] == .001
    assert world.manifest["validation"]["initial_ground_clearance_exempt_object_ids"] == ["foundation"]
    assert next(c for c in world.manifest["validation"]["checks"] if c["check"] == "physics_reset")["passed"]
    with pytest.raises(ValueError, match="native MuJoCo"):
        freeze.require_native_ready(world.manifest)


def native_manifest():
    checks = [{"check": name, "passed": True} for name in freeze.REQUIRED_CHECKS]
    next(c for c in checks if c["check"] == "physics_settling").update(status="passed", evidence={"complete": True})
    return {"validation": {"status": "ready", "profile": "fixed_reference", "checks": checks},
            "runtime": {"engine": "mujoco", "model_fingerprint": "a" * 64,
                        "implementation_fingerprint": "b" * 64}}


@pytest.mark.parametrize("change", ["fake", "preview", "missing", "failed", "unavailable", "incomplete", "fingerprint"])
def test_release_gate_refuses_incomplete_native_evidence(change):
    manifest = native_manifest()
    freeze.require_native_ready(manifest)
    if change == "fake":
        manifest["runtime"]["engine"] = "FAKE_ORCHESTRATION_TEST_ONLY"
    elif change == "preview":
        manifest["validation"]["status"] = "preview"
    elif change == "missing":
        manifest["validation"]["checks"] = [c for c in manifest["validation"]["checks"] if c["check"] != "physics_reset"]
    elif change == "failed":
        manifest["validation"]["checks"].append({"check": "runtime_cleanup", "passed": False})
    elif change in {"unavailable", "incomplete"}:
        check = next(c for c in manifest["validation"]["checks"] if c["check"] == "physics_settling")
        if change == "unavailable":
            check["status"] = "unavailable"
        else:
            check["evidence"]["complete"] = False
    else:
        manifest["runtime"]["model_fingerprint"] = "invalid"
    with pytest.raises(ValueError):
        freeze.require_native_ready(manifest)


def test_prepared_manifest_requires_exact_checksums_and_provenance(tmp_path):
    for name, value in {"spec.json": "{}", "provenance.json": '{"instances": 5}', "NOTICE.txt": "MIT-0"}.items():
        (tmp_path / name).write_text(value)
    expected = {p.name: file_digest(p) for p in tmp_path.iterdir()}
    manifest = {"id": "warehouse", "files": expected}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert freeze.verify_prepared(tmp_path, "warehouse") == {"instances": 5}
    (tmp_path / "NOTICE.txt").write_text("tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        freeze.verify_prepared(tmp_path, "warehouse")
    (tmp_path / "NOTICE.txt").write_text("MIT-0")
    (tmp_path / "unexpected.txt").write_text("extra")
    with pytest.raises(ValueError, match="do not match"):
        freeze.verify_prepared(tmp_path, "warehouse")


@pytest.mark.parametrize("failed", [set(), {"warehouse", "hospital"}])
def test_native_batch_attempts_every_scene_but_catalog_requires_all_ready(tmp_path, monkeypatch, failed):
    identifiers = ["warehouse", "house", "hospital", "bookstore"]
    catalog = tmp_path / "pinned-catalog.json"
    catalog.write_text(json.dumps({"environments": [{"id": identifier} for identifier in identifiers]}))
    output, evidence = tmp_path / "bundles", tmp_path / "evidence"
    output.mkdir()
    (output / "catalog.json").write_text("stale prior catalog")
    called = []

    def fixture_freeze(entry, source, destination, diagnostics):
        called.append(entry["id"])
        if entry["id"] in failed:
            raise ValueError("Test-only native failure")
        return {**entry, "world_id": "fixture-" + entry["id"]}

    monkeypatch.setattr(freeze, "freeze_one", fixture_freeze)
    arguments = ["--input", str(tmp_path / "prepared"), "--output", str(output),
                 "--evidence", str(evidence), "--catalog", str(catalog)]
    if failed:
        with pytest.raises(ValueError, match="no release catalog written"):
            freeze.main(arguments)
        assert not (output / "catalog.json").exists()
    else:
        freeze.main(arguments)
        assert [entry["id"] for entry in json.loads((output / "catalog.json").read_text())["environments"]] == identifiers
    assert called == identifiers
    outcomes = json.loads((evidence / "native-summary.json").read_text())["environments"]
    assert {outcome["starter_id"] for outcome in outcomes if outcome["status"] == "failed"} == failed


def cutaway_spec():
    data = source_spec().model_dump()
    for identifier, label, sourced, visible in (
        ("ceiling", "aws_robomaker_hospital_floor_01_ceiling_1", True, True),
        ("roof", "Warehouse roof panel", True, True),
        ("ceiling_light", "Ceiling light fixture", True, True),
        ("unsourced_roof", "Roof", False, True),
        ("hidden_ceiling", "Ceiling source collision", True, False),
        ("table", "Work table", True, True),
    ):
        data["objects"].append({"id": identifier, "label": label, "kind": "mesh", "mesh_path": "fixture.obj",
                                "position": [1, 1, 2], "size": [1, 1, .1], "visible": visible,
                                "source_notice": "NOTICE.txt" if sourced else None,
                                "source_metadata": "source.json" if sourced else None})
    return WorldSpec.model_validate(data)


def test_thumbnail_cutaway_only_selects_explicit_source_roofs_not_fixtures():
    assert freeze.cutaway_object_ids(cutaway_spec()) == ["ceiling", "roof"]


@pytest.mark.parametrize("fail_render", [False, True])
def test_aerial_thumbnail_restores_native_display_and_fits_bounds(monkeypatch, fail_render):
    plan = cutaway_spec()
    original_spec = plan.model_dump()
    rgba = np.array([[.2, .3, .4, .8], [.3, .2, .4, 1], [.5, .6, .7, .9], [.8, .5, .4, 1],
                     [.4, .2, .1, .75], [.1, .9, .8, .6]])
    original_rgba = rgba.copy()
    model = SimpleNamespace(geom_rgba=rgba, vis=SimpleNamespace(global_=SimpleNamespace(fovy=45)),
                            geom=lambda name: SimpleNamespace(id=4) if name == "floor" else pytest.fail("Only the named runtime background floor may be hidden"))
    updates = []

    class Camera:
        def __init__(self):
            self.lookat = np.zeros(3)

    class Renderer:
        def disable_depth_rendering(self):
            pass
        def disable_segmentation_rendering(self):
            pass
        def update_scene(self, data, *, camera):
            updates.append(camera)
            assert camera.type == 7 and camera.azimuth == 135 and camera.elevation == -65
            # Runtime ground (index4) is hidden; the source floor (index5),
            # source fixtures and remaining geometry are retained.
            assert model.geom_rgba[:, 3].tolist() == [0, 0, .9, 1, 0, .6]
        def render(self):
            if fail_render:
                raise RuntimeError("Mock native renderer failure")
            return np.full((2, 3, 3), 128, dtype=np.uint8)

    monkeypatch.setitem(sys.modules, "mujoco", SimpleNamespace(MjvCamera=Camera,
                                                            mjtCamera=SimpleNamespace(mjCAMERA_FREE=7)))
    runtime = SimpleNamespace(spec=plan, model=model, data=object(),
                              _geom_objects={0: "ceiling", 1: "roof", 2: "ceiling_light", 3: "table", 5: "foundation"},
                              _get_renderer=lambda *, overview: Renderer() if overview else pytest.fail("Aerial renderer required"))
    if fail_render:
        with pytest.raises(RuntimeError, match="Mock native renderer failure"):
            freeze.render_native_overview(runtime)
    else:
        image = freeze.render_native_overview(runtime)
        with Image.open(io.BytesIO(image)) as opened:
            assert opened.size == (3, 2)
    assert np.array_equal(model.geom_rgba, original_rgba)
    assert plan.model_dump() == original_spec
    camera = updates[0]
    radius = math.sqrt((plan.bounds[0] / 2) ** 2 + (plan.bounds[1] / 2) ** 2
                       + (plan.bounds[2] - camera.lookat[2]) ** 2)
    assert camera.distance * math.sin(math.radians(45) / 2) > radius
