"""Saved-trace regrading boundaries; no model requests or physics runtimes."""

import copy
from types import SimpleNamespace

import pytest

from robot_worlds import sdk
from robot_worlds.artifacts import file_digest, read_json, write_json
from robot_worlds.models import WorldSpec


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    spec = WorldSpec.model_validate({
        "name": "Integrity test", "description": "Test only", "task": "Move the part",
        "bounds": [8, 8, 3],
        "objects": [{"id": "part", "label": "part", "kind": "box", "position": [2, 0, .1],
                     "size": [.2, .2, .2], "movable": True}],
        "regions": [{"id": "target", "label": "target", "position": [-2, 0, .2], "size": [1, 1, .4]}],
        "rubric": [{"id": "move", "description": "Move part", "predicate": "in_region",
                    "object_id": "part", "target_id": "target"}],
    })
    run_dir = tmp_path / "runs" / "original-run"
    world_dir = run_dir / "world"
    revised_dir = tmp_path / "revised-world"
    for directory in (world_dir, revised_dir):
        write_json(directory / "world.json", spec.model_dump(mode="json"))
    runtime = {"model_fingerprint": "frozen-model", "implementation_fingerprint": "frozen-implementation"}
    manifest = {"physical_id": "physical-world", "runtime": runtime,
                "validation": {"status": "ready"}}
    original_world = SimpleNamespace(id="original-world", path=world_dir, spec=spec, manifest=manifest)
    world = SimpleNamespace(id="revised-world", path=revised_dir, spec=spec.model_copy(deep=True), manifest=copy.deepcopy(manifest))
    monkeypatch.setattr(sdk, "load", lambda path: world if path == revised_dir else original_world)
    monkeypatch.setattr(sdk.Report, "_write_html", lambda self: None)
    trace = [{"time": time, "model_fingerprint": "frozen-model", "objects": {
        "part": {"position": position, "size": [.2, .2, .2], "quaternion": [1, 0, 0, 0],
                 "held_by": None, "contacts": [], "velocity": [0, 0, 0]}},
        "robot": {"position": [0, 0, 0]}}
        for time, position in [(0, [2, 0, .1]), (.5, [0, 0, .1]), (1, [-2, 0, .1])]]
    episode = {"index": 0, "seed": 7, "status": "completed", "sim_seconds": 1,
               "runtime": copy.deepcopy(runtime), "rubric": {"status": "passed", "success": True}}
    result = {"id": "original-run", "world_id": "original-world", "physical_id": "physical-world",
              "status": "completed", "episodes": [episode]}
    trace_path = run_dir / "episode-000" / "trace.json"
    write_json(trace_path, trace)
    episode["trace_evidence"] = sdk._trace_evidence(trace_path, trace, episode, result, complete=True)
    write_json(run_dir / "result.json", result)
    return SimpleNamespace(report=sdk.Report(run_dir), world=world, trace=trace,
                           trace_path=trace_path, result=result, original_world=original_world)


def regrade(recorded):
    return recorded.report.regrade(recorded.world)


def assert_insufficient(report):
    result = report.result
    episode = result["episodes"][0]
    assert episode["status"] == "grading_error"
    assert episode["evidence_validation"]["status"] == "insufficient_evidence"
    assert "rubric" not in episode
    assert result["summary"]["valid"] == 0
    assert result["summary"]["successes"] == 0


def test_complete_frozen_trace_can_be_regraded_without_recollecting(recorded):
    revised = regrade(recorded)
    assert revised.summary["successes"] == 1
    episode = revised.result["episodes"][0]
    assert episode["evidence_validation"]["status"] == "verified"
    assert episode["trace_evidence"] == recorded.result["episodes"][0]["trace_evidence"]
    assert revised.result["evidence_origin_run_id"] == "original-run"
    assert revised.result["parent_run_id"] == "original-run"
    assert recorded.report.result == recorded.result


