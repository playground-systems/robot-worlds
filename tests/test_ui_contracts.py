"""Browser/API integration boundaries without model calls or simulated success."""

import copy
import json
import re
from concurrent.futures.process import BrokenProcessPool
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from robot_worlds.artifacts import file_digest, read_json, write_json
from robot_worlds.report import render_report
from robot_worlds.server import _job_worker, create_app


class IdleExecutor:
    def shutdown(self, **kwargs):
        pass


class BrokenExecutor(IdleExecutor):
    def submit(self, *args, **kwargs):
        raise BrokenProcessPool("Worker process is unavailable")


def test_rejected_worker_submission_never_leaves_a_queued_orphan(tmp_path):
    with TestClient(
        create_app(tmp_path, executor_factory=BrokenExecutor), raise_server_exceptions=False
    ) as client:
        response = client.post("/api/generate", json={"prompt": "A real requested scene"})
    assert response.status_code == 503
    jobs = [read_json(path) for path in (tmp_path / "jobs").glob("*/job.json")]
    assert all(job["status"] == "failed" for job in jobs)


def test_world_asset_endpoint_checks_recorded_hash_and_bundle_confinement(tmp_path):
    world_id = "a" * 64
    folder = tmp_path / "worlds" / world_id[:20]
    asset = folder / "assets" / "mesh.obj"
    asset.parent.mkdir(parents=True)
    asset.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    outside = tmp_path / "outside.obj"
    outside.write_text("private local contents")
    (folder / "assets" / "escape.obj").symlink_to(outside)
    write_json(
        folder / "manifest.json",
        {
            "id": world_id,
            "assets": {
                "assets/mesh.obj": file_digest(asset),
                "assets/escape.obj": file_digest(outside),
            },
        },
    )
    with TestClient(create_app(tmp_path, executor_factory=IdleExecutor)) as client:
        response = client.get(f"/api/worlds/{world_id}/files/assets/mesh.obj")
        assert response.status_code == 200 and response.text == asset.read_text()
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/api/worlds/{world_id}/files/assets/escape.obj").status_code == 404
        asset.write_text("changed after validation")
        assert client.get(f"/api/worlds/{world_id}/files/assets/mesh.obj").status_code == 409


def test_reference_endpoints_cannot_follow_a_directory_symlink_outside_bundle(tmp_path):
    outside = tmp_path / "private"
    write_json(outside / "world.json", {"private": True})
    references = tmp_path / "references"
    references.mkdir()
    (references / "escaped").symlink_to(outside, target_is_directory=True)
    with TestClient(create_app(tmp_path, executor_factory=IdleExecutor)) as client:
        assert client.get("/api/references/escaped/world.json").status_code == 404


def test_run_list_returns_summary_without_all_episode_steps_and_frames(tmp_path):
    run_id = "b" * 32
    write_json(
        tmp_path / "runs" / run_id / "result.json",
        {
            "id": run_id,
            "status": "completed",
            "world_id": "c" * 64,
            "summary": {"attempted": 1, "valid": 1, "successes": 0, "success_rate": 0},
            "episodes": [{"index": 0, "steps": [{"action": {"tool": "look"}}], "frames": []}],
        },
    )
    with TestClient(create_app(tmp_path, executor_factory=IdleExecutor)) as client:
        items = client.get("/api/runs").json()
        assert items[0]["id"] == run_id
        assert items[0]["summary"]["successes"] == 0
        assert "episodes" not in items[0]
        assert client.get(f"/api/runs/{run_id}").json()["episodes"][0]["steps"]


def test_cancelled_worker_does_not_invoke_generation_or_erase_saved_events(tmp_path, monkeypatch):
    def must_not_generate(*args, **kwargs):
        raise AssertionError("Cancelled queued work must not call the model")

    monkeypatch.setattr("robot_worlds.sdk.generate", must_not_generate)
    job_id = "d" * 32
    folder = tmp_path / "jobs" / job_id
    event = {"type": "queued_evidence", "message": "Saved before cancellation"}
    write_json(folder / "job.json", {"id": job_id, "status": "queued", "events": [event]})
    (folder / "cancel").touch()
    _job_worker(str(tmp_path), job_id, "generate", {"prompt": "request"})
    job = read_json(folder / "job.json")
    assert job["status"] == "cancelled" and job["finished_at"]
    assert job["events"] == [event]


class HTMLInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "script":
            self.scripts.append(values)


def test_browser_control_ids_and_local_module_dependencies_resolve():
    import robot_worlds

    static = Path(robot_worlds.__file__).parent / "static"
    inventory = HTMLInventory()
    inventory.feed((static / "index.html").read_text())
    app = (static / "app.js").read_text()
    referenced_ids = set(re.findall(r"\$\('([^']+)'\)", app))
    assert referenced_ids <= inventory.ids
    for filename in (
        "app.js",
        "scene.js",
        "vendor/OrbitControls.js",
        "vendor/OBJLoader.js",
        "vendor/three.module.js",
        "vendor/three.core.js",
    ):
        module = static / filename
        assert module.is_file()
        dependencies = re.findall(
            r"^\s*import\s+(?:[^;]*?\s+from\s+)?['\"]([^'\"]+)['\"]",
            module.read_text(),
            re.M | re.S,
        )
        for dependency in dependencies:
            assert dependency == "three" or dependency.startswith(".")
            if dependency.startswith("."):
                assert (module.parent / urlsplit(dependency).path).resolve().is_file(), (filename, dependency)


def test_standalone_report_escapes_untrusted_text_and_rejects_external_frame_paths():
    dangerous = '</script><script>alert("injected")</script><img src=x onerror=alert(1)>'
    paths = [
        "episode-000/frame 1.png",
        "../private.png",
        "https://example.com/x.png",
        "//example.com/x.png",
        "file:///tmp/private.png",
        "episode-000\\frame.png",
    ]
    result = {
        "id": "test",
        "episodes": [{"frames": [{"path": path, "time": i} for i, path in enumerate(paths)]}],
    }
    original = copy.deepcopy(result)
    rendered = render_report(result, {"name": dangerous, "task": dangerous})
    inventory = HTMLInventory()
    inventory.feed(rendered)
    assert len(inventory.scripts) == 2
    assert dangerous not in rendered
    embedded = re.search(
        r'<script type="application/json" id="report-data">(.*?)</script>', rendered, re.S
    )
    payload = json.loads(embedded.group(1))
    assert payload["spec"]["task"] == dangerous
    assert payload["result"]["episodes"][0]["frames"][0]["path"] == "episode-000/frame%201.png"
    assert all(frame["path"] is None for frame in payload["result"]["episodes"][0]["frames"][1:])
    assert result == original
