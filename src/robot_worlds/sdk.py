"""Generate, freeze, evaluate and inspect portable robot worlds."""

from __future__ import annotations

import copy
import json
import math
import platform
import shutil
import tempfile
import time
import uuid
import webbrowser
import zipfile
from pathlib import Path
from typing import Callable

from robot_worlds.artifacts import (
    canonical,
    digest,
    file_digest,
    inside,
    package_versions,
    public_error,
    read_json,
    utc_now,
    write_json,
)
from robot_worlds.models import WorldSpec

FINISH_TOOL = {
    "type": "function", "name": "finish",
    "description": "End this attempt. This does not declare success; the rubric grades the recorded behavior.",
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    "strict": True,
}


class WorldNotReady(ValueError):
    pass


def _runtime(spec, **kwargs):
    from robot_worlds.runtime import Runtime
    return Runtime(spec, **kwargs)


def _robot_assets(robot: str) -> dict:
    root = Path(__file__).parent / "assets" / "robots" / {"tiago++": "tiago", "g1": "g1"}[robot]
    return {str(p.relative_to(root)): file_digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _physical_spec(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if k not in ("rubric", "task", "name", "description", "source_prompt", "assumptions", "unvalidated_requirements", "assembly_templates", "assembly_instances")}


def _runtime_manifest(runtime) -> dict:
    manifest = getattr(runtime, "manifest", {})
    return manifest() if callable(manifest) else manifest


def _trace_evidence(path: Path, trace: list, episode: dict, result: dict, *, complete: bool) -> dict:
    """Freeze collection metadata; hashes detect changes, not malicious re-signing."""
    runtime = episode.get("runtime", {})
    return {
        "version": 1, "sha256": file_digest(path), "sample_count": len(trace),
        "start_time": trace[0].get("time") if trace and isinstance(trace[0], dict) else None,
        "end_time": trace[-1].get("time") if trace and isinstance(trace[-1], dict) else None,
        "complete": complete, "model_fingerprint": runtime.get("model_fingerprint"),
        "implementation_fingerprint": runtime.get("implementation_fingerprint"),
        "physical_id": result["physical_id"], "seed": episode["seed"],
        "episode_index": episode["index"], "source_run_id": result["id"],
    }


def _verified_regrade_trace(path: Path, episode: dict, result: dict, world: World) -> list:
    evidence = episode.get("trace_evidence")
    if not isinstance(evidence, dict) or evidence.get("version") != 1:
        raise ValueError("Legacy or missing trace integrity metadata; insufficient evidence for offline regrading")
    if evidence.get("complete") is not True:
        raise ValueError("Recorded trajectory is incomplete; insufficient evidence for offline regrading")
    source_run = episode.get("worker_run_id", result.get("evidence_origin_run_id", result["id"]))
    source_index = 0 if episode.get("worker_run_id") else episode["index"]
    bindings = {"source_run_id": source_run, "episode_index": source_index,
                "seed": episode["seed"], "physical_id": result["physical_id"]}
    if any(evidence.get(key) != value for key, value in bindings.items()):
        raise ValueError("Trace collection provenance does not match this episode")
    if not path.is_file() or file_digest(path) != evidence.get("sha256"):
        raise ValueError("Recorded trace is missing or its hash changed since collection")
    trace = read_json(path)
    if not isinstance(trace, list) or not trace or len(trace) != evidence.get("sample_count"):
        raise ValueError("Recorded trace sample count does not match collection evidence")
    times = [state.get("time") if isinstance(state, dict) else None for state in trace]
    if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in times):
        raise ValueError("Recorded trace contains invalid simulation timestamps")
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError("Recorded trace is not ordered by simulation time")
    if times[0] != evidence.get("start_time") or times[-1] != evidence.get("end_time"):
        raise ValueError("Recorded trace time span does not match collection evidence")
    if abs(times[0]) > 1e-9 or times[-1] != episode.get("sim_seconds"):
        raise ValueError("Recorded trace does not cover reset through the final episode state")
    runtime = episode.get("runtime", {})
    expected_runtime = world.manifest.get("runtime", {})
    fingerprint = evidence.get("model_fingerprint")
    if not fingerprint or fingerprint != runtime.get("model_fingerprint") or fingerprint != expected_runtime.get("model_fingerprint"):
        raise ValueError("Recorded trace model fingerprint is missing or incompatible with the frozen world")
    if any(state.get("model_fingerprint") != fingerprint for state in trace):
        raise ValueError("Recorded trace contains states from an unknown or different physical model")
    implementation = evidence.get("implementation_fingerprint")
    if not implementation or implementation != runtime.get("implementation_fingerprint") or implementation != expected_runtime.get("implementation_fingerprint"):
        raise ValueError("Recorded trace runtime implementation differs from its frozen world")
    return trace


def _wilson(success: int, count: int) -> list[float] | None:
    if not count:
        return None
    z = 1.959963984540054
    p = success / count
    denominator = 1 + z*z / count
    center = (p + z*z / (2*count)) / denominator
    radius = z * math.sqrt(p*(1-p)/count + z*z/(4*count*count)) / denominator
    return [max(0, center-radius), min(1, center+radius)]


