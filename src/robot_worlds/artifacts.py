"""Portable, content-addressed artifacts and atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, allow_nan=False).encode() + b"\n"
    fd, temp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


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
