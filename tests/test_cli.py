"""CLI dispatch contracts without model calls, simulator work or subprocesses."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from robot_worlds import batch, policy, remote_policy, sdk
from robot_worlds.cli import app


@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    report = SimpleNamespace(summary={"episodes": 1}, path=tmp_path / "run", open=Mock())
    world = SimpleNamespace(evaluate=Mock(return_value=report))
    loader = Mock(return_value=world)
    agent = Mock()
    constructor = Mock(return_value=agent)
    remote = Mock()
    remote_constructor = Mock(return_value=remote)
    parallel = Mock(return_value=report)
    monkeypatch.setattr(sdk, "load", loader)
    monkeypatch.setattr(policy, "LLMAgent", constructor)
    monkeypatch.setattr(remote_policy, "RemotePolicy", remote_constructor)
    monkeypatch.setattr(batch, "evaluate_batch", parallel)
    monkeypatch.delenv("ROBOT_WORLDS_POLICY_TOKEN", raising=False)
    return SimpleNamespace(runner=CliRunner(), report=report, world=world,
                           loader=loader, agent=agent, constructor=constructor,
                           remote=remote, remote_constructor=remote_constructor,
                           parallel=parallel)


def test_default_serial_llm_and_report_open(dispatch):
    result = dispatch.runner.invoke(app, ["evaluate", "saved-world", "--episodes", "3",
        "--seed", "41", "--max-steps", "7", "--max-sim-seconds", "12.5",
        "--model", "custom-model", "--reasoning", "low", "--max-cost-usd", "0.5",
        "--max-model-seconds", "19", "--output", "results", "--open-report"])
    assert result.exit_code == 0, result.output
    dispatch.parallel.assert_not_called()
    dispatch.constructor.assert_called_once_with(model="custom-model", reasoning="low",
        max_steps=7, max_cost_usd=0.5, max_seconds=19)
    dispatch.loader.assert_called_once_with(Path("saved-world"))
    args, kwargs = dispatch.world.evaluate.call_args
    assert args == (dispatch.agent,)
    assert {key: kwargs[key] for key in ("episodes", "seed", "max_steps", "max_sim_seconds", "output")} == {
        "episodes": 3, "seed": 41, "max_steps": 7, "max_sim_seconds": 12.5,
        "output": Path("results")}
    assert callable(kwargs["on_event"])
    dispatch.report.open.assert_called_once()
    assert str(dispatch.report.path / "report.html") in result.output


@pytest.mark.parametrize("worker_flags,workers,backend", [
    (["--workers", "3"], 3, "auto"),
    (["--graphics-backend", "egl"], 1, "egl"),
])
def test_spawned_llm_preserves_budgets_without_constructing_parent_agent(dispatch, worker_flags, workers, backend):
    result = dispatch.runner.invoke(app, ["evaluate", "saved-world", *worker_flags,
        "--episodes", "5", "--seed", "101", "--max-steps", "9",
        "--max-sim-seconds", "14", "--model", "custom-model", "--reasoning", "high",
        "--max-cost-usd", "0.8", "--max-model-seconds", "23"])
    assert result.exit_code == 0, result.output
    dispatch.constructor.assert_not_called()
    dispatch.remote_constructor.assert_not_called()
    dispatch.world.evaluate.assert_not_called()
    args, kwargs = dispatch.parallel.call_args
    assert args == (dispatch.world,)
    assert kwargs["policy_config"] == {"kind": "llm", "model": "custom-model",
        "reasoning": "high", "max_steps": 9, "max_cost_usd": 0.8, "max_seconds": 23}
    assert kwargs["workers"] == workers
    assert kwargs["graphics_backend"] == backend
    assert kwargs["episodes"] == 5
    assert kwargs["seed"] == 101
    assert kwargs["max_steps"] == 9
    assert kwargs["max_sim_seconds"] == 14
    dispatch.report.open.assert_not_called()


@pytest.mark.parametrize("with_token", [False, True])
def test_parallel_remote_config_uses_environment_name_not_secret(dispatch, monkeypatch, with_token):
    secret = "test-only-secret-never-serialize"
    if with_token:
        monkeypatch.setenv("ROBOT_WORLDS_POLICY_TOKEN", secret)
    result = dispatch.runner.invoke(app, ["evaluate", "saved-world", "--workers", "2",
        "--remote-url", "https://policy.example", "--max-steps", "11",
        "--max-model-seconds", "31"])
    assert result.exit_code == 0, result.output
    config = dispatch.parallel.call_args.kwargs["policy_config"]
    expected = {"kind": "remote", "base_url": "https://policy.example",
                "max_steps": 11, "max_seconds": 31}
    if with_token:
        expected["token_env"] = "ROBOT_WORLDS_POLICY_TOKEN"
    assert config == expected
    assert "token" not in config
    assert secret not in repr(dispatch.parallel.call_args)
    assert secret not in result.output
    dispatch.remote_constructor.assert_not_called()
    dispatch.constructor.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_serial_remote_receives_token_and_closes_even_on_failure(dispatch, monkeypatch, fails):
    monkeypatch.setenv("ROBOT_WORLDS_POLICY_TOKEN", "test-only-token")
    if fails:
        dispatch.world.evaluate.side_effect = RuntimeError("evaluation failed")
    result = dispatch.runner.invoke(app, ["evaluate", "saved-world",
        "--remote-url", "https://policy.example", "--max-model-seconds", "17"])
    assert result.exit_code == (1 if fails else 0), result.output
    dispatch.remote_constructor.assert_called_once_with("https://policy.example",
        token="test-only-token", max_steps=30, max_seconds=17)
    assert dispatch.world.evaluate.call_args.args == (dispatch.remote,)
    dispatch.remote.close.assert_called_once()
    dispatch.parallel.assert_not_called()
    assert "test-only-token" not in result.output


@pytest.mark.parametrize("as_class", [False, True])
def test_serial_python_policy_object_or_class_remains_supported(dispatch, monkeypatch, as_class):
    module = ModuleType("cli_test_policy_module")
    instance = object()
    if as_class:
        class LocalPolicy:
            def __new__(cls):
                return instance
        module.agent = LocalPolicy
    else:
        module.agent = instance
    monkeypatch.setitem(sys.modules, module.__name__, module)
    result = dispatch.runner.invoke(app, ["evaluate", "saved-world",
        "--policy", f"{module.__name__}:agent"])
    assert result.exit_code == 0, result.output
    assert dispatch.world.evaluate.call_args.args == (instance,)
    dispatch.constructor.assert_not_called()
    dispatch.parallel.assert_not_called()


@pytest.mark.parametrize("flags", [
    ["--workers", "0"], ["--workers", "33"], ["--graphics-backend", "cuda"],
    ["--policy", "nonexistent:agent", "--workers", "2"],
    ["--policy", "nonexistent:agent", "--graphics-backend", "egl"],
    ["--policy", "nonexistent:agent", "--remote-url", "https://policy.example"],
    ["--policy", "malformed"],
])
def test_invalid_or_opaque_spawn_requests_fail_before_loading_world(dispatch, flags):
    result = dispatch.runner.invoke(app, ["evaluate", "saved-world", *flags])
    assert result.exit_code == 2, result.output
    dispatch.loader.assert_not_called()
    dispatch.parallel.assert_not_called()
    dispatch.constructor.assert_not_called()
    dispatch.remote_constructor.assert_not_called()


def test_generate_forwards_explicit_large_scene_minimum_and_budgets(dispatch, monkeypatch):
    generator = Mock(return_value=SimpleNamespace(
        path=Path("worlds/generated"), manifest={"validation": {"status": "ready"}}))
    monkeypatch.setattr(sdk, "generate", generator)
    result = dispatch.runner.invoke(app, ["generate", "A large factory",
        "--scene-profile", "large", "--min-objects", "500", "--max-objects", "900",
        "--max-cost-usd", "2", "--max-model-seconds", "240"])
    assert result.exit_code == 0, result.output
    args, kwargs = generator.call_args
    assert args == ("A large factory",)
    assert kwargs["scene_profile"] == "large"
    assert kwargs["min_objects"] == 500
    assert kwargs["max_objects"] == 900
    assert kwargs["client_options"] == {"max_cost_usd": 2, "max_seconds": 240,
                                       "max_output_tokens": 16384}
