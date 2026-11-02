"""Reviewed upstream environments, installed as immutable world versions.

Release preparation performs conversion and native validation. Customer selection
only verifies and unpacks the bundled data; it makes no network or model calls.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from .sdk import load

_ROOT = Path(__file__).parent / "assets" / "starters"
_MAX_MEMBERS = 10_000
_MAX_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


def starter_catalog() -> list[dict]:
    entries = json.loads((_ROOT / "catalog.json").read_text())["environments"]
    result, identifiers = [], set()
    for entry in entries:
        identifier = entry["id"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier)
                or identifier in identifiers):
            raise ValueError("Invalid starter catalog identifier")
        identifiers.add(identifier)
        installed = (_ROOT / identifier / "world.zip").is_file()
        hashes = all(isinstance(entry.get(key), str) and _SHA256.fullmatch(entry[key])
                     for key in ("bundle_sha256", "world_id"))
        result.append(dict(entry, available=bool(installed and hashes),
                           preview_available=(_ROOT / identifier / "thumbnail.png").is_file()))
    return result


def _entry(identifier: str) -> dict:
    for entry in starter_catalog():
        if entry["id"] == identifier:
            return entry
    raise ValueError("Unknown starter environment")


def starter_preview(identifier: str) -> Path:
    entry = _entry(identifier)
    path = _ROOT / entry["id"] / "thumbnail.png"
    if not path.is_file():
        raise FileNotFoundError("Starter preview is not installed")
    return path


def _checked_world(path: Path, expected: str):
    if path.is_symlink():
        raise ValueError("Existing starter world must not be a symbolic link")
    world = load(path)
    if world.manifest["validation"]["status"] != "ready" or world.id != expected:
        raise ValueError("Starter lacks matching validated-world evidence")
    return world


def _extract(bundle: zipfile.ZipFile, stage: Path, check) -> None:
    members = bundle.infolist()
    if len(members) > _MAX_MEMBERS or sum(member.file_size for member in members) > _MAX_BYTES:
        raise ValueError("Starter bundle exceeds its extraction budget")
    seen, extracted = set(), 0
    for member in members:
        check()
        # ZIP paths are POSIX paths even on Windows. Reject aliases as well as
        # traversal so two members can never address the same output file.
        raw = member.filename.rstrip("/")
        parts = raw.split("/")
        name = PurePosixPath(raw)
        kind = stat.S_IFMT(member.external_attr >> 16)
        if (not raw or name.is_absolute() or PureWindowsPath(raw).drive
                or any(part in {"", ".", ".."} for part in parts) or "\\" in raw
                or raw in seen or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                or (kind == stat.S_IFDIR and not member.is_dir()) or member.flag_bits & 1):
            raise ValueError("Unsafe starter bundle member")
        seen.add(raw)
        path = stage.joinpath(*name.parts)
        if member.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(member) as source, path.open("xb") as target:
            while block := source.read(_CHUNK_BYTES):
                check()
                extracted += len(block)
                if extracted > _MAX_BYTES:
                    raise ValueError("Starter bundle exceeds its extraction budget")
                target.write(block)


def add_starter(identifier: str, output: str | Path = "worlds", *, on_event=None, cancelled=None):
    """Add a packaged scene without a model key; repeat selection reopens it.

    The original scene's version and validation evidence remain intact. Normal
    evaluation still checks compatibility with the installed native runtime.
    An existing immutable version is verified and reused without extracting again.
    """
    from .generator import GenerationCancelled

    entry = _entry(identifier)
    if not entry["available"]:
        raise FileNotFoundError("Starter data is missing. Install the complete Robot Worlds package.")
    emit = on_event or (lambda event: None)

    def check():
        if cancelled and cancelled():
            raise GenerationCancelled("Starter import cancelled")

    def completed(world):
        check()
        emit({"type": "world_created", "world_id": world.id,
              "validation": world.manifest["validation"],
              "message": f"{entry['name']} is available in your workspace"})
        return world

    check()
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / entry["world_id"][:20]
    if destination.exists() or destination.is_symlink():
        return completed(_checked_world(destination, entry["world_id"]))
    archive = _ROOT / identifier / "world.zip"
    emit({"type": "starter_import", "message": f"Verifying {entry['name']} source bundle"})
    stage = None
    try:
        # Keep one descriptor for verification and extraction: a package update
        # cannot replace the path between those two operations.
        with archive.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > _MAX_ARCHIVE_BYTES:
                raise ValueError("Starter bundle exceeds its extraction budget")
            checksum = hashlib.sha256()
            while block := stream.read(_CHUNK_BYTES):
                check()
                checksum.update(block)
            if checksum.hexdigest() != entry["bundle_sha256"]:
                raise ValueError("Starter bundle checksum mismatch; reinstall the package")
            stream.seek(0)
            stage = Path(tempfile.mkdtemp(prefix=".starter-", dir=root))
            with zipfile.ZipFile(stream) as bundle:
                _extract(bundle, stage, check)
        _checked_world(stage, entry["world_id"])
        check()
        try:
            os.rename(stage, destination)
        except OSError as error:
            # Another import of the same immutable version may have won the
            # rename. Never treat an unrelated filesystem error as a success.
            if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
        return completed(_checked_world(destination, entry["world_id"]))
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
