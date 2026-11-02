"""Replay a recorded live Astra patch through actual native scene validation."""
import copy
import json
from pathlib import Path

from robot_worlds.artifacts import file_digest
from robot_worlds.sdk import World, load


def test_recorded_astra_edit_preserves_parent_and_validates_real_physics(tmp_path, native_rendering):
    fixture = json.loads((Path(__file__).parent / "fixtures/astra-scoped-edit.json").read_text())
    parent = World.from_spec(fixture["source"], tmp_path / "worlds")
    assert parent.manifest["validation"]["status"] == "ready", parent.manifest["validation"]
    hashes = {str(p.relative_to(parent.path)): file_digest(p)
              for p in parent.path.rglob("*") if p.is_file()}

    class RecordedModel:
        def complete_json(self, instructions, prompt, schema):
            assert "metal_workpiece" in prompt
            return copy.deepcopy(fixture["patch"])

    revised = parent.refine(fixture["prompt"], scope=fixture["scope"],
                            client=RecordedModel(), max_repairs=0)
    assert revised.id != parent.id
    assert revised.manifest["validation"]["status"] == "ready", revised.manifest["validation"]
    assert revised.manifest["edit"]["changes"] == fixture["expected_changes"]
    assert revised.manifest["edit"]["counts"] == {"added": 0, "removed": 0, "changed": 1, "preserved": 16}
    assert revised.manifest["parent_id"] == parent.id
    assert revised.spec.task == parent.spec.task
    assert revised.spec.rubric == parent.spec.rubric
    assert revised.manifest["robot_assets"] == parent.manifest["robot_assets"]
    assert revised.manifest["runtime"]["implementation_fingerprint"] == parent.manifest["runtime"]["implementation_fingerprint"]
    assert load(revised.path).manifest["edit"] == revised.manifest["edit"]
    assert hashes == {str(p.relative_to(parent.path)): file_digest(p)
                      for p in parent.path.rglob("*") if p.is_file()}


def test_prepared_asset_edit_validates_and_exports_without_rewriting_parent(tmp_path, native_rendering):
    from test_generator import valid
    from test_prepared_asset_editing import asset_patch

    parent = World.from_spec(valid(), tmp_path / "worlds")
    hashes = {str(p.relative_to(parent.path)): file_digest(p) for p in parent.path.rglob("*") if p.is_file()}

    class MockEditModel:
        def complete_json(self, instructions, prompt, schema):
            request = json.loads(prompt)
            assert len(request["prepared_asset_catalog"]) == 2
            assert "AddPreparedAsset" in schema["$defs"]
            return asset_patch()

    events = []
    edited = parent.refine("Add the original porcelain bowl and microwave in the empty rear corners.",
                           client=MockEditModel(), max_repairs=0, on_event=events.append)
    assert edited.manifest["validation"]["status"] == "ready", edited.manifest["validation"]
    assert edited.manifest["parent_id"] == parent.id
    assert edited.spec.task == parent.spec.task
    assert edited.spec.rubric == parent.spec.rubric
    assert edited.spec.objects[0] == parent.spec.objects[0]
    assert len([e for e in events if e["type"] == "asset_loaded"]) == 2
    assert edited.manifest["edit"]["counts"]["added"] == 180
    assert all(op["op"] == "add_prepared_asset" for op in edited.manifest["edit"]["operations"])
    assert load(edited.path).id == edited.id
    assert edited.export(tmp_path / "edited.zip").is_file()
    assert hashes == {str(p.relative_to(parent.path)): file_digest(p) for p in parent.path.rglob("*") if p.is_file()}
