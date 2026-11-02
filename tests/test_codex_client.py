"""The optional CLI crosses only the sensor/decision boundary, never the simulator."""
import json
import subprocess
from pathlib import Path

import pytest
from test_llm_policy import TOOLS

from robot_worlds.codex_client import CodexClient
from robot_worlds.llm import LLMBudgetExceeded, LLMResponseError
from robot_worlds.policy import LLMAgent


@pytest.fixture
def cli(monkeypatch):
    calls = []
    monkeypatch.setattr("shutil.which", lambda _: "/fake/codex")

    class Process:
        returncode = 0
        pid = 123456

        def __init__(self, command, **options):
            self.command, self.options = command, options
            calls.append(self)

        def communicate(self, prompt=None, timeout=None):
            self.prompt = prompt
            root = Path(self.options["cwd"])
            self.image = (root / "sensor-0.png").read_bytes() if (root / "sensor-0.png").exists() else None
            (root / "response.json").write_text(json.dumps({"action": {"tool": "drive", "arguments": {"distance": 0.2, "turn": None}}}))
            events = [{"type": "thread.started", "thread_id": "test"},
                      {"type": "turn.completed", "usage": {"input_tokens": 123, "output_tokens": 25}}]
            if getattr(self, "extra", None):
                events.insert(1, self.extra)
            return "\n".join(json.dumps(e) for e in events), ""

    monkeypatch.setattr("subprocess.Popen", Process)
    return calls, Process


def test_codex_policy_transmits_current_image_and_actions_without_api_key_or_state(cli, monkeypatch):
    calls, _ = cli
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-private")
    monkeypatch.setenv("CODEX_API_KEY", "private")
    client = CodexClient(max_calls=1)
    agent = LLMAgent(client=client)
    agent.reset({"task": "Move the robot", "rubric": {"secret": True}})
    action = agent.act({"image": b"pixels", "tools": TOOLS, "time": 0,
                        "hidden_state": {"answer": 1}, "rubric": {"secret": True}})
    assert action == {"tool": "drive", "arguments": {"distance": 0.2, "turn": 0}}
    process = calls[0]
    assert process.image == b"pixels"
    assert not {"OPENAI_API_KEY", "CODEX_API_KEY"} & process.options["env"].keys()
    assert "hidden_state" not in process.prompt and "secret" not in process.prompt
    assert "--ignore-user-config" in process.command and "--ephemeral" in process.command
    assert "features.shell_tool=false" in process.command and "features.apps=false" in process.command
    assert not Path(process.options["cwd"]).exists()
    assert client.usage["total_tokens"] == 148
    assert client.usage["cost"]["charged_usd"] is None
    assert agent.provenance["client"]["declared_configuration"]["transport"] == "local_codex_exec"
    with pytest.raises(LLMBudgetExceeded):
        client.choose_action("", {}, TOOLS, [])
    assert len(calls) == 1


def test_nonrobot_tools_cannot_supply_an_accepted_action(cli):
    _, process = cli
    process.extra = {"type": "item.completed", "item": {"type": "command_execution"}}
    with pytest.raises(LLMResponseError, match="non-robot tool"):
        CodexClient().choose_action("", {}, TOOLS, [])


def test_timeout_kills_process_group_and_cleans_sensor_files(cli, monkeypatch):
    calls, process = cli
    killed = []
    monkeypatch.setattr("os.killpg", lambda pid, sig: killed.append(pid))

    def communicate(self, prompt=None, timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return "", ""

    monkeypatch.setattr(process, "communicate", communicate)
    with pytest.raises(LLMBudgetExceeded, match="wall-time"):
        CodexClient().choose_action("", {"image": b"pixels"}, TOOLS, [])
    assert killed == [123456]
    assert not Path(calls[0].options["cwd"]).exists()


def test_image_and_input_limits_prevent_cli_launch(cli):
    calls, _ = cli
    with pytest.raises(ValueError, match="two images"):
        CodexClient().choose_action("", {"images": [b"a"]*3}, TOOLS, [])
    with pytest.raises(LLMBudgetExceeded, match="text-size"):
        CodexClient(max_input_chars=1).choose_action("", {}, TOOLS, [])
    assert calls == []


@pytest.mark.parametrize("options", [
    {"max_calls": True}, {"max_calls": 1.5}, {"max_input_chars": -1},
    {"max_total_tokens": float("inf")}, {"max_seconds": float("nan")},
    {"timeout": float("inf")}, {"timeout": False},
])
def test_nonfinite_or_invalid_limits_cannot_disable_budgets(cli, options):
    calls, _ = cli
    with pytest.raises(ValueError):
        CodexClient(**options)
    assert calls == []
