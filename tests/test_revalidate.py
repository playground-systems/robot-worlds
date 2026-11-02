"""Mock-only revalidation orchestration; native scene checks run on hosted Linux."""
import json

import pytest

from robot_worlds.artifacts import file_digest
from robot_worlds.models import WorldSpec
from robot_worlds.sdk import World, load


class FixtureRuntime:
    fingerprint = "1" * 64
    def __init__(self, spec, **kwargs):
        self.spec = spec
        self.manifest = {"engine": "FAKE_REVALIDATION_ORCHESTRATION_ONLY",
                         "model_fingerprint": "2" * 64,
                         "implementation_fingerprint": self.fingerprint,
                         "action_contract": [], "sensor_contract": {"fixture": "mock only"}}
        self.trace = [self.snapshot()]
    def snapshot(self):
        return {"time": 0, "model_fingerprint": "2" * 64,
                "robot": {"position": [0, 0, 0]}, "objects": {
                    obj.id: {"position": list(obj.position), "size": list(obj.size),
                             "quaternion": [1, 0, 0, 0], "velocity": [0, 0, 0]}
                    for obj in self.spec.objects}}
    def render(self):
        return b"not an image; mock orchestration fixture only"
    def observe(self):
        return {"time": 0, "tools": []}
    def close(self):
        pass


class ChangedRuntime(FixtureRuntime):
    fingerprint = "3" * 64


def source(tmp_path, monkeypatch, profile):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda robot: {"fixture_robot": "unchanged"})
    assets = tmp_path / "source-assets"
    assets.mkdir()
    (assets / "NOTICE.txt").write_text("Fixture source license and adaptations")
    (assets / "provenance.json").write_text(json.dumps({"source": "fixture only", "revision": "pinned"}))
    spec = WorldSpec.model_validate({
        "name": "Fixture source", "description": "Mock-only source world", "task": "Drive ahead",
        "bounds": [8, 8, 3], "objects": [{
            "id": "source_floor", "label": "Source floor", "kind": "box",
            "position": [2, 0, -.1 if profile == "fixed_reference" else .1], "size": [1, 1, .2],
            "mass": 2, "friction": .4, "source_notice": "NOTICE.txt", "source_metadata": "provenance.json",
        }],
        "regions": [{"id": "arrival", "label": "Arrival", "position": [-2, 0, .5], "size": [1, 1, 1]}],
        "rubric": [{"id": "arrive", "description": "Arrive", "predicate": "robot_in_region",
                    "target_id": "arrival", "tolerance": .005}],
    })
    world = World.from_spec(spec, tmp_path / "worlds", asset_root=assets,
                            validation_profile=profile, runtime_factory=FixtureRuntime)
    assert world.manifest["validation"]["status"] == "ready"
    # Revalidation must use the self-contained frozen assets, not the importer's
    # original source directory, which is no longer available to customers.
    for path in assets.iterdir():
        path.unlink()
    return world


@pytest.mark.parametrize("profile", ["strict", "fixed_reference"])
def test_revalidate_changed_runtime_preserves_spec_assets_and_source(tmp_path, monkeypatch, profile):
    original = source(tmp_path, monkeypatch, profile)
    before = {str(path.relative_to(original.path)): file_digest(path)
              for path in original.path.rglob("*") if path.is_file()}
    revised = original.revalidate(runtime_factory=ChangedRuntime)
    assert revised.id != original.id
    assert revised.path.parent == original.path.parent
    assert revised.manifest["parent_id"] == original.id
    assert revised.manifest["edit"] == {"kind": "revalidate", "parent_world_id": original.id}
    assert revised.manifest["validation"]["status"] == "ready"
    assert revised.manifest["validation"]["profile"] == profile
    assert revised.manifest["runtime"]["implementation_fingerprint"] == ChangedRuntime.fingerprint
    assert revised.spec == original.spec
    assert revised.manifest["physical_id"] == original.manifest["physical_id"]
    assert revised.manifest["assets"] == original.manifest["assets"]
    assert revised.spec.rubric == original.spec.rubric
    assert before == {str(path.relative_to(original.path)): file_digest(path)
                      for path in original.path.rglob("*") if path.is_file()}
    assert load(original.path).manifest["runtime"]["implementation_fingerprint"] == FixtureRuntime.fingerprint
    for member, checksum in original.manifest["assets"].items():
        assert file_digest(revised.path / member) == checksum

    class FinishPolicy:
        def reset(self, context):
            pass
        def act(self, observation):
            return {"tool": "finish", "arguments": {}}

    # The old artifact honestly rejects the changed adapter; the revalidated
    # version accepts it. Completing a mock episode does not claim task success.
    rejected = original.evaluate(FinishPolicy(), runtime_factory=ChangedRuntime,
                                 render=False, output=tmp_path / "old-runs")
    assert rejected.summary["valid"] == 0
    assert rejected.result["episodes"][0]["error"]["type"] == "WorldNotReady"
    accepted = revised.evaluate(FinishPolicy(), runtime_factory=ChangedRuntime,
                                render=False, output=tmp_path / "new-runs")
    assert accepted.summary["valid"] == 1
    assert accepted.summary["successes"] == 0


def test_same_runtime_revalidation_still_creates_distinct_child(tmp_path, monkeypatch):
    original = source(tmp_path, monkeypatch, "strict")
    revised = original.revalidate(output=tmp_path / "local-versions", runtime_factory=FixtureRuntime)
    assert revised.id != original.id
    assert revised.path.parent == (tmp_path / "local-versions").resolve()
    assert revised.manifest["parent_id"] == original.id
    assert revised.spec == original.spec


def test_failed_revalidation_retains_source_and_reports_unvalidated_child(tmp_path, monkeypatch):
    original = source(tmp_path, monkeypatch, "fixed_reference")

    class FailedRuntime:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Mock reset failure")

    revised = original.revalidate(runtime_factory=FailedRuntime)
    assert revised.id != original.id
    assert revised.manifest["parent_id"] == original.id
    assert revised.manifest["validation"]["status"] == "unvalidated"
    assert revised.manifest["validation"]["profile"] == "fixed_reference"
    assert revised.spec == original.spec
    check = next(check for check in revised.manifest["validation"]["checks"] if check["check"] == "physics_probe")
    assert check["passed"] is False
    assert "Mock reset failure" in check["error"]["message"]
    assert load(original.path).manifest["validation"]["status"] == "ready"


def test_revalidate_rechecks_source_integrity_before_opening_runtime(tmp_path, monkeypatch):
    original = source(tmp_path, monkeypatch, "strict")
    member = next(iter(original.manifest["assets"]))
    (original.path / member).write_text("tampered")
    with pytest.raises(ValueError, match="asset was modified"):
        original.revalidate(runtime_factory=ChangedRuntime)
