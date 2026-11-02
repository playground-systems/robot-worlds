"""Small pinned SimReady furniture and props for source-backed scene generation.

Preparation preserves source visuals and records collision adaptations. It does
not create a scene, call a model, or establish native manipulation performance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from pydantic import TypeAdapter

from .appearance import PBR_RENDERER_NOTICE
from .asset_import import ImportedAsset
from .asset_library import AssetLibrary
from .asset_sourcing import AssetCandidate, AssetSearch, source_asset
from .gltf_decode import decode_glb
from .gltf_geometry import GLBGeometry
from .gltf_visuals import prepare_glb_visuals
from .models import Identifier, Region, SceneObject, Vec3
from .simready_structures import component_envelopes
from .workbench_library import _source

_CATALOG = Path(__file__).parent / "assets/simready/living_room_catalog.json"
_PHYSICS = "KHR_physics_rigid_bodies"
LIVING_ROOM_ASSETS = {
    "sofa_soft_b02": "Fixed two-seat sofa; source visual cushions/frame; rigid collision partitions, no soft-body seating",
    "furniture_table_coffee_low_a01": "Fixed low coffee table with addressable tabletop and lower shelf; original open spaces retained",
    "fixture_cabinet_b01": "Fixed wood side cabinet, front and drawer travel +X, with two passive source sliding drawers, separate handles, open-top drawer collision cavities and an addressable top",
    "misc_notebook_a01": "Movable rigid closed notebook; no page turning, opening or deformation",
    "book_open_a01": "Movable rigid book in its source open pose; no page turning or closing",
    "misc_golfBall_a01": "Movable golf ball using source-mesh convex contact geometry; rolling/contact behavior unverified",
    "handTool_screwdriver_phillips_ph2_a01": "Movable industrial Phillips screwdriver with source-mesh convex contact geometry; pick/place/reorientation only, no screw-driving model",
}


def living_room_asset_search(query: str) -> AssetSearch:
    """Return retained online source evidence, including rejected alternatives."""
    catalog = json.loads(_CATALOG.read_text())
    rows = [row for row in catalog["selected"] if row["asset_id"] == query]
    if len(rows) != 1 or query not in LIVING_ROOM_ASSETS:
        raise ValueError("Unknown reviewed living-room/portable-tool asset")
    row = copy.deepcopy(rows[0])
    candidate = AssetCandidate("simready:"+query, row["optimized_url"],
        catalog["optimized_revision"], "CC-BY-4.0", metadata={**row,
            "upstream_checked_revision": catalog["upstream_checked_revision"],
            "derivation_upstream_revision": catalog["optimized_derivation_upstream_revision"]})
    return AssetSearch(query, tuple(catalog["catalogs"]), (candidate,), True,
        {"scope": "Recorded bounded online catalog search; no fresh lookup during preparation",
         "queries": catalog["queries"], "rejected_candidates": catalog["rejected_candidates"],
         "source_search_notes": catalog["source_search_notes"],
         "manifest_sha256": catalog["manifest_sha256"],
         "source_joint_collision_policy": (catalog["cabinet_joint_collision_policy"]
                                            if query == "fixture_cabinet_b01" else None)})


def _bounds(primitives):
    points = np.concatenate([p.positions for p in primitives])
    return points.min(0), points.max(0)


def _free(boxes, low, high):
    low, high = np.asarray(low), np.asarray(high)
    if any(np.all(np.minimum(high, b)-np.maximum(low, a) > 1e-6) for a, b in boxes):
        raise ValueError("Furniture collision fills a reviewed usable clearance")
    return {"low": low.tolist(), "high": high.tolist()}


def _sofa_boxes(primitives):
    """Pinned-source cushion/back/arm/leg partitions, never a whole sofa hull."""
    points = np.concatenate([p.positions for p in primitives])
    low, high = points.min(0), points.max(0)
    if not np.allclose(high-low, [.7028727, 1.5255368, .7280135], atol=.002, rtol=0):
        raise ValueError("Sofa source dimensions changed")
    components = component_envelopes(primitives)
    seats = [(a, b) for a, b in components if .20 < b[2]-a[2] < .23 and b[1]-a[1] > 1.4]
    if len(seats) != 1:
        raise ValueError("Sofa seat component cannot be identified")
    boxes = [seats[0]]

    def envelope(mask):
        selected = points[mask]
        if len(selected) < 4:
            raise ValueError("Sofa source partition is missing")
        return selected.min(0), selected.max(0)

    boxes.append(envelope((points[:, 0] < -.18) & (points[:, 2] > .50)))
    for sign in (-1, 1):
        boxes.append(envelope((sign*points[:, 1] > .65) & (points[:, 2] > .50)))
    for sx in (-1, 1):
        for sy in (-1, 1):
            boxes.append(envelope((sx*points[:, 0] > .25) & (sy*points[:, 1] > .65)
                                  & (points[:, 2] < .29)))
    clearances = [
        _free(boxes, [-.24, -.59, .03], [.24, .59, .27]),
        _free(boxes, [-.15, -.60, .51], [.25, .60, .70]),
    ]
    return boxes, {"seat": 0}, clearances


def _rigid_parts(asset_id, geometry, primitives):
    document = geometry.document
    physics = document.get("extensions", {}).get(_PHYSICS, {})
    nodes = document.get("nodes", [])
    if physics.get("physicsJoints") or any(n.get("extras", {}).get("joints") or
            "joint" in n.get("extensions", {}).get(_PHYSICS, {}) for n in nodes):
        raise ValueError("An articulated source cannot be flattened into a rigid asset")
    motions = [n.get("extensions", {}).get(_PHYSICS, {}).get("motion") for n in nodes]
    motions = [m for m in motions if m is not None]
    if len(motions) != 1:
        raise ValueError("Reviewed rigid source needs exactly one source body")
    mass = motions[0].get("mass")
    materials = physics.get("physicsMaterials", [])
    friction = materials[0].get("dynamicFriction") if len(materials) == 1 else .7
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in (mass, friction)) or mass <= 0 or friction < 0:
        raise ValueError("Source mass/friction is invalid")
    low, high = _bounds(primitives)
    supports, clearances = {}, []
    if asset_id == "sofa_soft_b02":
        boxes, supports, clearances = _sofa_boxes(primitives)
    elif asset_id == "furniture_table_coffee_low_a01":
        boxes = component_envelopes(primitives)
        if len(boxes) != 12 or not np.allclose(high-low, [.6000916, 1.2, .4427259], atol=.002, rtol=0):
            raise ValueError("Coffee-table source differs from reviewed components")
        tops = [i for i, (a, b) in enumerate(boxes) if b[1]-a[1] > 1.19 and b[0]-a[0] > .59
                and b[2] > .44]
        shelves = [i for i, (a, b) in enumerate(boxes) if b[1]-a[1] > 1.19
                   and .16 < b[2] < .17]
        if len(tops) != 1 or len(shelves) != 1:
            raise ValueError("Coffee-table source support surfaces changed")
        supports = {"tabletop": tops[0], "lower_shelf": shelves[0]}
        clearances = [_free(boxes, [-.25, -.50, .18], [.25, .50, .39])]
    else:
        boxes = [(low, high)]
    return [{"name": asset_id, "mesh_node": None, "boxes": boxes, "mass": mass,
             "friction": friction, "joint": None, "support_components": supports,
             "handle_components": {}, "clearances": clearances,
             "limitations": (["Source contact friction is absent; runtime estimate 0.7 is used."]
                             if not materials else []),
             "mesh_collision": asset_id in {"misc_golfBall_a01", "handTool_screwdriver_phillips_ph2_a01"}}]


def import_living_room_asset(asset_id: str, cache_dir: str | Path, *, prefix="furniture",
                             position=(0, 0, 0), timeout=120, cancelled=None):
    """Adapt one pinned source, with explicit fixed/mobile/articulated behavior."""
    TypeAdapter(Identifier).validate_python(prefix+"_collision199")
    position = TypeAdapter(Vec3).validate_python(position)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 180:
        raise ValueError("Living-room preparation timeout must be in (0, 180]")
    cache = Path(cache_dir).expanduser().resolve() / asset_id
    deadline = time.monotonic()+timeout

    def check():
        if cancelled and cancelled():
            raise ValueError("Living-room preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("Living-room preparation exceeded its time budget")

    def remaining():
        check()
        return min(120, deadline-time.monotonic())

    def adapt(candidate):
        glb, notice, metadata = _source(candidate, cache, check=check, remaining=remaining)
        decoded = decode_glb(glb, cache / "decoded", timeout=remaining(), cancelled=cancelled)
        geometry = GLBGeometry(decoded.path)
        primitives = geometry.primitives()
        if asset_id == "fixture_cabinet_b01":
            from .living_room_cabinet import cabinet_parts
            parts = cabinet_parts(geometry.document, primitives)
        else:
            parts = _rigid_parts(asset_id, geometry, primitives)
        if not 1 <= sum(len(p["boxes"]) for p in parts) <= 200:
            raise ValueError("Furniture collision exceeds the 200-component budget")
        visuals = prepare_glb_visuals(decoded.path, cache / "visuals", source_notice=notice,
            source_metadata=metadata, timeout=remaining(), cancelled=cancelled)
        low, high = _bounds(primitives)
        offset = np.asarray(position)-np.array([(low[0]+high[0])/2, (low[1]+high[1])/2, low[2]])
        portable = asset_id in {"misc_notebook_a01", "book_open_a01", "misc_golfBall_a01",
                                "handTool_screwdriver_phillips_ph2_a01"}
        root_id = prefix+"_body"
        body_names = {part["name"]: root_id if i == 0 else f"{prefix}_body{i}"
                      for i, part in enumerate(parts)}
        parents = ({p.node_index: root_id for p in primitives} if len(parts) == 1 else
                   {part["mesh_node"]: body_names[part["name"]] for part in parts})
        appearance = visuals.objects(prefix=prefix, parents=parents)
        limitations = [LIVING_ROOM_ASSETS[asset_id]+".",
            "Original optimized source meshes and PBR material maps are retained as separate appearance geometry; the upstream conversion already simplified its original USD meshes/textures.",
            "Collision boxes are source-reviewed conservative envelopes/partitions; curvature and small details can be filled. Rigid book envelopes do not model pages.",
            "Source body masses are retained and distributed by collision proxy volume; source inertia, center of mass and restitution are not retained.",
            "Only geometric and saved-library integrity checks were performed; native contacts, grasp success, reachability and policy execution are unverified.", PBR_RENDERER_NOTICE]
        limitations.extend(note for part in parts for note in part.get("limitations", []))
        collision_policy = (json.loads(_CATALOG.read_text())["cabinet_joint_collision_policy"]
                            if asset_id == "fixture_cabinet_b01" else None)
        recipe_parts = []
        for part in parts:
            recipe_parts.append({key: value for key, value in part.items()
                if key not in {"boxes", "joint"}} | {
                "boxes": [{"low": a.tolist(), "high": b.tolist()} for a, b in part["boxes"]],
                "joint": part["joint"].model_dump() if part["joint"] else None})
        recipe = {"schema_version": 1, "profile": "simready-living-room-source-adaptation-v1",
            "asset_id": asset_id, "source": metadata,
            "decoded_sha256": json.loads(decoded.manifest_path.read_text())["sha256"],
            "mobility": "portable rigid object" if portable else "fixed furniture with declared source joints",
            "parts": recipe_parts, "limitations": limitations}
        if collision_policy:
            recipe["source_joint_collision_policy"] = collision_policy
        encoded = (json.dumps(recipe, indent=2, allow_nan=False)+"\n").encode()
        recipe_path = cache / ("physics-"+hashlib.sha256(encoded).hexdigest()+".json")
        if recipe_path.exists() or recipe_path.is_symlink():
            if recipe_path.is_symlink() or recipe_path.read_bytes() != encoded:
                raise ValueError("Furniture physics provenance cache changed")
        else:
            recipe_path.write_bytes(encoded)
        asset = ImportedAsset(source_files=visuals.source_files, disclosures=limitations)
        asset.source_files.update({"optimized-source.glb": candidate.metadata["download_sha256"],
            "upstream-license.txt": candidate.metadata["upstream_license_sha256"],
            "upstream-packaging.json": candidate.metadata["upstream_packaging_sha256"],
            "furniture-physics.json": hashlib.sha256(encoded).hexdigest()})
        for body_index, part in enumerate(parts):
            body_id = body_names[part["name"]]
            joint = part["joint"].model_copy(deep=True) if part["joint"] else None
            if joint:
                joint.anchor = np.asarray(joint.anchor)+offset
            objects = []
            if part.get("mesh_collision"):
                # Each original primitive supplies a native convex collider on
                # the same rigid body. No decomposition or native engine runs.
                mesh_volumes = np.array([np.prod(visual.size) for visual in appearance])
                for i, visual in enumerate(appearance):
                    if len(parts) != 1:
                        raise ValueError("Mesh prop unexpectedly contains articulated bodies")
                    objects.append(SceneObject(id=body_id if i == 0 else f"{prefix}_collision{i}",
                        label="Source rigid prop convex collision", kind="mesh", mesh_path=visual.mesh_path,
                        position=np.asarray(visual.position)+offset, size=visual.size,
                        movable=portable and i == 0, parent_id=None if i == 0 else body_id,
                        visible=False, mass=part["mass"]*mesh_volumes[i]/mesh_volumes.sum(), friction=part["friction"],
                        source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path)))
                asset.geom_ids["graspable_part"] = body_id
                if "screwdriver" in asset_id:
                    asset.geom_ids["tool_body"] = body_id
                    handle = max(objects, key=lambda obj: obj.size[1]*obj.size[2])
                    asset.geom_ids["grasp_handle"] = handle.id
                    asset.geom_ids["graspable_part"] = handle.id
            else:
                volumes = np.array([np.prod(b-a) for a, b in part["boxes"]])
                for i, ((a, b), volume) in enumerate(zip(part["boxes"], volumes)):
                    oid = body_id if i == 0 else f"{prefix}_body{body_index}_collision{i}"
                    objects.append(SceneObject(id=oid, label="Source furniture/prop collision component",
                        kind="box", position=(a+b)/2+offset, size=b-a,
                        mass=part["mass"]*volume/volumes.sum(), friction=part["friction"],
                        movable=portable and body_index == 0 and i == 0,
                        parent_id=(root_id if joint else None) if i == 0 else body_id,
                        joint=joint if i == 0 else None, visible=False,
                        source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path)))
                for mapping in (part.get("support_components", {}), part.get("handle_components", {})):
                    for name, i in mapping.items():
                        if not isinstance(i, int) or not 0 <= i < len(objects):
                            raise ValueError("Furniture support/handle mapping is invalid")
                        asset.geom_ids[name] = objects[i].id
            asset.objects.extend(objects)
            asset.body_ids[part["name"]] = body_id
            if joint:
                asset.joint_ids[part["name"]] = body_id
            for region in part.get("regions", []):
                if joint:
                    raise ValueError("Moving drawer cavities cannot become fixed world regions")
                a, b = np.asarray(region["low"]), np.asarray(region["high"])
                _free(part["boxes"], a, b)
                asset.regions.append(Region(id=f"{prefix}_{region['id']}",
                    label="Source fixed cabinet storage cavity", position=(a+b)/2+offset,
                    size=b-a, interior_of=body_id))
        for obj in appearance:
            obj.position = np.asarray(obj.position)+offset
            asset.objects.append(obj)
        check()
        return asset

    def unavailable(*args):
        raise ValueError("Source import unresolved; procedural visual replacement is not a fallback")

    return source_asset(asset_id, search=living_room_asset_search, import_asset=adapt,
                        create_asset=unavailable)


def prepare_living_room_library(destination: str | Path, *, cache_dir: str | Path | None = None,
                                asset_ids=None, timeout=420, cancelled=None, on_event=None) -> Path:
    """Publish a new portable library, with at most 30 MB of pinned GLB inputs."""
    requested = tuple(LIVING_ROOM_ASSETS) if asset_ids is None else tuple(asset_ids)
    if (isinstance(asset_ids, str) or not requested or len(set(requested)) != len(requested)
            or any(name not in LIVING_ROOM_ASSETS for name in requested)):
        raise ValueError("Choose distinct reviewed living-room/portable-tool IDs")
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("Living-room library timeout must be in (0, 600]")
    catalog = json.loads(_CATALOG.read_text())
    if sum(row["download_bytes"] for row in catalog["selected"] if row["asset_id"] in requested) > 30_000_000:
        raise ValueError("Living-room source selection exceeds 30 MB")
    destination = Path(destination).expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Library destination already exists; choose a new output")
    cache = Path(cache_dir).expanduser().resolve() if cache_dir else destination.parent / "living-room-preparation/sources"
    if cache.is_relative_to(destination):
        raise ValueError("Library cache must be outside the destination")
    deadline = time.monotonic()+timeout
    library = AssetLibrary()
    for name in requested:
        if cancelled and cancelled():
            raise ValueError("Living-room library preparation cancelled")
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise ValueError("Living-room library preparation exceeded its budget")
        if on_event:
            on_event({"type": "asset_preparing", "asset_id": name})
        sourced = import_living_room_asset(name, cache, timeout=min(180, remaining), cancelled=cancelled)
        library.add(name, sourced, description=LIVING_ROOM_ASSETS[name]+"; native manipulation validation pending")
        if on_event:
            on_event({"type": "asset_prepared", "asset_id": name, "components": len(sourced.asset.objects)})
    return library.save(destination)
