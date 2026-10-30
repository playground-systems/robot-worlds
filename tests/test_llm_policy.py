"""Offline contract tests: no model calls and no simulated tool execution."""

import copy
from types import SimpleNamespace

import pytest

from robot_worlds.llm import (
    LLMBudgetExceeded,
    LLMError,
    LLMResponseError,
    ResponsesClient,
    strict_schema,
    validate_action,
)
from robot_worlds.policy import LLMAgent, PolicyBudgetExceeded

TOOLS = [
    {
        "name": "drive",
        "description": "Drive a measured distance in metres.",
        "parameters": {
            "type": "object",
            "properties": {
                "distance": {"type": "number", "minimum": -1, "maximum": 1},
                "turn": {"type": "number", "default": 0, "minimum": -1, "maximum": 1},
            },
            "required": ["distance"],
            "additionalProperties": False,
        },
    }
]


def response(*, text="", calls=None, status="completed", tokens=50):
    return SimpleNamespace(
        id="resp_offline",
        status=status,
        output_text=text,
        output=calls or [],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=tokens,
            total_tokens=100 + tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
        ),
    )


def function_call(name="drive", arguments='{"distance": 0.2, "turn": null}'):
    return SimpleNamespace(
        type="function_call", name=name, arguments=arguments, call_id="call_offline"
    )


class FakeResponses:
    def __init__(self, *responses):
        self.pending = list(responses)
        self.requests = []
        self.count_requests = []
        self.count_value = 100
        self.count_error = None
        self.input_tokens = SimpleNamespace(count=self.count)

    def count(self, **kwargs):
        self.count_requests.append(copy.deepcopy(kwargs))
        if self.count_error:
            raise self.count_error
        return SimpleNamespace(input_tokens=self.count_value)

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        result = self.pending.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def transport(*responses, **limits):
    fake = FakeResponses(*responses)
    return ResponsesClient(client=SimpleNamespace(responses=fake), **limits), fake


