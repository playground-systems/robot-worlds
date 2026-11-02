"""Internal bounded generation/native validation runner; not an onboarding demo.

Generate locally with the caller's in-memory credential. Copy only resulting
public artifacts to a native worker and use ``validate`` there. No policy result
or full semantic correctness is inferred from these checks.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from scenario_contracts import acceptance_errors, grasp_review_warnings, scenarios  # noqa: E402

from robot_worlds.artifacts import public_error, utc_now, write_json  # noqa: E402
from robot_worlds.generator import GenerationError, generate_spec  # noqa: E402
from robot_worlds.llm import LLMError, ResponsesClient, audit_value  # noqa: E402
from robot_worlds.models import WorldSpec  # noqa: E402

PER_SCENARIO_USD = .50


def generate_one(contract, output):
    directory = output / contract["id"]
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / "generation.json"
    if result_path.exists():
        raise ValueError(f"Refusing to repeat a charged scenario: {contract['id']}")
    client = ResponsesClient(model="gpt-6-astra", reasoning="low", max_calls=1,
                             max_output_tokens=6500, max_total_output_tokens=6500,
                             max_cost_usd=PER_SCENARIO_USD, timeout=180, max_seconds=200)
    record = {"id": contract["id"], "started_at": utc_now(), "status": "started",
              "contract": contract, "protocol": {"model": client.model, "reasoning": "low",
              "max_calls": 1, "max_repairs": 0, "min_objects": 30, "max_objects": 80,
              "per_scenario_cost_limit_usd": PER_SCENARIO_USD, "suite_cost_limit_usd": 5.0,
              "native_execution": "remote only; no policy evaluation in this run"}}
    write_json(result_path, record)  # Repeated launches cannot silently spend again.
    original_complete = client.complete_json

    def record_response(*args, **kwargs):
        value = original_complete(*args, **kwargs)
        write_json(directory / "raw-response.json", value)
        return value

    client.complete_json = record_response
    candidate = None
    started = time.monotonic()
    try:
        candidate = generate_spec(contract["prompt"], client=client, max_repairs=0,
                                  min_objects=30, max_objects=80,
                                  on_candidate=lambda value: write_json(directory / "world-spec.json", value.model_dump(mode="json")))
        record["status"] = "static_pass"
        record["static_errors"] = []
    except GenerationError as error:
        candidate = error.candidate
        record.update(status="static_failure" if candidate is not None else "generation_failure",
                      static_errors=error.errors, generation_error_type=type(error).__name__,
                      cause_type=type(error.__cause__).__name__ if error.__cause__ else None,
                      history=error.history)
        if isinstance(error.__cause__, LLMError):
            # Our client strips provider bodies and reports only its own safe
            # failure description, exception class and optional HTTP status.
            record["model_failure_detail"] = str(error.__cause__)
    except Exception as error:
        record.update(status="generation_failure", error=public_error(error))
    finally:
        if candidate is not None:
            write_json(directory / "world-spec.json", candidate.model_dump(mode="json"))
            record["materialized_object_count"] = len(candidate.objects)
            record["independent_contract_errors"] = acceptance_errors(candidate, contract)
        else:
            record["independent_contract_errors"] = ["No typed candidate available"]
        record.update(finished_at=utc_now(), wall_seconds=time.monotonic()-started,
                      usage=audit_value(client.usage), budget=audit_value(client.budget),
                      response_id=client.last_response_id)
        write_json(result_path, record)
    print(json.dumps({k: record.get(k) for k in ("id", "status", "materialized_object_count", "static_errors", "independent_contract_errors", "usage")}), flush=True)


def validate_one(contract, output):
    from robot_worlds.sdk import World
    directory = output / contract["id"]
    spec_path = directory / "world-spec.json"
    result = {"id": contract["id"], "started_at": utc_now(), "policy_evaluated": False,
              "task_solvability": "not_evaluated"}
    started = time.monotonic()
    if not spec_path.exists():
        result.update(status="unvalidated", reason="No typed generated specification")
    else:
        try:
            spec = WorldSpec.model_validate_json(spec_path.read_text())
            result["descriptor_review_warnings"] = grasp_review_warnings(spec, contract)
            world = World.from_spec(spec, directory / "worlds", asset_root=directory)
            result.update(world_id=world.id, world_path=str(world.path), validation=world.manifest["validation"],
                          runtime=world.manifest.get("runtime"), independent_contract_errors=acceptance_errors(world.spec, contract))
            has_physics = any(c["check"] == "physics_reset" for c in world.manifest["validation"]["checks"])
            result["native_physics_executed"] = has_physics
            result["status"] = ("passed_structural_and_reset_checks" if world.manifest["validation"]["status"] == "ready"
                                and not result["independent_contract_errors"] else "unvalidated")
        except Exception as error:
            result.update(status="unvalidated", error=public_error(error))
    result.update(finished_at=utc_now(), wall_seconds=time.monotonic()-started)
    write_json(directory / "native-validation.json", result)
    print(json.dumps({"id": contract["id"], "status": result["status"],
                      "native_physics_executed": result.get("native_physics_executed", False),
                      "world_id": result.get("world_id"), "error": result.get("error")}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["generate", "validate"])
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contracts = {s["id"]: s for s in scenarios()}
    if len(args.ids) != len(set(args.ids)) or not set(args.ids) <= contracts.keys():
        parser.error("Use unique IDs from the frozen ten-scenario contract")
    args.output.mkdir(parents=True, exist_ok=True)
    for sid in args.ids:
        (generate_one if args.phase == "generate" else validate_one)(contracts[sid], args.output)


if __name__ == "__main__":
    main()
