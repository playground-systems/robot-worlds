"""Spawn-isolated repeated evaluation for declarative built-in policy configs.

Each process owns one policy episode, simulator, GL context and result bundle.
This is CPU MuJoCo parallelism; selecting EGL changes rendering, not physics.
"""
from __future__ import annotations

import copy
import inspect
import math
import multiprocessing
import os
import platform
import shutil
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Callable

from .artifacts import canonical, package_versions, public_error, read_json, utc_now, write_json
from .sdk import Report, World, WorldNotReady, _summarize, load

_CANCELLED = None


def _initialize_worker(cancel_event):
    global _CANCELLED
    _CANCELLED = cancel_event


def _policy_config(config):
    config = config if config is not None else {"kind": "llm"}
    if not isinstance(config, dict):
        raise ValueError("policy_config must be a JSON object")
    canonical(config)
    config = copy.deepcopy(config)
    canonical(config)  # no callables, opaque clients, credentials or pickle hooks
    kind = config.get("kind", "llm")
    if kind not in {"llm", "remote"}:
        raise ValueError("Parallel policy kind must be llm or remote")
    if kind == "llm":
        from .policy import LLMAgent
        allowed = set(inspect.signature(LLMAgent).parameters) - {"client"}
    else:
        from .remote_policy import RemotePolicy
        allowed = (set(inspect.signature(RemotePolicy).parameters) - {"token", "transport"}) | {"token_env"}
    unknown = set(config) - allowed - {"kind"}
    if unknown:
        raise ValueError("Unsupported parallel policy options: " + ", ".join(sorted(unknown)))
    config["kind"] = kind
    return config


def _build_policy(config, max_steps):
    options = dict(config)
    kind = options.pop("kind")
    options.setdefault("max_steps", max_steps)
    if kind == "llm":
        from .policy import LLMAgent
        return LLMAgent(**options)
    from .remote_policy import RemotePolicy
    token_env = options.pop("token_env", None)
    if token_env is not None:
        if not isinstance(token_env, str) or not token_env:
            raise ValueError("token_env must name an environment variable")
        if token_env not in os.environ:
            raise ValueError("Remote policy credential environment variable is unavailable")
        options["token"] = os.environ[token_env]
    return RemotePolicy(**options)


def _run_episode(payload):
    if payload["graphics_backend"] == "egl":
        os.environ["MUJOCO_GL"] = "egl"
    world = load(payload["world_path"])
    report = world.evaluate(
        policy_factory=lambda: _build_policy(payload["policy"], payload["max_steps"]),
        episodes=1, seed=payload["seed"], output=payload["output"],
        max_steps=payload["max_steps"], max_sim_seconds=payload["max_sim_seconds"],
        render=payload["render"], replay_fps=payload["replay_fps"],
        max_replay_frames=payload["max_replay_frames"],
        cancelled=_CANCELLED.is_set if _CANCELLED is not None else None,
    )
    return {"index": payload["index"], "path": str(report.path)}


def _copy_episode(source, destination, index, seed):
    child_result = read_json(source / "result.json")
    if not child_result.get("episodes"):
        return {"index": index, "seed": seed, "status": "cancelled", "steps": [], "frames": [], "worker_run_id": child_result["id"]}
    episode = copy.deepcopy(child_result["episodes"][0])
    source_dir = source / "episode-000"
    destination_dir = destination / f"episode-{index:03d}"
    if source_dir.is_dir():
        shutil.copytree(source_dir, destination_dir, dirs_exist_ok=True)
    for frame_list in (episode.get("frames", []), episode.get("actor_frames", [])):
        for frame in frame_list:
            relative = Path(frame["path"])
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "episode-000":
                raise ValueError("Worker returned an invalid frame path")
            frame["path"] = str(Path(destination_dir.name).joinpath(*relative.parts[1:]))
    episode.update(index=index, seed=seed, worker_run_id=child_result["id"])
    return episode


