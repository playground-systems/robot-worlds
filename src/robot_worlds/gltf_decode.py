"""Bounded, cached meshopt decompression; does not import or certify physics."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_CODECS = Path(__file__).parent / "assets/codecs"
_MAX_INPUT = 32 * 1024 * 1024
_MAX_OUTPUT = 128 * 1024 * 1024


class GLBDecodeError(RuntimeError):
    """Source decoding or cache verification failed; no physics claim is made."""


@dataclass(frozen=True)
class DecodedGLB:
    path: Path
    manifest_path: Path
    cache_hit: bool


def _identity():
    provenance = json.loads((_CODECS / "meshopt-source.json").read_text())
    for name in ("meshopt_decoder.mjs", "MESHOPT-LICENSE.md"):
        if hashlib.sha256((_CODECS / name).read_bytes()).hexdigest() != provenance["files"][name]:
            raise GLBDecodeError("Bundled meshopt decoder integrity mismatch")
    return {"profile": 1, "meshoptimizer_version": provenance["version"],
            "decoder_sha256": provenance["files"]["meshopt_decoder.mjs"],
            "worker_sha256": hashlib.sha256((_CODECS / "decode-glb.mjs").read_bytes()).hexdigest()}


def decode_glb(source: str | Path, cache_dir: str | Path, *, timeout=30,
               cancelled=None) -> DecodedGLB:
    """Decode a self-contained GLB once; keep textures, node frames and metadata.

Cold decoding requires Node.js (20+ recommended); verified cache reuse does not.
The worker is isolated from model credentials and has a finite deadline. It does
not perform mesh simplification, axis conversion, collision preparation or native
rendering. The returned GLB is an intermediate asset, not WorldSpec geometry.
"""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("GLB decode timeout must be finite and in (0, 120] seconds")
    deadline = time.monotonic() + timeout

    def check():
        if cancelled and cancelled():
            raise GLBDecodeError("GLB decoding cancelled")
        if time.monotonic() >= deadline:
            raise GLBDecodeError("GLB decoding exceeded its time budget")

    check()
    source = Path(source)
    if not source.is_file() or source.stat().st_size > _MAX_INPUT:
        raise GLBDecodeError("GLB source missing or exceeds 32 MiB")
    with source.open("rb") as stream:
        raw = stream.read(_MAX_INPUT + 1)
    if (not 28 <= len(raw) <= _MAX_INPUT or raw[:4] != b"glTF"
            or struct.unpack_from("<II", raw, 4) != (2, len(raw))):
        raise GLBDecodeError("Invalid GLB source header or size")
    identity = {**_identity(), "source_sha256": hashlib.sha256(raw).hexdigest()}
    root = Path(cache_dir).resolve()
    check()
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    target = root / key

    def verify(directory, cache_hit):
        check()
        if (directory.is_symlink() or not directory.is_dir()
                or {p.name for p in directory.iterdir()} != {"model.glb", "manifest.json"}):
            raise GLBDecodeError("Decoded GLB cache is incomplete or corrupt")
        output, manifest = directory / "model.glb", directory / "manifest.json"
        if (output.is_symlink() or manifest.is_symlink() or not output.is_file()
                or not manifest.is_file() or manifest.stat().st_size > 65536):
            raise GLBDecodeError("Decoded GLB cache contains unsafe files")
        try:
            record = json.loads(manifest.read_text())
            if record["identity"] != identity or not 28 <= record["bytes"] <= _MAX_OUTPUT:
                raise ValueError("identity/size")
            if output.stat().st_size != record["bytes"]:
                raise ValueError("size")
            digest = hashlib.sha256()
            count = 0
            with output.open("rb") as stream:
                while chunk := stream.read(65536):
                    check()
                    count += len(chunk)
                    if count > _MAX_OUTPUT:
                        raise ValueError("size")
                    digest.update(chunk)
            if count != record["bytes"] or digest.hexdigest() != record["sha256"]:
                raise ValueError("digest")
        except (KeyError, TypeError, ValueError) as error:
            raise GLBDecodeError("Decoded GLB cache failed integrity verification") from error
        return DecodedGLB(output, manifest, cache_hit)

    if target.exists() or target.is_symlink():
        return verify(target, True)
    node = shutil.which("node")
    if not node:
        raise GLBDecodeError("First-time GLB decoding requires Node.js; install Node.js 20+ or reuse a prepared cache")
    with tempfile.TemporaryDirectory(prefix=".decode-", dir=root) as scratch:
        scratch = Path(scratch)
        snapshot = scratch / "source.glb"
        snapshot.write_bytes(raw)
        staging = scratch / "decoded"
        staging.mkdir()
        output = staging / "model.glb"
        env = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP"}}
        check()
        process = subprocess.Popen([node, "--max-old-space-size=192", "--no-addons",
            str(_CODECS / "decode-glb.mjs"), str(snapshot), str(output)],
            env=env, cwd=scratch, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            while process.poll() is None:
                check()
                try:
                    process.wait(timeout=min(.05, max(.001, deadline-time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            check()
            if process.returncode:
                raise GLBDecodeError("GLB decoder rejected the source or exceeded worker resources")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        if not output.is_file() or not 28 <= output.stat().st_size <= _MAX_OUTPUT:
            raise GLBDecodeError("Decoded GLB exceeds output limits or is missing")
        # The bounded worker output is hashed before publication; original bytes
        # remain untouched. Cache identity also binds the decoding implementation.
        sha = hashlib.sha256()
        with output.open("rb") as stream:
            while chunk := stream.read(65536):
                check()
                sha.update(chunk)
        record = {"schema_version": 1, "identity": identity, "sha256": sha.hexdigest(),
                  "bytes": output.stat().st_size,
                  "status": "decompressed_only_not_physics_validated"}
        (staging / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        verify(staging, False)
        check()
        try:
            staging.rename(target)
        except OSError:
            if target.exists() or target.is_symlink():
                return verify(target, True)
            raise
        return verify(target, False)
