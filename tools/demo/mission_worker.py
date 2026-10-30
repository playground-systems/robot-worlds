"""Remote native-physics worker; model requests are relayed over SSH stdin/stdout.

Internal demonstration harness. No model credential is needed on the worker.
The relay is a declared custom transport; it is not itself a reproducibility proof.
"""
import argparse
import json
import sys
from pathlib import Path

from robot_worlds.artifacts import write_json
from robot_worlds.llm import LLMBudgetExceeded
from robot_worlds.policy import LLMAgent
from robot_worlds.remote_policy import _wire
from robot_worlds.sdk import generate, load


class RelayClient:
    model = "gpt-6-astra"
    reasoning = "medium"
    max_input_chars = 120000

    def __init__(self, channel):
        self.channel = channel
        self.usage = {}
        self.budget = {}
        self.last_usage = {}
        self.last_response_id = None

    def call(self, method, *args):
        print("RW_RPC " + json.dumps(_wire({"channel": self.channel, "method": method, "args": args})), flush=True)
        reply = json.loads(sys.stdin.readline())
        self.usage, self.budget = reply.get("usage", {}), reply.get("budget", {})
        self.last_usage = reply.get("last_usage", {})
        self.last_response_id = reply.get("response_id")
        if "error" in reply:
            error = reply["error"]
            if error["type"] == "LLMBudgetExceeded":
                raise LLMBudgetExceeded(error["message"])
            raise RuntimeError(error["message"])
        return reply.get("result")

    def reset_budget(self):
        self.call("reset_budget")

    def complete_json(self, *args):
        return self.call("complete_json", *args)

    def choose_action(self, *args):
        return self.call("choose_action", *args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompt", default="tools/demo/factory-mission.txt")
    parser.add_argument("--world")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)

    def event(value):
        if value["type"] in {"step", "validation", "world_created", "episode_completed"}:
            print("RW_EVENT " + json.dumps({k: v for k, v in value.items() if k != "spec"}), flush=True)

    if args.world:
        world = load(args.world)
    else:
        world = generate(Path(args.prompt).read_text(), output=root / "worlds",
                         client=RelayClient("generation"), max_repairs=3,
                         min_objects=30, max_objects=120, on_event=event)
    write_json(root / "world-selection.json", {"world_id": world.id, "path": str(world.path), "validation": world.manifest["validation"]})
    print("RW_WORLD " + json.dumps({"path": str(world.path), "status": world.manifest["validation"]["status"]}), flush=True)
    if world.manifest["validation"]["status"] != "ready":
        return 2
    policy = LLMAgent(client=RelayClient("policy"), max_steps=80, max_seconds=1800, history_steps=6)
    report = world.evaluate(policy, output=root / "runs", max_steps=80,
                            max_sim_seconds=240, seed=args.seed, render=True, on_event=event)
    write_json(root / "mission-result.json", {"report_path": str(report.path), "summary": report.summary})
    print("RW_RESULT " + json.dumps({"report_path": str(report.path), "summary": report.summary}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
