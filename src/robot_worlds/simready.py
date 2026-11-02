"""Small pinned SimReady source catalog and verified demand-driven acquisition.

Downloaded GLBs are source assets, not ready WorldSpec geometry. In particular,
their convex-hull physics must not silently fill shelves or other openings.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .asset_download import AssetDownloadError
from .asset_sourcing import AssetCandidate, AssetSearch

_CATALOG = Path(__file__).parent / "assets/simready/catalog.json"
_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class SimReadySource:
    glb_path: Path
    notice_path: Path
    metadata_path: Path
    cache_hit: bool


def _catalog():
    return json.loads(_CATALOG.read_text())


def simready_asset_catalog() -> list[AssetCandidate]:
    """Return reviewed source candidates, not a list of simulation-ready assets."""
    catalog = _catalog()
    return [AssetCandidate(
        id="simready:" + row["asset_id"], source_url=row["optimized_url"],
        revision=catalog["optimized_revision"], license="CC-BY-4.0",
        metadata={**copy.deepcopy(row), "upstream_checked_revision": catalog["upstream_checked_revision"],
                  "derivation_upstream_revision": catalog["optimized_derivation_upstream_revision"],
                  "compatibility": "source_only_requires_conversion_and_physical_validation"},
    ) for row in catalog["selected"]]


def simready_asset_search(asset_id: str) -> AssetSearch:
    """Exact lookup in the reviewed subset; unknown IDs are unresolved, not absent."""
    candidates = [item for item in simready_asset_catalog() if item.id == "simready:" + asset_id]
    if len(candidates) != 1:
        raise ValueError("Unknown reviewed SimReady asset ID")
    return AssetSearch(query=asset_id,
                       sources=("nvidia/simready-assets", "codepawl/simready-glb"),
                       candidates=tuple(candidates), completed=True,
                       evidence={"scope": "exact asset in reviewed subset; not an exhaustive online search"})


def _allowed_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment
            and (host == "huggingface.co" or host.endswith(".huggingface.co") or host.endswith(".hf.co")))


def download_simready_asset(asset_id: str, cache_dir: str | Path, *,
                           client: httpx.Client | None = None, timeout: float = 60,
                           cancelled=None) -> SimReadySource:
    """Acquire one original optimized GLB, with attribution and source evidence.

The fixed catalog binds every file to a revision, exact length and SHA-256.
Cache hits rehash bytes. No decoder, renderer, native engine or model is invoked.
Corrupt entries fail without redownload or procedural fallback.
"""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("Asset timeout must be finite and in (0, 600] seconds")
    selected = simready_asset_search(asset_id).candidates[0]
    record = selected.metadata
    expected, size = record["download_sha256"], record["download_bytes"]
    if (not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= _MAX_BYTES
            or not isinstance(expected, str) or len(expected) != 64
            or any(c not in "0123456789abcdef" for c in expected)
            or not _allowed_url(selected.source_url)):
        raise AssetDownloadError("Invalid pinned SimReady catalog entry")
    started = time.monotonic()

    def check():
        if cancelled and cancelled():
            raise AssetDownloadError("SimReady download cancelled")
        if time.monotonic() - started >= timeout:
            raise AssetDownloadError("SimReady download exceeded its time budget")

    check()
    notice = (f"# {asset_id}\n\nOriginal models: NVIDIA SimReady.\n"
              "License: CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/\n"
              "Optimized GLB conversion: CodePawl (meshopt geometry and WebP textures).\n"
              f"Source: {selected.source_url}\n"
              f"Upstream package/license: {record['upstream_license_url']}\n"
              "Robot Worlds acquisition preserves these GLB bytes unchanged.\n"
              "No conversion or physical compatibility is certified by this download.\n")
    metadata = {"schema_version": 1, "source_url": selected.source_url,
                "revision": selected.revision, "license": selected.license,
                "source": record, "status": "source_only_not_simulation_ready"}
    metadata_bytes = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode()
    notice_bytes = notice.encode()
    identity = hashlib.sha256(metadata_bytes + notice_bytes).hexdigest()
    root = Path(cache_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / identity

    def verify(directory, cache_hit):
        check()
        names = {"model.glb", "NOTICE.md", "source.json"}
        if (directory.is_symlink() or not directory.is_dir()
                or {p.name for p in directory.iterdir()} != names):
            raise AssetDownloadError("SimReady cache is incomplete or corrupt")
        for name in names:
            path = directory / name
            limit = size if name == "model.glb" else len(notice_bytes if name == "NOTICE.md" else metadata_bytes)
            if path.is_symlink() or not path.is_file() or path.stat().st_size != limit:
                raise AssetDownloadError("SimReady cache contains a changed or unsafe file")
        digest = hashlib.sha256()
        count = 0
        with (directory / "model.glb").open("rb") as stream:
            while chunk := stream.read(65536):
                check()
                count += len(chunk)
                if count > size:
                    raise AssetDownloadError("SimReady cache exceeds its pinned size")
                digest.update(chunk)
        if (count != size or digest.hexdigest() != expected
                or (directory / "NOTICE.md").read_bytes() != notice_bytes
                or (directory / "source.json").read_bytes() != metadata_bytes):
            raise AssetDownloadError("SimReady cache failed source integrity verification")
        return SimReadySource(directory / "model.glb", directory / "NOTICE.md",
                              directory / "source.json", cache_hit)

    if target.exists() or target.is_symlink():
        return verify(target, True)
    owned = client is None
    client = client or httpx.Client(follow_redirects=False, trust_env=False)
    try:
        with tempfile.TemporaryDirectory(prefix=".simready-", dir=root) as scratch:
            staging = Path(scratch) / "source"
            staging.mkdir()
            url = selected.source_url
            for redirect in range(6):
                check()
                # Do not inherit an injected client's automatic redirect policy.
                with client.stream("GET", url, follow_redirects=False,
                                   timeout=max(.001, timeout - (time.monotonic() - started))) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "")
                        url = urljoin(url, location)
                        if not location or redirect == 5 or not _allowed_url(url):
                            raise AssetDownloadError("SimReady source returned an unsupported redirect")
                        continue
                    if response.status_code != 200:
                        raise AssetDownloadError(f"SimReady source returned HTTP {response.status_code}")
                    length = response.headers.get("content-length")
                    if length is not None and length != str(size):
                        raise AssetDownloadError("SimReady source length differs from pinned size")
                    count = 0
                    with (staging / "model.glb").open("xb") as output:
                        for chunk in response.iter_bytes():
                            check()
                            count += len(chunk)
                            if count > size:
                                raise AssetDownloadError("SimReady source exceeds pinned size")
                            output.write(chunk)
                    break
            (staging / "NOTICE.md").write_bytes(notice_bytes)
            (staging / "source.json").write_bytes(metadata_bytes)
            verify(staging, False)
            check()
            try:
                staging.rename(target)
            except OSError:
                if target.exists() or target.is_symlink():
                    return verify(target, True)
                raise
            return verify(target, False)
    except httpx.HTTPError as error:
        # Signed CDN query strings and provider exception bodies stay out of logs.
        raise AssetDownloadError(f"SimReady request failed ({type(error).__name__})") from None
    finally:
        if owned:
            client.close()