def test_generation_uses_responses_strict_schema_restores_defaults_and_accounts_usage():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "mass": {"type": "number", "default": 0.2},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    original = copy.deepcopy(schema)
    client, fake = transport(response(text='{"name":"custom lab","mass":null}'))
    result = client.complete_json("Build the requested world.", "A custom laboratory.", schema)
    assert result == {"name": "custom lab", "mass": 0.2}
    request = fake.requests[0]
    assert request["model"] == "gpt-6-astra"
    assert request["reasoning"] == {"effort": "medium"}
    assert request["store"] is False
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["strict"] is True
    assert set(request["text"]["format"]["schema"]["required"]) == {"name", "mass"}
    assert schema == original
    assert {
        key: client.usage[key] for key in ("calls", "input_tokens", "output_tokens", "total_tokens")
    } == {
        "calls": 1,
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert client.usage["count_requests"] == 1
    assert client.usage["cost"]["charged_usd"] == pytest.approx(0.0035)
    assert client.usage["cost"]["unreconciled_usd"] == 0
    assert request["service_tier"] == "default"


def test_real_world_schema_tuple_and_nested_optional_fields_cross_api_boundary():
    from robot_worlds.models import WorldSpec

    schema = WorldSpec.model_json_schema()
    wire_schema = strict_schema(schema)
    assert "prefixItems" not in str(wire_schema)
    assert wire_schema["properties"]["bounds"]["minItems"] == 3
    assert wire_schema["properties"]["bounds"]["maxItems"] == 3
    # The optional joint contains optional non-null numeric defaults.
    data = {
        "name": "Door lab",
        "description": "A user-requested door lab",
        "task": "Open the door",
        "bounds": [4, 4, 3],
        "objects": [
            {
                "id": "door",
                "label": "door",
                "kind": "box",
                "position": [1, 1, 1],
                "size": [0.8, 0.1, 2],
                "joint": {
                    "type": "hinge",
                    "axis": [0, 0, 1],
                    "range": [0, 1.5],
                    "anchor": [0.6, 1, 1],
                    "initial": None,
                    "damping": None,
                },
            }
        ],
        "rubric": [
            {
                "id": "opened",
                "description": "Door open",
                "predicate": "joint_at",
                "object_id": "door",
                "threshold": 1.0,
            }
        ],
    }
    import json

    client, _ = transport(response(text=json.dumps(data)))
    result = client.complete_json("Build requested world.", "Door lab", schema)
    spec = WorldSpec.model_validate(result)
    assert spec.objects[0].joint.initial == 0
    assert spec.objects[0].joint.damping == 2


def test_tool_choice_includes_image_and_sensor_history_without_fabricated_tool_output():
    client, fake = transport(response(calls=[function_call()]))
    observation = {"time": 1.0, "image": b"sensor-png-bytes", "visible_objects": []}
    history = [
        {
            "selected_action": {"tool": "drive", "arguments": {"distance": 0.1}},
            "tool_result": {"moved_metres": 0.09},
        }
    ]
    assert client.choose_action("Use sensors.", observation, TOOLS, history) == {
        "tool": "drive",
        "arguments": {"distance": 0.2, "turn": 0},
    }
    request = fake.requests[0]
    assert request["tool_choice"] == "required"
    assert request["parallel_tool_calls"] is False
    assert len(request["input"]) == 1 and request["input"][0]["role"] == "user"
    content = request["input"][0]["content"]
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert '"moved_metres": 0.09' in content[0]["text"]
    assert "function_call_output" not in str(request)
    assert "previous_response_id" not in request
    assert request["tools"][0]["strict"] is True
    # Count the same vision/tool payload before reserving the generation call.
    counted = fake.count_requests[0]
    for key in (
        "model",
        "reasoning",
        "instructions",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
    ):
        assert counted[key] == request[key]
    assert not {"max_output_tokens", "store", "service_tier"} & counted.keys()


@pytest.mark.parametrize("calls", [[], [function_call(), function_call()]])
def test_zero_or_multiple_function_calls_are_rejected(calls):
    client, _ = transport(response(calls=calls))
    with pytest.raises(LLMResponseError, match="exactly one"):
        client.choose_action("Use sensors.", {}, TOOLS, [])


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "teleport", "arguments": {}},
        {"tool": "drive", "arguments": {"distance": 5}},
        {"tool": "drive", "arguments": {"distance": True}},
        {"tool": "drive", "arguments": {"distance": float("nan")}},
        {"tool": "drive", "arguments": {"distance": 0.1, "unrelated": True}},
        {"tool": "drive", "arguments": {}},
    ],
)
def test_action_contract_rejects_unsafe_or_incompatible_arguments(action):
    with pytest.raises(ValueError):
        validate_action(action, TOOLS)


def test_call_budget_and_output_budget_prevent_extra_api_requests():
    client, fake = transport(response(calls=[function_call()], tokens=32), max_calls=1)
    client.choose_action("Use sensors.", {}, TOOLS, [])
    with pytest.raises(LLMBudgetExceeded):
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert len(fake.requests) == 1
    client.reset_budget()
    assert client.usage["calls"] == 0

    client, fake = transport(
        response(calls=[function_call()], tokens=32), max_total_output_tokens=32
    )
    client.choose_action("Use sensors.", {}, TOOLS, [])
    assert fake.requests[0]["max_output_tokens"] == 32
    with pytest.raises(LLMBudgetExceeded):
        client.choose_action("Use sensors.", {}, TOOLS, [])


def test_time_and_sensor_size_budgets_stop_before_request():
    client, fake = transport(response(), max_seconds=1)
    client._started -= 2
    with pytest.raises(LLMBudgetExceeded):
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert not fake.requests

    client, fake = transport(response(), max_image_bytes=2)
    with pytest.raises(LLMBudgetExceeded, match="byte-size"):
        client.choose_action("Use sensors.", {"image": b"too large"}, TOOLS, [])
    assert not fake.requests


def test_incomplete_output_is_not_treated_as_a_valid_decision():
    client, _ = transport(response(calls=[function_call()], status="incomplete"))
    with pytest.raises(LLMResponseError, match="incomplete"):
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert client.usage["calls"] == 1


