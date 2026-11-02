"""Pinned Fuel source downloads with a bounded, verified on-disk cache.

Acquiring a package does not import its physics or establish task compatibility.
Download/cache failures deliberately are not AssetIncompatible exceptions.
"""
from __future__ import annotations

import hashlib
import json
import math
import stat
import tempfile
import time
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from .asset_catalog import FUEL_API
from .asset_sourcing import AssetCandidate

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 65536


class AssetDownloadError(RuntimeError):
    """The source could not be acquired intact; this is not an incompatibility."""


@dataclass(frozen=True)
class DownloadedAsset:
    source_dir: Path
    manifest_path: Path
    archive_sha256: str
    cache_hit: bool


def _identity(candidate: AssetCandidate) -> dict:
    owner, name = (candidate.metadata.get(key) for key in ("owner", "name"))
    for value in (owner, name):
        if (not isinstance(value, str) or not value.strip() or len(value) > 240
                or value in (".", "..") or any(c in value for c in "/\\\x00")
                or any(ord(c) < 32 for c in value)):
            raise ValueError("Fuel owner/name must be nonempty path segments")
    revision = candidate.revision
    if (not isinstance(revision, str) or not revision.isascii() or not revision.isdigit()
            or len(revision) > 12 or int(revision) < 1 or str(int(revision)) != revision):
        raise ValueError("Fuel downloads require a canonical positive integer revision")
    source = f"{FUEL_API}/{quote(owner, safe='')}/models/{quote(name, safe='')}/{revision}"
    download = f"{source}/{quote(name, safe='')}.zip"
    if (candidate.id != f"fuel:{owner}/{name}" or candidate.source_url != source
            or candidate.metadata.get("download_url") != download):
        raise ValueError("Fuel candidate identity does not match its pinned download URL")
    license_url = candidate.metadata.get("license_url")
    if not all(isinstance(v, str) and v.strip() for v in (candidate.license, license_url)):
        raise ValueError("Fuel downloads require license evidence")
    return {"id": candidate.id, "source_url": source, "revision": revision,
            "license": candidate.license, "license_url": license_url, "download_url": download}


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise AssetDownloadError("Fuel asset preparation exceeded its time budget")


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or "\\" in name or "\x00" in name or path.is_absolute()
            or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
            or ":" in name or any(ord(c) < 32 for c in name)):
        raise AssetDownloadError("ZIP contains an unsafe path")
    return path


def _extract(archive: Path, source: Path, deadline: float) -> dict:
    records, seen, components, total = {}, set(), {}, 0
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if not entries or len(entries) > MAX_ENTRIES:
            raise AssetDownloadError("ZIP entry count exceeds the supported limits")
        for entry in entries:
            _check_deadline(deadline)
            path = _member_path(entry.orig_filename)
            # Also reject case aliases on case-insensitive customer filesystems.
            identity = unicodedata.normalize("NFC", str(path)).casefold()
            if identity in seen:
                raise AssetDownloadError("ZIP contains duplicate paths")
            seen.add(identity)
            for length in range(1, len(path.parts) + 1):
                component = PurePosixPath(*path.parts[:length])
                key = unicodedata.normalize("NFC", str(component)).casefold()
                value = (str(component), length < len(path.parts) or entry.is_dir())
                if key in components and components[key] != value:
                    raise AssetDownloadError("ZIP contains conflicting paths")
                components[key] = value
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG):
                raise AssetDownloadError("ZIP contains a link or special file")
            if entry.flag_bits & 1:
                raise AssetDownloadError("Encrypted ZIP entries are unsupported")
            if entry.file_size > MAX_FILE_BYTES or total + entry.file_size > MAX_EXTRACTED_BYTES:
                raise AssetDownloadError("ZIP exceeds extracted size limits")
            target = source.joinpath(*path.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, count = hashlib.sha256(), 0
            with package.open(entry) as incoming, target.open("xb") as outgoing:
                while chunk := incoming.read(CHUNK_BYTES):
                    _check_deadline(deadline)
                    count += len(chunk)
                    total += len(chunk)
                    if count > MAX_FILE_BYTES or total > MAX_EXTRACTED_BYTES:
                        raise AssetDownloadError("ZIP exceeds extracted size limits")
                    digest.update(chunk)
                    outgoing.write(chunk)
            records[str(path)] = {"sha256": digest.hexdigest(), "bytes": count}
    if not records:
        raise AssetDownloadError("ZIP has no source files")
    return records


def _cached(directory: Path, identity: dict, deadline: float) -> DownloadedAsset:
    manifest_path, source = directory / "manifest.json", directory / "source"
    if (directory.is_symlink() or manifest_path.is_symlink() or source.is_symlink()
            or not manifest_path.is_file() or not source.is_dir()
            or manifest_path.stat().st_size > MAX_MANIFEST_BYTES):
        raise AssetDownloadError("Asset cache is incomplete or corrupt; remove this cache entry and retry")
    manifest = json.loads(manifest_path.read_text())
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("candidate") != identity):
        raise AssetDownloadError("Asset cache provenance does not match the pinned candidate")
    records = manifest.get("files")
    archive_sha = manifest.get("archive_sha256")
    if (not isinstance(records, dict) or not 1 <= len(records) <= MAX_ENTRIES
            or not isinstance(archive_sha, str) or len(archive_sha) != 64
            or any(c not in "0123456789abcdef" for c in archive_sha)):
        raise AssetDownloadError("Asset cache manifest is invalid")
    actual, total = {}, 0
    for path in source.rglob("*"):
        _check_deadline(deadline)
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise AssetDownloadError("Asset cache contains a link or special file")
        if path.is_dir():
            continue
        if len(actual) >= MAX_ENTRIES or path.stat().st_size > MAX_FILE_BYTES:
            raise AssetDownloadError("Asset cache exceeds its size limits")
        digest, count = hashlib.sha256(), 0
        with path.open("rb") as incoming:
            while chunk := incoming.read(CHUNK_BYTES):
                _check_deadline(deadline)
                count += len(chunk)
                total += len(chunk)
                if count > MAX_FILE_BYTES or total > MAX_EXTRACTED_BYTES:
                    raise AssetDownloadError("Asset cache exceeds its size limits")
                digest.update(chunk)
        actual[path.relative_to(source).as_posix()] = {"sha256": digest.hexdigest(), "bytes": count}
    if actual != records:
        raise AssetDownloadError("Asset cache integrity check failed; remove this cache entry and retry")
    return DownloadedAsset(source, manifest_path, archive_sha, True)


