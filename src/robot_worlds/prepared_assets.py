"""Pinned source assets available without a network request or conversion job.

References are generation input only. Saved worlds contain ordinary physical
objects and copied assets, so loading/exporting never depends on this catalog.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field

from .asset_import import import_convex_mesh_asset, import_mjcf_asset
from .asset_sourcing import AssetCandidate, AssetSearch, source_asset
from .models import Identifier, Model, Number, Positive, Vec3, WorldSpec

PreparedAssetId = Literal["google-porcelain-bowl", "nvidia-microwave079"]
_ROOT = Path(__file__).parent / "assets/household"
_FOLDERS = {"google-porcelain-bowl": "porcelain_bowl", "nvidia-microwave079": "microwave079"}


class PreparedJointInitial(Model):
    name: Identifier
    initial: Number


class PreparedAssetInstance(Model):
    id: Identifier = Field(description="Instance prefix. Materialized IDs are listed in the prepared asset catalog.")
    asset_id: PreparedAssetId
    position: Vec3 = Field(description="World position of the asset's full XY bounds center and lowest Z point, metres. Put Z just above the support top.")
    mass: Positive | None = Field(default=None, description="Required estimated total mass for the movable bowl. Leave null for the fixed microwave.")
    joint_initials: list[PreparedJointInitial] = Field(default_factory=list, max_length=16, description="Optional source joint names and initial values in radians/metres. Only catalog joints are accepted, with no duplicate names.")


class GenerationSpec(WorldSpec):
    prepared_assets: list[PreparedAssetInstance] = Field(default_factory=list, max_length=100,
                                                       description="Reuse pinned source assets instead of emitting their component geometry. Surrounding layout remains freely generated.")


def load_prepared_asset(asset_id: str, *, prefix: str, position=(0, 0, 0), mass=None, joint_initials=None):
    """Return a SourcedAsset using retained online-source evidence and local files.

    Placement uses the zero-joint configuration's bottom-center. Original size,
    UVs and geometry are preserved; arbitrary rotation/scaling is not supported.
    Unknown assets or import failures never trigger procedural replacement.
    """
    instance = PreparedAssetInstance(id=prefix, asset_id=asset_id, position=position,
                                     mass=mass, joint_initials=joint_initials or [])
    root = _ROOT / _FOLDERS[instance.asset_id]
    metadata = json.loads((root / "source-manifest.json").read_text())
    candidate = AssetCandidate(id=metadata["id"], source_url=metadata["source_url"],
                               revision=metadata["revision"], license=metadata["license"],
                               metadata={"archive_sha256": metadata["archive_sha256"]})
    report = AssetSearch(query=asset_id, sources=(metadata["source_url"],), candidates=(candidate,), completed=True,
                         evidence={"kind": "pinned_local_source", "scope": "Previously selected source; not a fresh internet search",
                                   "archive_sha256": metadata["archive_sha256"]})

    def import_local(_candidate):
        if instance.asset_id == "google-porcelain-bowl":
            if instance.mass is None:
                raise ValueError("Prepared bowl requires an explicit estimated mass")
            asset = import_convex_mesh_asset(root / "meshes/model.obj", root / "collision/manifest.json",
                                            prefix=prefix, mass=instance.mass, source_notice=root / "ATTRIBUTION.md",
                                            texture_path=root / "materials/textures/texture.png")
        else:
            if instance.mass is not None:
                raise ValueError("Fixed microwave uses source density estimates; mass override is unsupported")
            asset = import_mjcf_asset(root / "model.xml", prefix=prefix)
        lower = np.min([np.array(o.position)-np.array(o.size)/2 for o in asset.objects], axis=0)
        upper = np.max([np.array(o.position)+np.array(o.size)/2 for o in asset.objects], axis=0)
        origin = np.array([(lower[0]+upper[0])/2, (lower[1]+upper[1])/2, lower[2]])
        offset = np.array(instance.position)-origin
        objects = {o.id: o for o in asset.objects}
        names = [item.name for item in instance.joint_initials]
        if len(names) != len(set(names)):
            raise ValueError("Prepared asset joint overrides must have unique names")
        for item in instance.joint_initials:
            name, initial = item.name, item.initial
            if name not in asset.joint_ids:
                raise ValueError(f"Unknown prepared asset joint: {name}")
            objects[asset.joint_ids[name]].joint.initial = initial
        for obj in asset.objects:
            obj.position = np.array(obj.position)+offset
            if obj.joint:
                obj.joint.anchor = np.array(obj.joint.anchor)+offset
        for region in asset.regions:
            region.position = np.array(region.position)+offset
        return asset

    def no_creation(*_args):
        raise ValueError("Prepared asset could not be resolved; creation is not a fallback")

    return source_asset(asset_id, search=lambda _: report, import_asset=import_local, create_asset=no_creation)


def prepared_asset_catalog():
    """Compact grounded geometry/ID contract for model planning and SDK callers."""
    catalog = []
    for asset_id in _FOLDERS:
        sourced = load_prepared_asset(asset_id, prefix="asset", mass=.6 if asset_id == "google-porcelain-bowl" else None)
        asset = sourced.asset
        lower = np.min([np.array(o.position)-np.array(o.size)/2 for o in asset.objects], axis=0)
        upper = np.max([np.array(o.position)+np.array(o.size)/2 for o in asset.objects], axis=0)
        def ids(mapping):
            return {key: value.replace("asset_", "{prefix}_", 1) for key, value in mapping.items()}
        catalog.append({"asset_id": asset_id, "size": (upper-lower).tolist(), "components": len(asset.objects),
                        "source_url": sourced.selected.source_url, "revision": sourced.selected.revision,
                        "license": sourced.selected.license,
                        "placement": "position is bottom-center of zero-joint bounds; original size/orientation preserved",
                        "requirements": "explicit estimated mass; movable hollow bowl" if asset_id == "google-porcelain-bowl" else "fixed microwave; front faces -Y; passive door; no simulated heating unless a process cycle is defined",
                        "body_ids": ids(asset.body_ids), "geom_ids": ids(asset.geom_ids), "joint_ids": ids(asset.joint_ids),
                        "joints": {name: next(o.joint.model_dump(mode="json") for o in asset.objects if o.id == oid)
                                   for name, oid in asset.joint_ids.items()},
                        "regions": [{**r.model_dump(mode="json"), "id": r.id.replace("asset_", "{prefix}_", 1),
                                     "interior_of": r.interior_of.replace("asset_", "{prefix}_", 1) if r.interior_of else None}
                                    for r in asset.regions]})
    return catalog


def append_prepared_asset(payload: dict, instance: PreparedAssetInstance, *, max_objects: int, event_callback=None):
    """Append checked source components to a caller-owned candidate payload.

    Generation and typed edits share collision/budget/provenance rules. Callers
    work on private copies and validate/protect the complete result before saving.
    """
    used = {entry["id"] for field in ("objects", "regions", "process_cycles") for entry in payload.get(field, [])}
    if event_callback:
        event_callback({"type": "asset_loading", "asset_id": instance.asset_id, "message": f"Reusing {instance.asset_id}"})
    sourced = load_prepared_asset(instance.asset_id, prefix=instance.id, position=instance.position,
                                  mass=instance.mass, joint_initials=instance.joint_initials)
    asset = sourced.asset
    ids = {entry.id for entry in [*asset.objects, *asset.regions]}
    if used & ids:
        raise ValueError("Prepared asset IDs conflict with scene objects or regions: " + ", ".join(sorted(used & ids)))
    if len(payload["objects"])+len(asset.objects) > max_objects:
        raise ValueError(f"Prepared assets exceed the {max_objects}-object resource budget")
    payload["objects"].extend(o.model_dump(mode="json") for o in asset.objects)
    payload.setdefault("regions", []).extend(r.model_dump(mode="json") for r in asset.regions)
    assumptions = payload.setdefault("assumptions", [])
    for disclosure in asset.disclosures:
        if disclosure not in assumptions:
            assumptions.append(disclosure)
    assumptions.append(f"Prepared asset {instance.id}: {sourced.selected.id}, revision {sourced.selected.revision}, {sourced.selected.license}; {sourced.selected.source_url}")
    if instance.mass is not None:
        assumptions.append(f"{instance.id} total mass {instance.mass:g} kg is an estimate, not a measured source property.")
    if event_callback:
        event_callback({"type": "asset_loaded", "asset_id": instance.asset_id, "instance_id": instance.id,
                        "source_url": sourced.selected.source_url, "revision": sourced.selected.revision,
                        "license": sourced.selected.license, "components": len(asset.objects),
                        "message": f"Reused {instance.asset_id}; original geometry and texture retained"})


def materialize_prepared_assets(plan: GenerationSpec, *, max_objects: int, event_callback=None):
    """Expand references atomically before geometry validation or preview saving."""
    payload = plan.model_dump(mode="json", exclude={"prepared_assets"})
    prefixes = [instance.id for instance in plan.prepared_assets]
    if len(prefixes) != len(set(prefixes)):
        raise ValueError("Prepared asset instance prefixes must be unique")
    for instance in plan.prepared_assets:
        append_prepared_asset(payload, instance, max_objects=max_objects, event_callback=event_callback)
    return WorldSpec.model_validate(payload)
