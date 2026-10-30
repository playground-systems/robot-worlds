"""Explicit robot-worlds HTTP policy protocol; not a generic checkpoint adapter.

The caller supplies a service implementing /reset, /act and /result. Only actor
observations cross the boundary. Simulation and grading remain local.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import math
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx

from .llm import audit_value, validate_action
from .policy import PUBLIC_CONTEXT_KEYS, PUBLIC_OBSERVATION_KEYS, PolicyBudgetExceeded, PolicyError


class RemotePolicyError(PolicyError):
    """Transport or protocol failure, distinct from failure of the robot task."""


def _wire(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _wire(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise RemotePolicyError(f"Observation contains unsupported wire type: {type(value).__name__}")


class RemotePolicy:
    """A synchronous, per-episode HTTP policy with explicit remote reset.

    ``token`` is sent only as an Authorization header. HTTP is accepted only for
    loopback development; deployed endpoints require HTTPS. Redirects/retries
    are disabled because repeating stateful act/reset requests is ambiguous.
    ``timeout`` bounds each HTTP operation; ``max_seconds`` bounds the episode
    budget between requests and rejects late responses. The simulator is paused
    during requests, just as with an in-process policy.
    """

    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 30,
                 max_seconds: float = 600, max_steps: int = 100,
                 max_request_bytes: int = 32 * 1024 * 1024,
                 max_response_bytes: int = 1024 * 1024,
                 transport: httpx.BaseTransport | None = None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Policy URL needs an http(s) origin/path without credentials, query or fragment")
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if parsed.scheme == "http" and not loopback:
            raise ValueError("Remote policy endpoints require HTTPS; HTTP is supported for loopback development")
        if not all(math.isfinite(v) and v > 0 for v in (timeout, max_seconds)):
            raise ValueError("Remote policy timeouts and time budgets must be finite and positive")
        if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in (max_steps, max_request_bytes, max_response_bytes)):
            raise ValueError("Remote policy count and size budgets must be positive integers")
        self.base_url = base_url.rstrip("/")
        self.timeout, self.max_seconds, self.max_steps = timeout, max_seconds, max_steps
        self.max_request_bytes, self.max_response_bytes = max_request_bytes, max_response_bytes
        self._client = httpx.Client(timeout=timeout, follow_redirects=False, transport=transport,
                                    headers={"Authorization": f"Bearer {token}"} if token else {})
        self._session = None
        self._seen_sessions = set()
        self._episode_id = None
        self._started = None
        self._pending_result = None
        self._closed = False
        self.transcript = []
        self.usage = {"requests": 0, "policy_steps": 0, "request_bytes": 0, "response_bytes": 0}

    def _request(self, path, payload):
        if self._closed:
            raise RemotePolicyError("Remote policy is closed")
        elapsed = time.monotonic() - self._started if self._started is not None else 0
        remaining = self.max_seconds - elapsed
        if remaining <= 0:
            raise PolicyBudgetExceeded("Remote policy episode time budget exhausted")
        try:
            encoded = json.dumps(_wire(payload), allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise RemotePolicyError("Remote policy payload is not finite JSON") from exc
        if len(encoded) > self.max_request_bytes:
            raise RemotePolicyError("Remote policy observation exceeds request size budget")
        self.usage["requests"] += 1
        self.usage["request_bytes"] += len(encoded)
        try:
            with self._client.stream("POST", self.base_url + path, content=encoded,
                                     headers={"Content-Type": "application/json"},
                                     timeout=min(self.timeout, remaining)) as response:
                if response.status_code != 200:
                    raise RemotePolicyError(f"Remote policy {path} returned HTTP {response.status_code}")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise RemotePolicyError("Remote policy response exceeds size budget")
                    chunks.append(chunk)
                    if time.monotonic() - self._started > self.max_seconds:
                        raise PolicyBudgetExceeded("Remote policy response exceeded episode time budget")
                self.usage["response_bytes"] += size
                value = json.loads(b"".join(chunks))
        except (RemotePolicyError, PolicyBudgetExceeded):
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise RemotePolicyError(f"Remote policy {path} request failed ({type(exc).__name__})") from exc
        if not isinstance(value, dict) or value.get("protocol_version") != "1":
            raise RemotePolicyError("Remote policy response must be a protocol_version='1' object")
        return value

    def reset(self, context: dict) -> None:
        if not isinstance(context, dict):
            raise TypeError("Policy reset context must be a dictionary")
        # Invalidate the previous session before any network operation. Failed
        # resets must never leave a usable stale session or queued action.
        self._session, self._pending_result = None, None
        self._episode_id = uuid.uuid4().hex
        self._started = time.monotonic()
        self.transcript = []
        self.usage = {"requests": 0, "policy_steps": 0, "request_bytes": 0, "response_bytes": 0}
        public = audit_value({k: v for k, v in context.items() if k in PUBLIC_CONTEXT_KEYS})
        try:
            reply = self._request("/reset", {"protocol_version": "1", "episode_id": self._episode_id, "context": public})
            session = reply.get("session_id")
            if (not isinstance(session, str) or not session or len(session) > 256
                    or session in self._seen_sessions or reply.get("episode_id") != self._episode_id):
                raise RemotePolicyError("Remote reset must echo episode_id and issue a fresh session_id")
            self._session = session
            self._seen_sessions.add(session)
            self.transcript.append({"event": "reset", "episode_id": self._episode_id, "context": public,
                                    "session_fingerprint": hashlib.sha256(session.encode()).hexdigest()})
        except Exception as exc:
            self.transcript.append({"event": "reset", "error": {"type": type(exc).__name__}})
            raise

    def act(self, observation: dict) -> dict:
        if self._session is None:
            raise RemotePolicyError("A successful remote reset is required before act")
        if self._pending_result is not None:
            raise RemotePolicyError("Record the previous tool result before requesting another action")
        if self.usage["policy_steps"] >= self.max_steps:
            raise PolicyBudgetExceeded("Remote policy decision budget exhausted")
        if not isinstance(observation, dict):
            raise TypeError("Robot observations must be dictionaries")
        public = {k: v for k, v in observation.items() if k in PUBLIC_OBSERVATION_KEYS}
        tools = copy.deepcopy(observation.get("tools", []))
        sequence = self.usage["policy_steps"]
        self.usage["policy_steps"] += 1
        record = {"step": sequence, "observation": audit_value(public), "tools": audit_value(tools)}
        started = time.monotonic()
        try:
            reply = self._request("/act", {"protocol_version": "1", "session_id": self._session,
                "episode_id": self._episode_id, "sequence": sequence, "observation": public, "tools": tools})
            if reply.get("session_id") != self._session or reply.get("sequence") != sequence:
                raise RemotePolicyError("Remote action session/sequence does not match the pending request")
            action = validate_action(reply.get("action"), tools)
            self._pending_result = sequence
            record["selected_action"] = audit_value(action)
            return action
        except Exception as exc:
            record["error"] = {"type": type(exc).__name__}
            # A timed-out stateful act may have executed remotely. Do not retry
            # or resume this session; require a fresh episode reset.
            self._session = None
            raise
        finally:
            record["inference_seconds"] = time.monotonic() - started
            self.transcript.append(record)

    def record_result(self, result: dict) -> None:
        if self._session is None or self._pending_result is None:
            raise RemotePolicyError("No remote action is waiting for a tool result")
        sequence = self._pending_result
        public = audit_value(result)
        self.transcript[-1]["tool_result"] = public
        try:
            reply = self._request("/result", {"protocol_version": "1", "session_id": self._session,
                "episode_id": self._episode_id, "sequence": sequence, "result": public})
            if reply.get("session_id") != self._session or reply.get("sequence") != sequence or reply.get("accepted") is not True:
                raise RemotePolicyError("Remote result acknowledgement did not match the action")
            self._pending_result = None
        except Exception as exc:
            self.transcript[-1]["result_error"] = {"type": type(exc).__name__}
            self._session = None
            raise

    def close(self) -> None:
        self._closed = True
        self._session = None
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
