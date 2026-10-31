"""Bounded live prompt → sensors/actions → report → portable reload acceptance.

Run explicitly with an authorized OPENAI_API_KEY. A completed workflow does not
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
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    record = {"workflow_completed": False, "autonomous_manipulation_success": False,
              "model": "gpt-6-astra", "prompt": PROMPT, "events": []}
    client = ResponsesClient(model="gpt-6-astra", reasoning="medium", max_cost_usd=args.max_cost_usd,
                             max_calls=20, max_seconds=600, timeout=90,
                             max_output_tokens=8192, max_total_output_tokens=40000)

    def save():
        record.update(elapsed_seconds=time.monotonic()-started, model_usage=audit_value(client.usage))
        (args.output / "acceptance.json").write_text(json.dumps(record, indent=2)+"\n")

    def event(value):
        kind = value.get("type")
        if kind in {"generation_started", "world_preview", "validation", "world_created", "step", "episode_finished"}:
            record["events"].append({"type": kind, "elapsed_seconds": time.monotonic()-started})
            print(kind, flush=True)
            save()

    try:
        world = generate(PROMPT, client=client, max_repairs=2, output=args.output / "worlds",
                         online_assets=False, on_event=event)
        record.update(world_id=world.id, validation=world.manifest["validation"])
        save()
        if world.manifest["validation"]["status"] != "ready":
            raise RuntimeError("Generated environment did not pass physical validation")
        policy = LLMAgent(client=client, max_steps=12, max_seconds=420)
        report = world.evaluate(policy, max_steps=12, max_sim_seconds=90,
                                output=args.output / "runs", max_replay_frames=12, on_event=event)
        record.update(report_id=report.id if hasattr(report, "id") else report.path.name,
                      summary=report.summary, autonomous_manipulation_success=report.summary["successes"] > 0)
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
                          ("workflow_completed", "autonomous_manipulation_success", "elapsed_seconds", "error")}), flush=True)
    return 0 if record["workflow_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