def evaluate_batch(world: World | str | Path, *, policy_config: dict | None = None,
                   episodes: int = 1, workers: int = 1, seed: int = 0,
                   output: str | Path = "runs", max_steps: int = 30,
                   max_sim_seconds: float = 120, render: bool = True,
                   replay_fps: float = 5, max_replay_frames: int = 600,
                   graphics_backend: str = "auto", on_event: Callable | None = None,
                   cancelled: Callable | None = None) -> Report:
    """Evaluate fixed-seed episodes in fresh spawned processes and combine reports.

    Configuration accepts LLMAgent constructor options, or RemotePolicy options
    with token_env instead of a literal credential. Live Python policy objects
    are deliberately not pickled. When called from a script, use a __main__ guard.
    Cancellation is cooperative between bounded policy requests/physics actions.
    """
    for name, value, limit in (("episodes", episodes, 10000), ("workers", workers, 32), ("max_steps", max_steps, 100000)):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= limit:
            raise ValueError(f"{name} must be an integer from 1 to {limit}")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    if not math.isfinite(max_sim_seconds) or max_sim_seconds <= 0 or not math.isfinite(replay_fps) or replay_fps <= 0:
        raise ValueError("Simulation time and replay FPS must be finite and positive")
    if not isinstance(max_replay_frames, int) or isinstance(max_replay_frames, bool) or max_replay_frames < 2:
        raise ValueError("Replay needs at least two frames")
    if graphics_backend not in {"auto", "egl"}:
        raise ValueError("graphics_backend must be auto or egl")
    if graphics_backend == "egl" and platform.system() != "Linux":
        raise ValueError("Explicit EGL backend is supported only on Linux; use auto on macOS")
    config = _policy_config(policy_config)
    frozen = load(world.path if isinstance(world, World) else world)
    if frozen.manifest["validation"]["status"] != "ready":
        raise WorldNotReady("Parallel evaluation requires a validated immutable world")
    destination = Path(output).resolve() / uuid.uuid4().hex
    destination.mkdir(parents=True)
    shutil.copytree(frozen.path, destination / "world")
    staging = destination / "workers"
    result = {
        "schema_version": "1.0", "id": destination.name, "created_at": utc_now(),
        "status": "running", "world_id": frozen.id, "physical_id": frozen.manifest["physical_id"],
        "mode": "debug" if episodes == 1 else "repeated_trials", "requested_episodes": episodes,
        "protocol": {"clock": "stepped", "executor": "spawn", "workers": min(workers, episodes),
                     "graphics_backend": graphics_backend, "physics_backend": "native MuJoCo CPU",
                     "seeds": [seed+i for i in range(episodes)], "max_steps": max_steps,
                     "max_sim_seconds": max_sim_seconds, "render": render,
                     "replay_fps": replay_fps, "max_replay_frames": max_replay_frames,
                     "sampling_note": "Repeated seeds are not independent task diversity. Parallel speedup depends on CPU, memory and graphics contention; EGL rendering is not GPU physics."},
        "environment": {"packages": package_versions(), "platform": platform.platform()},
        "policy": {"class": "declarative_builtin", "config": config}, "episodes": [],
    }
    write_json(destination / "result.json", result)
    def emit(event):
        if on_event:
            try:
                on_event(event)
            except Exception as exc:
                result.setdefault("observer_errors", []).append(public_error(exc))
    emit({"type": "run_started", "run_id": result["id"], "path": str(destination), "workers": min(workers, episodes)})
    if cancelled and cancelled():
        result["status"] = "cancelled"
        _summarize(result)
        write_json(destination / "result.json", result)
        report = Report(destination)
        report._write_html()
        emit({"type": "run_finished", "run_id": result["id"], "summary": result["summary"]})
        return report
    context = multiprocessing.get_context("spawn")
    cancel_event = context.Event()
    completed = {}
    pending = {}
    try:
        pool = ProcessPoolExecutor(max_workers=min(workers, episodes), mp_context=context,
                                   initializer=_initialize_worker, initargs=(cancel_event,), max_tasks_per_child=1)
    except Exception as exc:
        result.update(status="completed_with_errors", batch_error=public_error(exc))
        _summarize(result)
        write_json(destination / "result.json", result)
        report = Report(destination)
        report._write_html()
        emit({"type": "run_finished", "run_id": result["id"], "summary": result["summary"]})
        return report
    try:
        for index in range(episodes):
            payload = {"index": index, "world_path": str(frozen.path), "policy": config,
                       "seed": seed+index, "output": str(staging / f"worker-{index:03d}"),
                       "max_steps": max_steps, "max_sim_seconds": max_sim_seconds,
                       "render": render, "replay_fps": replay_fps, "max_replay_frames": max_replay_frames,
                       "graphics_backend": graphics_backend}
            pending[pool.submit(_run_episode, payload)] = index
        while pending:
            if cancelled and cancelled():
                cancel_event.set()
                result["status"] = "cancelled"
                for future in pending:
                    future.cancel()
            done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                if future.cancelled():
                    completed[index] = {"index": index, "seed": seed+index, "status": "cancelled", "steps": [], "frames": []}
                else:
                    try:
                        worker = future.result()
                        completed[index] = _copy_episode(Path(worker["path"]), destination, index, seed+index)
                    except Exception as exc:
                        completed[index] = {"index": index, "seed": seed+index, "status": "worker_error", "error": public_error(exc), "steps": [], "frames": [], "partial_artifacts": f"workers/worker-{index:03d}"}
                result["episodes"] = [completed[i] for i in sorted(completed)]
                write_json(destination / "result.json", result)
                emit({"type": "episode_finished", "run_id": result["id"], "episode": copy.deepcopy(completed[index])})
    except Exception as exc:
        cancel_event.set()
        result["batch_error"] = public_error(exc)
        result["status"] = "completed_with_errors"
        for future, index in pending.items():
            future.cancel()
            completed[index] = {"index": index, "seed": seed+index, "status": "worker_error", "error": public_error(exc), "steps": [], "frames": [], "partial_artifacts": f"workers/worker-{index:03d}"}
        result["episodes"] = [completed[i] for i in sorted(completed)]
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        _summarize(result)
        write_json(destination / "result.json", result)
    report = Report(destination)
    report._write_html()
    emit({"type": "run_finished", "run_id": result["id"], "summary": result["summary"]})
    return report