@pytest.mark.parametrize("alteration", ["truncated", "changed", "missing", "swapped_model"])
def test_modified_or_missing_saved_trace_never_reuses_previous_success(recorded, alteration):
    trace = copy.deepcopy(recorded.trace)
    if alteration == "truncated":
        trace.pop()
    elif alteration == "changed":
        trace[-1]["objects"]["part"]["position"] = [2, 0, .1]
    elif alteration == "swapped_model":
        for state in trace:
            state["model_fingerprint"] = "another-model"
    else:
        recorded.trace_path.unlink()
    if alteration != "missing":
        write_json(recorded.trace_path, trace)
    assert_insufficient(regrade(recorded))


@pytest.mark.parametrize("field,value", [
    ("sample_count", 9), ("start_time", .25), ("end_time", 2),
    ("model_fingerprint", "another-model"), ("implementation_fingerprint", "other-code"),
    ("seed", 123), ("episode_index", 1), ("source_run_id", "another-run"),
    ("physical_id", "another-world"), ("complete", False),
])
def test_collection_metadata_must_match_episode_and_frozen_model(recorded, field, value):
    recorded.result["episodes"][0]["trace_evidence"][field] = value
    write_json(recorded.report.path / "result.json", recorded.result)
    assert_insufficient(regrade(recorded))


def test_legacy_run_without_collection_integrity_is_explicitly_insufficient(recorded):
    del recorded.result["episodes"][0]["trace_evidence"]
    write_json(recorded.report.path / "result.json", recorded.result)
    revised = regrade(recorded)
    assert_insufficient(revised)
    assert "Legacy" in revised.result["episodes"][0]["grading_error"]["message"]


@pytest.mark.parametrize("mutation", ["missing_reset", "short_final", "wrong_state_model", "backwards_time"])
def test_structural_completeness_is_checked_even_when_hash_matches(recorded, mutation):
    trace = copy.deepcopy(recorded.trace)
    if mutation == "missing_reset":
        trace = trace[1:]
    elif mutation == "short_final":
        trace = trace[:-1]
    elif mutation == "wrong_state_model":
        trace[1]["model_fingerprint"] = "wrong-model"
    else:
        trace[1]["time"] = 2
    write_json(recorded.trace_path, trace)
    episode = recorded.result["episodes"][0]
    episode["trace_evidence"] = sdk._trace_evidence(recorded.trace_path, trace, episode,
                                                    recorded.result, complete=True)
    write_json(recorded.report.path / "result.json", recorded.result)
    assert_insufficient(regrade(recorded))


def test_collection_metadata_freezes_byte_hash_count_and_span(recorded):
    evidence = recorded.result["episodes"][0]["trace_evidence"]
    assert evidence["sha256"] == file_digest(recorded.trace_path)
    assert evidence["sample_count"] == 3
    assert evidence["start_time"] == 0
    assert evidence["end_time"] == 1
    assert evidence["model_fingerprint"] == "frozen-model"


def test_batch_episode_keeps_worker_collection_identity(recorded):
    episode = recorded.result["episodes"][0]
    episode.update(index=4, worker_run_id="original-run")
    recorded.result["id"] = "aggregate-run"
    recorded.trace_path.parent.rename(recorded.report.path / "episode-004")
    write_json(recorded.report.path / "result.json", recorded.result)
    revised = regrade(recorded)
    assert revised.summary["successes"] == 1
    assert revised.result["episodes"][0]["trace_evidence"]["episode_index"] == 0


def test_second_regrade_retains_original_collection_binding(recorded, monkeypatch):
    first = regrade(recorded)
    # Its bundled world is the revised rubric version, with unchanged physics.
    monkeypatch.setattr(sdk, "load", lambda path: recorded.world)
    second = first.regrade(recorded.world)
    assert second.summary["successes"] == 1
    assert second.result["evidence_origin_run_id"] == "original-run"
    assert second.result["parent_run_id"] == first.result["id"]
    assert read_json(recorded.trace_path) == recorded.trace


