"""API-to-worker evaluation dispatch; no models, processes or simulators run."""
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from robot_worlds import batch, policy, sdk
from robot_worlds.artifacts import read_json, write_json
from robot_worlds.server import EvaluateRequest, _job_worker, create_app

WORLD_ID = "a" * 64
RUN_ID = "b" * 32


class RecordingQueue:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def shutdown(self, **kwargs):
        for future, _, _ in self.jobs:
            future.cancel()


@pytest.fixture
def dispatch(tmp_path, monkeypatch):
    world_path = tmp_path / "worlds" / WORLD_ID[:20]
    write_json(world_path / "manifest.json", {"id": WORLD_ID})
    report = SimpleNamespace(result={"id": RUN_ID, "status": "completed"})
    world = SimpleNamespace(
        manifest={"validation": {"status": "ready"}},
        evaluate=Mock(return_value=report),
    )
    loader = Mock(return_value=world)
    agent = Mock(name="constructed_policy")
    constructor = Mock(return_value=agent)
    parallel = Mock(return_value=report)
    monkeypatch.setattr(sdk, "load", loader)
    monkeypatch.setattr(policy, "LLMAgent", constructor)
    monkeypatch.setattr(batch, "evaluate_batch", parallel)
    return SimpleNamespace(root=tmp_path, world=world, loader=loader,
                           constructor=constructor, agent=agent,
                           parallel=parallel, report=report)


def run_worker(dispatch, **options):
    job_id = "c" * 32
    job_path = dispatch.root / "jobs" / job_id / "job.json"
    write_json(job_path, {"id": job_id, "status": "queued", "events": []})
    request = EvaluateRequest(**options)
    _job_worker(str(dispatch.root), job_id, "evaluate",
                {"world_id": WORLD_ID, **request.model_dump()})
    return job_path


@pytest.mark.parametrize("workers,backend", [(2, "auto"), (1, "egl"), (32, "egl")])
def test_batch_dispatch_preserves_policy_and_episode_budgets(dispatch, workers, backend):
    path = run_worker(
        dispatch, workers=workers, graphics_backend=backend, episodes=7,
        seed=123, max_steps=19, max_sim_seconds=46.5,
        model="custom-model", reasoning="high", max_cost_usd=0.75,
        max_model_seconds=73, input_price_per_million=2,
        output_price_per_million=8,
    )
    dispatch.constructor.assert_not_called()
    dispatch.world.evaluate.assert_not_called()
    dispatch.parallel.assert_called_once()
    args, kwargs = dispatch.parallel.call_args
    assert args == (dispatch.world,)
    assert kwargs["policy_config"] == {
        "kind": "llm", "model": "custom-model", "reasoning": "high",
        "max_steps": 19, "max_cost_usd": 0.75, "max_seconds": 73,
        "input_price_per_million": 2, "output_price_per_million": 8,
    }
    assert {key: kwargs[key] for key in (
        "episodes", "seed", "max_steps", "max_sim_seconds", "workers", "graphics_backend"
    )} == {"episodes": 7, "seed": 123, "max_steps": 19,
           "max_sim_seconds": 46.5, "workers": workers, "graphics_backend": backend}
    assert kwargs["output"] == dispatch.root / "runs"
    assert callable(kwargs["on_event"])
    assert callable(kwargs["cancelled"])
    job = read_json(path)
    assert job["status"] == "completed"
    assert job["run_id"] == RUN_ID
    assert job["finished_at"]


def test_default_dispatch_constructs_policy_only_for_serial_path(dispatch):
    path = run_worker(dispatch, episodes=3, seed=91, max_steps=12,
                      max_sim_seconds=8, max_cost_usd=1.25, max_model_seconds=44)
    dispatch.parallel.assert_not_called()
    dispatch.constructor.assert_called_once_with(
        model="gpt-6-astra", reasoning="medium", max_steps=12,
        max_cost_usd=1.25, max_seconds=44,
        input_price_per_million=None, output_price_per_million=None,
    )
    args, kwargs = dispatch.world.evaluate.call_args
    assert args == (dispatch.agent,)
    assert kwargs["episodes"] == 3
    assert kwargs["seed"] == 91
    assert kwargs["max_steps"] == 12
    assert kwargs["max_sim_seconds"] == 8
    assert "workers" not in kwargs and "graphics_backend" not in kwargs
    assert read_json(path)["status"] == "completed"


def test_parallel_events_and_cancellation_reach_persistent_job(dispatch):
    def evaluate(world, **kwargs):
        assert kwargs["cancelled"]() is False
        kwargs["on_event"]({"type": "run_started", "run_id": RUN_ID})
        job_path = dispatch.root / "jobs" / ("c" * 32) / "job.json"
        assert read_json(job_path)["run_id"] == RUN_ID
        (job_path.parent / "cancel").touch()
        assert kwargs["cancelled"]() is True
        return dispatch.report

    dispatch.parallel.side_effect = evaluate
    path = run_worker(dispatch, workers=2)
    job = read_json(path)
    assert job["status"] == "cancelled"
    assert job["events"] == [{"type": "run_started", "run_id": RUN_ID}]
    dispatch.constructor.assert_not_called()


def test_parallel_failure_remains_inspectable(dispatch):
    dispatch.parallel.side_effect = RuntimeError("worker pool unavailable")
    path = run_worker(dispatch, workers=2)
    job = read_json(path)
    assert job["status"] == "failed"
    assert job["error"]["message"] == "worker pool unavailable"
    assert job["finished_at"]
    dispatch.constructor.assert_not_called()


def test_api_queues_parallel_options_without_constructing_policy(dispatch):
    queue = RecordingQueue()
    with TestClient(create_app(dispatch.root, executor_factory=lambda: queue)) as client:
        response = client.post(f"/api/worlds/{WORLD_ID}/evaluate", json={
            "workers": 3, "graphics_backend": "egl", "episodes": 5,
            "seed": 77, "max_steps": 9, "max_sim_seconds": 11,
            "model": "custom-model", "reasoning": "low",
            "max_cost_usd": 0.6, "max_model_seconds": 17,
            "input_price_per_million": 1, "output_price_per_million": 4,
        })
        assert response.status_code == 202
        _, fn, args = queue.jobs[0]
        assert fn is _job_worker
        assert args[2] == "evaluate"
        assert args[3] == {"world_id": WORLD_ID, **EvaluateRequest(
            workers=3, graphics_backend="egl", episodes=5, seed=77,
            max_steps=9, max_sim_seconds=11, model="custom-model", reasoning="low",
            max_cost_usd=0.6, max_model_seconds=17,
            input_price_per_million=1, output_price_per_million=4,
        ).model_dump()}
        dispatch.constructor.assert_not_called()
        dispatch.parallel.assert_not_called()


@pytest.mark.parametrize("options", [
    {"workers": 0}, {"workers": -1}, {"workers": 33}, {"workers": 1.5},
    {"graphics_backend": "cuda"},
])
def test_api_rejects_invalid_parallel_options_before_queueing(dispatch, options):
    queue = RecordingQueue()
    with TestClient(create_app(dispatch.root, executor_factory=lambda: queue)) as client:
        response = client.post(f"/api/worlds/{WORLD_ID}/evaluate", json=options)
        assert response.status_code == 422
        assert queue.jobs == []
        dispatch.constructor.assert_not_called()
        dispatch.parallel.assert_not_called()
