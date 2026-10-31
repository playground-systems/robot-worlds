"""Orchestration tests use a deliberately fake runtime, never a shipped controller."""

import pytest

from robot_worlds.artifacts import read_json, write_json
from robot_worlds.llm import LLMBudgetExceeded, LLMError
from robot_worlds.models import WorldSpec
from robot_worlds.policy import LLMAgent
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
        self.manifest = {"engine": "FAKE_ORCHESTRATION_TEST_ONLY", "seed": seed,
                         "model_fingerprint": "1" * 64, "implementation_fingerprint": "2" * 64,
                         "action_contract": self.observe()["tools"],
                         "sensor_contract": {"fixture": "fake_observations_only"}}
        self.instances.append(self)

    def snapshot(self):
        return {"time": self.time, "objects": {"part": {"position": self.position[:], "size": [.2, .2, .2], "quaternion": [1, 0, 0, 0], "held_by": None, "contacts": [], "velocity": [0, 0, 0]}}, "robot": {"position": [0, 0, 0]}, "model_fingerprint": "1" * 64}

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
    assert all(set(context) == {"task", "robot", "action_contract", "sensor_contract"} for context in policy.contexts)
    assert len(FakeRuntime.instances) == 3
    assert all(runtime.closed for runtime in FakeRuntime.instances)
    assert read_json(report.path / "episode-001" / "trace.json")[0]["objects"]["part"]["position"] == [2, 0, .1]
    assert (report.path / "report.html").is_file()


def test_live_view_events_do_not_enter_policy_observations_or_claim_final_success(tmp_path):
    class LiveRuntime(FakeRuntime):
        def set_live_observer(self, callback):
            self.callback = callback
        def publish_live(self, *, force=False):
            if self.callback:
                self.callback({"image": b"spectator", "time": self.time, "sequence": int(self.time),
                               "source": "live_simulator_overview"})
        def execute(self, action):
            result = super().execute(action)
            self.publish_live()
            return result

    class SensorPolicy(Policy):
        def act(self, observation):
            assert "rubric" not in observation and "criteria" not in observation
            assert "spectator" not in str(observation)
            return super().act(observation)

    events = []
    report = ready(tmp_path, runtime_factory=LiveRuntime).evaluate(
        SensorPolicy(), output=tmp_path / "runs", runtime_factory=LiveRuntime, on_event=events.append)
    frames = [e for e in events if e["type"] == "live_frame"]
    assert len(frames) >= 2 and frames[-1]["time"] == 1
    assert all("image" not in event for event in frames)
    assert (report.path / frames[-1]["path"]).read_bytes() == b"spectator"
    progress = [e for e in events if e["type"] == "task_progress"]
    assert progress[0]["provisional"] and "success" not in progress[0]
    assert report.summary["successes"] == 1


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


@pytest.mark.parametrize("limit", ["model-spend", "output-token"])
def test_llm_budget_exhaustion_is_a_valid_failed_episode(tmp_path, limit):
    class ExhaustedClient:
        usage = {"calls": 0}
        last_usage = {}
        budget = {"limit": limit}

        def choose_action(self, *args):
            raise LLMBudgetExceeded(f"The {limit} budget is exhausted")

    policy = LLMAgent(client=ExhaustedClient())
    report = ready(tmp_path).evaluate(
        policy, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime
    )
    episode = report.result["episodes"][0]
    assert report.result["status"] == "completed"
    assert episode["status"] == "completed"
    assert episode["termination"] == "policy_budget"
    assert episode["rubric"]["status"] == "failed"
    assert not episode["rubric"]["success"]
    assert report.summary["attempted"] == report.summary["valid"] == 1
    assert report.summary["successes"] == report.summary["invalid_or_interrupted"] == 0
    assert report.summary["success_rate"] == 0
    assert episode["steps"] == []
    assert read_json(report.path / "episode-000" / "trace.json")
    transcript = read_json(report.path / "episode-000" / "transcript.json")
    assert transcript[0]["error"] == {"type": "LLMBudgetExceeded"}
    assert transcript[0]["budget"]["limit"] == limit
    assert episode["policy_usage"]["policy_steps"] == 1
    assert all(runtime.closed for runtime in FakeRuntime.instances)


def test_llm_auth_or_transport_failure_remains_an_invalid_policy_episode(tmp_path):
    class FailedClient:
        def choose_action(self, *args):
            raise LLMError("OpenAI Responses request failed (AuthenticationError, HTTP 401)")

    report = ready(tmp_path).evaluate(
        LLMAgent(client=FailedClient()),
        render=False,
        output=tmp_path / "runs",
        runtime_factory=FakeRuntime,
    )
    episode = report.result["episodes"][0]
    assert episode["status"] == "policy_error"
    assert "termination" not in episode
    assert report.summary["valid"] == 0
    assert report.summary["invalid_or_interrupted"] == 1
    assert report.summary["success_rate"] is None
    transcript = read_json(report.path / "episode-000" / "transcript.json")
    assert transcript[0]["error"] == {"type": "LLMError"}
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


