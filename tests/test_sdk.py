"""Orchestration tests use a deliberately fake runtime, never a shipped controller."""

import pytest

from robot_worlds.artifacts import read_json, write_json
from robot_worlds.models import WorldSpec
from robot_worlds.sdk import World, WorldNotReady, generate, load


def spec():
    return WorldSpec.model_validate({"name": "Task", "description": "unit-test world", "task": "move part", "bounds": [8, 8, 3],
        "objects": [{"id": "part", "label": "part", "kind": "box", "position": [2, 0, .1], "size": [.2, .2, .2], "movable": True}],
        "regions": [{"id": "target", "label": "target", "position": [-2, 0, .2], "size": [1, 1, .4]}],
        "rubric": [{"id": "move", "description": "move", "predicate": "in_region", "object_id": "part", "target_id": "target"}]})


class FakeRuntime:
    instances = []
    def __init__(self, world, *, render=True, seed=0, asset_root=None, max_sim_seconds=None):
        self.spec = world
        self.limit = max_sim_seconds
        self.time = 0
        self.closed = False
        self.position = list(world.objects[0].position)
        self.trace = [self.snapshot()]
        self.manifest = {"engine": "FAKE_ORCHESTRATION_TEST_ONLY", "seed": seed}
        self.instances.append(self)

    def snapshot(self):
        return {"time": self.time, "objects": {"part": {"position": self.position[:], "size": [.2, .2, .2], "quaternion": [1, 0, 0, 0], "held_by": None, "contacts": [], "velocity": [0, 0, 0]}}, "robot": {"position": [0, 0, 0]}}

    def observe(self):
        return {"time": self.time, "tools": [{"name": "test_move", "description": "test only", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}]}

    def execute(self, action):
        self.time = min(self.time+1, self.limit) if self.limit else self.time+1
        self.position = [-2, 0, .1]
        self.trace.append(self.snapshot())
        return {"status": "time_limit" if self.limit and self.time >= self.limit else "executed"}

    def render(self):
        return b"fake_image_bytes"

    def close(self):
        self.closed = True


class Policy:
    def __init__(self):
        self.contexts = []
        self.transcript = []
    def reset(self, context):
        self.contexts.append(context)
        self.steps = 0
        self.transcript = []
    def act(self, observation):
        action = {"tool": "test_move" if self.steps == 0 else "finish", "arguments": {}}
        self.steps += 1
        self.transcript.append(action)
        return action


@pytest.fixture(autouse=True)
def cheap_assets(monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda robot: {"test_asset": "frozen"})
    FakeRuntime.instances = []


def ready(tmp_path, runtime_factory=FakeRuntime):
    return World.from_spec(spec(), tmp_path / "worlds", runtime_factory=runtime_factory)


def test_preview_promotion_is_new_version_and_tampered_ready_rejected(tmp_path):
    preview = World.from_spec(spec(), tmp_path / "worlds", validate=False)
    world = ready(tmp_path)
    assert world.manifest["validation"]["status"] == "ready"
    assert preview.id != world.id
    assert load(preview.path).manifest["validation"]["status"] == "preview"
    assert preview.manifest["physical_id"] == world.manifest["physical_id"]
    data = read_json(preview.path / "manifest.json")
    data["validation"]["status"] = "ready"
    write_json(preview.path / "manifest.json", data)
    with pytest.raises(ValueError, match="validation evidence"):
        load(preview.path)


def test_world_spec_is_copy_and_preview_cannot_evaluate(tmp_path):
    world = World.from_spec(spec(), tmp_path / "worlds", validate=False)
    changed = world.spec
    changed.task = "different"
    assert world.spec.task == "move part"
    with pytest.raises(WorldNotReady):
        world.evaluate(Policy(), output=tmp_path / "runs", runtime_factory=FakeRuntime)


