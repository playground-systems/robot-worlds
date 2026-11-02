"""Bounded live prompt → sensors/actions → report → portable reload acceptance.

Run explicitly with an authorized OPENAI_API_KEY or existing Codex sign-in. A completed workflow does not
imply the policy succeeded at the physical task; both outcomes are recorded.
"""
import argparse
import json
import time
import zipfile
from pathlib import Path

from robot_worlds import generate, load
from robot_worlds.artifacts import public_error
from robot_worlds.llm import ResponsesClient, audit_value
from robot_worlds.policy import LLMAgent
from robot_worlds.sdk import Report

PROMPT = """Create a compact accessible kitchen workspace for TIAGo++.
Task: put the original white porcelain bowl inside the original microwave and
leave it supported and released. Use the installed google-porcelain-bowl and
nvidia-microwave079 prepared assets, their original geometry and articulated door.
Estimate bowl mass as 0.6 kg. Include stable shallow counters, room for approaching
the bowl and microwave front, and an unobstructed door sweep. Keep the original
door mechanism; the robot must interact physically if opening is needed. No
heating or door closure requirement. The bowl must start outside the appliance.
Require full bowl containment, support on the source tray and release for at
least 0.2 seconds. Do not replace contact with proximity. Use no additional
object assets. The operator task must not expose coordinates or privileged state.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-cost-usd", type=float, default=5)
    parser.add_argument("--transport", choices=("api", "codex"), default="api")
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--asset-library", type=Path)
    parser.add_argument("--scene-profile", choices=("standard", "large"), default="standard")
    parser.add_argument("--generation-input-chars", type=int, default=120000)
    parser.add_argument("--generation-token-limit", type=int, default=150000)
    parser.add_argument("--generation-timeout", type=float, default=180)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    prompt = args.prompt_file.read_text() if args.prompt_file else PROMPT
    record = {"workflow_completed": False, "task_success": False,
              "model": "gpt-6-astra", "transport": args.transport, "prompt": prompt, "events": []}

    def make_client(phase):
        generation = phase == "generation"
        reasoning = "high" if generation else "medium"
        if args.transport == "codex":
            from robot_worlds.codex_client import CodexClient
            return CodexClient(model="gpt-6-astra", reasoning=reasoning, executable=args.codex_executable,
                               max_calls=3 if generation else 12, max_seconds=2*args.generation_timeout+180 if generation else 420,
                               timeout=args.generation_timeout if generation else 180, max_input_chars=args.generation_input_chars if generation else 120000,
                               max_total_tokens=args.generation_token_limit if generation else 450000)
        return ResponsesClient(model="gpt-6-astra", reasoning=reasoning,
                               max_cost_usd=args.max_cost_usd*(.4 if generation else .6),
                               max_calls=3 if generation else 12, max_seconds=300 if generation else 420,
                               timeout=90, max_output_tokens=8192, max_total_output_tokens=40000)

    # LLMAgent resets its client for each episode. Separate clients keep the
    # generation ledger intact and partition the full API spending cap.
    clients = {phase: make_client(phase) for phase in ("generation", "policy")}
    record["budgets"] = {phase: client.budget for phase, client in clients.items()}
    # Keep invalid raw plans for diagnosis as well as validated previews. No
    # credentials, request headers or hidden model reasoning are recorded.
    generation_complete = clients['generation'].complete_json
    plan_number = 0
    def record_plan(*params):
        nonlocal plan_number
        plan = generation_complete(*params)
        plan_number += 1
        (args.output/f'generation-plan-{plan_number}.json').write_text(json.dumps(plan, indent=2)+'\n')
        return plan
    clients['generation'].complete_json = record_plan

    def save():
        record.update(elapsed_seconds=time.monotonic()-started,
                      model_usage={phase: audit_value(client.usage) for phase, client in clients.items()})
        (args.output / "acceptance.json").write_text(json.dumps(record, indent=2)+"\n")

    def event(value):
        kind = value.get("type")
        if kind in {"generation_started", "world_preview", "validation", "world_created", "step", "episode_finished"}:
            record["events"].append({"type": kind, "elapsed_seconds": time.monotonic()-started})
            print(kind, flush=True)
            save()

    try:
        world = generate(prompt, client=clients["generation"], max_repairs=2, output=args.output / "worlds",
                         online_assets=False, on_event=event, asset_library=args.asset_library, scene_profile=args.scene_profile)
        record.update(world_id=world.id, validation=world.manifest["validation"])
        save()
        if world.manifest["validation"]["status"] != "ready":
            raise RuntimeError("Generated environment did not pass physical validation")
        policy = LLMAgent(client=clients["policy"], max_steps=12, max_seconds=420)
        report = world.evaluate(policy, max_steps=12, max_sim_seconds=90,
                                output=args.output / "runs", max_replay_frames=12, on_event=event)
        record.update(report_id=report.id if hasattr(report, "id") else report.path.name,
                      summary=report.summary, task_success=report.summary["successes"] > 0,
                      termination=report.result["episodes"][0]["termination"],
                      executed_steps=len(report.result["episodes"][0]["steps"]))
        archives = [(world.export(args.output / "world.zip"), "restored-world"),
                    (report.export(args.output / "report.zip"), "restored-report")]
        for path, directory in archives:
            # These archives were just produced by this run, not supplied inputs.
            with zipfile.ZipFile(path) as bundle:
                bundle.extractall(args.output / directory)
        restored = load(args.output / "restored-world")
        saved_report = Report(args.output / "restored-report")
        assert restored.id == world.id
        assert saved_report.summary == report.summary
        assert saved_report.regrade(restored, output=args.output / "regraded").summary == report.summary
        record["workflow_completed"] = report.summary["valid"] == 1
        record["portable_reload_and_regrade"] = True
    except Exception as error:
        record["error"] = public_error(error)
        if error.__cause__ is not None:
            record["cause"] = public_error(error.__cause__)
    finally:
        save()
        print(json.dumps({key: record.get(key) for key in
                          ("workflow_completed", "task_success", "elapsed_seconds", "error")}), flush=True)
    return 0 if record["workflow_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