@pytest.mark.parametrize(
    "change", ["implementation", "actions", "sensors", "missing_implementation"]
)
def test_changed_runtime_implementation_rejects_before_policy_reset(tmp_path, change):
    class ChangedRuntime(FakeRuntime):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if change == "implementation":
                self.manifest["implementation_fingerprint"] = "3" * 64
            elif change == "actions":
                self.manifest["action_contract"] = []
            elif change == "sensors":
                self.manifest["sensor_contract"] = {"fixture": "different_sensor"}
            else:
                self.manifest.pop("implementation_fingerprint")

    policy = Policy()
    report = ready(tmp_path).evaluate(
        policy, render=False, output=tmp_path / "runs", runtime_factory=ChangedRuntime
    )
    episode = report.result["episodes"][0]
    assert episode["status"] == "simulation_error"
    assert episode["error"]["type"] == "WorldNotReady"
    assert report.summary["valid"] == 0
    assert policy.contexts == []
    assert episode["steps"] == []
    assert all(runtime.closed for runtime in FakeRuntime.instances)


def test_legacy_ready_world_requires_revalidation_of_runtime_implementation(tmp_path):
    class LegacyRuntime(FakeRuntime):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.manifest.pop("implementation_fingerprint")

    world = ready(tmp_path, runtime_factory=LegacyRuntime)
    assert world.manifest["validation"]["status"] == "ready"
    policy = Policy()
    with pytest.raises(WorldNotReady, match="fingerprints are missing"):
        world.evaluate(policy, output=tmp_path / "runs", runtime_factory=FakeRuntime)
    assert policy.contexts == []
    assert len(FakeRuntime.instances) == 1  # No evaluation runtime was started.


@pytest.mark.parametrize("use_factory", [False, True])
def test_resolved_llm_policy_identity_is_saved_before_first_decision(tmp_path, use_factory):
    class InspectingClient:
        model = "fixture-model"
        reasoning = "low"
        api_key = "never-serialize-client-credential"

        def choose_action(self, *args):
            run_path = next((tmp_path / "runs").glob("*/result.json"))
            saved = read_json(run_path)
            identity = saved["episodes"][0]["policy"]["identity"]
            assert len(identity["fingerprint"]) == 64
            assert len(identity["instructions_sha256"]) == 64
            assert identity["settings"]["history_steps"] == 2
            assert identity["settings"]["model"] == "fixture-model"
            assert identity["configuration_status"] == "opaque_client_configuration_unvalidated"
            assert self.api_key not in str(saved)
            return {"tool": "finish", "arguments": {}}

    policy = LLMAgent(client=InspectingClient(), history_steps=2)
    kwargs = {"policy_factory": lambda: policy} if use_factory else {"policy": policy}
    report = ready(tmp_path).evaluate(
        **kwargs, render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime
    )
    assert report.summary["valid"] == 1, report.result
    episode = report.result["episodes"][0]
    assert episode["termination"] == "policy_finished"
    assert episode["policy"]["model"] == "fixture-model"
    assert episode["policy"]["identity"] == policy.provenance
    if not use_factory:
        assert report.result["policy"] == episode["policy"]


def test_policy_cannot_change_declared_configuration_between_actions(tmp_path):
    class ChangingPolicy(Policy):
        version = 1

        @property
        def provenance(self):
            return {"configuration_version": self.version}

        def act(self, observation):
            action = super().act(observation)
            self.version += 1
            return action

    report = ready(tmp_path).evaluate(
        ChangingPolicy(), render=False, output=tmp_path / "runs", runtime_factory=FakeRuntime
    )
    episode = report.result["episodes"][0]
    assert episode["status"] == "policy_error"
    assert "identity changed" in episode["error"]["message"]
    assert len(episode["steps"]) == 1
    assert report.summary["valid"] == 0


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


def test_generation_record_retains_total_and_stage_times_without_changing_world_identity(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("robot_worlds.sdk.time.perf_counter", lambda: clock[0])

    class Timed(Client):
        def complete_json(self, *args):
            clock[0] += 5
            return super().complete_json(*args)

    def factory(*args, **kwargs):
        clock[0] += 2
        return FakeRuntime(*args, **kwargs)

    worlds, records = [], []
    for attempt in range(2):
        world = generate("same task", client=Timed(), output=tmp_path / str(attempt), runtime_factory=factory)
        worlds.append(world)
        records.append(read_json(next((tmp_path / str(attempt)).glob("generation-*.json"))))
        clock[0] += 100
    assert worlds[0].id == worlds[1].id
    for record in records:
        assert record["elapsed_seconds"] == 7
        phases = [e for e in record["events"] if e["type"] == "generation_phase_finished"]
        assert next(e for e in phases if e["phase"] == "model")["duration_seconds"] == 5
        assert next(e for e in phases if e["phase"] == "physical_validation")["duration_seconds"] == 2
        assert record["events"][-1]["elapsed_seconds"] == record["elapsed_seconds"]
        assert all(a["elapsed_seconds"] <= b["elapsed_seconds"] for a, b in zip(record["events"], record["events"][1:]))
        assert "elapsed_seconds" not in str(worlds[0].manifest)


def test_unexpected_asset_failure_saves_terminal_timing_record(tmp_path, monkeypatch):
    def unavailable():
        raise OSError("private local setup detail")
    monkeypatch.setattr("robot_worlds.prepared_assets.prepared_asset_catalog", unavailable)
    with pytest.raises(OSError, match="private local setup detail"):
        generate("mission", client=Client(), output=tmp_path)
    record = read_json(next(tmp_path.glob("generation-*.json")))
    assert record["status"] == "failed"
    assert record["elapsed_seconds"] >= 0
    assert record["error_type"] == "OSError"
    assert record["events"][-1]["type"] == "generation_failed"
    assert "private local setup detail" not in str(record)