class World:
    """An immutable world version. Editing creates a different content address."""

    def __init__(self, path: Path, spec: WorldSpec, manifest: dict):
        self.path = path.resolve()
        self._spec = spec.model_copy(deep=True)
        self.manifest = copy.deepcopy(manifest)

    @property
    def spec(self) -> WorldSpec:
        return self._spec.model_copy(deep=True)

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @classmethod
    def from_spec(cls, spec: WorldSpec | dict, output: str | Path = "worlds", *,
                  asset_root: str | Path | None = None, parent_id: str | None = None,
                  validate: bool = True, runtime_factory: Callable | None = None,
                  validation_profile: str = "strict", edit_metadata: dict | None = None) -> World:
        """Freeze and probe a supplied specification.

        ``fixed_reference`` permits overlap only between bodies whose complete
        ancestry is fixed to the world. It records each skipped pair and keeps
        robot clearance, dynamic geometry, bounds, rubric and native reset checks.
        This opt-in import profile does not certify source process behavior or
        exact concave collision geometry; generation always uses strict checks.
        """
        from robot_worlds.generator import validate_spec
        from robot_worlds.rubric import _extents, measure_predicate
        if validation_profile not in {"strict", "fixed_reference"}:
            raise ValueError("validation_profile must be strict or fixed_reference")
        spec = WorldSpec.model_validate(spec.model_dump(mode="json") if isinstance(spec, WorldSpec) else spec).model_copy(deep=True)
        from robot_worlds.large_scene import expand_assemblies
        assembly_errors = []
        try:
            spec = expand_assemblies(spec)
        except ValueError as exc:
            assembly_errors.append(str(exc))
        root = Path(output).resolve()
        root.mkdir(parents=True, exist_ok=True)
        assets, sources, asset_errors = {}, {}, []
        mesh_members = {}
        for obj in spec.objects:
            for field in ("mesh_path", "texture_path", "source_notice", "source_metadata"):
                value = getattr(obj, field)
                if not value:
                    continue
                source = Path(value).expanduser()
                if not source.is_absolute():
                    source = Path(asset_root or ".") / source
                if not source.is_file():
                    asset_errors.append(f"{obj.id}: referenced {field} asset is unavailable: {value}")
                    continue
                source = source.resolve()
                if str(source) in mesh_members:
                    setattr(obj, field, mesh_members[str(source)])
                    continue
                fingerprint = file_digest(source)
                member = f"assets/{fingerprint}{source.suffix.lower()}"
                assets[member], sources[member] = fingerprint, source
                mesh_members[str(source)] = member
                setattr(obj, field, member)
        # Keep declarative mesh sources aligned with materialized paths so a
        # saved assembly can be revalidated or refined without stale originals.
        for template in spec.assembly_templates:
            for part in template.parts:
                for field in ("mesh_path", "texture_path", "source_notice", "source_metadata"):
                    value = getattr(part, field)
                    if not value:
                        continue
                    original = Path(value).expanduser()
                    if not original.is_absolute():
                        original = Path(asset_root or ".") / original
                    if str(original.resolve()) in mesh_members:
                        setattr(part, field, mesh_members[str(original.resolve())])
        payload = spec.model_dump(mode="json")
        robot_assets = _robot_assets(spec.robot)
        stage = Path(tempfile.mkdtemp(prefix=".world-", dir=root))
        try:
            for member, source in sources.items():
                target = stage / member
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            checks = [{"check": "schema", "passed": True}]
            skipped_fixed_pairs = []
            connectivity = {}
            errors = assembly_errors + asset_errors + list(validate_spec(
                spec, asset_root=stage, validation_profile=validation_profile,
                skipped_fixed_pairs=skipped_fixed_pairs, connectivity_diagnostics=connectivity))
            skipped_fixed_pairs.sort()
            checks.append({"check": "geometry_and_rubric", "passed": not errors, "errors": errors.copy()})
            manifest = {
                "schema_version": "1.0", "identity_version": 2, "kind": "world",
                "created_at": utc_now(), "parent_id": parent_id,
                "assets": assets, "robot_assets": robot_assets,
                "physical_id": digest({"physical_spec": _physical_spec(payload), "assets": assets, "robot_assets": robot_assets}),
                "validation": {"status": "preview", "profile": validation_profile, "checks": checks,
                               "connectivity": connectivity,
                               "skipped_fixed_overlap_pairs": skipped_fixed_pairs,
                               "skipped_fixed_overlap_count": len(skipped_fixed_pairs),
                               "limitations": ["Static geometry and a reset probe do not prove task reachability, completeness of natural-language interpretation, or policy solvability.",
                                               "Material properties are estimated unless independently measured; temporal measurements have the recorded sampling resolution.",
                                               "Surface finishes, floor pattern and studio lighting are visual estimates, not measured materials."]},
            }
            if edit_metadata is not None:
                manifest["identity_version"] = 3
                manifest["edit"] = copy.deepcopy(edit_metadata)
                if manifest["edit"].get("parent_world_id") != parent_id:
                    raise ValueError("Edit metadata must identify the immutable parent world")
            if validation_profile == "fixed_reference":
                manifest["validation"]["limitations"].extend([
                    "Fixed-reference profile allows overlapping geometry only where both complete body ancestries are world-welded; robot and movable/articulated collision checks remain enabled.",
                    "Static reference geometry does not reproduce source animation or manufacturing processes. Native mesh colliders are convex hulls per part, which may fill concave openings."])
            if validate and not errors:
                runtime = None
                try:
                    runtime = (runtime_factory or _runtime)(spec, render=True, asset_root=stage, seed=0)
                    state = runtime.snapshot()
                    canonical(state)
                    from robot_worlds.reset_diagnostics import settling_check
                    settling = settling_check(getattr(runtime, "reset_diagnostics", None))
                    canonical(settling)
                    checks.append(settling)
                    errors.extend(settling["errors"])
                    if settling.get("status") == "unavailable":
                        manifest["validation"]["limitations"].append("Custom runtime did not expose reset/settling history; only final reset-state checks were available.")
                    reset_errors = []
                    for obj in spec.objects:
                        actual = state.get("objects", {}).get(obj.id)
                        if actual is None:
                            reset_errors.append(f"Missing physical object state: {obj.id}")
                            continue
                        position = actual.get("position")
                        try:
                            half = _extents(obj, actual)
                            if (not isinstance(position, (tuple, list)) or len(position) != 3
                                    or any(abs(position[i])+half[i] > spec.bounds[i]/2 + .01 for i in (0, 1))
                                    or position[2]-half[2] < -.01 or position[2]+half[2] > spec.bounds[2]+.01):
                                reset_errors.append(f"{obj.id}: settled physical geometry leaves world bounds")
                        except (TypeError, ValueError) as exc:
                            reset_errors.append(f"{obj.id}: cannot measure settled physical bounds: {exc}")
                        if obj.movable and not obj.joint:
                            position = actual.get("position")
                            if not position or math.dist(position, obj.position) > .05:
                                reset_errors.append(f"{obj.id}: unstable initial placement moved more than 5cm while settling")
                            if math.sqrt(sum(float(v)**2 for v in actual.get("velocity", [0, 0, 0]))) > .2:
                                reset_errors.append(f"{obj.id}: initial object has not settled below 0.2m/s")
                    errors.extend(reset_errors)
                    checks.append({"check": "physics_reset", "passed": not reset_errors, "errors": reset_errors})
                    manifest["runtime"] = _runtime_manifest(runtime)
                    bad = [c.id for c in spec.rubric if c.required and c.intent == "achieve" and not c.after
                           and measure_predicate(spec, c, state)["satisfied"] is True]
                    missing = [c.id for c in spec.rubric if measure_predicate(spec, c, state)["satisfied"] is None]
                    rubric_errors = []
                    if bad:
                        rubric_errors.append("Required achievement criteria already satisfied at reset: " + ", ".join(bad))
                    if missing:
                        rubric_errors.append("Rubric lacks runtime measurements for: " + ", ".join(missing))
                    errors.extend(rubric_errors)
                    checks.append({"check": "initial_rubric", "passed": not rubric_errors, "errors": rubric_errors})
                    image = runtime.render()
                    if image:
                        (stage / "preview.png").write_bytes(image)
                    else:
                        errors.append("Renderer returned no preview image")
                    checks.append({"check": "render", "passed": bool(image)})
                    manifest["validation"]["status"] = "ready" if not errors else "unvalidated"
                except Exception as exc:
                    checks.append({"check": "physics_probe", "passed": False, "error": public_error(exc)})
                    manifest["validation"]["status"] = "unvalidated"
                finally:
                    if runtime is not None:
                        try:
                            runtime.close()
                        except Exception as exc:
                            checks.append({"check": "runtime_cleanup", "passed": False, "error": public_error(exc)})
                            manifest["validation"]["status"] = "unvalidated"
            elif errors:
                manifest["validation"]["status"] = "unvalidated"
            # Validation evidence is part of version identity. A preview can be
            # promoted by creating a new validated version without mutating it.
            identity = {"spec": payload, "assets": assets, "robot_assets": robot_assets,
                        "validation": manifest["validation"], "runtime": manifest.get("runtime", {})}
            if manifest["identity_version"] == 3:
                identity["edit"] = manifest["edit"]
            manifest["id"] = digest(identity)
            destination = root / manifest["id"][:20]
            write_json(stage / "world.json", payload)
            write_json(stage / "manifest.json", manifest)
            if destination.exists():
                shutil.rmtree(stage)
            else:
                try:
                    stage.rename(destination)
                except FileExistsError:
                    shutil.rmtree(stage)
            return load(destination)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def refine(self, prompt: str, *, scope: list[str] | None = None,
               allow_task_changes: bool = False, allow_physics_changes: bool = False,
               output: str | Path | None = None, **kwargs) -> World:
        """Create a validated patch version; preserve every unmodified entity.

        Explicit scope is an exact ID allowlist, including added IDs. It never
        unlocks protected task or physics changes by itself. Assembly parts are
        materialized before editing so their persistent IDs remain authoritative.
        """
        from robot_worlds.editing import edit_world
        return edit_world(self, prompt, kind="refine", scope=scope,
                          allow_task_changes=allow_task_changes,
                          allow_physics_changes=allow_physics_changes, output=output, **kwargs)

    def repair(self, prompt: str | None = None, *, scope: list[str] | None = None,
               allow_task_changes: bool = False, allow_physics_changes: bool = False,
               output: str | Path | None = None, **kwargs) -> World:
        """Repair geometry/pose failures without weakening task or physics."""
        from robot_worlds.editing import edit_world
        return edit_world(self, prompt, kind="repair", scope=scope,
                          allow_task_changes=allow_task_changes,
                          allow_physics_changes=allow_physics_changes, output=output, **kwargs)

    def with_rubric(self, rubric: list, *, output: str | Path | None = None,
                    runtime_factory: Callable | None = None) -> World:
        payload = self._spec.model_dump(mode="json")
        payload["rubric"] = [c.model_dump(mode="json") if hasattr(c, "model_dump") else c for c in rubric]
        return World.from_spec(payload, output or self.path.parent, asset_root=self.path, parent_id=self.id,
                               runtime_factory=runtime_factory,
                               validation_profile=self.manifest["validation"].get("profile", "strict"))

    def evaluate(self, policy=None, *, episodes: int = 1, max_steps: int = 30,
                 max_sim_seconds: float = 120, seed: int = 0,
                 output: str | Path = "runs", render: bool = True,
                 on_event: Callable[[dict], None] | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 runtime_factory: Callable | None = None,
                 policy_factory: Callable | None = None,
                 replay_fps: float = 5, max_replay_frames: int = 600) -> Report:
        from robot_worlds.llm import audit_value, validate_action
        from robot_worlds.policy import LLMAgent, PolicyBudgetExceeded, PolicyError, policy_record
        from robot_worlds.rubric import grade
        if (not isinstance(episodes, int) or isinstance(episodes, bool) or episodes < 1
                or not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1
                or not math.isfinite(max_sim_seconds) or max_sim_seconds <= 0
                or not isinstance(seed, int) or isinstance(seed, bool)):
            raise ValueError("Episodes and steps must be positive integers, seed an integer, and simulation time finite and positive")
        if (not math.isfinite(replay_fps) or replay_fps <= 0 or not isinstance(max_replay_frames, int)
                or isinstance(max_replay_frames, bool) or max_replay_frames < 2):
            raise ValueError("Replay FPS must be positive and frame budget at least two")
        if policy is not None and policy_factory is not None:
            raise ValueError("Supply policy or policy_factory, not both")
        frozen = load(self.path)
        if frozen.manifest["validation"]["status"] != "ready":
            raise WorldNotReady(json.dumps(frozen.manifest["validation"]))
        expected_runtime = frozen.manifest.get("runtime", {})
        if any(not expected_runtime.get(key) for key in ("model_fingerprint", "implementation_fingerprint")):
            raise WorldNotReady("Frozen runtime fingerprints are missing; create and validate a new world version")
        if _robot_assets(frozen._spec.robot) != frozen.manifest["robot_assets"]:
            raise WorldNotReady("Installed robot assets changed; create and validate a new world version")
        run_id = uuid.uuid4().hex
        folder = Path(output).resolve() / run_id
        folder.mkdir(parents=True, exist_ok=False)
        shutil.copytree(frozen.path, folder / "world")
        result = {
            "schema_version": "1.0", "id": run_id, "created_at": utc_now(), "status": "running",
            "world_id": frozen.id, "physical_id": frozen.manifest["physical_id"],
            "mode": "debug" if episodes == 1 else "repeated_trials", "requested_episodes": episodes,
            "protocol": {"clock": "stepped", "max_steps": max_steps, "max_sim_seconds": max_sim_seconds,
                         "seeds": [seed+i for i in range(episodes)], "render": render,
                         "replay_fps": replay_fps, "max_replay_frames": max_replay_frames,
                         "sampling_note": "Repeated deterministic seeds are not independent task diversity or a guarantee of statistical precision."},
            "environment": {"packages": package_versions(), "platform": platform.platform()},
            "policy": policy_record(policy) if policy is not None else {
                "class": "factory" if policy_factory else "robot_worlds.policy.LLMAgent",
                "identity_status": "resolved_per_episode_before_first_action",
            },
            "episodes": [],
        }
        def emit(event):
            if on_event:
                try:
                    on_event(event)
                except Exception as exc:
                    result.setdefault("observer_errors", []).append(public_error(exc))
        def cancellation_requested():
            if cancelled:
                try:
                    return bool(cancelled())
                except Exception as exc:
                    result["cancellation_error"] = public_error(exc)
                    return True
            return False
        write_json(folder / "result.json", result)
        emit({"type": "run_started", "run_id": run_id, "path": str(folder)})
        for episode_index in range(episodes):
            if cancellation_requested():
                result["status"] = "cancelled"
                break
            episode_dir = folder / f"episode-{episode_index:03d}"
            episode_dir.mkdir()
            episode = {"index": episode_index, "seed": seed+episode_index, "status": "running",
                       "steps": [], "frames": [], "inference_seconds": 0.0}
            result["episodes"].append(episode)
            runtime, episode_policy = None, None
            phase = "runtime"
            started = time.monotonic()
            try:
                runtime = (runtime_factory or _runtime)(frozen.spec, render=render,
                    asset_root=frozen.path, seed=episode["seed"], max_sim_seconds=max_sim_seconds)
                episode["runtime"] = _runtime_manifest(runtime)
                changed = [key for key in ("engine", "engine_version", "timestep", "controller", "model_fingerprint", "implementation_fingerprint", "trace_interval", "clock", "action_contract", "sensor_contract", "policy_sha256", "policy_source_revision", "policy_frequency_hz")
                           if key in expected_runtime and episode["runtime"].get(key) != expected_runtime[key]]
                if changed:
                    raise WorldNotReady("Runtime contract changed since validation; create a new world version: " + ", ".join(changed))
                if render and on_event and hasattr(runtime, "set_live_observer"):
                    def live_frame(frame):
                        target = episode_dir / "live.png"
                        temporary = episode_dir / "live.png.tmp"
                        temporary.write_bytes(frame["image"])
                        temporary.replace(target)
                        emit({"type": "live_frame", "run_id": run_id, "episode": episode_index,
                              "time": frame["time"], "sequence": frame["sequence"],
                              "path": f"{episode_dir.name}/live.png", "source": frame["source"]})
                    runtime.set_live_observer(live_frame)
                    runtime.publish_live(force=True)
                phase = "policy"
                episode_policy = policy if policy is not None else policy_factory() if policy_factory else LLMAgent(max_steps=max_steps)
                episode_policy.reset({"task": frozen._spec.task, "robot": frozen._spec.robot,
                                      "action_contract": episode["runtime"].get("action_contract"),
                                      "sensor_contract": episode["runtime"].get("sensor_contract")})
                episode["policy"] = policy_record(episode_policy)
                if policy_factory is None and episode_index == 0:
                    result["policy"] = copy.deepcopy(episode["policy"])
                # Persist resolved source/settings before the policy's first act().
                write_json(folder / "result.json", result)
                phase = "runtime"
                for step in range(max_steps):
                    if cancellation_requested():
                        episode["status"] = "cancelled"
                        break
                    observation = runtime.observe()
                    observation["tools"] = list(observation.get("tools", [])) + [copy.deepcopy(FINISH_TOOL)]
                    current_time = float(runtime.snapshot()["time"])
                    if current_time >= max_sim_seconds - 1e-9:
                        episode["termination"] = "simulation_time_limit"
                        break
                    if render:
                        image = runtime.render()
                        name = f"frame-{step:04d}.png"
                        (episode_dir / name).write_bytes(image)
                        episode["frames"].append({"time": current_time, "path": f"{episode_dir.name}/{name}"})
                    phase = "policy"
                    emit({"type": "policy_started", "run_id": run_id, "episode": episode_index,
                          "step": step, "time": current_time})
                    if policy_record(episode_policy) != episode["policy"]:
                        raise PolicyError("Policy identity changed after its episode configuration was frozen")
                    tick = time.monotonic()
                    try:
                        action = episode_policy.act(observation)
                    finally:
                        latency = time.monotonic() - tick
                        episode["inference_seconds"] += latency
                    action = validate_action(action, observation["tools"])
                    step_record = {"step": step, "time": current_time,
                                   "action": audit_value(action), "inference_seconds": latency}
                    episode["steps"].append(step_record)
                    emit({"type": "action_started", "run_id": run_id, "episode": episode_index,
                          **step_record})
                    if action.get("tool") == "finish":
                        step_record["result"] = {"ended": True, "success": "determined_by_rubric"}
                        if hasattr(episode_policy, "record_result"):
                            episode_policy.record_result(step_record["result"])
                        episode["termination"] = "policy_finished"
                        break
                    phase = "runtime"
                    step_record["result"] = audit_value(runtime.execute(action))
                    if float(runtime.snapshot()["time"]) > max_sim_seconds + 1e-6:
                        raise RuntimeError("Runtime exceeded the hard simulation time budget")
                    phase = "policy"
                    if hasattr(episode_policy, "record_result"):
                        episode_policy.record_result(step_record["result"])
                    phase = "runtime"
                    write_json(episode_dir / "trace.json", runtime.trace)
                    write_json(folder / "result.json", result)
                    emit({"type": "step", "run_id": run_id, "episode": episode_index, **step_record})
                    if render and on_event and hasattr(runtime, "publish_live"):
                        runtime.publish_live(force=True)
                    if on_event:
                        progress = grade(frozen.spec, runtime.trace)
                        emit({"type": "task_progress", "run_id": run_id, "episode": episode_index,
                              "time": float(runtime.snapshot()["time"]), "provisional": True,
                              "criteria": [{key: criterion[key] for key in ("id", "description", "satisfied", "completion_time")}
                                           for criterion in progress["criteria"]]})
                    if step_record["result"].get("status") == "time_limit" or float(runtime.snapshot()["time"]) >= max_sim_seconds - 1e-9:
                        episode["termination"] = "simulation_time_limit"
                        break
                if episode["status"] == "running":
                    episode["status"] = "completed"
                    episode.setdefault("termination", "step_limit")
            except PolicyBudgetExceeded as exc:
                episode.update(status="completed", termination="policy_budget", error=public_error(exc))
            except Exception as exc:
                episode.update(status="policy_error" if phase == "policy" else "simulation_error", error=public_error(exc))
            finally:
                episode["wall_seconds"] = time.monotonic() - started
                try:
                    episode["policy_usage"] = audit_value(getattr(episode_policy, "usage", {}))
                    write_json(episode_dir / "transcript.json", audit_value(getattr(episode_policy, "transcript", [])))
                except Exception as exc:
                    episode["transcript_error"] = public_error(exc)
                    if episode["status"] == "completed":
                        episode["status"] = "policy_error"
                    write_json(episode_dir / "transcript.json", {"unavailable": public_error(exc)})
                if runtime is not None:
                    live = getattr(runtime, "_live_view", None)
                    if live and live.error:
                        episode["live_view_error"] = live.error
                    if hasattr(runtime, "set_live_observer"):
                        runtime.set_live_observer(None)
                    trace, last = [], None
                    try:
                        trace = list(runtime.trace)
                        last = runtime.snapshot()
                        if not trace or trace[-1] != last:
                            trace.append(last)
                        episode["sim_seconds"] = float(last["time"])
                        canonical(trace)
                        write_json(episode_dir / "trace.json", trace)
                        episode["trace_evidence"] = _trace_evidence(episode_dir / "trace.json", trace, episode, result, complete=True)
                        episode["rubric"] = grade(frozen.spec, trace)
                    except Exception as exc:
                        episode["grading_error"] = public_error(exc)
                        if episode["status"] == "completed":
                            episode["status"] = "grading_error"
                        # Preserve every complete, finite sample after a simulator
                        # failure rather than discarding the whole trajectory.
                        safe = []
                        for state in trace:
                            try:
                                canonical(state)
                                safe.append(state)
                            except (TypeError, ValueError):
                                break
                        trace = safe
                        write_json(episode_dir / "trace.json", safe)
                        episode["trace_evidence"] = _trace_evidence(episode_dir / "trace.json", safe, episode, result, complete=False)
                    try:
                        if render and last is not None:
                            name = "final.png"
                            (episode_dir / name).write_bytes(runtime.render())
                            episode["frames"].append({"time": last["time"], "path": f"{episode_dir.name}/{name}"})
                    except Exception as exc:
                        episode["render_error"] = public_error(exc)
                    try:
                        if render and trace and hasattr(runtime, "restore_snapshot"):
                            from robot_worlds.replay import render_replay
                            evidence_times = [c["completion_time"] for c in episode.get("rubric", {}).get("criteria", [])
                                              if c.get("completion_time") is not None]
                            evidence_times.extend(c["evidence"]["decisive_failure"]["time"]
                                                  for c in episode.get("rubric", {}).get("criteria", [])
                                                  if c.get("evidence", {}).get("decisive_failure"))
                            frames = render_replay(runtime, trace, episode_dir, fps=replay_fps,
                                                   max_frames=max_replay_frames, evidence_times=evidence_times)
                            if frames:
                                episode["actor_frames"] = episode["frames"]
                                episode["frames"] = [{**frame, "path": f"{episode_dir.name}/{frame['path']}"} for frame in frames]
                                episode["replay"] = {"source": "recorded_simulation_states", "view": "overview", "frame_count": len(frames)}
                        if "replay" not in episode:
                            episode["replay"] = {"source": "action_boundary_camera_frames", "limitation": "Continuous state replay is unavailable for this runtime or render mode."}
                    except Exception as exc:
                        episode["replay_error"] = public_error(exc)
                        episode["replay"] = {"source": "action_boundary_camera_frames", "limitation": "Continuous replay rendering failed; task grading still uses the full saved trace."}
                    finally:
                        try:
                            runtime.close()
                        except Exception as exc:
                            episode["cleanup_error"] = public_error(exc)
                            if episode["status"] == "completed":
                                episode["status"] = "simulation_error"
                else:
                    write_json(episode_dir / "trace.json", [])
                    episode["trace_evidence"] = _trace_evidence(episode_dir / "trace.json", [], episode, result, complete=False)
                if policy_factory is not None and episode_policy is not None and hasattr(episode_policy, "close"):
                    try:
                        episode_policy.close()
                    except Exception as exc:
                        episode["policy_cleanup_error"] = public_error(exc)
                        if episode["status"] == "completed":
                            episode["status"] = "policy_error"
                write_json(folder / "result.json", result)
                emit({"type": "episode_finished", "run_id": run_id, "episode": copy.deepcopy(episode)})
            if episode["status"] == "cancelled":
                result["status"] = "cancelled"
                break
        _summarize(result)
        write_json(folder / "result.json", result)
        report = Report(folder)
        report._write_html()
        emit({"type": "run_finished", "run_id": run_id, "summary": result["summary"]})
        return report

    def export(self, destination: str | Path) -> Path:
        return _export(self.path, destination)