def download_fuel_asset(candidate: AssetCandidate, cache_dir: str | Path, *,
                        client: httpx.Client | None = None, timeout: float = 60) -> DownloadedAsset:
    """Download a pinned search result once and reuse verified source bytes.

    Plain HTTPS Fuel ZIPs only; redirects/referrals are not followed. Limits are
    64 MiB downloaded, 256 MiB extracted, 32 MiB/file and 2048 ZIP entries.
    Nothing is executed or converted. Callers still check license and physical
    compatibility in their source_asset import adapter. A corrupt cache fails
    explicitly instead of silently trusting or replacing customer-modified files.
    """
    identity = _identity(candidate)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 300):
        raise ValueError("timeout must be positive and at most 300 seconds")
    deadline = time.monotonic() + timeout
    cache = Path(cache_dir)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    destination = cache / key
    try:
        if destination.exists() or destination.is_symlink():
            return _cached(destination, identity, deadline)
        cache.mkdir(parents=True, exist_ok=True)
        if client is None:
            with httpx.Client(follow_redirects=False) as owned:
                _check_deadline(deadline)
                return download_fuel_asset(candidate, cache, client=owned,
                                           timeout=deadline - time.monotonic())
        with tempfile.TemporaryDirectory(prefix=".fuel-", dir=cache) as temporary:
            staging = Path(temporary)
            archive, prepared = staging / "download.zip", staging / "prepared"
            source = prepared / "source"
            source.mkdir(parents=True)
            digest, count = hashlib.sha256(), 0
            _check_deadline(deadline)
            with client.stream("GET", identity["download_url"], follow_redirects=False,
                               timeout=min(10, deadline - time.monotonic())) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise AssetDownloadError("Fuel must return a complete ZIP response")
                if response.headers.get("content-type", "").split(";")[0].strip() not in (
                        "application/zip", "application/octet-stream", "application/x-zip-compressed"):
                    raise AssetDownloadError("Fuel returned an unsupported referral or non-ZIP response")
                version = response.headers.get("X-Ign-Resource-Version")
                if version is not None and version != candidate.revision:
                    raise AssetDownloadError("Fuel response revision does not match the requested version")
                with archive.open("xb") as outgoing:
                    for chunk in response.iter_bytes(chunk_size=CHUNK_BYTES):
                        _check_deadline(deadline)
                        count += len(chunk)
                        if count > MAX_ARCHIVE_BYTES:
                            raise AssetDownloadError("Fuel archive exceeds the download size limit")
                        digest.update(chunk)
                        outgoing.write(chunk)
            files = _extract(archive, source, deadline)
            manifest = {"schema_version": 1, "candidate": identity, "response_revision": version,
                        "archive_sha256": digest.hexdigest(), "archive_bytes": count, "files": files}
            (prepared / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            _check_deadline(deadline)
            try:
                prepared.rename(destination)
            except OSError:
                if destination.exists():
                    return _cached(destination, identity, deadline)
                raise
            return DownloadedAsset(destination / "source", destination / "manifest.json",
                                   digest.hexdigest(), False)
    except AssetDownloadError:
        raise
    except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as error:
        raise AssetDownloadError(f"Could not acquire the pinned Fuel source: {error}") from error
