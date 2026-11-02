"""Optional local Codex transport using the user's existing supported sign-in.

No token extraction, credential copying, shell commands, or simulator access is
exposed to the decision model. The CLI runs in an empty temporary directory and
returns a schema-constrained answer; the runtime alone executes robot actions.
"""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .llm import (
    LLMBudgetExceeded,
    LLMError,
    LLMResponseError,
    _restore_defaults,
    _validate,
    audit_value,
    normalize_tools,
    strict_schema,
    validate_action,
)


class CodexClient:
    """Explicit opt-in; consumes Codex plan usage, without an API dollar cap.

    Requires a recent `codex exec` with --ignore-user-config and --ephemeral.
    Calls and wall time are bounded. Reported token totals gate subsequent
    requests, not the output of an already executing CLI turn.
    """

    def __init__(self, *, model="gpt-6-astra", reasoning="medium", executable="codex",
                 max_calls=80, max_seconds=1800, timeout=90, max_input_chars=120000,
                 max_total_tokens=3_000_000):
        for name, value in (("max_calls", max_calls), ("max_input_chars", max_input_chars),
                            ("max_total_tokens", max_total_tokens)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name, value in (("max_seconds", max_seconds), ("timeout", timeout)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self.model, self.reasoning = model, reasoning
        self.executable = shutil.which(executable)
        if self.executable is None:
            raise ValueError("Install Codex CLI and sign in before using CodexClient")
        self.max_calls, self.max_seconds, self.timeout = max_calls, max_seconds, timeout
        self.max_input_chars, self.max_total_tokens = max_input_chars, max_total_tokens
        self.reset_budget()

    @property
    def provenance(self):
        return {"transport": "local_codex_exec", "model": self.model,
                "reasoning": self.reasoning, "budget": self.budget,
                "authentication": "existing Codex sign-in; API-key environment removed",
                "isolation": "ephemeral empty working directory; read-only sandbox; shell/apps/web/delegation disabled",
                "limits": "CLI and account configuration outside this process are not attested"}

    @property
    def budget(self):
        return {"max_calls": self.max_calls, "max_seconds": self.max_seconds,
                "timeout": self.timeout, "max_input_chars": self.max_input_chars,
                "max_total_tokens": self.max_total_tokens, "max_cost_usd": None,
                "billing": "Codex plan usage; no monetary cap or API pricing inference",
                "token_limit": "Stops subsequent calls after reported total reaches limit"}

    def reset_budget(self):
        self._started = time.monotonic()
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                      "transport": "codex_cli", "cost": {"mode": "codex_plan", "charged_usd": None}}
        self.last_usage, self.last_response_id = {}, None

    def _request(self, instructions, prompt, schema, images=()):
        remaining = self.max_seconds - (time.monotonic() - self._started)
        if self.usage["calls"] >= self.max_calls or remaining <= 0 or self.usage["total_tokens"] >= self.max_total_tokens:
            raise LLMBudgetExceeded("The Codex decision budget is exhausted")
        if len(instructions) + len(prompt) + len(json.dumps(schema)) > self.max_input_chars:
            raise LLMBudgetExceeded("The Codex input exceeds its text-size limit")
        if len(images) > 2 or any(not isinstance(value, bytes) or len(value) > 4*1024*1024 for value in images):
            raise ValueError("Codex observations accept at most two images of at most 4 MiB each")
        self.last_usage, self.last_response_id = {}, None
        with tempfile.TemporaryDirectory(prefix="robot-worlds-policy-") as folder:
            root = Path(folder)
            schema_file, response_file = root / "schema.json", root / "response.json"
            schema_file.write_text(json.dumps(strict_schema(schema)))
            command = [self.executable, "exec", "--ignore-user-config", "--ephemeral",
                       "--skip-git-repo-check", "--model", self.model, "--sandbox", "read-only",
                       "-c", "features.shell_tool=false", "-c", "features.apps=false",
                       "-c", "features.multi_agent=false", "-c", 'web_search="disabled"',
                       "-c", "model_reasoning_effort=" + json.dumps(self.reasoning),
                       "-c", "developer_instructions=" + json.dumps(
                           "Return the requested JSON from the supplied observations only. "
                           "Do not call any tools, inspect files, access the network or change state. "
                           "Robot action names in the response are data for the external simulator.\n" + instructions),
                       "--output-schema", str(schema_file), "--json",
                       "--output-last-message", str(response_file)]
            for index, value in enumerate(images):
                path = root / f"sensor-{index}.png"
                path.write_bytes(value)
                command += ["--image", str(path)]
            command.append("-")
            environment = {key: value for key, value in os.environ.items()
                           if key not in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"}}
            self.usage["calls"] += 1
            process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            try:
                stdout, _ = process.communicate(prompt, timeout=min(self.timeout, remaining))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise LLMBudgetExceeded("Codex decision exceeded its wall-time limit") from None
            if len(stdout) > 2*1024*1024:
                raise LLMResponseError("Codex event output exceeded the allowed size")
            completed, tool_used = False, False
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    raise LLMResponseError("Codex returned malformed JSON events") from None
                if event.get("type") == "thread.started":
                    self.last_response_id = event.get("thread_id")
                if event.get("type") in {"item.started", "item.completed", "item.updated"}:
                    kind = event.get("item", {}).get("type")
                    if kind not in {"agent_message", "reasoning", "error"}:
                        tool_used = True
                if event.get("type") == "turn.completed":
                    usage = event.get("usage", {})
                    amounts = [usage.get("input_tokens"), usage.get("output_tokens")]
                    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in amounts):
                        raise LLMResponseError("Codex returned invalid usage")
                    self.last_usage = {"input_tokens": amounts[0], "output_tokens": amounts[1],
                                       "total_tokens": sum(amounts)}
                    for key, value in self.last_usage.items():
                        self.usage[key] += value
                    completed = True
            if tool_used:
                raise LLMResponseError("Codex attempted a non-robot tool; its decision was rejected")
            if process.returncode or not completed or not response_file.is_file():
                raise LLMError("Codex did not complete a decision; check its sign-in, model access and plan limits")
            if response_file.stat().st_size > 1024*1024:
                raise LLMResponseError("Codex response exceeded the allowed size")
            try:
                result = _restore_defaults(json.loads(response_file.read_text()), schema, schema)
                _validate(result, schema, schema)
                return result
            except (ValueError, TypeError, KeyError):
                raise LLMResponseError("Codex returned an invalid structured decision") from None

    def complete_json(self, instructions, prompt, schema):
        return self._request(instructions, prompt, schema)

    def choose_action(self, instructions, observation, tools, history):
        variants = []
        for tool in normalize_tools(tools):
            variants.append({"type": "object", "properties": {
                "tool": {"type": "string", "enum": [tool["name"]], "description": tool["description"]},
                "arguments": copy.deepcopy(tool["parameters"])},
                "required": ["tool", "arguments"], "additionalProperties": False})
        schema = {"type": "object", "properties": {"action": {"anyOf": variants}},
                  "required": ["action"], "additionalProperties": False}
        images = observation.get("images", [])
        if isinstance(images, dict):
            images = list(images.values())
        if not isinstance(images, list):
            images = [images]
        if observation.get("image") is not None:
            images = [observation["image"], *images]
        public = {key: value for key, value in observation.items() if key not in {"image", "images"}}
        prompt = json.dumps({"observation": audit_value(public), "history": audit_value(history),
                             "action_contract": tools,
                             "instruction": "Choose exactly one robot action. Past selections are not proof of execution."})
        result = self._request(instructions, prompt, schema, [i for i in images if i is not None])
        return validate_action(result["action"], tools)
