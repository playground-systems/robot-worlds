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
