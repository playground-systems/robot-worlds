"""Bounded optional collision preparation with immutable, verified cache entries.

No native converter is imported into the calling process. A per-cache OS lock
serializes workers; published results can be reused without installing CoACD.
"""
from __future__ import annotations

import errno
import hashlib
import importlib.metadata
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .artifacts import digest
from .mesh_assets import (
    MAX_CONVEX_COMPONENTS,
    _obj_bytes,
    convex_volume,
    read_triangle_obj,
    verified_convex_obj,
)

COACD_VERSION = "1.0.14"


def conversion_settings():
    return dict(threshold=.002, real_metric=True, preprocess_mode="auto",
                preprocess_resolution=100, resolution=2000, mcts_nodes=20,
                mcts_iterations=100, mcts_max_depth=3, seed=0,
                merge=True, decimate=True, max_ch_vertex=128, max_convex_hull=-1)


class CollisionPreparationError(RuntimeError):
    """Preparation is unresolved; an error is never permission to create an asset."""


class CollisionPreparationCancelled(CollisionPreparationError):
    pass


@dataclass(frozen=True)
class PreparedCollision:
    manifest_path: Path
    source_sha256: str
    parts: int
    cache_hit: bool
    method: str


def _check(deadline, cancelled):
    if cancelled and cancelled():
        raise CollisionPreparationCancelled("Collision preparation cancelled")
    if time.monotonic() >= deadline:
        raise CollisionPreparationError("Collision preparation exceeded its time budget")


@contextmanager
def _cache_lock(cache, deadline, cancelled, emit):
    with (cache / ".preparation.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
        else:
            import fcntl
        waiting = False
        while True:
            _check(deadline, cancelled)
            try:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK}:
                    raise
                if not waiting:
                    emit({"type": "asset_preparation_waiting", "message": "Waiting for the current asset preparation to finish"})
                    waiting = True
                time.sleep(.05)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _verify(directory, identity, bounds, deadline, cancelled, *, cache_hit):
    path = directory / "manifest.json"
    if directory.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise CollisionPreparationError("Collision cache is incomplete or corrupt")
    manifest = json.loads(path.read_text())
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("preparation") != identity or manifest.get("source_sha256") != identity["source_sha256"]):
        raise CollisionPreparationError("Collision cache source/profile does not match")
    method = manifest.get("tool")
    if method == "CoACD":
        if manifest.get("tool_version") != COACD_VERSION or manifest.get("settings") != conversion_settings():
            raise CollisionPreparationError("Collision cache converter settings do not match")
    elif method != "verified-source" or manifest.get("tool_version") != "1" or manifest.get("settings") != {}:
        raise CollisionPreparationError("Unknown collision preparation method")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_CONVEX_COMPONENTS or not all(isinstance(part, dict) for part in parts):
        raise CollisionPreparationError("Invalid collision cache component count")
    if method == "verified-source" and (len(parts) != 1 or parts[0].get("sha256") != identity["source_sha256"]):
        raise CollisionPreparationError("Verified-source cache must retain the unchanged convex mesh")
    names, total = set(), 0
    for part in parts:
        _check(deadline, cancelled)
        if not isinstance(part, dict):
            raise CollisionPreparationError("Invalid collision cache component record")
        name = part.get("file")
        if (not isinstance(name, str) or Path(name).name != name or "\\" in name
                or not name.endswith(".obj") or name in names):
            raise CollisionPreparationError("Collision component paths must be unique local OBJ filenames")
        names.add(name)
        component = directory / name
        if component.is_symlink() or not component.is_file():
            raise CollisionPreparationError("Collision cache contains a missing/link component")
        total += component.stat().st_size
        if total > 32 * 1024 * 1024:
            raise CollisionPreparationError("Collision cache exceeds its 32 MiB output limit")
        low, high, volume = verified_convex_obj(component, part.get("sha256"))
        if np.any(low < bounds[0]-.005) or np.any(high > bounds[1]+.005):
            raise CollisionPreparationError("Prepared geometry expands over 5 mm beyond source bounds")
        if not math.isclose(volume, part.get("volume_m3", -1), rel_tol=1e-6, abs_tol=1e-12):
            raise CollisionPreparationError("Collision cache volume does not match its geometry")
    if {entry.name for entry in directory.iterdir()} != names | {"manifest.json"}:
        raise CollisionPreparationError("Collision cache contains unrecorded files")
    _check(deadline, cancelled)
    return PreparedCollision(path.resolve(), identity["source_sha256"], len(parts), cache_hit, method)


