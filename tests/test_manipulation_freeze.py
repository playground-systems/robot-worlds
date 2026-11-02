"""Release identity/input binding with explicit fake evidence and no native runtime."""
import copy
import importlib.util
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from robot_worlds.artifacts import file_digest, read_json, write_json
from robot_worlds.asset_sourcing import AssetCandidate
from robot_worlds.models import WorldSpec
from robot_worlds.task_catalog import TaskCatalog


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "tools/acceptance/freeze_manipulation_preset.py"
    script_spec = importlib.util.spec_from_file_location("fixture_manipulation_freeze", path)
    script = importlib.util.module_from_spec(script_spec)
    script_spec.loader.exec_module(script)
    creation, authored, destination = tmp_path / "creation", tmp_path / "authored", tmp_path / "release"
    creation.mkdir()
    (authored / "asset-library").mkdir(parents=True)
    spec = WorldSpec.model_validate({
        "name": "FAKE release fixture", "description": "No real native evidence or assets", "task": "Move test part",
        "bounds": [8, 8, 3], "objects": [{"id": "part", "label": "Part", "kind": "box", "movable": True,
                                        "position": [2, 0, .1], "size": [.2, .2, .2]}],
        "rubric": [{"id": "hold", "description": "Hold part", "predicate": "holding", "object_id": "part"}],
    })
    catalog = TaskCatalog(tasks=[{"id": f"task{i}", "name": f"Task {i}", "task": f"Perform interaction {i}",
                                  "rubric": spec.model_dump(mode="json")["rubric"]} for i in range(12)])
    world_id = "a" * 64
    runtime_manifest = {"engine": "mujoco", "engine_version": "FAKE_TEST_ONLY", "synthetic_fixture": True,
                        "model_fingerprint": "1" * 64, "implementation_fingerprint": "2" * 64}
    checks = [{"check": check, "passed": True} for check in
              ("schema", "geometry_and_rubric", "physics_settling", "physics_reset", "initial_rubric", "render")]
    checks[2]["evidence"] = {"complete": True, "synthetic_fixture": "NOT_NATIVE_EVIDENCE"}
    world = SimpleNamespace(id=world_id, spec=spec, tasks=catalog.tasks, path=tmp_path / "frozen-world",
                            manifest={"id": world_id, "validation": {"status": "ready", "profile": "strict", "checks": checks},
                                      "runtime": runtime_manifest, "task_catalog": catalog.model_dump(mode="json")})

    def frozen_export(output):
        with zipfile.ZipFile(output, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps(world.manifest))
            bundle.writestr("world.json", spec.model_dump_json())
        return output

    world.export = Mock(side_effect=frozen_export)
    # The stale archive must never be used to publish another validated world's ID.
    (creation / "world.zip").write_bytes(b"STALE_CREATION_ZIP_NOT_THE_VALIDATED_WORLD")
    write_json(authored / "generation-plan.json", {"prepared_assets": [{"id": "part", "asset_id": "fixture"}]})
    write_json(authored / "task-suite.json", catalog.model_dump(mode="json"))
    (authored / "semantic-plan.md").write_text("Explicit fake authoring sidecar")
    write_json(authored / "asset-library/library.json", {"synthetic_fixture": True})
    names = ("generation-plan.json", "task-suite.json", "semantic-plan.md", "asset-library/library.json")
    record = {"status": "completed", "policy_runs": 0, "world_id": world.id, "world_path": str(world.path),
              "authoring_input_sha256": {name: file_digest(authored / name) for name in names},
              "task_reset_checks": [{"task_id": task.id, "passed": True, "errors": []} for task in world.tasks]}
    write_json(creation / "creation.json", record)
    monkeypatch.setattr(script, "load", lambda _: world)
    source = AssetCandidate("simready:fixture", "https://example.invalid/test-only", "fixture-1", "Test fixture only")
    library = SimpleNamespace(asset_ids=("fixture",), load=lambda *args, **kwargs: SimpleNamespace(selected=source))
    monkeypatch.setattr(script.AssetLibrary, "open", lambda _: library)
    instances = []

    class FakeRuntime:
        manifest_data = copy.deepcopy(runtime_manifest)

        def __init__(self, *args, **kwargs):
            instances.append(self)
            self.manifest = copy.deepcopy(self.manifest_data)
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr("robot_worlds.runtime.Runtime", FakeRuntime)
    overview = Mock(return_value=b"FAKE_THUMBNAIL_NOT_A_NATIVE_RENDER")
    monkeypatch.setattr(script, "overview", overview)
    return SimpleNamespace(script=script, creation=creation, authored=authored, destination=destination,
                           record=record, world=world, runtime=FakeRuntime, instances=instances, overview=overview)


