"""Task API and worker boundaries; no process, model or simulator is started."""
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from robot_worlds.artifacts import read_json, write_json
from robot_worlds.server import _job_worker, create_app
from robot_worlds.task_catalog import TaskVariant

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
def api(tmp_path, monkeypatch):
    queue = Queue()
    tasks = [TaskVariant(id=f"task{i}", name=f"Manipulation {i}", task=f"Perform interaction {i}",
                         rubric=[{"id": "hold", "description": "Hold the part", "predicate": "holding",
                                  "object_id": "part"}]) for i in range(12)]
    child = SimpleNamespace(id=CHILD_ID, manifest={"validation": {"status": "preview"}})
    world = SimpleNamespace(id=WORLD_ID, tasks=tasks, selected_task_id="task0", with_task=Mock(return_value=child))
    monkeypatch.setattr("robot_worlds.sdk.load", lambda _: world)
    write_json(tmp_path / "worlds" / WORLD_ID[:20] / "manifest.json", {"id": WORLD_ID})
    with TestClient(create_app(tmp_path, executor_factory=lambda: queue)) as client:
        yield client, queue, tmp_path, world


def test_task_catalog_returns_named_variants_without_starting_a_job(api):
    client, queue, _, world = api
    response = client.get(f"/api/worlds/{WORLD_ID}/tasks")
    assert response.status_code == 200
    assert response.json() == {"world_id": WORLD_ID, "count": 12, "selected_task_id": "task0",
                               "tasks": [task.model_dump(mode="json") for task in world.tasks]}
    assert queue.jobs == []
    world.tasks = []
    world.selected_task_id = None
    assert client.get(f"/api/worlds/{WORLD_ID}/tasks").json()["count"] == 0


@pytest.mark.parametrize("task_id", ["unknown", "invalid.name", "x" * 81])
def test_unknown_and_malformed_task_ids_are_rejected_before_queueing(api, task_id):
    client, queue, _, world = api
    assert client.post(f"/api/worlds/{WORLD_ID}/tasks/{task_id}/select", json={}).status_code == 422
    assert queue.jobs == []
    world.with_task.assert_not_called()


def test_task_selection_accepts_no_filesystem_or_callback_options(api):
    client, queue, _, _ = api
    path = f"/api/worlds/{WORLD_ID}/tasks/task1/select"
    assert client.post(path, json={"output": "/private"}).status_code == 422
    assert client.post(path, json={"asset_root": "/private"}).status_code == 422
    assert client.post(path, json={"runtime_factory": "arbitrary"}).status_code == 422
    assert client.post("/api/worlds/not-a-world/tasks/task1/select", json={}).status_code == 404
    assert queue.jobs == []


def test_task_worker_selects_immutable_version_and_emits_world_created(api):
    client, queue, root, world = api
    response = client.post(f"/api/worlds/{WORLD_ID}/tasks/task1/select", json={})
    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "task"
    assert queue.jobs[0][2][-1] == {"world_id": WORLD_ID, "task_id": "task1"}
    _job_worker(*queue.jobs[0][2])
    saved = read_json(root / "jobs" / job["id"] / "job.json")
    assert saved["status"] == "completed"
    assert saved["world_id"] == CHILD_ID
    assert saved["validation"] == {"status": "preview"}
    assert saved["events"][-1]["type"] == "world_created"
    assert saved["events"][-1]["world_id"] == CHILD_ID
    world.with_task.assert_called_once_with("task1", output=root / "worlds")
    assert read_json(root / "worlds" / WORLD_ID[:20] / "manifest.json") == {"id": WORLD_ID}


def test_cancelled_task_job_does_not_select_or_revalidate(api):
    client, queue, root, world = api
    job = client.post(f"/api/worlds/{WORLD_ID}/tasks/task1/select").json()
    client.post(f"/api/jobs/{job['id']}/cancel")
    _job_worker(*queue.jobs[0][2])
    world.with_task.assert_not_called()
    assert read_json(root / "jobs" / job["id"] / "job.json")["status"] == "cancelled"
