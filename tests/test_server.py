"""Local API boundaries and persistent job lifecycle without model or GPU calls."""

from concurrent.futures import Future

import pytest
from fastapi.testclient import TestClient

from robot_worlds.artifacts import read_json, write_json
from robot_worlds.server import create_app


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
    with TestClient(create_app(tmp_path, executor_factory=lambda: queue)) as client:
        yield client, queue, tmp_path


def test_workbench_served_and_cross_site_mutations_rejected(api):
    client, queue, root = api
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    health = client.get("/api/health").json()
    assert isinstance(health["key_configured"], bool)
    assert "key" not in health
    assert client.post("/api/generate", json={"prompt": "factory"},
                       headers={"Origin": "https://external.example"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "external.example"}).status_code == 400
    assert not queue.jobs


def test_queue_payload_cancel_and_capacity(api):
    client, queue, root = api
    response = client.post("/api/generate", json={"prompt": "A workshop", "max_repairs": 1})
    assert response.status_code == 202
    job = response.json()
    future, fn, args = queue.jobs[0]
    assert args[-1]["prompt"] == "A workshop"
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "queued"
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert future.cancelled()
    assert (root / "jobs" / job["id"] / "cancel").exists()
    for _ in range(8):
        assert client.post("/api/generate", json={"prompt": "Workshop"}).status_code == 202
    assert client.post("/api/generate", json={"prompt": "Workshop"}).status_code == 429


def test_worker_crash_and_restart_leave_inspectable_failure(api):
    client, queue, root = api
    job = client.post("/api/generate", json={"prompt": "Workshop"}).json()
    queue.jobs[0][0].set_exception(RuntimeError("Worker exited"))
    record = client.get(f"/api/jobs/{job['id']}").json()
    assert record["status"] == "failed"
    assert record["error"]["message"] == "Worker exited"
    stale_id = "a" * 32
    write_json(root / "jobs" / stale_id / "job.json", {"id": stale_id, "status": "running"})
    # A second server is rejected while the first owns this data directory.
    with pytest.raises(RuntimeError, match="Another Robot Worlds"):
        with TestClient(create_app(root, executor_factory=Queue)):
            pass


def test_restart_marks_unfinished_jobs_interrupted(tmp_path):
    stale_id = "a" * 32
    write_json(tmp_path / "jobs" / stale_id / "job.json", {"id": stale_id, "status": "running"})
    with TestClient(create_app(tmp_path, executor_factory=Queue)) as client:
        assert client.get(f"/api/jobs/{stale_id}").json()["status"] == "interrupted"


def test_inputs_and_artifact_boundaries(api):
    client, queue, root = api
    assert client.post("/api/generate", json={"prompt": "", "max_repairs": 99}).status_code == 422
    assert client.post("/api/generate", json={"prompt": "factory", "api_key": "not accepted"}).status_code == 422
    assert client.post("/api/generate", json={"prompt": "factory", "min_objects": 50, "max_objects": 10}).status_code == 422
    assert client.get("/api/worlds/not-a-world").status_code == 404
    assert client.get("/api/jobs/unknown").status_code == 404
    run_id = "b" * 32
    run_dir = root / "runs" / run_id
    write_json(run_dir / "result.json", {"id": run_id, "status": "completed"})
    secret = root / "outside.json"
    write_json(secret, {"private": True})
    (run_dir / "escape.json").symlink_to(secret)
    assert client.get(f"/api/runs/{run_id}/files/escape.json").status_code == 404
    assert client.get(f"/api/runs/{run_id}/files/result.json").json()["id"] == run_id
    assert client.get(f"/api/runs/{run_id}/report").status_code == 409
    assert read_json(secret) == {"private": True}


def test_uploaded_mesh_is_content_addressed_and_explicitly_bound_to_generation(api):
    client, queue, root = api
    mesh = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    response = client.post("/api/assets/fixture.obj", content=mesh,
                           headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 201
    asset_id = response.json()["id"]
    assert (root / "assets" / asset_id).read_bytes() == mesh
    assert client.post("/api/generate", json={"prompt": "Use my fixture", "asset_ids": [asset_id]}).status_code == 202
    prompt = queue.jobs[0][2][-1]["prompt"]
    assert str(root / "assets" / asset_id) in prompt
    assert "convex collision" in prompt
    assert client.post("/api/assets/script.py", content=b"code").status_code == 422
    assert client.post("/api/assets/empty.obj", content=b"").status_code == 422
    assert client.post("/api/generate", json={"prompt": "Use asset", "asset_ids": ["../outside.obj"]}).status_code == 422


def test_compressed_trace_download_is_not_mislabeled_as_plain_json(api):
    import gzip
    client, _, root = api
    run_id = 'b'*32
    run = root/'runs'/run_id
    write_json(run/'result.json', {'id':run_id,'status':'completed'})
    trace = run/'episode-000'/'trace.json.gz'
    trace.parent.mkdir()
    trace.write_bytes(gzip.compress(b'[{"time":0}]'))
    response = client.get(f'/api/runs/{run_id}/files/episode-000/trace.json.gz')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/gzip'
    assert 'attachment' in response.headers['content-disposition']
    assert gzip.decompress(response.content) == b'[{"time":0}]'