def _installed_converter():
    try:
        version = importlib.metadata.version("coacd")
    except importlib.metadata.PackageNotFoundError as error:
        raise CollisionPreparationError("New concave assets need the preparation extra (coacd==1.0.14); run uv sync --extra preparation in the checkout") from error
    if version != COACD_VERSION:
        raise CollisionPreparationError(f"Collision preparation requires coacd=={COACD_VERSION}, found {version}")


def _stop_worker(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=.5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=1)


def _run_worker(source, output, deadline, cancelled):
    _check(deadline, cancelled)
    env = dict(os.environ)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = "1"
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "ROBOT_WORLDS_API_TOKEN"):
        env.pop(name, None)
    process = subprocess.Popen([sys.executable, "-m", "robot_worlds.collision_worker", str(source), str(output)],
                               env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=os.name == "posix")
    try:
        while process.poll() is None:
            _check(deadline, cancelled)
            try:
                process.wait(timeout=min(.1, max(.001, deadline-time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        _check(deadline, cancelled)
        if process.returncode:
            report = output.parent / "worker-error.json"
            detail = ""
            if report.is_file() and report.stat().st_size <= 4096:
                detail = ": " + report.read_text()
            raise CollisionPreparationError(f"Collision worker exited with code {process.returncode}{detail}")
    finally:
        _stop_worker(process)


def _write_manifest(output, source_hash, records, method):
    manifest = {"schema_version": 1, "source_sha256": source_hash, "tool": method,
                "tool_version": COACD_VERSION if method == "CoACD" else "1",
                "settings": conversion_settings() if method == "CoACD" else {}, "parts": records,
                "limitations": (["Approximate convex collision decomposition; original visuals are unchanged.",
                                 "The 2 mm concavity setting is a converter parameter, not a certified surface error."]
                                if method == "CoACD" else ["Original source was verified closed and convex; collision bytes are unchanged."])
                               + ["Native cavity/contact checks remain required for the intended task."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def _native_worker(source, output):
    # Thread limits are set before this interpreter starts. Import the optional
    # native extension only here, never in the SDK/server calling process.
    if os.name == "posix":
        os.nice(10)
    _installed_converter()
    import coacd
    vertices, faces = read_triangle_obj(source)
    parts = coacd.run_coacd(coacd.Mesh(vertices, faces), **conversion_settings())
    if not 1 <= len(parts) <= MAX_CONVEX_COMPONENTS:
        raise CollisionPreparationError("Converter exceeded its component budget")
    records = []
    output.mkdir()
    for index, (points, triangles) in enumerate(parts):
        if len(points) > 128 or len(triangles) > 256:
            raise CollisionPreparationError("Converter exceeded its per-component geometry budget")
        volume = convex_volume(points, triangles)
        filename = f"part-{index:03}.obj"
        text = "# Derived collision geometry; original visual file is unchanged.\n"
        text += "".join("v " + " ".join(f"{float(x):.17g}" for x in row) + "\n" for row in points)
        text += "".join("f " + " ".join(str(int(x)+1) for x in row) + "\n" for row in triangles)
        data = text.encode()
        (output / filename).write_bytes(data)
        records.append({"file": filename, "sha256": hashlib.sha256(data).hexdigest(),
                        "vertices": len(points), "triangles": len(triangles), "volume_m3": volume})
    _write_manifest(output, hashlib.sha256(_obj_bytes(source)).hexdigest(), records, "CoACD")


def prepare_collision_mesh(source: str | Path, cache_dir: str | Path, *, timeout=120,
                            cancelled=None, on_event=None) -> PreparedCollision:
    """Prepare once per source hash/profile, then reuse checked collision files.

    Closed convex sources need no CoACD installation or worker. New concave/open
    sources require the preparation extra. A single-thread, low-priority worker
    is cancellable with a maximum 600-second deadline, plus up to 1.5 s for cleanup.
    A per-cache lock prevents duplicate/concurrent conversion work. No partial
    result is published and a corrupt existing cache fails instead of being used.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("Preparation timeout must be positive and at most 600 seconds")
    deadline = time.monotonic() + timeout
    emit = on_event or (lambda event: None)
    _check(deadline, cancelled)
    try:
        data = _obj_bytes(source)
        source_hash = hashlib.sha256(data).hexdigest()
        identity = {"source_sha256": source_hash, "version": 1, "converter": "CoACD", "converter_version": COACD_VERSION,
                    "settings": conversion_settings(), "worker_threads": 1}
        cache = Path(cache_dir).resolve()
        cache.mkdir(parents=True, exist_ok=True)
        destination = cache / digest(identity)
        with tempfile.TemporaryDirectory(prefix=".preparation-", dir=cache) as temporary:
            stage = Path(temporary)
            snapshot = stage / "source.obj"
            snapshot.write_bytes(data)
            del data
            vertices, faces = read_triangle_obj(snapshot)
            bounds = (vertices.min(0), vertices.max(0))
            if destination.exists() or destination.is_symlink():
                result = _verify(destination, identity, bounds, deadline, cancelled, cache_hit=True)
                emit({"type": "asset_preparation_reused", "source_sha256": source_hash,
                      "components": result.parts, "message": "Reused verified collision preparation"})
                return result
            with _cache_lock(cache, deadline, cancelled, emit):
                if destination.exists() or destination.is_symlink():
                    result = _verify(destination, identity, bounds, deadline, cancelled, cache_hit=True)
                    emit({"type": "asset_preparation_reused", "source_sha256": source_hash,
                          "components": result.parts, "message": "Reused verified collision preparation"})
                    return result
                output = stage / "collision"
                try:
                    volume = convex_volume(vertices, faces)
                except ValueError:
                    _installed_converter()
                    emit({"type": "asset_preparation_started", "source_sha256": source_hash,
                          "message": "Preparing source collision geometry in a bounded worker"})
                    _run_worker(snapshot, output, deadline, cancelled)
                else:
                    output.mkdir()
                    unchanged = output / "part-000.obj"
                    unchanged.write_bytes(_obj_bytes(snapshot))
                    records = [{"file": unchanged.name, "sha256": source_hash, "vertices": len(vertices),
                                "triangles": len(faces), "volume_m3": volume}]
                    _write_manifest(output, source_hash, records, "verified-source")
                manifest_path = output / "manifest.json"
                if not manifest_path.is_file() or manifest_path.stat().st_size > 2 * 1024 * 1024:
                    raise CollisionPreparationError("Worker returned no bounded collision manifest")
                manifest = json.loads(manifest_path.read_text())
                manifest["preparation"] = identity
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
                result = _verify(output, identity, bounds, deadline, cancelled, cache_hit=False)
                output.rename(destination)
                result = PreparedCollision(destination / "manifest.json", source_hash, result.parts, False, result.method)
                emit({"type": "asset_preparation_finished", "source_sha256": source_hash,
                      "components": result.parts, "method": result.method, "message": "Collision geometry prepared and verified"})
                return result
    except CollisionPreparationError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise CollisionPreparationError(f"Collision preparation failed: {error}") from error