def _summarize(result: dict) -> None:
    attempted = len(result["episodes"])
    valid = [e for e in result["episodes"] if e["status"] == "completed" and e.get("rubric", {}).get("status") in {"passed", "failed"}]
    successes = sum(bool(e.get("rubric", {}).get("success")) for e in valid)
    result["summary"] = {"attempted": attempted, "valid": len(valid), "successes": successes,
                         "invalid_or_interrupted": attempted-len(valid),
                         "success_rate": successes/len(valid) if valid else None,
                         "success_interval_95": _wilson(successes, len(valid))}
    if result["status"] == "running":
        result["status"] = "completed" if len(valid) == attempted else "completed_with_errors"
    result["finished_at"] = utc_now()


def load(path: str | Path) -> World:
    path = Path(path).resolve()
    if path.is_file():
        path = path.parent
    manifest = read_json(path / "manifest.json")
    stored_spec = read_json(path / "world.json")
    spec = WorldSpec.model_validate(stored_spec)
    for member, expected in manifest["assets"].items():
        if file_digest(inside(path, member)) != expected:
            raise ValueError(f"World asset was modified: {member}")
    identity = {"spec": stored_spec, "assets": manifest["assets"],
                "robot_assets": manifest["robot_assets"]}
    if manifest.get("identity_version") in {2, 3}:
        identity.update(validation=manifest["validation"], runtime=manifest.get("runtime", {}))
        if manifest["identity_version"] == 3:
            edit = manifest.get("edit")
            if not isinstance(edit, dict) or edit.get("parent_world_id") != manifest.get("parent_id"):
                raise ValueError("Edited world is missing its parent-bound edit metadata")
            identity["edit"] = edit
    else:
        raise ValueError("Legacy world validation is not integrity-protected; create a new world version")
    if digest(identity) != manifest["id"]:
        raise ValueError("World specification or validation evidence was modified; save edits as a new version")
    physical_id = digest({"physical_spec": _physical_spec(stored_spec), "assets": manifest["assets"], "robot_assets": manifest["robot_assets"]})
    if physical_id != manifest["physical_id"]:
        raise ValueError("World physical identity was modified")
    return World(path, spec, manifest)


