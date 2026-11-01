"""Portable, content-addressed artifacts and atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    options = {"separators": (",", ":")} if compact else {"indent": 2}
    payload = json.dumps(value, allow_nan=False, **options).encode()
    fd, temp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


class _TraceCheckpoint:
    """Atomic JSON-array checkpoints for owned, append-only native snapshots.

    Copy the previous encoded prefix and encode only new samples. Readers still
    see the ordinary complete trace.json array, including after interruption.
    Custom runtime adapters must use write_json because they may mutate history.
    """
    def __init__(self, path: Path):
        self.path = path
        self.frames = []
        self.initialized = False

    def write(self, trace):
        unchanged_prefix = len(trace) >= len(self.frames) and all(a is b for a, b in zip(self.frames, trace))
        if not self.initialized or not unchanged_prefix:
            write_json(self.path, trace, compact=True)
        elif len(trace) == len(self.frames):
            return
        else:
            # Serialize before touching the previous durable checkpoint. A bad
            # sample (e.g. NaN) must leave the old valid JSON and cursor intact.
            payload = json.dumps(trace[len(self.frames):], separators=(",", ":"), allow_nan=False).encode()[1:-1]
            fd, temporary = tempfile.mkstemp(prefix=".writing-trace-", dir=self.path.parent)
            os.close(fd)
            try:
                shutil.copyfile(self.path, temporary)
                with open(temporary, "r+b") as stream:
                    stream.seek(-2, os.SEEK_END)
                    if stream.read(2) != b"]\n":
                        raise ValueError("Unexpected trace checkpoint encoding")
                    stream.seek(-2, os.SEEK_END)
                    if self.frames:
                        stream.write(b",")
                    stream.write(payload)
                    stream.write(b"]\n")
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        self.frames = list(trace)
        self.initialized = True


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def public_error(exc: Exception) -> dict:
    message = str(exc)
    # Provider errors are useful, but credentials must never enter saved artifacts.
    for key in ("OPENAI_API_KEY", "ROBOT_WORLDS_API_TOKEN"):
        secret = os.environ.get(key)
        if secret:
            message = message.replace(secret, "[redacted]")
    return {"type": type(exc).__name__, "message": message[:2000]}


def package_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    versions = {}
    for name in ("robot-worlds", "mujoco", "numpy", "openai", "pydantic"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "uninstalled"
    return versions


def inside(root: Path, relative: str) -> Path:
    """Resolve a bundle member without allowing directory escape or symlinks out."""
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"Artifact path is outside its bundle: {relative}")
    return candidate