def test_freeze_ignores_stale_zip_and_exports_the_exact_checked_world(prepared):
    p = prepared
    entry = p.script.freeze(p.creation, p.authored, "fixture-preset", p.destination)
    p.world.export.assert_called_once_with(p.destination / "world.zip")
    with zipfile.ZipFile(p.destination / "world.zip") as bundle:
        assert json.loads(bundle.read("manifest.json"))["id"] == p.world.id
    assert entry["world_id"] == p.world.id
    assert entry["bundle_sha256"] == file_digest(p.destination / "world.zip")
    assert p.instances[0].closed
    evidence = read_json(p.destination / "preparation-evidence.json")
    assert evidence["world_id"] == p.world.id
    assert evidence["policy_runs"] == 0 and evidence["autonomous_task_success_verified"] is False


@pytest.mark.parametrize("name", ["generation-plan.json", "task-suite.json", "semantic-plan.md", "asset-library/library.json"])
def test_freeze_rejects_changed_authoring_input_before_thumbnail_or_export(prepared, name):
    p = prepared
    (p.authored / name).write_text("Changed after native validation")
    with pytest.raises(ValueError, match="Authored inputs do not match native-checked creation evidence"):
        p.script.freeze(p.creation, p.authored, "fixture-preset", p.destination)
    assert p.instances == []
    p.overview.assert_not_called()
    p.world.export.assert_not_called()


def test_even_rehashed_task_sidecar_must_equal_integrity_bound_catalog(prepared):
    p = prepared
    sidecar = read_json(p.authored / "task-suite.json")
    sidecar["tasks"][0]["task"] = "A different task must not inherit reset checks"
    write_json(p.authored / "task-suite.json", sidecar)
    p.record["authoring_input_sha256"]["task-suite.json"] = file_digest(p.authored / "task-suite.json")
    write_json(p.creation / "creation.json", p.record)
    with pytest.raises(ValueError, match="Task sidecar differs from the validated world catalog"):
        p.script.freeze(p.creation, p.authored, "fixture-preset", p.destination)
    assert p.instances == []
    p.world.export.assert_not_called()


def test_creation_world_id_must_match_loaded_world(prepared):
    p = prepared
    p.record["world_id"] = "b" * 64
    write_json(p.creation / "creation.json", p.record)
    with pytest.raises(ValueError, match="Creation evidence identifies a different world"):
        p.script.freeze(p.creation, p.authored, "fixture-preset", p.destination)
    assert p.instances == []
    p.world.export.assert_not_called()


def test_task_reset_check_ids_must_match_catalog_not_just_count(prepared):
    p = prepared
    p.record["task_reset_checks"][0]["task_id"] = "task1"
    write_json(p.creation / "creation.json", p.record)
    with pytest.raises(ValueError, match="at least twelve checked task variants"):
        p.script.freeze(p.creation, p.authored, "fixture-preset", p.destination)
    assert p.instances == []
    p.world.export.assert_not_called()


@pytest.mark.parametrize("fingerprint", ["model_fingerprint", "implementation_fingerprint"])
def test_thumbnail_runtime_must_match_native_validation_and_close_on_mismatch(prepared, fingerprint):
    p = prepared
    p.runtime.manifest_data[fingerprint] = "0" * 64
    with pytest.raises(ValueError, match="Runtime changed since native validation"):
        p.script.freeze(p.creation, p.authored, "fixture-preset", p.destination)
    assert p.instances[0].closed
    p.overview.assert_not_called()
    p.world.export.assert_not_called()
