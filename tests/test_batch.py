"""Real spawned MuJoCo processes with a local protocol test service, no hosted model."""
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from robot_worlds.batch import _copy_episode, _policy_config, evaluate_batch
from robot_worlds.models import WorldSpec
from robot_worlds.sdk import World


@pytest.fixture
def remote_url():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = {"protocol_version": "1"}
            if self.path == "/reset":
                reply.update(session_id=uuid.uuid4().hex, episode_id=data["episode_id"])
            else:
                reply.update(session_id=data["session_id"], sequence=data["sequence"])
                if self.path == "/act":
                    reply["action"] = {"tool": "wait", "arguments": {"seconds": .05}} if data["sequence"] == 0 else {"tool": "finish", "arguments": {}}
                else:
                    reply["accepted"] = True
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def native_world(tmp_path):
    spec = WorldSpec.model_validate({"name": "Batch fixture", "description": "Internal test fixture",
        "task": "Move part to output", "bounds": [8, 8, 3],
        "objects": [{"id": "part", "label": "part", "kind": "box", "position": [2, 0, .1], "size": [.2, .2, .2], "movable": True}],
        "regions": [{"id": "output", "label": "output", "position": [-2, 0, .2], "size": [1, 1, .4]}],
        "rubric": [{"id": "move", "description": "move", "predicate": "in_region", "object_id": "part", "target_id": "output"}]})
    world = World.from_spec(spec, tmp_path / "worlds")
    assert world.manifest["validation"]["status"] == "ready"
    return world


@pytest.mark.parametrize("workers", [1, 2])
def test_actual_spawned_physics_policies_and_frames_preserve_episode_identity(tmp_path, native_rendering, remote_url, workers):
    world = native_world(tmp_path)
    started = time.monotonic()
    report = evaluate_batch(world, policy_config={"kind": "remote", "base_url": remote_url, "timeout": 5},
                            episodes=2, workers=workers, seed=11, max_steps=2, max_sim_seconds=.2,
                            render=True, replay_fps=5, max_replay_frames=10, output=tmp_path / "runs")
    elapsed = time.monotonic()-started
    assert report.summary["valid"] == 2, report.result
    assert report.summary["successes"] == 0
    assert report.result["protocol"]["executor"] == "spawn"
    assert [e["seed"] for e in report.result["episodes"]] == [11, 12]
    for index, episode in enumerate(report.result["episodes"]):
        assert episode["index"] == index
        assert episode["runtime"]["seed"] == 11+index
        assert 0 < episode["sim_seconds"] <= .2
        assert episode["runtime"]["engine"] == "mujoco"
        assert episode["frames"]
        for frame in episode["frames"]:
            assert frame["path"].startswith(f"episode-{index:03d}/")
            assert (report.path / frame["path"]).is_file()
        assert (report.path / f"episode-{index:03d}" / "trace.json.gz").is_file()
    print(f"Two native MuJoCo render+step episodes, {workers} spawned workers: {elapsed:.3f}s ({2/elapsed:.3f} episodes/s), physics CPU")


def test_worker_policy_failure_is_not_an_ordinary_task_failure(tmp_path, native_rendering):
    world = native_world(tmp_path)
    report = evaluate_batch(world, policy_config={"kind": "remote", "base_url": "http://127.0.0.1:1", "timeout": .2},
                            episodes=2, workers=2, max_steps=1, render=False, output=tmp_path / "runs")
    assert report.summary["valid"] == 0
    assert report.summary["success_rate"] is None
    assert all(e["status"] == "policy_error" for e in report.result["episodes"])
    assert all((report.path / f"episode-{i:03d}" / "trace.json.gz").is_file() for i in range(2))


def test_parallel_configs_reject_opaque_clients_and_literal_credentials():
    for config in [{"kind": "custom"}, {"kind": "llm", "client": {}}, {"kind": "remote", "base_url": "https://example.com", "token": "secret"}]:
        with pytest.raises(ValueError):
            _policy_config(config)


def test_frame_copy_rejects_worker_path_escape(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "result.json").write_text(json.dumps({"id": "child", "episodes": [{"index": 0, "seed": 0, "frames": [{"time": 0, "path": "../../secret"}]}]}))
    with pytest.raises(ValueError, match="frame path"):
        _copy_episode(source, tmp_path / "dest", 0, 0)
