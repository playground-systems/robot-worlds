import json

import httpx
import pytest

from robot_worlds.policy import PolicyBudgetExceeded
from robot_worlds.remote_policy import RemotePolicy, RemotePolicyError

TOOLS = [{"name": "wait", "description": "wait", "parameters": {"type": "object", "properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 5}}, "required": ["seconds"], "additionalProperties": False}}]


class Service:
    def __init__(self):
        self.requests = []
        self.count = 0
        self.session = None
    def __call__(self, request):
        data = json.loads(request.content)
        self.requests.append((request.url.path, data, request.headers))
        common = {"protocol_version": "1"}
        if request.url.path == "/reset":
            self.count += 1
            self.session = f"session-{self.count}"
            return httpx.Response(200, json={**common, "session_id": self.session, "episode_id": data["episode_id"]})
        if request.url.path == "/act":
            return httpx.Response(200, json={**common, "session_id": self.session, "sequence": data["sequence"], "action": {"tool": "wait", "arguments": {"seconds": .1}}})
        return httpx.Response(200, json={**common, "session_id": self.session, "sequence": data["sequence"], "accepted": True})


def test_protocol_sends_only_actor_context_and_observations_and_resets():
    service = Service()
    with RemotePolicy("http://127.0.0.1:8001", token="private-token", transport=httpx.MockTransport(service)) as policy:
        policy.reset({"task": "move", "robot": "tiago++", "rubric": "secret", "world": "secret"})
        action = policy.act({"time": 0, "image": b"png-data", "visible_objects": [], "tools": TOOLS, "objects": "privileged", "rubric": "secret", "qpos": "private"})
        policy.record_result({"status": "executed"})
        assert action["tool"] == "wait"
        reset, act, result = [r[1] for r in service.requests]
        assert set(reset["context"]) == {"task", "robot"}
        assert set(act["observation"]) == {"time", "image"}
        assert act["observation"]["image"] == {"encoding": "base64", "data": "cG5nLWRhdGE="}
        assert result["result"] == {"status": "executed"}
        assert "private-token" not in json.dumps(policy.transcript)
        assert "cG5nLWRhdGE=" not in json.dumps(policy.transcript)
        assert service.requests[0][2]["authorization"] == "Bearer private-token"
        previous_episode = reset["episode_id"]
        policy.reset({"task": "second"})
        assert service.requests[-1][1]["episode_id"] != previous_episode
        assert policy.usage["policy_steps"] == 0
        assert len(policy.transcript) == 1


def test_reset_failure_invalidates_previous_session():
    service = Service()
    def handler(request):
        if len(service.requests) >= 1:
            return httpx.Response(503)
        return service(request)
    with RemotePolicy("http://localhost:8000", transport=httpx.MockTransport(handler)) as policy:
        policy.reset({})
        with pytest.raises(RemotePolicyError, match="HTTP 503"):
            policy.reset({})
        with pytest.raises(RemotePolicyError, match="successful remote reset"):
            policy.act({"tools": TOOLS})


def test_reused_session_and_stale_action_response_rejected():
    service = Service()
    def stale_action(request):
        response = service(request)
        if request.url.path == "/act":
            data = response.json()
            data["sequence"] = 99
            return httpx.Response(200, json=data)
        return response
    with RemotePolicy("http://localhost:8000", transport=httpx.MockTransport(stale_action)) as policy:
        policy.reset({})
        with pytest.raises(RemotePolicyError, match="sequence"):
            policy.act({"tools": TOOLS})
        with pytest.raises(RemotePolicyError, match="successful remote reset"):
            policy.act({"tools": TOOLS})
    def reused(request):
        return httpx.Response(200, json={"protocol_version": "1", "session_id": "same", "episode_id": json.loads(request.content)["episode_id"]})
    with RemotePolicy("http://localhost:8000", transport=httpx.MockTransport(reused)) as policy:
        policy.reset({})
        with pytest.raises(RemotePolicyError, match="fresh session"):
            policy.reset({})


def test_unknown_action_and_bad_arguments_fail_before_execution():
    service = Service()
    def handler(request):
        response = service(request)
        if request.url.path == "/act":
            data = response.json()
            data["action"]["arguments"]["seconds"] = 1000
            return httpx.Response(200, json=data)
        return response
    with RemotePolicy("http://localhost:8000", transport=httpx.MockTransport(handler)) as policy:
        policy.reset({})
        with pytest.raises(ValueError):
            policy.act({"tools": TOOLS})
        assert policy.transcript[-1]["error"]


def test_pending_result_must_be_acknowledged_and_decisions_bounded():
    service = Service()
    with RemotePolicy("http://localhost:8000", max_steps=1, transport=httpx.MockTransport(service)) as policy:
        policy.reset({})
        policy.act({"tools": TOOLS})
        with pytest.raises(RemotePolicyError, match="previous tool result"):
            policy.act({"tools": TOOLS})
        policy.record_result({"status": "executed"})
        with pytest.raises(PolicyBudgetExceeded):
            policy.act({"tools": TOOLS})


def test_timeouts_redirects_oversized_and_nonfinite_payloads_are_rejected():
    def timeout(request):
        raise httpx.ReadTimeout("provider may include secrets", request=request)
    with RemotePolicy("http://localhost:8000", transport=httpx.MockTransport(timeout)) as policy:
        with pytest.raises(RemotePolicyError, match="ReadTimeout") as error:
            policy.reset({})
        assert "provider may include secrets" not in str(error.value)
    with RemotePolicy("http://localhost:8000", transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "https://example.com"}))) as policy:
        with pytest.raises(RemotePolicyError, match="HTTP 302"):
            policy.reset({})
    with RemotePolicy("http://localhost:8000", max_response_bytes=10, transport=httpx.MockTransport(Service())) as policy:
        with pytest.raises(RemotePolicyError, match="response exceeds"):
            policy.reset({})
    with RemotePolicy("http://localhost:8000", max_request_bytes=10, transport=httpx.MockTransport(Service())) as policy:
        with pytest.raises(RemotePolicyError, match="request size"):
            policy.reset({})
    with RemotePolicy("http://localhost:8000", transport=httpx.MockTransport(Service())) as policy:
        policy.reset({})
        with pytest.raises(RemotePolicyError, match="finite JSON"):
            policy.act({"time": float("nan"), "tools": TOOLS})


def test_url_requires_secure_remote_endpoint_and_no_embedded_credentials():
    for url in ["http://example.com", "https://user:pass@example.com", "https://example.com?token=x", "file:///tmp/policy", "https://example.com/#fragment"]:
        with pytest.raises(ValueError):
            RemotePolicy(url)


def test_real_loopback_http_lifecycle():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, data))
            response = {"protocol_version": "1", "session_id": "local-session"}
            if self.path == "/reset":
                response["episode_id"] = data["episode_id"]
            else:
                response["sequence"] = data["sequence"]
                if self.path == "/act":
                    response["action"] = {"tool": "wait", "arguments": {"seconds": .01}}
                else:
                    response["accepted"] = True
            body = json.dumps(response).encode()
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
        with RemotePolicy(f"http://127.0.0.1:{server.server_port}", timeout=2) as policy:
            policy.reset({"task": "wait"})
            assert policy.act({"tools": TOOLS})["arguments"]["seconds"] == .01
            policy.record_result({"status": "executed"})
        assert [p for p, _ in requests] == ["/reset", "/act", "/result"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
