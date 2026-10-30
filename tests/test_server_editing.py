"""Editing API contracts without executing a model or simulator."""
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from robot_worlds.artifacts import read_json, write_json
from robot_worlds.server import _job_worker, create_app

WORLD_ID = "a" * 64
CHILD_ID = "b" * 64


class Queue:
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
def api(tmp_path):
    queue = Queue()
    write_json(tmp_path / "worlds" / WORLD_ID[:20] / "manifest.json", {"id": WORLD_ID})
    with TestClient(create_app(tmp_path, executor_factory=lambda: queue)) as client:
        yield client, queue, tmp_path


def test_scoped_edit_forwards_budgets_without_unlocking_physics(api):
    client, queue, _ = api
    response = client.post(f"/api/worlds/{WORLD_ID}/refine", json={
        "prompt": "Move the inspection table half a metre east", "scope": ["inspection_table"],
        "max_cost_usd": .5, "max_model_seconds": 30,
    })
    assert response.status_code == 202
    payload = queue.jobs[0][2][-1]
    assert payload["scope"] == ["inspection_table"]
    assert payload["allow_task_changes"] is False
    assert payload["allow_physics_changes"] is False
    assert payload["client_options"]["max_cost_usd"] == .5
    assert payload["client_options"]["max_seconds"] == 30


@pytest.mark.parametrize("payload", [
    {"scope": []}, {"scope": ["x", "x"]}, {"scope": ["../private"]},
    {"allow_task_changes": True}, {"allow_physics_changes": True},
])
def test_repair_cannot_unlock_protected_content_or_take_invalid_scope(api, payload):
    client, queue, _ = api
    assert client.post(f"/api/worlds/{WORLD_ID}/repair", json=payload).status_code == 422
    assert queue.jobs == []


@pytest.mark.parametrize("kind", ["refine", "repair"])
def test_worker_keeps_computed_edit_evidence_and_passes_cancellation(api, monkeypatch, kind):
    client, queue, root = api
    edit = {"kind": kind, "parent_world_id": WORLD_ID, "changes": [
        {"entity_id": "table", "property": "position", "before": [0, 1, .5], "after": [.5, 1, .5]},
    ], "counts": {"changed": 1, "preserved": 9}}
    child = SimpleNamespace(id=CHILD_ID, manifest={"validation": {"status": "ready"}, "edit": edit})
    method = Mock(return_value=child)
    monkeypatch.setattr("robot_worlds.sdk.load", lambda _: SimpleNamespace(**{kind: method}))
    response = client.post(f"/api/worlds/{WORLD_ID}/{kind}", json={"prompt": "Move table"})
    job_id = response.json()["id"]
    _, _, args = queue.jobs[0]
    _job_worker(*args)
    saved = read_json(root / "jobs" / job_id / "job.json")
    assert saved["world_id"] == CHILD_ID
    assert saved["edit"] == edit
    assert saved["status"] == "completed"
    assert callable(method.call_args.kwargs["on_event"])
    assert method.call_args.kwargs["cancelled"]() is False
    assert read_json(root / "worlds" / WORLD_ID[:20] / "manifest.json") == {"id": WORLD_ID}


def test_repair_has_a_safe_default_request(api):
    client, queue, _ = api
    assert client.post(f"/api/worlds/{WORLD_ID}/repair", json={}).status_code == 202
    payload = queue.jobs[0][2][-1]
    assert "preserving" in payload["prompt"]
    assert payload["scope"] is None
    assert not payload["allow_task_changes"] and not payload["allow_physics_changes"]
