#!/usr/bin/env python3
"""Acquire Astra's selected source bytes; never adapt geometry or certify physics.

Uses the standard library, a 40 MB cumulative network budget, pinned metadata and
unique same-parent temporary files. Meshopt geometry remains compressed: this
step inspects the original GLB JSON/hierarchy, not decoded vertex clearances.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SELECTION = REPO / "docs/factory-v3-source-selection-2026-11-02.json"
DEFAULT_OUTPUT = REPO / ".robot-worlds/factory-v3-sources-2026-11-02"
CANDIDATES = REPO / ".robot-worlds/factory-candidate-research-2026-11-02"
CATALOG = REPO / ".robot-worlds/living-room-preparation/optimized-manifest.jsonl"
CATALOG_REVISION = "e52172290393dde80e8d383861db94b8d0e92ec3"
CATALOG_SHA256 = "a3801ee46efbfb22fd78ff477023f730b5a1c523fdc02dc152c5c3fb9615f3fd"
NETWORK_CAP = 40_000_000
META_LIMIT = 256_000
MAX_JSON = 2_000_000


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            value.update(chunk)
    return value.hexdigest()


def safe_path(root, relative):
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in {".", ".."} for p in parts):
        raise ValueError("Unsafe cache path")
    target = root.joinpath(*parts)
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Source cache contains a symlink")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Source cache escaped its checked parent")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def atomic_bytes(path, data, *, replace=False):
    if path.is_symlink():
        raise ValueError("Source output is a symlink")
    if path.exists() and not replace:
        if not path.is_file() or path.read_bytes() != data:
            raise ValueError(f"Source cache changed: {path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".source-", delete=False) as staged:
        temporary = Path(staged.name)
        try:
            staged.write(data)
            staged.flush()
            os.fsync(staged.fileno())
            if path.is_symlink():
                raise ValueError("Source output became a symlink")
            if path.exists() and not replace:
                if path.read_bytes() != data:
                    raise ValueError("Source output changed during publication")
            else:
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def encoded(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()


def allowed_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment
            and (host == "huggingface.co" or host.endswith(".huggingface.co")
                 or host.endswith(".hf.co")))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError("Unapproved source redirect")
        return super().redirect_request(request, fp, code, message, headers, newurl)


class Acquisition:
    def __init__(self, root):
        self.root = root
        self.ledger_path = safe_path(root, "download-ledger.json")
        self.ledger = (json.loads(self.ledger_path.read_text()) if self.ledger_path.exists()
                       else {"schema_version": 1, "cap_bytes": NETWORK_CAP, "requests": []})
        if self.ledger.get("cap_bytes") != NETWORK_CAP:
            raise ValueError("Source download budget changed")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())

    def save(self):
        atomic_bytes(self.ledger_path, encoded(self.ledger), replace=True)

    def fetch(self, url, maximum, *, data=None):
        if not allowed_url(url) or not 0 < maximum <= 8_000_000:
            raise ValueError("Invalid source request")
        # Pending reservations remain charged after an interruption, so resuming
        # cannot accidentally reset the cumulative cap or forget failed bytes.
        spent = sum(r.get("bytes", r["reserved_bytes"]) for r in self.ledger["requests"])
        if spent + maximum + 1 > NETWORK_CAP:
            raise ValueError("Source retrieval would exceed the cumulative 40 MB cap")
        request_record = {"url": url, "reserved_bytes": maximum + 1, "status": "pending"}
        self.ledger["requests"].append(request_record)
        self.save()
        count = 0
        try:
            request = urllib.request.Request(url, data=data,
                headers={"User-Agent": "Robot-Worlds-source-acquisition/1",
                         **({"Content-Type": "application/json"} if data is not None else {})})
            with self.opener.open(request, timeout=45) as response:
                if response.status != 200 or not allowed_url(response.url):
                    raise ValueError("Unexpected source response")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > maximum:
                    raise ValueError("Source response exceeds its bound")
                chunks = []
                while chunk := response.read(min(65536, maximum - count + 1)):
                    count += len(chunk)
                    if count > maximum:
                        raise ValueError("Source response exceeded its bound")
                    chunks.append(chunk)
                if length is not None and count != int(length):
                    raise ValueError("Truncated source response")
            payload = b"".join(chunks)
            request_record.update(status="complete", sha256=hashlib.sha256(payload).hexdigest())
            return payload
        except urllib.error.HTTPError as error:
            request_record.update(status="http_error", http_status=error.code)
            raise
        except BaseException:
            request_record["status"] = "interrupted_or_failed"
            raise
        finally:
            request_record["bytes"] = count
            self.save()


def load_catalog(acquisition, revision):
    """Resolve one hash-pinned catalog without relying on a research checkout."""
    if revision != CATALOG_REVISION:
        raise ValueError("No verified manifest hash for the selected optimized revision")
    target = safe_path(acquisition.root, "optimized-manifest.jsonl")
    if not target.exists():
        if CATALOG.is_file() and not CATALOG.is_symlink():
            if digest(CATALOG) != CATALOG_SHA256:
                raise ValueError("Prior cached optimized manifest hash changed")
            data = CATALOG.read_bytes()
        else:
            data = acquisition.fetch(
                "https://huggingface.co/datasets/codepawl/simready-glb/resolve/"
                + revision + "/manifest.jsonl", 2_000_000)
        if hashlib.sha256(data).hexdigest() != CATALOG_SHA256:
            raise ValueError("Pinned optimized manifest checksum mismatch")
        atomic_bytes(target, data)
    if digest(target) != CATALOG_SHA256:
        raise ValueError("Output optimized manifest checksum mismatch")
    return {row["asset"]: row for row in map(json.loads, target.read_text().splitlines())}


def verify_source(path, size, publisher_hash):
    """Check LFS content SHA-256 or a regular file's Git blob object identity."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ValueError("Cached original GLB length or path changed")
    sha256 = hashlib.sha256()
    git_sha1 = hashlib.sha1(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            sha256.update(chunk)
            git_sha1.update(chunk)
    actual = sha256.hexdigest() if publisher_hash["algorithm"] == "sha256" else git_sha1.hexdigest()
    if actual != publisher_hash["value"]:
        raise ValueError("Original GLB publisher checksum mismatch")
    return sha256.hexdigest()


def read_glb(path):
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("Truncated GLB header")
        magic, version, length, json_length, kind = struct.unpack("<4sIIII", header)
        if (magic != b"glTF" or version != 2 or length != path.stat().st_size
                or kind != 0x4E4F534A or json_length % 4 or json_length > MAX_JSON):
            raise ValueError("Invalid source GLB header/JSON bound")
        document = json.loads(stream.read(json_length))
        binary_header = stream.read(8)
        if len(binary_header) != 8:
            raise ValueError("Missing embedded GLB binary")
        binary_length, binary_kind = struct.unpack("<II", binary_header)
        if (binary_kind != 0x004E4942 or binary_length % 4
                or 28 + json_length + binary_length != length):
            raise ValueError("Invalid source GLB chunk lengths")
    if (document.get("asset", {}).get("version") != "2.0"
            or any("uri" in item for item in document.get("buffers", []))
            or any("uri" in item for item in document.get("images", []))):
        raise ValueError("Source is not a self-contained GLB 2.0")
    buffers = document.get("buffers", [])
    if not buffers or not 0 <= binary_length - buffers[0]["byteLength"] <= 3:
        raise ValueError("Embedded buffer length disagrees")
    return document


def inventory(document):
    nodes = document.get("nodes", [])
    physics = document.get("extensions", {}).get("KHR_physics_rigid_bodies", {})
    parents = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            if not isinstance(child, int) or not 0 <= child < len(nodes) or child in parents:
                raise ValueError("Invalid source node hierarchy")
            parents[child] = index
    for start in range(len(nodes)):
        seen, current = set(), start
        while current in parents:
            if current in seen:
                raise ValueError("Cyclic source node hierarchy")
            seen.add(current)
            current = parents[current]
    return {
        "status": "source_json_inventory_only_not_decoded_or_physically_validated",
        "extensions_used": document.get("extensionsUsed", []),
        "extensions_required": document.get("extensionsRequired", []),
        "nodes": [{"index": i, "parent_index": parents.get(i), **node}
                  for i, node in enumerate(nodes)],
        "physics_extension": physics,
        "materials": document.get("materials", []),
        "textures": document.get("textures", []),
        "images": document.get("images", []),
        "accessors": document.get("accessors", []),
        "meshes": document.get("meshes", []),
        "geometry_status": "original compressed mesh buffers retained; no vertex decoding or adaptation",
        "cavity_status": "unknown; convex-hull definitions do not establish empty cavities or usable clearances",
    }


def prepare(selection_path, output):
    if output.is_symlink():
        raise ValueError("Output root is a symlink")
    root = output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 256_000_000:
        raise ValueError("At least 256 MB free space is required")
    source_selection = selection_path.read_bytes()
    selection = json.loads(source_selection)
    records = selection["records"]
    if (selection.get("author", {}).get("model") != "gpt-6-astra"
            or selection["author"].get("reasoning") != "high"
            or len(records) != 15 or len({r["asset_id"] for r in records}) != 15):
        raise ValueError("Expected Astra high's exact 15-record source selection")
    for row in records:
        if not re.fullmatch(r"[A-Za-z0-9_]+", row["asset_id"]):
            raise ValueError("Unsafe selected source ID")
    if sum(r["optimized_glb_bytes"] for r in records) != selection["selected_glb_bytes"]:
        raise ValueError("Selected source size total disagrees")
    for key in ("official_revision", "optimized_revision"):
        if not re.fullmatch(r"[a-f0-9]{40}", selection[key]):
            raise ValueError("Source revisions must be immutable Git hashes")
    candidate_ledger_path = CANDIDATES / "metadata-ledger.json"
    cached = {}
    if candidate_ledger_path.exists():
        if candidate_ledger_path.is_symlink():
            raise ValueError("Prior candidate ledger is a symlink")
        candidate_ledger = json.loads(candidate_ledger_path.read_text())
        if candidate_ledger["revision"] != selection["official_revision"]:
            raise ValueError("Cached official source revision disagrees")
        cached = {(r["asset"], r["kind"]): r for r in candidate_ledger["files"] if "sha256" in r}
    atomic_bytes(safe_path(root, "source-selection.json"), source_selection)
    acquisition = Acquisition(root)
    catalog = load_catalog(acquisition, selection["optimized_revision"])
    metadata_path = safe_path(root, "optimized-paths-info.json")
    if not metadata_path.exists():
        url = ("https://huggingface.co/api/datasets/codepawl/simready-glb/paths-info/"
               + selection["optimized_revision"])
        payload = acquisition.fetch(url, 64_000,
            data=json.dumps({"paths": ["glb/" + r["asset_id"] + ".glb" for r in records]}).encode())
        atomic_bytes(metadata_path, payload)
    else:
        matches = [r for r in acquisition.ledger["requests"]
                   if r.get("sha256") == digest(metadata_path) and r.get("status") == "complete"]
        if not matches:
            raise ValueError("Cached path metadata has no matching download hash")
    path_info = {r["path"]: r for r in json.loads(metadata_path.read_text())}
    report = {"schema_version": 1, "status": "acquiring_source_only",
        "selection_sha256": hashlib.sha256(source_selection).hexdigest(),
        "acquisition_script_sha256": digest(Path(__file__)),
        "optimized_manifest_sha256": CATALOG_SHA256,
        "official_revision": selection["official_revision"],
        "optimized_revision": selection["optimized_revision"],
        "derivation_notice": "Optimized publisher does not declare its exact upstream NVIDIA revision.",
        "records": []}
    manifest_path = safe_path(root, "source-manifest.json")
    for selected in records:
        asset = selected["asset_id"]
        row, info = catalog[asset], path_info["glb/" + asset + ".glb"]
        if info.get("lfs"):
            expected = info["lfs"]["oid"]
            publisher_hash = {"algorithm": "sha256", "value": expected}
            hash_pattern = r"[a-f0-9]{64}"
        else:
            expected = info["oid"]
            publisher_hash = {"algorithm": "git-blob-sha1", "value": expected}
            hash_pattern = r"[a-f0-9]{40}"
        if (not isinstance(expected, str) or not re.fullmatch(hash_pattern, expected)
                or info["size"] != selected["optimized_glb_bytes"]
                or row["optimized"]["bytes"] != info["size"]
                or row["bounds"] != selected["source_bounds_y_up"]
                or row["physics"] != selected["manifest_physics_counts"]):
            raise ValueError("Publisher metadata or catalog disagrees with Astra's selection")
        url = ("https://huggingface.co/datasets/codepawl/simready-glb/resolve/"
               + selection["optimized_revision"] + "/glb/" + asset + ".glb")
        path = safe_path(root, "sources/" + asset + "/model.glb")
        if not path.exists():
            data = acquisition.fetch(url, info["size"])
            if len(data) != info["size"]:
                raise ValueError("Original GLB length mismatch")
            actual = (hashlib.sha256(data).hexdigest() if publisher_hash["algorithm"] == "sha256"
                      else hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest())
            if actual != expected:
                raise ValueError("Original GLB publisher checksum mismatch")
            atomic_bytes(path, data)
        source_sha256 = verify_source(path, info["size"], publisher_hash)
        document = read_glb(path)
        inv = inventory(document)
        atomic_bytes(safe_path(root, f"sources/{asset}/glb-inventory.json"), encoded(inv))
        files = [{"path": str(path.relative_to(root)), "url": url, "revision": selection["optimized_revision"],
                  "bytes": info["size"], "sha256": source_sha256,
                  "publisher_hash": publisher_hash, "format": "glb"}]
        metadata = [
            ("physics", "physics.usda", "simready_usd/payloads/Physics/physics.usda"),
            ("package", "packaging.json", "com.nvidia.simready.packaging.json"),
            ("license", "LICENSE.txt", ".metadata/LICENSE.txt"),
            ("mujoco-instance-metadata", "instances_mujoco.usda", "simready_usd/payloads/instances_mujoco.usda"),
            ("mujoco-enabled", "mujoco-enabled.usda", "simready_usd/payloads/MuJoCo/enabled.usda"),
        ]
        for kind, name, suffix in metadata:
            target = safe_path(root, f"sources/{asset}/{name}")
            source_url = ("https://huggingface.co/datasets/nvidia/simready-assets/resolve/"
                          + selection["official_revision"] + "/" + asset + "/" + suffix)
            cached_record = cached.get((asset, kind))
            if cached_record:
                original = safe_path(CANDIDATES.resolve(), cached_record["path"])
                if cached_record["url"] != source_url or digest(original) != cached_record["sha256"]:
                    raise ValueError("Cached original metadata changed")
                data = original.read_bytes()
            elif target.exists():
                data = target.read_bytes()
                if not any(r.get("url") == source_url and r.get("sha256") == hashlib.sha256(data).hexdigest()
                           for r in acquisition.ledger["requests"]):
                    raise ValueError("Cached downloaded metadata has no matching hash")
            else:
                try:
                    data = acquisition.fetch(source_url, META_LIMIT)
                except urllib.error.HTTPError as error:
                    if kind != "mujoco-enabled" or error.code != 404:
                        raise
                    files.append({"url": source_url, "revision": selection["official_revision"],
                                  "status": "optional_override_not_published_at_pinned_path",
                                  "http_status": 404})
                    continue
            if len(data) > META_LIMIT:
                raise ValueError("Source metadata exceeds its bound")
            atomic_bytes(target, data)
            files.append({"path": str(target.relative_to(root)), "url": source_url,
                          "revision": selection["official_revision"], "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "reused_candidate_metadata": bool(cached_record)})
        package = json.loads(safe_path(root, f"sources/{asset}/packaging.json").read_text())
        license_text = safe_path(root, f"sources/{asset}/LICENSE.txt").read_text()
        if package.get("license") != "CC-BY-4.0" or "Attribution 4.0 International" not in license_text:
            raise ValueError("Selected source license is not the declared CC-BY-4.0")
        notice = (f"# {asset}\n\nOriginal author: NVIDIA SimReady. Optimized conversion: CodePawl.\n"
                  f"License: CC-BY-4.0; see LICENSE.txt.\nOriginal source: {files[1]['url']}\n"
                  f"Optimized source: {url}\nThe optimized publisher's exact upstream NVIDIA revision is not declared.\n"
                  "Source bytes, transforms, materials and physics remain unchanged. No contact, grasp, cavity, "
                  "visual-quality or task-success validation has been performed.\n")
        atomic_bytes(safe_path(root, f"sources/{asset}/NOTICE.md"), notice.encode())
        record = {"asset_id": asset, "author_selection": selected, "license": package["license"],
                  "files": files, "optimized_manifest_record": row,
                  "inventory_path": f"sources/{asset}/glb-inventory.json",
                  "status": "original_bytes_and_glb_json_verified_no_conversion_or_physics_validation"}
        report["records"].append(record)
        atomic_bytes(manifest_path, encoded(report), replace=True)
        print(f"Verified {asset}: {info['size']:,} GLB bytes", flush=True)
    report["status"] = "all_selected_source_bytes_resolved_no_import_or_physics_validation"
    report["new_download_bytes"] = sum(r.get("bytes", r["reserved_bytes"]) for r in acquisition.ledger["requests"])
    report["glb_bytes"] = sum(r["files"][0]["bytes"] for r in report["records"])
    atomic_bytes(manifest_path, encoded(report), replace=True)
    print(f"Complete: {len(records)} GLBs, {report['new_download_bytes']:,} new downloaded bytes", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    prepare(arguments.selection, arguments.output)