def test_evaluation_resets_runtime_and_policy_and_saves_valid_evidence(tmp_path):
    world = ready(tmp_path)
    policy = Policy()
    report = world.evaluate(policy, episodes=2, max_steps=3, seed=7, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert report.summary["successes"] == 2
    assert report.summary["valid"] == 2
    assert [e["seed"] for e in report.result["episodes"]] == [7, 8]
    assert len(policy.contexts) == 2
    assert all(set(context) == {"task", "robot", "action_contract"} for context in policy.contexts)
    assert len(FakeRuntime.instances) == 3
    assert all(runtime.closed for runtime in FakeRuntime.instances)
    assert read_json(report.path / "episode-001" / "trace.json")[0]["objects"]["part"]["position"] == [2, 0, .1]
    assert (report.path / "report.html").is_file()


def test_policy_factory_builds_independent_policies(tmp_path):
    policies = []
    def factory():
        policies.append(Policy())
        return policies[-1]
    report = ready(tmp_path).evaluate(policy_factory=factory, episodes=2, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert report.summary["successes"] == 2
    assert len(policies) == 2
    assert all(len(p.contexts) == 1 for p in policies)


def test_unknown_tool_is_policy_error_not_task_failure(tmp_path):
    class BadPolicy(Policy):
        def act(self, observation):
            return {"tool": "teleport", "arguments": {}}
    report = ready(tmp_path).evaluate(BadPolicy(), render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert report.result["episodes"][0]["status"] == "policy_error"
    assert report.summary["valid"] == 0
    assert report.summary["success_rate"] is None
    assert all(runtime.closed for runtime in FakeRuntime.instances)


def test_transcript_failure_does_not_drop_trace_or_skip_close(tmp_path):
    class BrokenTranscript(Policy):
        def reset(self, context):
            self.steps = 0
        def act(self, observation):
            return {"tool": "finish", "arguments": {}}
        @property
        def transcript(self):
            raise RuntimeError("transcript unavailable")
        @transcript.setter
        def transcript(self, value):
            pass
    report = ready(tmp_path).evaluate(BrokenTranscript(), render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert report.result["episodes"][0]["status"] == "policy_error"
    assert read_json(report.path / "episode-000" / "trace.json")
    assert "unavailable" in read_json(report.path / "episode-000" / "transcript.json")
    assert all(runtime.closed for runtime in FakeRuntime.instances)


def test_snapshot_failure_preserves_complete_samples_and_closes(tmp_path):
    class BrokenSnapshot(FakeRuntime):
        def snapshot(self):
            if getattr(self, "broken", False):
                raise RuntimeError("state unavailable")
            return super().snapshot()
        def execute(self, action):
            result = super().execute(action)
            self.broken = True
            return result
    report = ready(tmp_path).evaluate(Policy(), render=False, output=tmp_path / "runs", runtime_factory=BrokenSnapshot)
    assert report.result["episodes"][0]["status"] == "simulation_error"
    assert len(read_json(report.path / "episode-000" / "trace.json")) == 2
    assert FakeRuntime.instances[-1].closed


def test_hard_time_limit_and_runtime_overrun_are_distinguished(tmp_path):
    world = ready(tmp_path)
    report = world.evaluate(Policy(), max_sim_seconds=.5, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    episode = report.result["episodes"][0]
    assert episode["sim_seconds"] <= .5
    assert episode["termination"] == "simulation_time_limit"
    class Overrun(FakeRuntime):
        def execute(self, action):
            self.limit = None
            return super().execute(action)
    bad = world.evaluate(Policy(), max_sim_seconds=.5, render=False, output=tmp_path / "runs", runtime_factory=Overrun)
    assert bad.result["episodes"][0]["status"] == "simulation_error"
    assert bad.summary["valid"] == 0


def test_missing_mesh_and_unstable_physics_retain_unvalidated_world(tmp_path):
    mesh = spec().model_dump()
    mesh["objects"][0].update(kind="mesh", mesh_path="absent.obj")
    missing = World.from_spec(mesh, tmp_path / "worlds", asset_root=tmp_path, runtime_factory=FakeRuntime)
    assert missing.manifest["validation"]["status"] == "unvalidated"
    assert (missing.path / "world.json").is_file()
    class Unstable(FakeRuntime):
        def snapshot(self):
            state = super().snapshot()
            state["objects"]["part"]["position"][2] += .5
            return state
    unstable = World.from_spec(spec(), tmp_path / "worlds", runtime_factory=Unstable)
    assert unstable.manifest["validation"]["status"] == "unvalidated"
    assert any("settling" in str(check) for check in unstable.manifest["validation"]["checks"])


class Client:
    def __init__(self):
        self.prompts = []
    def complete_json(self, instructions, prompt, schema):
        self.prompts.append(prompt)
        return spec().model_dump(mode="json")


def test_generation_repair_uses_actual_physics_errors_and_preserves_previews(tmp_path):
    attempts = []
    def factory(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("fixture has invalid collision geometry")
        return FakeRuntime(*args, **kwargs)
    client, events = Client(), []
    world = generate("preserve a workshop mission", client=client, output=tmp_path / "worlds", runtime_factory=factory, on_event=events.append)
    assert world.manifest["validation"]["status"] == "ready"
    assert len(client.prompts) == 2
    assert "invalid collision geometry" in client.prompts[1]
    assert "preserve a workshop mission" in client.prompts[1]
    assert any(e["type"] == "world_preview" for e in events)
    records = list((tmp_path / "worlds").glob("generation-*.json"))
    assert len(records) == 1
    record = read_json(records[0])
    assert record["status"] == "completed"
    assert len(record["versions"]) == 4


def test_exhausted_physics_repair_returns_unvalidated_latest_artifact(tmp_path):
    def unavailable(*args, **kwargs):
        raise RuntimeError("physics unavailable")
    world = generate("mission", client=Client(), max_repairs=1, output=tmp_path / "worlds", runtime_factory=unavailable)
    assert world.manifest["validation"]["status"] == "unvalidated"
    record = read_json(next((tmp_path / "worlds").glob("generation-*.json")))
    assert record["status"] == "incomplete"
    assert record["attempts"] == 2
    assert any("physics unavailable" in error for error in record["errors"])


def test_rubric_edit_and_regrade_create_versions_without_rerunning_policy(tmp_path):
    world = ready(tmp_path)
    policy = Policy()
    report = world.evaluate(policy, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    rubric = world.spec.rubric
    rubric[0].duration = 5
    revised = world.with_rubric(rubric, runtime_factory=FakeRuntime)
    assert revised.id != world.id
    assert revised.manifest["physical_id"] == world.manifest["physical_id"]
    regraded = report.regrade(revised)
    assert report.summary["successes"] == 1
    assert regraded.summary["successes"] == 0
    assert len(policy.contexts) == 1
    assert regraded.result["parent_run_id"] == report.result["id"]


def test_cancel_before_first_episode_is_preserved_and_observer_failure_is_nonfatal(tmp_path):
    world = ready(tmp_path)
    def broken_observer(event):
        raise RuntimeError("UI listener failed")
    report = world.evaluate(Policy(), cancelled=lambda: True, on_event=broken_observer, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert report.result["status"] == "cancelled"
    assert report.summary["attempted"] == 0
    assert report.result["observer_errors"]


def test_regrade_missing_trace_cannot_reuse_previous_success(tmp_path):
    world = ready(tmp_path)
    report = world.evaluate(Policy(), render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert report.summary["successes"] == 1
    (report.path / "episode-000" / "trace.json").unlink()
    revised = report.regrade(world)
    assert revised.summary["successes"] == 0
    assert revised.summary["valid"] == 0
    assert revised.result["episodes"][0]["status"] == "grading_error"


def test_runtime_factory_failure_preserves_report_without_scoring(tmp_path):
    def broken(*args, **kwargs):
        raise RuntimeError("runtime creation failed")
    report = ready(tmp_path).evaluate(Policy(), render=False, output=tmp_path / "runs", runtime_factory=broken)
    assert report.summary["valid"] == 0
    assert report.result["episodes"][0]["status"] == "simulation_error"
    assert read_json(report.path / "episode-000" / "trace.json") == []
    assert (report.path / "episode-000" / "transcript.json").is_file()


def test_reset_probe_checks_raw_contact_truth_after_settling(tmp_path):
    data = spec().model_dump()
    data["objects"][0]["position"] = [2, 0, 1.1]
    data["objects"].append({"id": "table", "label": "support", "kind": "box", "position": [2, 0, .5], "size": [1, 1, 1]})
    data["rubric"] = [{"id": "support", "description": "Place on table", "predicate": "on_top", "object_id": "part", "target_id": "table"}]
    class ContactAtReset(FakeRuntime):
        def snapshot(self):
            state = super().snapshot()
            state["objects"]["part"]["contacts"] = ["table"]
            state["objects"]["table"] = {"position": [2, 0, .5], "size": [1, 1, 1], "quaternion": [1, 0, 0, 0], "contacts": ["part"]}
            return state
    world = World.from_spec(data, tmp_path / "worlds", runtime_factory=ContactAtReset)
    assert world.manifest["validation"]["status"] == "unvalidated"
    checks = {check["check"]: check for check in world.manifest["validation"]["checks"]}
    assert checks["geometry_and_rubric"]["passed"]
    assert checks["physics_reset"]["passed"]
    assert not checks["initial_rubric"]["passed"]


def test_cleanup_failure_is_an_infrastructure_error(tmp_path):
    class BrokenClose(FakeRuntime):
        def close(self):
            super().close()
            raise RuntimeError("cleanup failed")
    report = ready(tmp_path).evaluate(Policy(), render=False, output=tmp_path / "runs", runtime_factory=BrokenClose)
    assert report.summary["valid"] == 0
    assert report.result["episodes"][0]["status"] == "simulation_error"
    assert FakeRuntime.instances[-1].closed


def test_changed_runtime_version_cannot_silently_reuse_validation(tmp_path):
    world = ready(tmp_path)
    class ChangedEngine(FakeRuntime):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.manifest["engine"] = "DIFFERENT_ENGINE"
    policy = Policy()
    report = world.evaluate(policy, render=False, output=tmp_path / "runs", runtime_factory=ChangedEngine)
    assert report.result["episodes"][0]["status"] == "simulation_error"
    assert report.summary["valid"] == 0
    assert policy.contexts == []


def test_cancelled_generation_retains_latest_preview_and_audit(tmp_path):
    cancelled = False
    def event(value):
        nonlocal cancelled
        if value["type"] == "world_preview":
            cancelled = True
    world = generate("mission", client=Client(), output=tmp_path / "worlds", on_event=event,
                     cancelled=lambda: cancelled, runtime_factory=FakeRuntime)
    assert world.manifest["validation"]["status"] == "preview"
    assert FakeRuntime.instances == []
    record = read_json(next((tmp_path / "worlds").glob("generation-*.json")))
    assert record["status"] == "cancelled"


def test_factory_owned_policy_is_closed_but_explicit_policy_remains_owned_by_caller(tmp_path):
    class ClosablePolicy(Policy):
        def __init__(self):
            super().__init__()
            self.closed = False
        def close(self):
            self.closed = True
    created = []
    def factory():
        item = ClosablePolicy()
        created.append(item)
        return item
    world = ready(tmp_path)
    world.evaluate(policy_factory=factory, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert created[0].closed
    explicit = ClosablePolicy()
    world.evaluate(explicit, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert not explicit.closed


def test_continuous_replay_gets_exact_criterion_times_without_changing_grade(tmp_path, monkeypatch):
    calls = []
    class Restorable(FakeRuntime):
        def restore_snapshot(self, state):
            self.time = state["time"]
            self.position = state["objects"]["part"]["position"][:]
    def render(runtime, trace, directory, **kwargs):
        calls.append(kwargs)
        # Derived inspection state may change after grading; grade must be frozen.
        runtime.restore_snapshot(trace[0])
        return [{"time": 0, "path": "replay/first.png"}, {"time": 1, "path": "replay/evidence.png"}]
    monkeypatch.setattr("robot_worlds.replay.render_replay", render)
    report = ready(tmp_path).evaluate(Policy(), render=True, output=tmp_path / "runs", runtime_factory=Restorable)
    episode = report.result["episodes"][0]
    assert episode["rubric"]["success"]
    assert calls[0]["evidence_times"] == [1]
    assert episode["frames"][1]["path"] == "episode-000/replay/evidence.png"
    assert episode["actor_frames"]
    assert episode["replay"]["source"] == "recorded_simulation_states"
    assert episode["sim_seconds"] == 1
    assert FakeRuntime.instances[-1].closed


def test_replay_failure_preserves_score_and_boundary_frames(tmp_path, monkeypatch):
    class Restorable(FakeRuntime):
        def restore_snapshot(self, state):
            pass
    def fail(*args, **kwargs):
        raise RuntimeError("offscreen rendering unavailable")
    monkeypatch.setattr("robot_worlds.replay.render_replay", fail)
    report = ready(tmp_path).evaluate(Policy(), render=True, output=tmp_path / "runs", runtime_factory=Restorable)
    episode = report.result["episodes"][0]
    assert episode["status"] == "completed"
    assert report.summary["successes"] == 1
    assert episode["replay_error"]
    assert episode["frames"]
    assert episode["replay"]["source"] == "action_boundary_camera_frames"


def test_from_spec_revalidates_mutated_model_instances_before_creating_artifact(tmp_path):
    from pydantic import ValidationError
    edited = spec()
    with pytest.raises(ValidationError):
        edited.objects[0].kind = "mesh"  # cross-field failure can leave assignment applied
    assert edited.objects[0].kind == "mesh"
    with pytest.raises(ValidationError, match="mesh_path"):
        World.from_spec(edited, tmp_path / "worlds", runtime_factory=FakeRuntime)
    assert not (tmp_path / "worlds").exists()
