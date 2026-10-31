"""Hosted native SDK acceptance: original assets, real sockets, synthetic policy.

This exercises the public sensor/action loop, not autonomous bowl insertion.
No model calls, privileged policy observations or object relocation are used.
"""
import base64
import copy
import json
import shutil
import threading
import uuid
import zipfile
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_prepared_assets import prepared_plan

from robot_worlds import generate, load
from robot_worlds.artifacts import read_json
from robot_worlds.remote_policy import RemotePolicy
from robot_worlds.sdk import Report


class SensorPolicy:
    """Small public-interface probe; deliberately does not attempt insertion."""

    def reset(self, context):
        assert set(context) == {"task", "robot", "action_contract", "sensor_contract"}
        assert context["robot"] == "tiago++"
        self.observations, self.results = [], []

    def act(self, observation):
        assert set(observation) == {"time", "robot", "tools", "last_tool_result", "sensors", "image"}
        assert observation["image"].startswith(b"\x89PNG\r\n\x1a\n")
        assert observation["robot"]["frame"] == "base"
        assert "position" not in observation["robot"]
        assert "held_by" not in json.dumps(observation["robot"])
        assert observation["sensors"]["camera"]["frame"] == "base"
        depth = observation["sensors"]["depth"]
        assert len(depth["values"]) == 24 and len(depth["values"][0]) == 32
        if self.results:
            assert observation["last_tool_result"] == self.results[-1]
        self.observations.append(copy.deepcopy(observation))
        step = len(self.observations) - 1
        if step == 0:
            # Select a visible sample from the sensor, not a hidden object pose.
            samples = [(x, y) for y, row in enumerate(depth["values"])
                       for x, value in enumerate(row) if value is not None and .1 < value < 8]
            assert samples, "Camera must expose at least one usable depth sample"
            x, y = samples[len(samples)//2]
            pixel = [depth["pixel_origin"][0] + x*depth["pixel_stride"],
                     depth["pixel_origin"][1] + y*depth["pixel_stride"]]
            return {"tool": "query_depth", "arguments": {"pixel": pixel}}
        return [
            {"tool": "drive", "arguments": {"distance": .08, "turn": 0}},
            {"tool": "gripper", "arguments": {"hand": "right", "opening": .035}},
            {"tool": "wait", "arguments": {"seconds": .2}},
            {"tool": "finish", "arguments": {}},
        ][step-1]

    def record_result(self, result):
        self.results.append(copy.deepcopy(result))


@contextmanager
def policy_server(policy):
    """Real loopback HTTP transport, with server assertion errors surfaced."""
    errors, requests = [], []
    session = uuid.uuid4().hex

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, payload))
                reply = {"protocol_version": "1", "session_id": session}
                if self.path == "/reset":
                    policy.reset(payload["context"])
                    reply["episode_id"] = payload["episode_id"]
                else:
                    assert payload["session_id"] == session
                    reply["sequence"] = payload["sequence"]
                    if self.path == "/act":
                        obs = payload["observation"]
                        assert obs["image"]["encoding"] == "base64"
                        obs["image"] = base64.b64decode(obs["image"]["data"], validate=True)
                        obs["tools"] = payload["tools"]
                        reply["action"] = policy.act(obs)
                    elif self.path == "/result":
                        policy.record_result(payload["result"])
                        reply["accepted"] = True
                    else:
                        raise AssertionError(self.path)
                content = json.dumps(reply).encode()
                self.send_response(200)
            except Exception as error:
                errors.append(error)
                content = b'{}'
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert not errors, errors


@pytest.fixture
def household_world(tmp_path, native_rendering):
    class Model:
        def complete_json(self, *args):
            return prepared_plan()

    world = generate("Put the original porcelain bowl inside the microwave", client=Model(),
                     output=tmp_path / "worlds", max_repairs=0, online_assets=False)
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    return load(world.path)


@pytest.mark.parametrize("connection", ["in_process", "http"])
def test_household_sensor_action_report_and_portable_reload(household_world, tmp_path, connection):
    world = household_world
    probe = SensorPolicy()
    with policy_server(probe) if connection == "http" else nullcontext((None, [])) as (url, requests):
        with RemotePolicy(url, timeout=10, max_seconds=120) if url else nullcontext(probe) as policy:
            report = world.evaluate(policy, max_steps=5, max_sim_seconds=15,
                                    output=tmp_path / "runs", max_replay_frames=8)
        if url:
            assert [path for path, _ in requests] == ["/reset"] + ["/act", "/result"]*5
            assert [data["sequence"] for path, data in requests if path == "/act"] == list(range(5))
    assert report.summary["valid"] == 1, report.result
    assert report.summary["successes"] == 0  # Movement alone cannot earn placement.
    assert len(probe.observations) == len(probe.results) == 5
    assert probe.results[0]["status"] == "measured"
    assert probe.results[0]["frame"] == "base"
    assert probe.results[1]["translation_feedback"] == "wheel_odometry"
    assert probe.results[1]["estimated_distance_m"] > .02
    assert probe.observations[2]["time"] > probe.observations[1]["time"]
    assert probe.results[-1]["ended"] is True

    episode = report.result["episodes"][0]
    assert episode["termination"] == "policy_finished"
    assert episode["runtime"]["engine"] == "mujoco"
    assert episode["replay"]["source"] == "recorded_simulation_states"
    assert episode["replay"]["frame_count"] >= 2
    for frame in episode["frames"]:
        assert (report.path / frame["path"]).read_bytes().startswith(b"\x89PNG")
    trace = read_json(report.path / "episode-000/trace.json")
    # Privileged state is used only here, outside the policy, to prove real motion.
    assert trace[-1]["robot"]["position"][0] - trace[0]["robot"]["position"][0] > .02

    world_id, summary = world.id, report.summary
    world_zip = world.export(tmp_path / "world.zip")
    report_zip = report.export(tmp_path / "report.zip")
    shutil.rmtree(world.path.parent)
    shutil.rmtree(report.path.parent)
    if (tmp_path / "asset-cache").exists():
        shutil.rmtree(tmp_path / "asset-cache")
    # These archives were produced above, not supplied by an untrusted caller.
    for archive, target in ((world_zip, "restored-world"), (report_zip, "restored-report")):
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(tmp_path / target)
    restored = load(tmp_path / "restored-world")
    saved_report = Report(tmp_path / "restored-report")
    assert restored.id == world_id
    assert load(saved_report.path / "world").id == world_id
    assert saved_report.summary == summary
    assert saved_report.regrade(restored, output=tmp_path / "regraded").summary == summary
    assert (saved_report.path / "report.html").is_file()
    # Reloaded geometry must also run, not merely pass hash checks.
    repeated = restored.evaluate(SensorPolicy(), max_steps=5, max_sim_seconds=15,
                                 output=tmp_path / "reloaded-runs", max_replay_frames=2)
    assert repeated.summary == summary, repeated.result