def generate(prompt: str, *, output: str | Path = "worlds", model: str = "gpt-6-astra",
             reasoning: str = "medium", client=None, max_repairs: int = 2,
             asset_root: str | Path | None = None, parent_id: str | None = None,
             on_event: Callable | None = None, runtime_factory: Callable | None = None,
             cancelled: Callable | None = None, client_options: dict | None = None,
             scene_profile: str = "standard", max_objects: int = 5000, min_objects: int = 0, asset_library=None,
             online_assets: bool = True, asset_resolver=None) -> World:
    from robot_worlds.generator import GenerationCancelled, GenerationError, generate_spec
    if not isinstance(online_assets, bool) or (not online_assets and asset_resolver is not None):
        raise ValueError("online_assets must be boolean; a resolver cannot be supplied when disabled")
    started = time.perf_counter()
    emit = on_event or (lambda event: None)
    output = Path(output).resolve()
    generation_id = uuid.uuid4().hex
    record = {"id": generation_id, "model": model, "reasoning": reasoning, "scene_profile": scene_profile, "max_objects": max_objects, "min_objects": min_objects, "started_at": utc_now(), "status": "running", "versions": [], "events": []}
    record_path = output / f"generation-{generation_id}.json"
    latest = None
    def event(value):
        elapsed = max(0.0, time.perf_counter() - started)
        value = {**value, "elapsed_seconds": elapsed}
        record["elapsed_seconds"] = elapsed
        record["events"].append(value)
        write_json(record_path, record)
        emit(value)
    def candidate(spec):
        nonlocal latest
        latest = World.from_spec(spec, output, asset_root=asset_root, parent_id=parent_id, validate=False)
        record["versions"].append(latest.id)
        event({"type": "world_preview", "world_id": latest.id, "path": str(latest.path), "validation": latest.manifest["validation"]})
    def validate_physics(spec):
        nonlocal latest
        latest = World.from_spec(spec, output, asset_root=asset_root, parent_id=parent_id, runtime_factory=runtime_factory)
        record["versions"].append(latest.id)
        errors = []
        for check in latest.manifest["validation"]["checks"]:
            if not check["passed"]:
                errors.extend(check.get("errors", []))
                if check.get("error"):
                    errors.append(f"{check['check']}: {check['error']['message']}")
                if not check.get("errors") and not check.get("error"):
                    errors.append(f"Validation failed: {check['check']}")
        return errors
    event({"type": "generation_started", "model": model, "generation_id": generation_id})
    try:
        generate_spec(prompt, model=model, reasoning=reasoning, client=client,
                      max_repairs=max_repairs, asset_root=asset_root, on_candidate=candidate,
                      validator=validate_physics, event_callback=event, cancelled=cancelled, client_options=client_options,
                      scene_profile=scene_profile, max_objects=max_objects, min_objects=min_objects, asset_library=asset_library,
                      online_asset_cache=output.parent / "asset-cache", asset_resolver=asset_resolver, online_assets=online_assets)
    except GenerationError as exc:
        was_cancelled = isinstance(exc, GenerationCancelled)
        record.update(status="cancelled" if was_cancelled else "incomplete", errors=exc.errors, attempts=exc.attempts, history=exc.history)
        event({"type": "generation_cancelled" if was_cancelled else "generation_incomplete",
               "world_id": latest.id if latest else None, "errors": exc.errors, "generation_path": str(record_path)})
        if latest is None:
            raise
        return latest
    except Exception as exc:
        # Keep unexpected importer/filesystem failures terminal in the diagnostic
        # record too. The exception still propagates; it never authorizes fallback.
        record.update(status="failed", error_type=type(exc).__name__)
        event({"type": "generation_failed", "error_type": type(exc).__name__,
               "world_id": latest.id if latest else None, "generation_path": str(record_path)})
        raise
    record.update(status="completed", world_id=latest.id)
    event({"type": "world_created", "world_id": latest.id, "path": str(latest.path),
          "validation": latest.manifest["validation"], "generation_path": str(record_path)})
    return latest


