"""Local API and browser workbench. Simulation jobs use isolated spawned workers."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import re
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from robot_worlds.artifacts import (
    digest,
    file_digest,
    inside,
    public_error,
    read_json,
    utc_now,
    write_json,
)
from robot_worlds.models import Identifier


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateRequest(RequestModel):
    prompt: str = Field(min_length=1, max_length=32000)
    model: str = Field(default="gpt-6-astra", min_length=1, max_length=100)
    reasoning: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_repairs: int = Field(default=2, ge=0, le=10)
    scene_profile: Literal["standard", "large"] = "standard"
    max_objects: int = Field(default=5000, ge=1, le=10000)
    min_objects: int = Field(default=0, ge=0, le=10000)
    asset_ids: list[str] = Field(default_factory=list, max_length=10)
    max_cost_usd: float = Field(default=5, gt=0, le=1000)
    max_model_seconds: float = Field(default=600, gt=0, le=3600)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def object_limits(self):
        if self.min_objects > self.max_objects:
            raise ValueError("min_objects must not exceed max_objects")
        return self


class RefineRequest(GenerateRequest):
    scope: list[Identifier] | None = Field(default=None, min_length=1, max_length=1000)
    allow_task_changes: bool = False
    allow_physics_changes: bool = False

    @model_validator(mode="after")
    def unique_scope(self):
        if self.scope is not None and len(self.scope) != len(set(self.scope)):
            raise ValueError("scope must contain unique entity identifiers")
        return self


class RepairRequest(RefineRequest):
    prompt: str = Field(default="Fix unresolved validation failures while preserving the task and physical properties.",
                        min_length=1, max_length=32000)
    allow_task_changes: Literal[False] = False
    allow_physics_changes: Literal[False] = False


class EvaluateRequest(RequestModel):
    model: str = Field(default="gpt-6-astra", min_length=1, max_length=100)
    reasoning: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    episodes: int = Field(default=1, ge=1, le=100)
    workers: int = Field(default=1, ge=1, le=32)
    graphics_backend: Literal["auto", "egl"] = "auto"
    max_steps: int = Field(default=30, ge=1, le=500)
    max_sim_seconds: float = Field(default=120, gt=0, le=3600)
    seed: int = Field(default=0, ge=0, le=2**31-101)
    max_cost_usd: float = Field(default=5, gt=0, le=1000)
    max_model_seconds: float = Field(default=600, gt=0, le=3600)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)


class RubricRequest(RequestModel):
    rubric: list[dict] = Field(min_length=1, max_length=200)


def _job_worker(root_string: str, job_id: str, kind: str, payload: dict) -> None:
    # A spawned worker imports MuJoCo and creates/uses/closes its GL context on
    # one thread. Web request threads never touch mutable simulator objects.
    from robot_worlds.generator import GenerationCancelled
    from robot_worlds.policy import LLMAgent
    from robot_worlds.sdk import generate, load

    root = Path(root_string)
    job_path = root / "jobs" / job_id / "job.json"
    job = read_json(job_path)
    job.update(status="running", started_at=utc_now())
    write_json(job_path, job)

    def event(data):
        data = dict(data)
        if data.get("type") == "live_frame":
            job["live_frame"] = data
            write_json(job_path, job)
            return
        if data.get("type") == "candidate":
            data["type"] = "preview"
        job["events"].append(data)
        job["events"] = job["events"][-200:]
        if "run_id" in data:
            job["run_id"] = data["run_id"]
        if "world_id" in data:
            job["world_id"] = data["world_id"]
        write_json(job_path, job)

    def cancelled():
        return (job_path.parent / "cancel").exists()

    try:
        # Owner-installed libraries are discovered by workers. HTTP requests
        # cannot select arbitrary filesystem paths or inject importer callbacks.
        library = root / "asset-library"
        if kind in {"generate", "refine", "repair"} and library.exists():
            payload["asset_library"] = str(library)
        if cancelled():
            job["status"] = "cancelled"
        elif kind == "starter":
            from robot_worlds.starter_environments import add_starter
            world = add_starter(payload['starter_id'], root/'worlds', on_event=event, cancelled=cancelled)
            job.update(world_id=world.id, status='completed', validation=world.manifest['validation'])
        elif kind == "revalidate":
            world = load(_world_directory(root, payload['world_id']))
            event({'type': 'validation_started', 'message': 'Running native checks on this computer; no model calls'})
            revised = world.revalidate(output=root/'worlds')
            job.update(world_id=revised.id, status='completed', validation=revised.manifest['validation'])
            event({'type': 'world_created', 'world_id': revised.id, 'validation': revised.manifest['validation']})
        elif kind == "task":
            world = load(_world_directory(root, payload["world_id"]))
            revised = world.with_task(payload["task_id"], output=root / "worlds")
            job.update(world_id=revised.id, status="completed", validation=revised.manifest["validation"])
            event({"type": "world_created", "world_id": revised.id,
                   "validation": revised.manifest["validation"],
                   "message": "Task selected; physical scene unchanged"})
        elif kind == "generate":
            world = generate(**payload, output=root / "worlds", on_event=event, cancelled=cancelled)
            job.update(world_id=world.id, status="completed",
                       validation=world.manifest["validation"])
        elif kind in {"refine", "repair"}:
            world = load(_world_directory(root, payload.pop("world_id")))
            revised = getattr(world, kind)(**payload, output=root / "worlds", on_event=event, cancelled=cancelled)
            job.update(world_id=revised.id, status="completed", validation=revised.manifest["validation"],
                       edit=revised.manifest.get("edit"))
        elif kind == "rubric":
            world = load(_world_directory(root, payload["world_id"]))
            revised = world.with_rubric(payload["rubric"], output=root / "worlds")
            job.update(world_id=revised.id, status="completed", validation=revised.manifest["validation"])
        elif kind == "evaluate":
            world = load(_world_directory(root, payload.pop("world_id")))
            budget = {key: payload.pop(key) for key in ("max_cost_usd", "input_price_per_million", "output_price_per_million")}
            budget["max_seconds"] = payload.pop("max_model_seconds")
            policy_options = dict(model=payload.pop("model"), reasoning=payload.pop("reasoning"),
                                  max_steps=payload["max_steps"], **budget)
            workers = payload.pop("workers", 1)
            graphics_backend = payload.pop("graphics_backend", "auto")
            if workers > 1 or graphics_backend != "auto":
                from robot_worlds.batch import evaluate_batch
                report = evaluate_batch(world, policy_config={"kind": "llm", **policy_options},
                                        workers=workers, graphics_backend=graphics_backend,
                                        **payload, output=root / "runs", on_event=event,
                                        cancelled=cancelled)
            else:
                agent = LLMAgent(**policy_options)
                report = world.evaluate(agent, **payload, output=root / "runs", on_event=event,
                                        cancelled=cancelled)
            job.update(run_id=report.result["id"], status=report.result["status"])
        else:
            raise ValueError("Unknown job type")
        if cancelled() and job["status"] == "completed":
            job["status"] = "cancelled"
    except GenerationCancelled:
        job.update(status="cancelled")
    except Exception as exc:
        job.update(status="failed", error=public_error(exc))
    finally:
        job["finished_at"] = utc_now()
        write_json(job_path, job)


def _identifier(value: str, length: int = 32) -> str:
    if not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise HTTPException(404, "Artifact not found")
    return value


def _world_directory(root: Path, world_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{20}|[0-9a-f]{64}", world_id):
        raise HTTPException(404, "World not found")
    path = root / "worlds" / world_id[:20]
    if not (path / "manifest.json").exists():
        raise HTTPException(404, "World not found")
    if len(world_id) == 64 and read_json(path / "manifest.json")["id"] != world_id:
        raise HTTPException(404, "World not found")
    return path


def create_app(data_dir: str | Path = ".robot-worlds", *, executor_factory=None) -> FastAPI:
    root = Path(data_dir).resolve()
    for name in ("worlds", "runs", "jobs", "assets"):
        (root / name).mkdir(parents=True, exist_ok=True)
    static = Path(__file__).parent / "static"
    futures = {}
    pool = None
    lock_file = None
    submit_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        nonlocal pool, lock_file
        import fcntl
        lock_file = (root / ".server.lock").open("a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.close()
            raise RuntimeError("Another Robot Worlds server uses this data directory") from exc
        for path in (root / "jobs").glob("*/job.json"):
            job = read_json(path)
            if job.get("status") in ("queued", "running"):
                job.update(status="interrupted", error={"message": "Server exited before this job completed. Saved artifacts remain available."})
                write_json(path, job)
        pool = (executor_factory or (lambda: ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context("spawn"))))()
        try:
            yield
        finally:
            for job_id, future in futures.items():
                if not future.done():
                    (root / "jobs" / job_id / "cancel").touch()
            # Retain ownership of the data directory until workers have stopped.
            # In-flight model calls have bounded transport timeouts; cancellation
            # is observed before the next model request or robot action.
            pool.shutdown(wait=True, cancel_futures=True)
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()

    app = FastAPI(title="Robot Worlds", version="0.1.0a1", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin mutations are disabled"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def submit(kind: str, payload: dict) -> dict:
        if pool is None:
            raise HTTPException(503, "Worker is not ready")
        with submit_lock:
            active = sum(not future.done() for future in futures.values())
            if active >= 8:
                raise HTTPException(429, "The local job queue is full")
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "kind": kind, "status": "queued", "created_at": utc_now(), "events": []}
            write_json(root / "jobs" / job_id / "job.json", job)
            try:
                futures[job_id] = pool.submit(_job_worker, str(root), job_id, kind, payload)
            except Exception as exc:
                job.update(status="failed", error=public_error(exc), finished_at=utc_now())
                write_json(root / "jobs" / job_id / "job.json", job)
                raise HTTPException(503, "Worker unavailable; restart the server before retrying") from exc
            def completed(future):
                path = root / "jobs" / job_id / "job.json"
                saved = read_json(path)
                if saved["status"] not in ("queued", "running"):
                    return
                if future.cancelled():
                    saved.update(status="cancelled", finished_at=utc_now())
                else:
                    error = future.exception()
                    if error is None:
                        return
                    saved.update(status="failed", error=public_error(error), finished_at=utc_now())
                write_json(path, saved)
            futures[job_id].add_done_callback(completed)
            return job

    @app.get("/api/health")
    def health():
        from robot_worlds.robot_assets import ROBOT_MANIFESTS
        return {"status": "ok", "model": "gpt-6-astra", "key_configured": bool(os.environ.get("OPENAI_API_KEY")),
                "robots": {name: {"name": data["name"], "capabilities": data["capabilities"],
                                  "limitations": data["limitations"]}
                           for name, data in ROBOT_MANIFESTS.items()}}

    @app.post("/api/assets/{filename}", status_code=201)
    async def upload_asset(filename: str, request: Request):
        suffix = Path(filename).suffix.lower()
        if suffix not in {".obj", ".stl", ".msh"} or Path(filename).name != filename:
            raise HTTPException(422, "Choose an OBJ, STL or MSH mesh file")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 20 * 1024 * 1024:
                raise HTTPException(413, "Mesh files must be at most 20 MiB")
        if not data:
            raise HTTPException(422, "Mesh file is empty")
        asset_id = hashlib.sha256(data).hexdigest() + suffix
        destination = root / "assets" / asset_id
        if not destination.exists():
            temporary = root / "assets" / f".upload-{uuid.uuid4().hex}"
            try:
                temporary.write_bytes(data)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return {"id": asset_id, "name": filename, "bytes": len(data),
                "validation": "Mesh compilation and collision checks happen during world validation."}

    def generation_payload(request: GenerateRequest):
        payload = request.model_dump()
        assets = payload.pop("asset_ids")
        budget = {key: payload.pop(key) for key in ("max_cost_usd", "input_price_per_million", "output_price_per_million")}
        budget["max_seconds"] = payload.pop("max_model_seconds")
        budget["max_output_tokens"] = 16384
        payload["client_options"] = budget
        references = []
        for asset_id in assets:
            if not re.fullmatch(r"[0-9a-f]{64}\.(obj|stl|msh)", asset_id):
                raise HTTPException(422, "Invalid uploaded mesh identifier")
            path = root / "assets" / asset_id
            if not path.is_file():
                raise HTTPException(404, "Uploaded mesh is unavailable")
            references.append(str(path))
        if references:
            payload["prompt"] += ("\nUser-supplied local mesh files available for this world:\n"
                                  + "\n".join(references)
                                  + "\nUse the supplied mesh geometry where the request calls for it. "
                                  "Set each mesh object's size to its intended full dimensions in metres; "
                                  "the runtime normalizes the source mesh to that size. "
                                  "Single meshes use convex collision hulls; model open interiors with separate collision pieces.")
        return payload

    @app.get("/api/worlds")
    def worlds():
        items = []
        for path in sorted((root / "worlds").glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.parent.name.startswith("."):
                continue
            try:
                manifest, spec = read_json(path), read_json(path.parent / "world.json")
                edit = manifest.get("edit") or {}
                items.append({"id": manifest["id"], "name": spec["name"], "task": spec["task"],
                              "validation": manifest["validation"], "created_at": manifest["created_at"],
                              "parent_id": manifest.get("parent_id"),
                              "edit": {"kind": edit.get("kind"), "prompt": edit.get("prompt", "")[:240],
                                       "counts": edit.get("counts", {})} if edit else None})
            except (OSError, ValueError, KeyError):
                continue
        return items

    @app.get("/api/starters")
    def starters():
        from robot_worlds.starter_environments import starter_catalog
        return starter_catalog()

    @app.get("/api/starters/{starter_id}/preview")
    def preview_starter(starter_id: str):
        from robot_worlds.starter_environments import starter_preview
        try:
            return FileResponse(starter_preview(starter_id), media_type='image/png')
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(404, str(error)) from error

    @app.post("/api/starters/{starter_id}/add", status_code=202)
    def import_starter(starter_id: str):
        from robot_worlds.starter_environments import starter_catalog
        entry = next((e for e in starter_catalog() if e['id'] == starter_id), None)
        if entry is None:
            raise HTTPException(404, "Starter environment not found")
        if not entry['available']:
            raise HTTPException(503, "Starter data is missing; install the complete package")
        return submit('starter', {'starter_id': starter_id})

    @app.get("/api/references")
    def references():
        items = []
        for path in (root / "references").glob("*/reference-manifest.json"):
            try:
                manifest = read_json(path)
                items.append({"id": path.parent.name, "name": path.parent.name.replace("-", " "),
                              "counts": manifest.get("counts"), "repository": manifest.get("repository")})
            except (OSError, ValueError):
                continue
        return items

    @app.get("/api/references/{reference_id}/{filename}")
    def reference_file(reference_id: str, filename: str):
        if not re.fullmatch(r"[a-z0-9-]{1,80}", reference_id) or filename not in {
            "reference-manifest.json", "visual-scene.json", "environment-scene.json", "world.json", "AIRSUP-LICENSE.txt"
        }:
            raise HTTPException(404, "Reference artifact not found")
        try:
            path = inside(root / "references", f"{reference_id}/{filename}")
        except ValueError as exc:
            raise HTTPException(404, "Reference artifact not found") from exc
        if not path.is_file():
            raise HTTPException(404, "Reference artifact not found")
        return FileResponse(path)

    @app.get("/api/worlds/{world_id}")
    def get_world(world_id: str):
        from robot_worlds.sdk import load
        world = load(_world_directory(root, world_id))
        spec = world.spec
        runtime = world.manifest.get('runtime', {})
        compatibility = {'requires_native_revalidation': False}
        if runtime.get('engine') == 'mujoco' and runtime.get('implementation_fingerprint'):
            from robot_worlds.runtime import runtime_implementation_identity
            current = digest(runtime_implementation_identity(spec.robot, runtime.get('action_contract', [])))
            compatibility['requires_native_revalidation'] = current != runtime['implementation_fingerprint']
        return {"spec": spec.model_dump(mode="json"), "manifest": world.manifest,
                'runtime_compatibility': compatibility}

    @app.get("/api/worlds/{world_id}/preview.png")
    def preview(world_id: str):
        path = _world_directory(root, world_id) / "preview.png"
        if not path.exists():
            raise HTTPException(404, "Physical preview is not available for this version")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/worlds/{world_id}/tasks")
    def world_tasks(world_id: str):
        from robot_worlds.sdk import load
        world = load(_world_directory(root, world_id))
        tasks = world.tasks
        return {"world_id": world.id, "tasks": [task.model_dump(mode="json") for task in tasks],
                "count": len(tasks), "selected_task_id": world.selected_task_id}

    @app.post("/api/worlds/{world_id}/tasks/{task_id}/select", status_code=202)
    def select_task(world_id: str, task_id: Identifier, request: RequestModel | None = None):
        from robot_worlds.sdk import load
        world = load(_world_directory(root, world_id))
        if not any(task.id == task_id for task in world.tasks):
            raise HTTPException(422, f"Unknown task ID: {task_id}")
        return submit("task", {"world_id": world_id, "task_id": task_id})

    @app.get("/api/worlds/{world_id}/files/{member:path}")
    def world_asset(world_id: str, member: str):
        folder = _world_directory(root, world_id)
        manifest = read_json(folder / "manifest.json")
        expected = manifest.get("assets", {}).get(member)
        if expected is None:
            raise HTTPException(404, "World asset not found")
        try:
            path = inside(folder, member)
            if file_digest(path) != expected:
                raise HTTPException(409, "World asset changed after validation")
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "World asset not found") from exc
        return FileResponse(path, media_type="application/octet-stream")

    @app.post("/api/generate", status_code=202)
    def generate_job(request: GenerateRequest):
        return submit("generate", generation_payload(request))

    @app.post("/api/worlds/{world_id}/refine", status_code=202)
    def refine_job(world_id: str, request: RefineRequest):
        _world_directory(root, world_id)
        return submit("refine", {"world_id": world_id, **generation_payload(request)})

    @app.post('/api/worlds/{world_id}/revalidate', status_code=202)
    def revalidate_job(world_id: str):
        _world_directory(root, world_id)
        return submit('revalidate', {'world_id': world_id})

    @app.post("/api/worlds/{world_id}/repair", status_code=202)
    def repair_job(world_id: str, request: RepairRequest):
        _world_directory(root, world_id)
        return submit("repair", {"world_id": world_id, **generation_payload(request)})

    @app.post("/api/worlds/{world_id}/rubric", status_code=202)
    def rubric_job(world_id: str, request: RubricRequest):
        _world_directory(root, world_id)
        from robot_worlds.models import Criterion
        try:
            for criterion in request.rubric:
                Criterion.model_validate(criterion)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return submit("rubric", {"world_id": world_id, **request.model_dump()})

    @app.post("/api/worlds/{world_id}/evaluate", status_code=202)
    def evaluate_job(world_id: str, request: EvaluateRequest):
        from robot_worlds.sdk import load
        world = load(_world_directory(root, world_id))
        if world.manifest["validation"]["status"] != "ready":
            raise HTTPException(409, "This world needs validation before evaluation")
        return submit("evaluate", {"world_id": world_id, **request.model_dump()})

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        path = root / "jobs" / _identifier(job_id) / "job.json"
        if not path.exists():
            raise HTTPException(404, "Job not found")
        job = read_json(path)
        future = futures.get(job_id)
        if future is not None and future.done() and job["status"] in ("queued", "running"):
            try:
                future.result()
            except Exception as exc:
                job.update(status="failed", error=public_error(exc))
                write_json(path, job)
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = get_job(job_id)
        if job["status"] in ("queued", "running"):
            (root / "jobs" / job_id / "cancel").touch()
            future = futures.get(job_id)
            if future and future.cancel():
                job["status"] = "cancelled"
                write_json(root / "jobs" / job_id / "job.json", job)
        return {"id": job_id, "cancel_requested": True, "status": job["status"]}

    @app.get("/api/runs")
    def runs():
        items = []
        for path in sorted((root / "runs").glob("*/result.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                result = read_json(path)
                items.append({key: result[key] for key in (
                    "id", "status", "world_id", "world_name", "name", "created_at", "summary"
                ) if key in result})
            except (OSError, ValueError):
                continue
        return items

    def run_folder(run_id):
        path = root / "runs" / _identifier(run_id)
        if not (path / "result.json").exists():
            raise HTTPException(404, "Run not found")
        return path

    @app.get("/api/runs/{run_id}")
    def run_result(run_id: str):
        return read_json(run_folder(run_id) / "result.json")

    @app.get("/api/runs/{run_id}/report")
    def report(run_id: str):
        path = run_folder(run_id) / "report.html"
        if not path.exists():
            raise HTTPException(409, "Report is still being written")
        return FileResponse(path, media_type="text/html")

    @app.get("/api/runs/{run_id}/files/{member:path}")
    def run_file(run_id: str, member: str):
        try:
            path = inside(run_folder(run_id), member)
        except ValueError as exc:
            raise HTTPException(404, "Artifact not found") from exc
        if not path.is_file():
            raise HTTPException(404, "Artifact not found")
        if path.suffix == ".gz":
            return FileResponse(path, media_type="application/gzip", filename=path.name)
        return FileResponse(path)

    @app.get("/api/worlds/{world_id}/export")
    def export_world(world_id: str):
        from robot_worlds.sdk import load
        world = load(_world_directory(root, world_id))
        exported = world.export(root / "exports" / f"world-{world.id[:20]}.zip")
        return FileResponse(exported, filename=exported.name)

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str):
        from robot_worlds.sdk import Report
        exported = Report(run_folder(run_id)).export(root / "exports" / f"run-{run_id}.zip")
        return FileResponse(exported, filename=exported.name)

    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app
