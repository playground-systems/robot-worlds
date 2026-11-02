"""Bounded Responses API requests for generation and robot tool selection.

Robot decisions are independent requests. Prior decisions and real tool results
are supplied as sensor-history data, not fabricated Responses tool-output items.
This keeps episode reset and bounded history explicit.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import time
from decimal import ROUND_FLOOR, Decimal
from typing import Any


class LLMError(RuntimeError):
    """An API request or its model output could not be used."""


class LLMBudgetExceeded(LLMError):
    """No further request is permitted by the configured budget."""


class LLMResponseError(LLMError):
    """The response did not contain the required structured result."""


_PRIVATE_KEYS = {
    "api_key",
    "openai_api_key",
    "authorization",
    "access_token",
    "secret",
    "rubric",
    "criteria",
    "grader",
    "grading",
    "evaluator",
    "evaluation",
    "scores",
    "score",
    "privileged",
    "ground_truth",
    "world_spec",
    "snapshot",
    "trace",
}


def audit_value(value: Any) -> Any:
    """Make bounded audit data JSON-compatible without image or credential bytes."""
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {
            str(k): audit_value(v) for k, v in value.items() if str(k).lower() not in _PRIVATE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [audit_value(v) for v in value]
    if isinstance(value, str):
        secret = os.environ.get("OPENAI_API_KEY")
        if secret:
            value = value.replace(secret, "[REDACTED]")
        if value.startswith("data:image/"):
            return {
                "image_data_url_chars": len(value),
                "sha256": hashlib.sha256(value.encode()).hexdigest(),
            }
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "tolist"):
        return audit_value(value.tolist())
    raise TypeError(f"Unsupported observation value: {type(value).__name__}")


def _field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _resolve(schema: dict, root: dict) -> dict:
    if "$ref" not in schema:
        return schema
    ref = schema["$ref"]
    if not ref.startswith("#/"):
        raise ValueError("Only local JSON schema references are supported")
    node = root
    for key in ref[2:].split("/"):
        node = node[key.replace("~1", "/").replace("~0", "~")]
    return node


def strict_schema(schema: dict) -> dict:
    """Adapt optional object fields to the Responses strict schema contract."""
    result = copy.deepcopy(schema)

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        # Pydantic fixed tuples use prefixItems, which is outside the strict
        # API subset. Preserve length bounds; validate individual positions
        # against the original schema again after receiving the result.
        if "prefixItems" in node:
            positional = node.pop("prefixItems")
            if positional:
                node["items"] = (
                    positional[0]
                    if all(p == positional[0] for p in positional)
                    else {"anyOf": positional}
                )
            node.pop("additionalItems", None)
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties", {})
            required = set(node.get("required", []))
            for name, child in list(properties.items()):
                visit(child)
                if name not in required and not _allows_null(child):
                    properties[name] = {"anyOf": [child, {"type": "null"}]}
            node["required"] = list(properties)
            node["additionalProperties"] = False
        for key, child in node.items():
            if key == "properties":
                continue
            if isinstance(child, dict):
                if key == "$defs":
                    for definition in child.values():
                        visit(definition)
                else:
                    visit(child)
            elif isinstance(child, list):
                for item in child:
                    visit(item)

    visit(result)
    return result


def _allows_null(schema: dict) -> bool:
    return (
        schema.get("type") == "null"
        or "null" in schema.get("type", [])
        or any(_allows_null(branch) for branch in schema.get("anyOf", []))
    )


def _restore_defaults(value: Any, schema: dict, root: dict) -> Any:
    schema = _resolve(schema, root)
    alternatives = schema.get("anyOf", schema.get("oneOf", []))
    if alternatives:
        for branch in alternatives:
            candidate = _restore_defaults(value, branch, root)
            try:
                _validate(candidate, branch, root)
                return candidate
            except ValueError:
                continue
        return value
    if isinstance(value, dict):
        value = dict(value)
        required = schema.get("required", [])
        for name, child in schema.get("properties", {}).items():
            if name in value:
                if value[name] is None and name not in required and not _allows_null(child):
                    if "default" in child:
                        value[name] = copy.deepcopy(child["default"])
                    else:
                        value.pop(name)
                        continue
                value[name] = _restore_defaults(value[name], child, root)
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        value = [_restore_defaults(item, schema["items"], root) for item in value]
    return value


def _validate(value: Any, schema: dict, root: dict, path: str = "result") -> None:
    """Check the JSON-schema vocabulary used by generated specs and robot tools.

    The owning Pydantic model still validates generation. This local check also
    rejects invalid fake/custom-client tool arguments before physical execution.
    """
    schema = _resolve(schema, root)
    for union in ("anyOf", "oneOf"):
        if union in schema:
            matches = 0
            for branch in schema[union]:
                try:
                    _validate(value, branch, root, path)
                    matches += 1
                except ValueError:
                    pass
            if not matches or (union == "oneOf" and matches != 1):
                raise ValueError(f"{path} does not match {union}")
            return
    types = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    declared = schema.get("type")
    if declared is not None:
        options = declared if isinstance(declared, list) else [declared]
        if not any(t in types and types[t](value) for t in options):
            raise ValueError(f"{path} must be {declared}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is outside its enum")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} does not match its constant")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise ValueError(f"{path} is missing fields: {', '.join(sorted(missing))}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - properties.keys():
            raise ValueError(f"{path} contains unknown fields")
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], root, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ValueError(f"{path} has an invalid length")
        for index, item in enumerate(value):
            positional = schema.get("prefixItems", [])
            if index < len(positional):
                _validate(item, positional[index], root, f"{path}[{index}]")
            elif isinstance(schema.get("items"), dict):
                _validate(item, schema["items"], root, f"{path}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError(f"{path} is outside its numeric bounds")
        if value <= schema.get("exclusiveMinimum", -math.inf) or value >= schema.get(
            "exclusiveMaximum", math.inf
        ):
            raise ValueError(f"{path} is outside its exclusive numeric bounds")
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get(
            "maxLength", math.inf
        ):
            raise ValueError(f"{path} has an invalid string length")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path} does not match its pattern")


def normalize_tools(tools: list[dict]) -> list[dict]:
    normalized = []
    names = set()
    for descriptor in tools:
        tool = descriptor.get("function", descriptor)
        name = tool.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError("Every robot tool must have a valid function name")
        if name in names:
            raise ValueError(f"Duplicate robot tool: {name}")
        names.add(name)
        parameters = tool.get("parameters", {"type": "object", "properties": {}})
        if parameters.get("type") != "object":
            raise ValueError(f"Robot tool {name} needs an object parameter schema")
        normalized.append(
            {
                "type": "function",
                "name": name,
                "description": tool.get("description", ""),
                "parameters": parameters,
            }
        )
    if not normalized:
        raise ValueError("No robot tools are available in this observation")
    return normalized


def validate_action(action: dict, tools: list[dict]) -> dict:
    if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
        raise ValueError("An action must contain exactly tool and arguments")
    available = {tool["name"]: tool for tool in normalize_tools(tools)}
    if action["tool"] not in available:
        raise ValueError("The model selected an unavailable robot tool")
    if not isinstance(action["arguments"], dict):
        raise ValueError("Robot tool arguments must be an object")
    schema = available[action["tool"]]["parameters"]
    arguments = _restore_defaults(action["arguments"], schema, schema)
    _validate(arguments, schema, schema, "arguments")
    return {"tool": action["tool"], "arguments": arguments}


class ResponsesClient:
    """Responses transport with a spend cap against an explicit pricing profile.

    Monetary reservations use the exact input-token counting endpoint (including
    images, tools and schemas), then the maximum output allocation. No generation
    is sent if counting is unavailable. Dollar figures price reported token
    usage; they are not invoice data or a guarantee against provider rate changes.
    """

    def __init__(
        self,
        model: str = "gpt-6-astra",
        reasoning: str = "medium",
        *,
        client: Any = None,
        timeout: float = 60.0,
        max_output_tokens: int = 8192,
        max_calls: int = 32,
        max_total_output_tokens: int = 65536,
        max_seconds: float = 600.0,
        max_input_chars: int = 120000,
        max_images: int = 2,
        max_image_bytes: int = 4 * 1024 * 1024,
        max_cost_usd: float | None = 5.0,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
        cached_input_price_per_million: float | None = None,
        cache_write_price_per_million: float | None = None,
    ) -> None:
        if not model or not reasoning:
            raise ValueError("Model and reasoning effort must be nonempty")
        if any(
            v <= 0
            for v in (
                timeout,
                max_output_tokens,
                max_calls,
                max_total_output_tokens,
                max_seconds,
                max_input_chars,
                max_images,
                max_image_bytes,
            )
        ):
            raise ValueError("API budgets and limits must be positive")
        self.model, self.reasoning = model, reasoning
        self.timeout, self.max_output_tokens = timeout, max_output_tokens
        self.max_calls, self.max_total_output_tokens = max_calls, max_total_output_tokens
        self.max_seconds, self.max_input_chars = max_seconds, max_input_chars
        self.max_images, self.max_image_bytes = max_images, max_image_bytes
        if max_cost_usd is not None and (not math.isfinite(max_cost_usd) or max_cost_usd <= 0):
            raise ValueError(
                "max_cost_usd must be positive and finite, or None for token-only limits"
            )
        supplied = any(
            v is not None
            for v in (
                input_price_per_million,
                output_price_per_million,
                cached_input_price_per_million,
                cache_write_price_per_million,
            )
        )
        base_supplied = input_price_per_million is not None or output_price_per_million is not None
        if base_supplied and (input_price_per_million is None or output_price_per_million is None):
            raise ValueError("Supply both input and output token prices")
        if not base_supplied and model == "gpt-6-astra":
            input_price_per_million, output_price_per_million = 10.0, 50.0
            cached_input_price_per_million = (
                1.0 if cached_input_price_per_million is None else cached_input_price_per_million
            )
            cache_write_price_per_million = (
                12.5 if cache_write_price_per_million is None else cache_write_price_per_million
            )
        if input_price_per_million is None and max_cost_usd is not None:
            raise ValueError(
                "Unknown model pricing: supply input/output prices, or explicitly set max_cost_usd=None for token-only limits"
            )
        self.max_cost_usd = max_cost_usd
        self._cost_limit = Decimal(str(max_cost_usd)) if max_cost_usd is not None else None
        self.pricing = None
        if input_price_per_million is not None:
            rates = {
                "input_per_million": input_price_per_million,
                "output_per_million": output_price_per_million,
                "cached_input_per_million": cached_input_price_per_million
                if cached_input_price_per_million is not None
                else input_price_per_million,
                "cache_write_per_million": cache_write_price_per_million
                if cache_write_price_per_million is not None
                else input_price_per_million,
            }
            if any(not math.isfinite(v) or v < 0 for v in rates.values()):
                raise ValueError("Configured token prices must be finite and nonnegative")
            self.pricing = {
                **rates,
                "currency": "USD",
                "model": model,
                "source": "caller_configured"
                if supplied
                else "https://developers.openai.com/api/docs/models/gpt-6-astra",
                "verified_at": None if supplied else "2026-10-30",
                "large_context_threshold": 272000 if model == "gpt-6-astra" else None,
                "large_context_input_multiplier": 2 if model == "gpt-6-astra" else 1,
                "large_context_output_multiplier": 1.5 if model == "gpt-6-astra" else 1,
            }
        self._client = client
        self.reset_budget()

    def reset_budget(self) -> None:
        self.usage = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "count_requests": 0,
        }
        self.last_usage: dict = {}
        self.last_response_id: str | None = None
        self._charged_output_tokens = 0
        self._cost_charged = Decimal(0)
        self._cost_reported = Decimal(0)
        self._cost_unreconciled = Decimal(0)
        self._last_reservation = Decimal(0)
        self._last_count: int | None = None
        self._accounting_issues: list[str] = []
        self._started = time.monotonic()
        self._sync_cost()

    @property
    def budget(self) -> dict:
        return {
            **copy.deepcopy(self.usage["cost"]),
            "max_calls": self.max_calls,
            "max_output_tokens": self.max_output_tokens,
            "max_total_output_tokens": self.max_total_output_tokens,
            "max_seconds": self.max_seconds,
        }

    def _sync_cost(self) -> None:
        self.usage["cost"] = {
            "mode": "usd_budget" if self._cost_limit is not None else "token_only",
            "limit_usd": self.max_cost_usd,
            "estimated_reported_usd": float(self._cost_reported) if self.pricing else None,
            "charged_usd": float(self._cost_charged) if self.pricing else None,
            "unreconciled_usd": float(self._cost_unreconciled) if self.pricing else None,
            "last_reservation_usd": float(self._last_reservation),
            "last_counted_input_tokens": self._last_count,
            "pricing": copy.deepcopy(self.pricing),
            "accounting_issues": list(self._accounting_issues),
            "method": (
                "exact_input_count_plus_max_output; ambiguous failures retain reservation"
                if self._cost_limit is not None
                else "reported_usage_only; no monetary cap"
            ),
        }

    def _rates(self, input_tokens: int) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        p = self.pricing
        if p is None:
            return (Decimal(0),) * 4
        large = (
            p["large_context_threshold"] is not None and input_tokens > p["large_context_threshold"]
        )
        input_multiplier = Decimal(str(p["large_context_input_multiplier"] if large else 1))
        output_multiplier = Decimal(str(p["large_context_output_multiplier"] if large else 1))
        return tuple(
            Decimal(str(p[k])) * input_multiplier / 1000000
            for k in ("input_per_million", "cached_input_per_million", "cache_write_per_million")
        ) + (Decimal(str(p["output_per_million"])) * output_multiplier / 1000000,)

    def _reserve(self, transport: Any, request: dict, output_limit: int) -> tuple[int, Decimal]:
        if self._cost_limit is None:
            return output_limit, Decimal(0)
        if self._cost_charged >= self._cost_limit:
            raise LLMBudgetExceeded("The model-spend budget is exhausted")
        if self.usage["count_requests"] >= self.max_calls:
            raise LLMBudgetExceeded("The input-token counting request budget is exhausted")
        self.usage["count_requests"] += 1
        try:
            counter = transport.responses.input_tokens.count
            count = counter(
                **request,
                timeout=min(self.timeout, self.max_seconds - (time.monotonic() - self._started)),
            )
            count = _field(count, "input_tokens")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("Invalid token count")
        except Exception:
            raise LLMError(
                "Exact input-token counting is unavailable; no generation request was sent under the monetary budget"
            ) from None
        self._last_count = count
        input_rate, cached_rate, write_rate, output_rate = self._rates(count)
        input_reservation = count * max(input_rate, cached_rate, write_rate)
        available = self._cost_limit - self._cost_charged - input_reservation
        if available < 0:
            self._sync_cost()
            raise LLMBudgetExceeded("The counted input exceeds the remaining model-spend budget")
        if output_rate:
            output_limit = min(
                output_limit, int((available / output_rate).to_integral_value(rounding=ROUND_FLOOR))
            )
        if output_limit < 1:
            self._sync_cost()
            raise LLMBudgetExceeded("The model-spend budget cannot fund an output token")
        reservation = input_reservation + output_limit * output_rate
        self._last_reservation = reservation
        self._cost_charged += reservation
        self._sync_cost()
        return output_limit, reservation

    def _reconcile(self, usage: Any, reservation: Decimal) -> None:
        if not self.pricing:
            return
        if not usage:
            self._cost_unreconciled += reservation
            self._sync_cost()
            return
        input_tokens = self.last_usage["input_tokens"]
        input_rate, cached_rate, write_rate, output_rate = self._rates(
            max(input_tokens, self._last_count or 0)
        )
        details = _field(usage, "input_tokens_details")
        cached = _field(details, "cached_tokens", 0) or 0
        written = _field(details, "cache_write_tokens", 0) or 0
        # Missing/malformed detail must not create an optimistic accounting credit.
        if (
            details is None
            or any(
                not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (cached, written)
            )
            or cached + written > input_tokens
        ):
            self._cost_unreconciled += reservation
            self._accounting_issues.append("Missing or invalid cache usage; reservation retained")
            self._sync_cost()
            return
        actual = (
            (input_tokens - cached - written) * input_rate
            + cached * cached_rate
            + written * write_rate
            + self.last_usage["output_tokens"] * output_rate
        )
        self._cost_reported += actual
        self._cost_charged += actual - reservation
        self._sync_cost()

    def _request(self, *, instructions: str, content: list[dict], **options: Any) -> Any:
        self.last_usage = {}
        self.last_response_id = None
        remaining_seconds = self.max_seconds - (time.monotonic() - self._started)
        remaining_tokens = self.max_total_output_tokens - self._charged_output_tokens
        if self.usage["calls"] >= self.max_calls or remaining_tokens <= 0 or remaining_seconds <= 0:
            raise LLMBudgetExceeded("The model request budget is exhausted")
        text_size = len(instructions) + sum(len(p.get("text", "")) for p in content)
        text_size += len(json.dumps(options, allow_nan=False))
        if text_size > self.max_input_chars:
            raise LLMBudgetExceeded("The model input exceeds the configured text-size limit")
        if self._client is None:
            from openai import OpenAI

            if not os.environ.get("OPENAI_API_KEY"):
                raise LLMError("Set OPENAI_API_KEY to use the generation or robot agent model")
            self._client = OpenAI(timeout=self.timeout, max_retries=0)
        output_limit = min(self.max_output_tokens, remaining_tokens)
        transport = self._client
        if hasattr(transport, "with_options"):
            transport = transport.with_options(max_retries=0)
        request = {
            "model": self.model,
            "reasoning": {"effort": self.reasoning},
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            **options,
        }
        output_limit, reservation = self._reserve(transport, request, output_limit)
        remaining_seconds = self.max_seconds - (time.monotonic() - self._started)
        if remaining_seconds <= 0:
            self._cost_charged -= reservation
            self._sync_cost()
            raise LLMBudgetExceeded(
                "The model request time budget is exhausted after token counting"
            )
        self.usage["calls"] += 1
        try:
            response = transport.responses.create(
                **request,
                max_output_tokens=output_limit,
                store=False,
                service_tier="default",
                timeout=min(self.timeout, remaining_seconds),
            )
        except Exception as error:
            # Account conservatively when a failed/ambiguous request has no usage.
            self._charged_output_tokens += output_limit
            self._cost_unreconciled += reservation
            self._sync_cost()
            status = getattr(error, "status_code", None)
            detail = f", HTTP {status}" if isinstance(status, int) else ""
            # Keep useful recovery guidance without copying provider messages,
            # request headers or arbitrary response fields into public artifacts.
            hints = {
                "credit_balance_exhausted": "Restore credits or configure a funded API account.",
                "insufficient_quota": "Restore credits or increase the API account quota.",
                "invalid_api_key": "Check the configured API key and restart the server.",
                "rate_limit_exceeded": "Retry after the provider rate-limit window.",
                "model_not_found": "Check the model identifier and account access.",
            }
            code = getattr(error, "code", None)
            body = getattr(error, "body", None)
            if not isinstance(code, str) and isinstance(body, dict):
                nested = body.get("error", body)
                code = nested.get("code") if isinstance(nested, dict) else None
            hint = f" [{code}] {hints[code]}" if isinstance(code, str) and code in hints else ""
            raise LLMError(
                f"OpenAI Responses request failed ({type(error).__name__}{detail}){hint}"
            ) from None
        usage = _field(response, "usage", {}) or {}
        amounts = {
            key: _field(usage, key) for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        if usage and (
            any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in amounts.values())
            or amounts["total_tokens"] != amounts["input_tokens"] + amounts["output_tokens"]
        ):
            self._charged_output_tokens += output_limit
            self._cost_unreconciled += reservation
            self._accounting_issues.append("Invalid response token usage; reservation retained")
            self._sync_cost()
            raise LLMResponseError(
                "The model returned invalid token usage; budget reservation retained"
            )
        self.last_usage = amounts if usage else {}
        for key, amount in self.last_usage.items():
            self.usage[key] += amount
        self._charged_output_tokens += self.last_usage["output_tokens"] if usage else output_limit
        self._reconcile(usage, reservation)
        self.last_response_id = _field(response, "id")
        if usage and (
            self.last_usage["output_tokens"] > output_limit
            or (self._last_count is not None and self.last_usage["input_tokens"] > self._last_count)
        ):
            self._accounting_issues.append(
                "Provider usage exceeded counted input or output allocation"
            )
            self._sync_cost()
            raise LLMResponseError("Provider usage exceeded its counted input or output allocation")
        if _field(response, "status", "completed") != "completed":
            raise LLMResponseError(
                "The model response was incomplete; increase its output budget or retry explicitly"
            )
        for item in _field(response, "output", []):
            for part in _field(item, "content", []):
                if _field(part, "type") == "refusal":
                    raise LLMResponseError("The model refused the request")
        return response

    def complete_json(self, instructions: str, prompt: str, schema: dict) -> dict:
        response = self._request(
            instructions=instructions,
            content=[{"type": "input_text", "text": prompt}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "world_spec",
                    "schema": strict_schema(schema),
                    "strict": True,
                }
            },
        )
        output = _field(response, "output_text", "")
        if not output:
            output = "".join(
                _field(part, "text", "")
                for item in _field(response, "output", [])
                for part in _field(item, "content", [])
                if _field(part, "type") == "output_text"
            )
        try:
            result = _restore_defaults(json.loads(output), schema, schema)
            _validate(result, schema, schema)
            if not isinstance(result, dict):
                raise ValueError("Generated world must be an object")
            return result
        except (ValueError, TypeError, KeyError) as error:
            raise LLMResponseError(f"Invalid structured generation response: {error}") from error

    def _image_parts(self, observation: dict) -> list[dict]:
        images = observation.get("images", [])
        if isinstance(images, dict):
            images = list(images.values())
        elif not isinstance(images, list):
            images = [images]
        if observation.get("image") is not None:
            images = [observation["image"], *images]
        images = [image for image in images if image is not None]
        if len(images) > self.max_images:
            raise LLMBudgetExceeded("Observation exceeds the configured image-count limit")
        parts = []
        for image in images:
            if isinstance(image, bytes):
                if len(image) > self.max_image_bytes:
                    raise LLMBudgetExceeded("Sensor image exceeds the configured byte-size limit")
                mime = "image/jpeg" if image.startswith(b"\xff\xd8") else "image/png"
                image = f"data:{mime};base64,{base64.b64encode(image).decode()}"
            if not isinstance(image, str) or not image.startswith("data:image/"):
                raise ValueError("Sensor images must be image bytes or data:image URLs")
            if len(image) > self.max_image_bytes * 4 // 3 + 128:
                raise LLMBudgetExceeded("Sensor image exceeds the configured byte-size limit")
            parts.append({"type": "input_image", "image_url": image, "detail": "auto"})
        return parts

    def choose_action(
        self,
        instructions: str,
        observation: dict,
        tools: list[dict],
        history: list[dict],
    ) -> dict:
        available = normalize_tools(tools)
        request_tools = [
            dict(tool, parameters=strict_schema(tool["parameters"]), strict=True)
            for tool in available
        ]
        sensor_data = {
            k: v for k, v in observation.items() if k not in {"image", "images", "tools"}
        }
        payload = {
            "observation": audit_value(sensor_data),
            "history": audit_value(history),
            "history_note": "Prior selections are decisions, not proof of execution. Only recorded tool results describe execution.",
        }
        content = [{"type": "input_text", "text": json.dumps(payload, allow_nan=False)}]
        content.extend(self._image_parts(observation))
        response = self._request(
            instructions=instructions,
            content=content,
            tools=request_tools,
            tool_choice="required",
            parallel_tool_calls=False,
        )
        calls = [
            item
            for item in _field(response, "output", [])
            if _field(item, "type") == "function_call"
        ]
        if len(calls) != 1:
            raise LLMResponseError("A robot decision must contain exactly one function call")
        try:
            arguments = json.loads(_field(calls[0], "arguments", ""))
            return validate_action(
                {"tool": _field(calls[0], "name"), "arguments": arguments}, available
            )
        except (TypeError, ValueError, KeyError) as error:
            raise LLMResponseError(f"Invalid robot action: {error}") from error