class Report:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    @property
    def result(self) -> dict:
        return read_json(self.path / "result.json")

    @property
    def summary(self) -> dict:
        return self.result["summary"]

    def open(self) -> None:
        webbrowser.open((self.path / "report.html").as_uri())

    def export(self, destination: str | Path) -> Path:
        return _export(self.path, destination)

    def _write_html(self) -> None:
        from robot_worlds.report import render_report
        (self.path / "report.html").write_text(render_report(self.result, read_json(self.path / "world" / "world.json")))

    def regrade(self, world: World, *, output: str | Path | None = None) -> Report:
        """Apply rubric edits to verified evidence from a compatible frozen runtime.

        Both compiled-model and implementation fingerprints must remain identical.
        Controller/source or compiled physics changes require a new evaluation;
        rubric-only edits with unchanged runtime fingerprints remain supported.
        """
        from robot_worlds.rubric import grade
        original = self.result
        original_world = load(self.path / "world")
        if original_world.id != original["world_id"] or original_world.manifest["physical_id"] != original["physical_id"]:
            raise ValueError("Run provenance does not match its recorded world")
        world = load(world.path)
        if world.manifest["validation"]["status"] != "ready":
            raise WorldNotReady("Regrading requires a validated rubric/world version")
        if original["physical_id"] != world.manifest["physical_id"]:
            raise ValueError("Regrading requires the same physical world; run a new evaluation")
        recorded_runtime = original_world.manifest.get("runtime", {})
        target_runtime = world.manifest.get("runtime", {})
        incompatible = [key for key in ("model_fingerprint", "implementation_fingerprint")
                        if not recorded_runtime.get(key) or not target_runtime.get(key)
                        or recorded_runtime[key] != target_runtime[key]]
        if incompatible:
            raise ValueError("Regrading requires a compatible frozen world with matching compiled-model and runtime implementation fingerprints; "
                             "controller/source or physics changes require a new evaluation: " + ", ".join(incompatible))
        destination = Path(output or self.path.parent) / uuid.uuid4().hex
        shutil.copytree(self.path, destination)
        shutil.rmtree(destination / "world")
        shutil.copytree(world.path, destination / "world")
        revised = copy.deepcopy(original)
        revised.update(id=destination.name, parent_run_id=original["id"], world_id=world.id,
                       created_at=utc_now(), status="running", regraded=True,
                       evidence_origin_run_id=original.get("evidence_origin_run_id", original["id"]))
        for episode in revised["episodes"]:
            trace_path = destination / f"episode-{episode['index']:03d}" / "trace.json"
            try:
                trace = _verified_regrade_trace(trace_path, episode, original, original_world)
            except Exception as exc:
                episode.pop("rubric", None)
                episode["evidence_validation"] = {"status": "insufficient_evidence", "error": public_error(exc)}
                episode.update(status="grading_error", grading_error=public_error(exc))
                continue
            episode["evidence_validation"] = {"status": "verified", "trace_sha256": episode["trace_evidence"]["sha256"]}
            try:
                episode["rubric"] = grade(world.spec, trace)
            except Exception as exc:
                episode.pop("rubric", None)
                episode.update(status="grading_error", grading_error=public_error(exc))
        _summarize(revised)
        write_json(destination / "result.json", revised)
        report = Report(destination)
        report._write_html()
        return report


def _export(root: Path, destination: str | Path) -> Path:
    destination = Path(destination).resolve()
    if destination.is_relative_to(root.resolve()):
        raise ValueError("Export destination must be outside the artifact")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in sorted(root.rglob("*")):
            if member.is_file() and not member.is_symlink():
                archive.write(member, member.relative_to(root))
    return destination
