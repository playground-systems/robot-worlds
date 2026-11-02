"""Validate procedural object groups against completed empty-search evidence."""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import Field

from .artifacts import digest, write_json
from .asset_import import ImportedAsset
from .asset_resolution import AssetResolutionError, SourceAssetRequest, _compatible
from .asset_sourcing import AssetSearch, source_asset
from .models import ASSET_FILE_FIELDS, Identifier, Model


@dataclass(frozen=True)
class AssetCreationEvidence:
    request: SourceAssetRequest
    search: AssetSearch

    def validate(self):
        if (not isinstance(self.request, SourceAssetRequest) or not isinstance(self.search, AssetSearch)
                or self.search.completed is not True or self.search.query != self.request.query
                or not self.search.sources or not all(isinstance(s, str) and s.strip() for s in self.search.sources)
                or self.search.candidates):
            raise ValueError("Procedural creation needs a completed, matching search with zero candidates")


class CreatedAssetGroup(Model):
    asset_id: Identifier = Field(description="Exact ID authorized by the supplied empty-search evidence, not an imported asset ID.")
    object_ids: list[Identifier] = Field(min_length=1, max_length=256,
        description="All physical/visual objects of one generated asset instance, including every attached child. One connected root; no external parent.")
    region_ids: list[Identifier] = Field(default_factory=list, max_length=32,
        description="Cavity regions belonging to this asset. Ordinary task destination regions stay outside this group.")


def _group_asset(spec, group, request):
    objects = {obj.id: obj for obj in spec.objects}
    regions = {region.id: region for region in spec.regions}
    ids = set(group.object_ids)
    if len(ids) != len(group.object_ids) or not ids <= objects.keys():
        raise ValueError("Created asset object IDs must be unique and present in the expanded scene")
    if len(set(group.region_ids)) != len(group.region_ids) or not set(group.region_ids) <= regions.keys():
        raise ValueError("Created asset cavity IDs must be unique and present")
    parts = [objects[oid] for oid in group.object_ids]
    cavities = [regions[rid] for rid in group.region_ids]
    roots = [obj for obj in parts if obj.parent_id is None]
    if len(roots) != 1 or not roots[0].collision:
        raise ValueError("Created asset needs one physical root and a connected component hierarchy")
    root = roots[0]
    for obj in parts:
        if obj.kind == "mesh" or any(getattr(obj, field) for field in ASSET_FILE_FIELDS):
            raise ValueError("Generated groups use procedural shapes, not invented mesh/texture/source files")
        current, seen = obj, set()
        while current.id != root.id:
            if current.id in seen or current.parent_id not in ids:
                raise ValueError("Created asset has an external parent, cycle or disconnected component")
            seen.add(current.id)
            current = objects[current.parent_id]
        if obj.id != root.id and obj.movable:
            raise ValueError("Only the created asset root may be freely movable")
    if any(obj.parent_id in ids and obj.id not in ids for obj in spec.objects):
        raise ValueError("Created asset group omitted an attached component")
    if any(region.interior_of not in ids for region in cavities):
        raise ValueError("Created asset regions must describe cavities in its own components")
    if any(region.interior_of in ids and region.id not in group.region_ids for region in spec.regions):
        raise ValueError("Created asset group omitted an owned cavity")
    mass = sum(obj.mass for obj in parts if obj.collision)
    if not math.isclose(mass, request.mass, rel_tol=1e-6, abs_tol=1e-9):
        raise ValueError(f"{group.asset_id}: distribute the requested estimated mass {request.mass:g} kg across collision parts; got {mass:g} kg")
    asset = ImportedAsset(objects=parts, regions=cavities, body_ids={"object": root.id},
                          geom_ids={obj.id: obj.id for obj in parts},
                          joint_ids={obj.id: obj.id for obj in parts if obj.joint}, disclosures=[])
    try:
        _compatible(asset, request)
    except AssetResolutionError as exc:
        raise ValueError(str(exc)) from exc
    return asset


def materialize_created_assets(spec, groups, evidence, *, output, on_event=None):
    """Attach evidence only after checking every generated group, before preview.

    This establishes source-search and structural provenance, not semantic fidelity
    or native physics success. The whole world still passes ordinary validation.
    """
    if not groups:
        return spec
    if output is None:
        raise ValueError("Procedural source evidence needs a persistent output directory")
    result = spec.model_copy(deep=True)
    seen, checked = set(), []
    for group in groups:
        if group.asset_id not in evidence:
            raise ValueError(f"{group.asset_id}: no completed empty-search evidence authorizes this generated asset")
        authorization = evidence[group.asset_id]
        authorization.validate()
        if set(group.object_ids + group.region_ids) & seen:
            raise ValueError("Created asset groups must own disjoint components and cavities")
        seen.update(group.object_ids + group.region_ids)
        asset = _group_asset(result, group, authorization.request)
        def unexpected(*_):
            raise ValueError("A discovered source must be imported; it cannot authorize generated replacement")
        sourced = source_asset(authorization.request.query, search=lambda _: authorization.search,
                               import_asset=unexpected, create_asset=lambda *_: asset)
        checked.append((group, authorization, sourced))
    for group, authorization, sourced in checked:
        payload = {"schema_version": 1, "decision": sourced.decision, "search": asdict(sourced.search),
                   "request": authorization.request.model_dump(mode="json"), "selected": None, "rejected": sourced.rejected,
                   "objects": [obj.model_dump(mode="json") for obj in sourced.asset.objects],
                   "regions": [region.model_dump(mode="json") for region in sourced.asset.regions],
                   "scope": "Generated after the recorded catalog returned no candidates; not proof that no source exists elsewhere. Native validation is separate."}
        if len(json.dumps(payload, indent=2).encode())+1 > 2 * 1024 * 1024:
            raise ValueError("Generated source evidence exceeds 2 MiB")
        folder = Path(output).resolve() / "generated-evidence" / digest(payload)
        metadata = folder / "source.json"
        write_json(metadata, payload)
        notice = folder / "NOTICE.md"
        descriptor, temporary = tempfile.mkstemp(prefix=".notice-", dir=folder)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write("Procedural geometry generated by Robot Worlds after a completed catalog search.\n"
                             "No external geometry was imported for this object. This does not establish exhaustive online absence, "
                             "visual correctness or task success. Mass/material properties are estimates. "
                             "See source.json for the query, catalog scope and generated geometry.\n")
            os.replace(temporary, notice)
        finally:
            Path(temporary).unlink(missing_ok=True)
        for obj in sourced.asset.objects:
            obj.source_metadata, obj.source_notice = str(metadata), str(notice)
        result.assumptions.append(f"{group.asset_id}: generated procedural geometry after the recorded search for {authorization.request.query!r} "
                                  "returned zero candidates. Search scope is limited to the recorded catalogs; physical/material properties are estimates.")
        if on_event:
            on_event({"type": "asset_created_after_search", "asset_id": group.asset_id,
                      "components": len(sourced.asset.objects), "sources": list(sourced.search.sources),
                      "message": f"Created {group.asset_id} after the recorded catalog search; physics checks follow"})
    return result