def test_provider_error_does_not_copy_sensitive_error_body():
    client, _ = transport(RuntimeError("Authorization: secret provider response"))
    with pytest.raises(LLMError) as error:
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert "secret provider" not in str(error.value)


def test_spend_budget_rejects_unfunded_input_without_generation():
    client, fake = transport(response(calls=[function_call()]), max_cost_usd=0.001)
    with pytest.raises(LLMBudgetExceeded, match="counted input"):
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert len(fake.count_requests) == 1 and not fake.requests
    assert client.usage["cost"]["charged_usd"] == 0


def test_spend_budget_allocates_output_conservatively_and_reconciles_usage():
    client, fake = transport(response(calls=[function_call()], tokens=10), max_cost_usd=0.00225)
    client.choose_action("Use sensors.", {}, TOOLS, [])
    # All counted input is reserved at the highest possible input/cache-write rate.
    assert fake.requests[0]["max_output_tokens"] == 20
    cost = client.usage["cost"]
    assert cost["last_reservation_usd"] == pytest.approx(0.00225)
    assert cost["estimated_reported_usd"] == pytest.approx(0.0015)
    assert cost["charged_usd"] == pytest.approx(0.0015)
    assert cost["unreconciled_usd"] == 0


def test_counter_failure_fails_closed_and_hides_provider_body():
    client, fake = transport(response(calls=[function_call()]))
    fake.count_error = RuntimeError("secret provider details")
    with pytest.raises(LLMError, match="no generation request") as error:
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert "secret provider" not in str(error.value)
    assert not fake.requests


def test_ambiguous_request_retains_full_reservation_and_reset_clears_spend():
    client, fake = transport(RuntimeError("timeout"), max_cost_usd=0.00225)
    with pytest.raises(LLMError):
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert client.usage["cost"]["charged_usd"] == pytest.approx(0.00225)
    assert client.usage["cost"]["unreconciled_usd"] == pytest.approx(0.00225)
    with pytest.raises(LLMBudgetExceeded, match="spend budget"):
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert len(fake.requests) == 1 and len(fake.count_requests) == 1
    client.reset_budget()
    assert client.usage["cost"]["charged_usd"] == 0
    assert client.usage["cost"]["unreconciled_usd"] == 0
    assert client.usage["calls"] == 0


def test_unknown_model_requires_rates_or_explicit_token_only_mode():
    with pytest.raises(ValueError, match="Unknown model pricing"):
        ResponsesClient(model="other-model")
    client, fake = transport(
        response(calls=[function_call()]), model="other-model", max_cost_usd=None
    )
    client.choose_action("Use sensors.", {}, TOOLS, [])
    assert not fake.count_requests
    assert client.usage["cost"]["mode"] == "token_only"
    assert client.usage["cost"]["charged_usd"] is None
    client, _ = transport(
        response(calls=[function_call()]),
        model="other-model",
        input_price_per_million=2,
        output_price_per_million=8,
    )
    client.choose_action("Use sensors.", {}, TOOLS, [])
    assert client.usage["cost"]["charged_usd"] == pytest.approx(0.0006)
    assert client.budget["pricing"]["source"] == "caller_configured"


def test_astra_large_context_pricing_changes_reservation_for_full_request():
    result = response(calls=[function_call()], tokens=10)
    result.usage.input_tokens = 272001
    result.usage.total_tokens = 272011
    client, fake = transport(result, max_output_tokens=10, max_cost_usd=8)
    fake.count_value = 272001
    client.choose_action("Use sensors.", {}, TOOLS, [])
    assert client.usage["cost"]["last_reservation_usd"] == pytest.approx(6.800775)
    assert client.usage["cost"]["charged_usd"] == pytest.approx(5.44077)


