"""Portable, provenance-backed imports for generation and targeted edits.

Registration retains the importer's physical parts; it does not certify task
suitability. Every materialized world still passes the normal validation gates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from pydantic import TypeAdapter

from .artifacts import digest
from .asset_import import ImportedAsset
from .asset_sourcing import AssetCandidate, AssetSearch, SourcedAsset, source_asset
from .models import TEXTURE_FIELDS, Identifier, Region, SceneObject

_FILE_SUFFIXES = {"mesh_path": {".obj", ".stl", ".msh"}, "texture_path": {".png", ".jpg", ".jpeg"},
                  "normal_texture_path": {".png"}, "orm_texture_path": {".png"}, "emissive_texture_path": {".png"},
                  "source_notice": {".md", ".txt"}, "source_metadata": {".json"}}
_FILE_FIELDS = tuple(_FILE_SUFFIXES)
_MAX_FILE = 32 * 1024 * 1024
_MAX_TOTAL = 256 * 1024 * 1024


def _source_record(sourced):
    if (not isinstance(sourced, SourcedAsset) or sourced.decision != "reuse_or_adapt"
            or not isinstance(sourced.selected, AssetCandidate) or not isinstance(sourced.search, AssetSearch)
            or sourced.search.completed is not True or not isinstance(sourced.search.query, str)
            or not sourced.search.query.strip() or not sourced.search.sources
            or not all(isinstance(value, str) and value.strip() for value in sourced.search.sources)
            or sourced.selected not in sourced.search.candidates):
        raise ValueError("Library registration needs a selected import with completed source-search evidence")
    if not all(isinstance(value, str) and value.strip() for value in (
            sourced.selected.id, sourced.selected.source_url, sourced.selected.revision, sourced.selected.license)):
        raise ValueError("Library source must identify its origin, pinned revision and license")
    return json.loads(json.dumps({"decision": sourced.decision, "search": asdict(sourced.search),
                                  "selected": asdict(sourced.selected), "rejected": sourced.rejected}))


def _evidence(record):
    record = copy.deepcopy(record)
    search = record["search"]
    report = AssetSearch(query=search["query"], sources=tuple(search["sources"]),
                         candidates=tuple(AssetCandidate(**item) for item in search["candidates"]),
                         completed=search["completed"], evidence=search.get("evidence", {}))
    result = SourcedAsset(None, record["decision"], report, AssetCandidate(**record["selected"]), record["rejected"])
    _source_record(result)
    return result


def _asset(payload):
    asset = ImportedAsset(objects=[SceneObject.model_validate(obj) for obj in payload["objects"]],
                          regions=[Region.model_validate(region) for region in payload["regions"]],
                          **{key: copy.deepcopy(payload[key]) for key in
                             ("body_ids", "geom_ids", "joint_ids", "source_files", "disclosures")})
    if not 1 <= len(asset.objects) <= 1000:
        raise ValueError("A library asset must contain 1–1000 physical/visual components")
    objects = {obj.id: obj for obj in asset.objects}
    ids = [obj.id for obj in [*asset.objects, *asset.regions]]
    if len(ids) != len(set(ids)):
        raise ValueError("Library asset contains duplicate entity IDs")
    for obj in asset.objects:
        seen, current = set(), obj
        while current.parent_id:
            if current.id in seen or current.parent_id not in objects:
                raise ValueError("Library asset has a parent cycle or missing parent")
            seen.add(current.id)
            current = objects[current.parent_id]
    for region in asset.regions:
        if region.interior_of is not None and region.interior_of not in objects:
            raise ValueError("Library cavity references a missing physical object")
    for field in ("body_ids", "geom_ids", "joint_ids"):
        mapping = getattr(asset, field)
        if not isinstance(mapping, dict) or any(not isinstance(name, str) or oid not in objects for name, oid in mapping.items()):
            raise ValueError("Library source IDs reference missing physical components")
    if any(objects[oid].joint is None for oid in asset.joint_ids.values()):
        raise ValueError("Library joint map references a component without a joint")
    return asset


def _payload(asset):
    return {"objects": [obj.model_dump(mode="json") for obj in asset.objects],
            "regions": [region.model_dump(mode="json") for region in asset.regions],
            **{key: copy.deepcopy(getattr(asset, key)) for key in
               ("body_ids", "geom_ids", "joint_ids", "source_files", "disclosures")}}


def _rename(asset, prefix):
    names = {obj.id: f"{prefix}_part{i}" for i, obj in enumerate(asset.objects)}
    names.update({region.id: f"{prefix}_region{i}" for i, region in enumerate(asset.regions)})
    for obj in asset.objects:
        obj.id = names[obj.id]
        if obj.parent_id:
            obj.parent_id = names[obj.parent_id]
    for region in asset.regions:
        region.id = names[region.id]
        if region.interior_of:
            region.interior_of = names[region.interior_of]
    for field in ("body_ids", "geom_ids", "joint_ids"):
        setattr(asset, field, {key: names[value] for key, value in getattr(asset, field).items()})
    return asset


def _fingerprint(path):
    if not path.is_file() or path.stat().st_size > _MAX_FILE:
        raise ValueError("Library source files must exist and be at most 32 MiB each")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > _MAX_FILE:
                raise ValueError("Library source file exceeds 32 MiB")
            digest.update(chunk)
    return digest.hexdigest(), size


class AssetLibrary:
    """Register existing imports once; reuse them through SDK or self-hosted jobs.

    Source mass, scale and orientation are preserved. Instances support translated
    placement and valid joint initial values, not implicit remeshing or rescaling.
    Saved libraries contain the referenced geometry, textures, notices, metadata
    and import/search evidence. Source file digests are rechecked before reuse.
    """

    def __init__(self):
        self._entries = {}

    @property
    def asset_ids(self):
        return tuple(self._entries)

    def description(self, asset_id):
        return self._entries[asset_id]["description"]

    def copy(self):
        """Copy registration metadata; source files are still verified on load/save."""
        return copy.deepcopy(self)

    def add(self, asset_id: str, sourced: SourcedAsset, *, description: str):
        asset_id = TypeAdapter(Identifier).validate_python(asset_id)
        if asset_id in self._entries or len(self._entries) >= 32:
            raise ValueError("Library IDs must be unique; at most 32 assets are supported")
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 2000:
            raise ValueError("Library assets need a description of 1–2000 characters")
        source = _source_record(sourced)
        if not isinstance(sourced.asset, ImportedAsset):
            raise ValueError("Register an ImportedAsset returned by a supported import adapter")
        asset = _rename(_asset(_payload(sourced.asset)), "asset")
        if not asset.source_files or any(not isinstance(value, str) or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value) for value in asset.source_files.values()):
            raise ValueError("Imported asset must retain original source file checksums")
        files, total = {}, 0
        for obj in asset.objects:
            for field in _FILE_FIELDS:
                value = getattr(obj, field)
                if not value:
                    continue
                path = Path(value).expanduser().resolve()
                if path.suffix.lower() not in _FILE_SUFFIXES[field]:
                    raise ValueError(f"Unsupported library file type for {field}")
                if str(path) not in files:
                    fingerprint, size = _fingerprint(path)
                    total += size
                    files[str(path)] = fingerprint
                if field in {"mesh_path", *TEXTURE_FIELDS} and files[str(path)] not in asset.source_files.values():
                    raise ValueError("Geometry or texture changed since import; re-import before registration")
                setattr(obj, field, str(path))
        previous = {filename for entry in self._entries.values() for filename in entry["files"]}
        total += sum(Path(filename).stat().st_size for filename in previous - set(files))
        if total > _MAX_TOTAL:
            raise ValueError("Library source files exceed 256 MiB")
        if not any(obj.source_notice for obj in asset.objects):
            raise ValueError("Library assets require a retained attribution notice")
        self._entries[asset_id] = {"description": description, "source": source, "asset": _payload(asset), "files": files}
        return self

    def load(self, asset_id: str, *, prefix: str):
        TypeAdapter(Identifier).validate_python(prefix)
        if asset_id not in self._entries:
            raise ValueError(f"Unknown library asset: {asset_id}")
        entry = self._entries[asset_id]
        for filename, expected in entry["files"].items():
            if _fingerprint(Path(filename))[0] != expected:
                raise ValueError(f"Library source changed; register a new version: {asset_id}")
        evidence = _evidence(entry["source"])

        def unavailable(*args):
            raise ValueError("Library import is unavailable; procedural replacement is not a fallback")

        # Only the retained selected source belongs to this registered import.
        report = evidence.search
        selected_report = AssetSearch(report.query, report.sources, (evidence.selected,), True,
                                      {**report.evidence, "kind": "registered_local_import",
                                       "scope": "Retained search evidence; no new online lookup"})
        result = source_asset(report.query, search=lambda _: selected_report,
                              import_asset=lambda _: _rename(_asset(entry["asset"]), prefix), create_asset=unavailable)
        result.search, result.rejected = report, copy.deepcopy(evidence.rejected)
        return result

    def save(self, path: str | Path):
        """Write a new portable library atomically; never overwrite an existing one."""
        destination = Path(path).resolve()
        if destination.exists():
            raise ValueError("Library destination already exists; use a new directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        entries, files, total = {}, {}, 0
        with tempfile.TemporaryDirectory(prefix=".asset-library-", dir=destination.parent) as temporary:
            stage = Path(temporary) / "library"
            (stage / "files").mkdir(parents=True)
            for asset_id, entry in self._entries.items():
                asset = self.load(asset_id, prefix="asset").asset
                for obj in asset.objects:
                    for field in _FILE_FIELDS:
                        value = getattr(obj, field)
                        if not value:
                            continue
                        source = Path(value)
                        expected = entry["files"][value]
                        member = f"files/{expected}{source.suffix.lower()}"
                        if member not in files:
                            shutil.copyfile(source, stage / member)
                            actual, size = _fingerprint(stage / member)
                            if actual != expected:
                                raise ValueError("Source changed while saving the library")
                            total += size
                            if total > _MAX_TOTAL:
                                raise ValueError("Portable library exceeds 256 MiB")
                            files[member] = expected
                        setattr(obj, field, member)
                entries[asset_id] = {"description": entry["description"], "source": entry["source"], "asset": _payload(asset)}
            payload = {"schema_version": 1, "entries": entries, "files": files}
            manifest = json.dumps({**payload, "id": digest(payload)}, indent=2)
            if len(manifest.encode()) > 8 * 1024 * 1024:
                raise ValueError("Library manifest exceeds 8 MiB")
            (stage / "library.json").write_text(manifest + "\n")
            stage.rename(destination)
        return destination

    @classmethod
    def open(cls, path: str | Path):
        root = Path(path).resolve()
        manifest = root / "library.json"
        if not manifest.is_file() or manifest.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Library needs a manifest of at most 8 MiB")
        data = json.loads(manifest.read_text())
        if not isinstance(data, dict):
            raise ValueError("Unsupported library manifest")
        identity = data.pop("id", None)
        if identity != digest(data):
            raise ValueError("Library manifest integrity check failed")
        if data.get("schema_version") != 1 or not isinstance(data.get("entries"), dict) or not isinstance(data.get("files"), dict):
            raise ValueError("Unsupported library manifest")
        if len(data["entries"]) > 32 or len(data["files"]) > 8192:
            raise ValueError("Library manifest exceeds asset/file count limits")
        resolved, total = {}, 0
        for member, expected in data["files"].items():
            target = (root / member).resolve()
            if not target.is_relative_to(root / "files") or target.suffix.lower() not in {".obj", ".stl", ".msh", ".png", ".jpg", ".jpeg", ".md", ".txt", ".json"}:
                raise ValueError("Library file path escapes its supported source directory")
            actual, size = _fingerprint(target)
            total += size
            if total > _MAX_TOTAL or actual != expected:
                raise ValueError("Library source integrity check failed")
            resolved[member] = str(target)
        result = cls()
        for asset_id, entry in data["entries"].items():
            asset = _asset(entry["asset"])
            for obj in asset.objects:
                for field in _FILE_FIELDS:
                    value = getattr(obj, field)
                    if value:
                        if value not in resolved:
                            raise ValueError("Library references a source file missing from its manifest")
                        setattr(obj, field, resolved[value])
            sourced = _evidence(entry["source"])
            sourced.asset = asset
            result.add(asset_id, sourced, description=entry["description"])
        return result


def resolve_asset_library(value):
    if value is None or isinstance(value, AssetLibrary):
        return value
    return AssetLibrary.open(value)