def test_grader_failure_keeps_verified_evidence_and_removes_previous_score(recorded, monkeypatch):
    def broken_grader(spec, trace):
        raise RuntimeError("grader computation failed")

    monkeypatch.setattr("robot_worlds.rubric.grade", broken_grader)
    revised = regrade(recorded)
    episode = revised.result["episodes"][0]
    assert episode["status"] == "grading_error"
    assert episode["evidence_validation"] == {
        "status": "verified", "trace_sha256": file_digest(recorded.trace_path)}
    assert episode["grading_error"]["message"] == "grader computation failed"
    assert "rubric" not in episode
    assert revised.summary["valid"] == 0
    assert revised.summary["successes"] == 0


@pytest.mark.parametrize("status", ["simulation_error", "policy_error", "cancelled"])
def test_complete_recording_does_not_promote_failed_episode_after_regrade(recorded, status):
    recorded.result["episodes"][0]["status"] = status
    write_json(recorded.report.path / "result.json", recorded.result)
    revised = regrade(recorded)
    episode = revised.result["episodes"][0]
    assert episode["evidence_validation"]["status"] == "verified"
    assert episode["trace_evidence"]["complete"] is True
    assert episode["status"] == status
    assert revised.summary["valid"] == 0
    assert revised.summary["successes"] == 0


@pytest.mark.parametrize("fingerprint", ["model_fingerprint", "implementation_fingerprint"])
@pytest.mark.parametrize("change", ["changed", "missing_target", "missing_original"])
def test_regrade_requires_compatible_frozen_runtime_before_copying(recorded, tmp_path, fingerprint, change):
    if change == "changed":
        recorded.world.manifest["runtime"][fingerprint] = "changed-since-recording"
    elif change == "missing_target":
        recorded.world.manifest["runtime"].pop(fingerprint)
    else:
        recorded.original_world.manifest["runtime"].pop(fingerprint)
    output = tmp_path / "should-not-be-created"
    with pytest.raises(ValueError, match="compatible frozen world"):
        recorded.report.regrade(recorded.world, output=output)
    assert not output.exists()
    assert recorded.report.result == recorded.result


def test_changed_rubric_with_identical_runtime_can_be_regraded_twice(recorded, monkeypatch):
    recorded.world.spec.rubric[0].duration = 5
    first = regrade(recorded)
    assert first.summary["valid"] == 1
    assert first.summary["successes"] == 0
    assert first.result["episodes"][0]["evidence_validation"]["status"] == "verified"
    monkeypatch.setattr(sdk, "load", lambda path: recorded.world)
    second = first.regrade(recorded.world)
    assert second.summary["valid"] == 1
    assert second.summary["successes"] == 0
    assert second.result["evidence_origin_run_id"] == "original-run"


def test_compressed_trace_retains_identity_and_regrades_without_recollection(recorded):
    from robot_worlds.trace_storage import TraceCheckpoint
    path = recorded.trace_path.with_suffix('.json.gz')
    TraceCheckpoint(path).write(recorded.trace)
    recorded.trace_path.unlink()
    episode = recorded.result['episodes'][0]
    episode['trace_evidence'] = sdk._trace_evidence(path, recorded.trace, episode, recorded.result, complete=True)
    write_json(recorded.report.path/'result.json', recorded.result)
    revised = regrade(recorded)
    assert revised.summary['successes'] == 1
    assert revised.result['episodes'][0]['trace_evidence']['file'] == 'trace.json.gz'


def test_trace_evidence_cannot_redirect_reader_outside_the_episode(recorded):
    recorded.result['episodes'][0]['trace_evidence']['file'] = '../../private.json'
    write_json(recorded.report.path/'result.json', recorded.result)
    assert_insufficient(regrade(recorded))