@pytest.mark.parametrize(
    "bad_usage",
    [
        None,
        {"input_tokens": -100, "output_tokens": 5, "total_tokens": -95},
        {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
    ],
)
def test_missing_or_malformed_usage_never_releases_a_spend_reservation(bad_usage):
    result = response(calls=[function_call()], tokens=10)
    result.usage = bad_usage
    client, _ = transport(result, max_output_tokens=20, max_cost_usd=0.00225)
    if bad_usage and bad_usage["input_tokens"] < 0:
        with pytest.raises(LLMResponseError, match="invalid token usage"):
            client.choose_action("Use sensors.", {}, TOOLS, [])
    else:
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert client.usage["cost"]["charged_usd"] == pytest.approx(0.00225)
    assert client.usage["cost"]["unreconciled_usd"] == pytest.approx(0.00225)


def test_provider_usage_cannot_silently_exceed_input_count():
    result = response(calls=[function_call()])
    result.usage.input_tokens = 101
    result.usage.total_tokens = 151
    client, _ = transport(result)
    with pytest.raises(LLMResponseError, match="exceeded"):
        client.choose_action("Use sensors.", {}, TOOLS, [])
    assert client.usage["cost"]["accounting_issues"]


def test_agent_exposes_and_resets_episode_cost_provenance():
    client, _ = transport(response(calls=[function_call()]))
    agent = LLMAgent(client=client)
    agent.act({"tools": TOOLS})
    assert agent.usage["cost"]["charged_usd"] > 0
    assert agent.transcript[0]["budget"]["pricing"]["model"] == "gpt-6-astra"
    agent.reset({"task": "Independent episode"})
    assert agent.usage["cost"]["charged_usd"] == 0


class FakeDecisionClient:
    def __init__(self):
        self.decisions = []

    def choose_action(self, instructions, observation, tools, history):
        self.decisions.append(copy.deepcopy((instructions, observation, tools, history)))
        return {"tool": "drive", "arguments": {"distance": 0.1}}


def test_agent_only_passes_sensor_context_and_resets_all_episode_memory(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-never-written-to-audit")
    fake = FakeDecisionClient()
    agent = LLMAgent(client=fake, max_steps=3)
    agent.reset(
        {
            "task": "Move to the object",
            "rubric": {"threshold": 0.2},
            "objects": {"hidden": {"position": [1, 2, 3]}},
        }
    )
    first = {
        "tools": TOOLS,
        "time": 0,
        "robot": {"position": [0, 0, 0]},
        "image": b"sensor pixels",
        "visible_objects": [],
        "rubric": {"secret": "grader"},
        "snapshot": {"hidden": True},
        "api_key": "sk-test-key-never-written-to-audit",
    }
    assert agent.act(first)["tool"] == "drive"
    _, received, _, history = fake.decisions[0]
    assert history == [] and received["image"] == b"sensor pixels"
    assert received["task"] == "Move to the object"
    assert not {"rubric", "snapshot", "api_key", "objects"} & received.keys()
    assert agent.transcript[0]["observation"]["image"]["bytes"] == len(b"sensor pixels")
    agent.record_result({"moved_metres": 0.08})
    second = {"tools": TOOLS, "time": 1, "last_tool_result": {"moved_metres": 0.08}}
    agent.act(second)
    assert fake.decisions[1][3][0]["tool_result"] == {"moved_metres": 0.08}
    assert "secret" not in str(agent.transcript)
    assert "sk-test" not in str(agent.transcript)
    agent.reset({"task": "New independent task"})
    assert agent.steps == 0 and agent.transcript == [] and agent._history == []
    agent.act({"tools": TOOLS, "time": 0})
    assert fake.decisions[-1][3] == []


def test_policy_step_limit_stops_without_an_additional_model_decision():
    fake = FakeDecisionClient()
    agent = LLMAgent(client=fake, max_steps=1)
    agent.act({"tools": TOOLS})
    with pytest.raises(PolicyBudgetExceeded):
        agent.act({"tools": TOOLS})
    assert len(fake.decisions) == 1


def test_injected_policy_client_cannot_bypass_runtime_tool_contract():
    class InvalidClient:
        def choose_action(self, *args):
            return {"tool": "teleport", "arguments": {}}

    agent = LLMAgent(client=InvalidClient())
    with pytest.raises(ValueError, match="unavailable"):
        agent.act({"tools": TOOLS})
    assert agent.transcript[0]["error"] == {"type": "ValueError"}
