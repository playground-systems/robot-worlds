"""Run a bounded native remote demonstration while retaining model keys locally."""
import argparse
import base64
import json
import os
import shlex
import subprocess
import tarfile
import tempfile
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
    parser.add_argument("--prompt", default="tools/demo/factory-mission.txt", help="Prompt file on the remote worker")
    parser.add_argument("--min-objects", type=int, default=30)
    parser.add_argument("--max-objects", type=int, default=120)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--backend", choices=["api", "codex"], default="api")
    parser.add_argument("--live-root")
    parser.add_argument("--revalidate", action="store_true")
    args = parser.parse_args()
    if args.backend == "api" and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required locally")
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    mirror = None
    if args.live_root:
        from live_mirror import LiveMirror
        mirror = LiveMirror(args.live_root)
        print("RW_LIVE " + json.dumps({"job_id": mirror.id, "url": "http://127.0.0.1:8766/?job=" + mirror.id}), flush=True)
    clients = {
        "generation": ResponsesClient(model="gpt-6-astra", reasoning="medium", max_cost_usd=2,
                                      max_calls=4, max_seconds=900, timeout=240,
                                      max_output_tokens=16384, max_total_output_tokens=48000),
        "policy": ResponsesClient(model="gpt-6-astra", reasoning="medium", max_cost_usd=5,
                                  max_calls=80, max_seconds=1800, timeout=90,
                                  max_output_tokens=2048, max_total_output_tokens=48000),
    }
    if args.backend == "codex":
        from robot_worlds.codex_client import CodexClient
        clients = {
            "generation": CodexClient(max_calls=4, timeout=300, max_seconds=1200),
            "policy": CodexClient(max_calls=80, timeout=90, max_seconds=1800),
        }
    spent = {k: 0.0 for k in clients}
    limits = {"generation": 2.0, "policy": 5.0}
    remote = [args.remote_root + "/.venv/bin/python", "-u", "tools/demo/mission_worker.py",
              "--output", args.remote_output, "--seed", str(args.seed), "--prompt", args.prompt,
              "--min-objects", str(args.min_objects), "--max-objects", str(args.max_objects)]
    if args.world:
        remote += ["--world", args.world]
    if args.revalidate:
        remote += ["--revalidate"]
    command = "cd " + shlex.quote(args.remote_root) + " && python3 /workspace/robot-worlds-control/idle_guard.py --root /workspace/robot-worlds-control run -- env MUJOCO_GL=egl " + shlex.join(remote)
    process = subprocess.Popen(["ssh", "-F", args.ssh_config, args.host, command],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=root.joinpath("worker.stderr").open("w"), text=True)
    started = time.monotonic()
    try:
        with root.joinpath("events.jsonl").open("w") as events:
            for line in process.stdout:
                if line.startswith("RW_FRAME "):
                    if mirror:
                        mirror.event(json.loads(line.removeprefix("RW_FRAME ")))
                    continue
                if not line.startswith("RW_RPC "):
                    print(line.rstrip(), flush=True)
                    events.write(line)
                    events.flush()
                    if mirror and line.startswith("RW_EVENT "):
                        mirror.event(json.loads(line.removeprefix("RW_EVENT ")))
                    continue
                request = json.loads(line.removeprefix("RW_RPC "))
                channel, method = request["channel"], request["method"]
                client = clients[channel]
                try:
                    if method == "cancelled":
                        result = bool(mirror and mirror.cancelled)
                    elif method == "reset_budget":
                        spent[channel] += client.usage.get("cost", {}).get("charged_usd") or 0
                        remaining = limits[channel] - spent[channel]
                        if remaining <= 0:
                            from robot_worlds.llm import LLMBudgetExceeded
                            raise LLMBudgetExceeded("Relay aggregate spend limit exhausted")
                        if args.backend == "api":
                            from decimal import Decimal
                            client.max_cost_usd = remaining
                            client._cost_limit = Decimal(str(remaining))
                        result = client.reset_budget()
                    elif method in {"complete_json", "choose_action"}:
                        result = getattr(client, method)(*unwire(request["args"]))
                    else:
                        raise ValueError("Unknown relay method")
                    reply = {"result": result, "usage": client.usage, "budget": client.budget,
                             "client_provenance": getattr(client, "provenance", {}),
                             "last_usage": getattr(client, "last_usage", {}),
                             "response_id": getattr(client, "last_response_id", None)}
                except Exception as error:
                    reply = {"error": public_error(error), "usage": client.usage,
                             "client_provenance": getattr(client, "provenance", {}),
                             "budget": client.budget,
                             "last_usage": getattr(client, "last_usage", {}),
                             "response_id": getattr(client, "last_response_id", None)}
                write_json(root / "model-usage.json", {k: {"previous_spend": spent[k], "usage": v.usage} for k, v in clients.items()})
                process.stdin.write(json.dumps(reply) + "\n")
                process.stdin.flush()
                if method != "cancelled":
                    print(json.dumps({"channel": channel, "method": method, "calls": client.usage.get("calls"),
                                      "charged_usd": client.usage.get("cost", {}).get("charged_usd"), "elapsed_seconds": round(time.monotonic()-started, 1)}), flush=True)
        code = process.wait()
        if mirror and mirror.job.get("run_id"):
            # Import the complete, original evidence only after the worker exits.
            with tempfile.TemporaryFile() as archive:
                transfer = "tar -C " + shlex.quote(args.remote_output) + " -czf - runs"
                subprocess.run(["ssh", "-F", args.ssh_config, args.host, transfer], stdout=archive, check=True, timeout=120)
                archive.seek(0)
                with tarfile.open(fileobj=archive, mode="r:gz") as bundle:
                    allowed = "runs/" + mirror.job["run_id"]
                    if any(not (entry.name == "runs" or entry.name == allowed or entry.name.startswith(allowed + "/")) or entry.issym() or entry.islnk() for entry in bundle.getmembers()):
                        raise ValueError("Remote run archive contains unexpected members")
                    bundle.extractall(mirror.root, filter="data")
            mirror.finish("completed" if code == 0 else "failed")
        elif mirror:
            mirror.finish("failed", {"message": "No executable run was created; inspect generation/validation results."})
        return code
    except Exception as error:
        if mirror:
            mirror.finish("failed", public_error(error))
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
