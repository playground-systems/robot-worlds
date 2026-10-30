"""Run a bounded native remote demonstration while retaining model keys locally."""
import argparse
import base64
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from robot_worlds.artifacts import public_error, write_json
from robot_worlds.llm import ResponsesClient


def unwire(value):
    if isinstance(value, dict):
        if value.get("encoding") == "base64" and set(value) == {"encoding", "data"}:
            return base64.b64decode(value["data"], validate=True)
        return {k: unwire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwire(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-config", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--remote-root", default="/workspace/robot-worlds")
    parser.add_argument("--output", required=True)
    parser.add_argument("--remote-output", required=True)
    parser.add_argument("--world")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required locally")
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    clients = {
        "generation": ResponsesClient(model="gpt-6-astra", reasoning="medium", max_cost_usd=2,
                                      max_calls=4, max_seconds=900, timeout=240,
                                      max_output_tokens=16384, max_total_output_tokens=48000),
        "policy": ResponsesClient(model="gpt-6-astra", reasoning="medium", max_cost_usd=5,
                                  max_calls=80, max_seconds=1800, timeout=90,
                                  max_output_tokens=2048, max_total_output_tokens=48000),
    }
    spent = {k: 0.0 for k in clients}
    limits = {"generation": 2.0, "policy": 5.0}
    remote = [args.remote_root + "/.venv/bin/python", "-u", "tools/demo/mission_worker.py",
              "--output", args.remote_output, "--seed", str(args.seed)]
    if args.world:
        remote += ["--world", args.world]
    command = "cd " + shlex.quote(args.remote_root) + " && python3 /workspace/robot-worlds-control/idle_guard.py --root /workspace/robot-worlds-control run -- env MUJOCO_GL=egl " + shlex.join(remote)
    process = subprocess.Popen(["ssh", "-F", args.ssh_config, args.host, command],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=root.joinpath("worker.stderr").open("w"), text=True)
    started = time.monotonic()
    try:
        with root.joinpath("events.jsonl").open("w") as events:
            for line in process.stdout:
                if not line.startswith("RW_RPC "):
                    print(line.rstrip(), flush=True)
                    events.write(line)
                    events.flush()
                    continue
                request = json.loads(line.removeprefix("RW_RPC "))
                channel, method = request["channel"], request["method"]
                client = clients[channel]
                try:
                    if method == "reset_budget":
                        spent[channel] += client.usage.get("cost", {}).get("charged_usd") or 0
                        remaining = limits[channel] - spent[channel]
                        if remaining <= 0:
                            from robot_worlds.llm import LLMBudgetExceeded
                            raise LLMBudgetExceeded("Relay aggregate spend limit exhausted")
                        from decimal import Decimal
                        client.max_cost_usd = remaining
                        client._cost_limit = Decimal(str(remaining))
                        result = client.reset_budget()
                    elif method in {"complete_json", "choose_action"}:
                        result = getattr(client, method)(*unwire(request["args"]))
                    else:
                        raise ValueError("Unknown relay method")
                    reply = {"result": result, "usage": client.usage, "budget": client.budget,
                             "last_usage": getattr(client, "last_usage", {}),
                             "response_id": getattr(client, "last_response_id", None)}
                except Exception as error:
                    reply = {"error": public_error(error), "usage": client.usage,
                             "budget": client.budget,
                             "last_usage": getattr(client, "last_usage", {}),
                             "response_id": getattr(client, "last_response_id", None)}
                write_json(root / "model-usage.json", {k: {"previous_spend": spent[k], "usage": v.usage} for k, v in clients.items()})
                process.stdin.write(json.dumps(reply) + "\n")
                process.stdin.flush()
                print(json.dumps({"channel": channel, "method": method, "calls": client.usage.get("calls"),
                                  "charged_usd": client.usage.get("cost", {}).get("charged_usd"), "elapsed_seconds": round(time.monotonic()-started, 1)}), flush=True)
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
