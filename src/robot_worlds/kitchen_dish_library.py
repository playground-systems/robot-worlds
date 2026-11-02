"""One small SimReady bowl with source appearance and a hollow contact profile.

This is a bounded source adaptation, not a replacement visual model or a claim
that native contact, grasping, or robot policies have been validated.
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
from .models import Identifier, RevolvedShape, SceneObject, Vec3
from .revolved import collision_count
from .workbench_library import _source

BOWL_ID = "kitchenware_bowl_b01"
_CATALOG = Path(__file__).parent / "assets/simready/kitchen_dish_catalog.json"
_PHYSICS = "KHR_physics_rigid_bodies"


def kitchen_dish_asset_search(query: str) -> AssetSearch:
    """Return the pinned catalog and checked per-package license evidence."""
    if query != BOWL_ID:
        raise ValueError("Unknown reviewed SimReady dish")
    catalog = json.loads(_CATALOG.read_text())
    row = copy.deepcopy(catalog["selected"])
    candidate = AssetCandidate("simready:"+query, row["optimized_url"],
        catalog["optimized_revision"], "CC-BY-4.0", metadata={**row,
            "upstream_checked_revision": catalog["upstream_checked_revision"],
            "derivation_upstream_revision": catalog["optimized_derivation_upstream_revision"]})
    return AssetSearch(query, tuple(catalog["catalogs"]), (candidate,), True,
        {"queries": catalog["queries"], "rejected_candidates": catalog["rejected"],
         "microwave_lookup": catalog["microwave_lookup"],
         "manifest_sha256": catalog["manifest_sha256"],
         "scope": "Recorded bounded source lookup; no fresh online lookup during preparation"})


def _bowl_profile(primitives):
    """Sample twelve reviewed radial rings from this pinned hollow source mesh."""
    vertices = np.concatenate([primitive.positions for primitive in primitives])
    low, high = vertices.min(0), vertices.max(0)
    if not np.allclose(high-low, [.12985, .12985, .059992566], atol=.00001, rtol=0):
        raise ValueError("The reviewed bowl mesh bounds changed")
    radial = np.linalg.norm(vertices[:, :2], axis=1)
    radius, height = float(radial.max()), float(high[2]-low[2])

    def ring(z, *, exact_radius=None):
        near = np.abs(vertices[:, 2]-z) < .000005
        if np.count_nonzero(near) < 10:
            raise ValueError("A reviewed source bowl radial ring is missing")
        r = float(np.median(radial[near])) if exact_radius is None else exact_radius
        return (r, float(np.median(vertices[near, 2])-low[2]))

    # Clockwise material boundary: underside center/foot, outer wall, rounded
    # lip, inner wall, interior bottom. All heights/radii sample source rings.
    bottom_center = vertices[np.argmin(radial+100*np.abs(vertices[:, 2]-.002563))]
    inner_center = vertices[np.argmin(radial+100*np.abs(vertices[:, 2]-.004962))]
    lowest = int(np.argmin(vertices[:, 2]))
    widest = int(np.argmax(radial))
    highest = int(np.argmax(vertices[:, 2]))
    profile = [
        (0., float(bottom_center[2]-low[2])),
        (float(radial[lowest]), 0.),
        ring(.005929), ring(.014445), ring(.025549),
        (radius, float(vertices[widest, 2]-low[2])),
        (float(radial[highest]), height),
        ring(.058753), ring(.040923), ring(.014754), ring(.006895),
        (0., float(inner_center[2]-low[2])),
    ]
    # Bound the simplification error against every original vertex in radial
    # cross-section. Optimized rings are slightly noncircular (about 0.1 mm).
    sample = np.column_stack((radial, vertices[:, 2]-low[2]))
    distances = []
    for a, b in zip(profile, profile[1:]+profile[:1]):
        a, b = np.asarray(a), np.asarray(b)
        t = np.clip(((sample-a)@(b-a))/np.dot(b-a, b-a), 0, 1)
        distances.append(np.linalg.norm(sample-(a+t[:, None]*(b-a)), axis=1))
    error = float(np.min(distances, axis=0).max())
    if error > .004:
        raise ValueError("Bowl source differs from the reviewed radial simplification")
    normalized = [(r/(2*radius), min(.5, max(-.5, z/height-.5))) for r, z in profile]
    shape = RevolvedShape(profile=normalized, segments=16)
    return low, high, radius, profile, shape, error


def import_kitchen_dish(cache_dir: str | Path, *, prefix="bowl", position=(0, 0, 0),
                        timeout=120, cancelled=None):
    """Import the original bowl visuals and a disclosed source-sampled cavity."""
    TypeAdapter(Identifier).validate_python(prefix+"_body")
    position = TypeAdapter(Vec3).validate_python(position)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 180:
        raise ValueError("Dish preparation timeout must be in (0, 180]")
    cache = Path(cache_dir).expanduser().resolve() / BOWL_ID
    deadline = time.monotonic()+timeout

    def check():
        if cancelled and cancelled():
            raise ValueError("Dish preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("Dish preparation exceeded its time budget")

    def remaining():
        check()
        return min(120, deadline-time.monotonic())

    def adapt(candidate):
        glb, notice, metadata = _source(candidate, cache, check=check, remaining=remaining)
        decoded = decode_glb(glb, cache / "decoded", timeout=remaining(), cancelled=cancelled)
        geometry = GLBGeometry(decoded.path)
        primitives = geometry.primitives()
        nodes = geometry.document.get("nodes", [])
        physics = geometry.document.get("extensions", {}).get(_PHYSICS, {})
        motions = [node.get("extensions", {}).get(_PHYSICS, {}).get("motion") for node in nodes]
        motions = [motion for motion in motions if motion is not None]
        if (len(motions) != 1 or physics.get("physicsJoints") or
                any(node.get("extras", {}).get("joints") or
                    "joint" in node.get("extensions", {}).get(_PHYSICS, {}) for node in nodes)):
            raise ValueError("Reviewed source dish must remain one unarticulated rigid body")
        mass = motions[0].get("mass")
        if isinstance(mass, bool) or not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass <= 0:
            raise ValueError("Source dish mass is invalid")
        low, high, radius, profile, shape, error = _bowl_profile(primitives)
        offset = np.asarray(position)-np.array([(low[0]+high[0])/2, (low[1]+high[1])/2, low[2]])
        limitations = [
            "Original SimReady bowl optimized meshes, UVs and material maps are retained as appearance geometry.",
            "Source convex-hull contact fills the bowl; only contact geometry is replaced with a hollow twelve-ring radial cross-section sampled from the actual source mesh, swept into sixteen angular sectors.",
            f"Maximum original-vertex to simplified radial-boundary distance is {error:.6f} m; sixteen-sector contact facets also approximate curvature.",
            "The source mass is retained; source center of mass/inertia and restitution are not retained. Source contact friction is absent, so runtime friction 0.7 is an explicit estimate.",
            "The source bowl is about 13 cm wide; rim grasping, reachability, contacts and placement require native validation. No fluid model or moving interior region is provided.",
            "Only geometry and portable-library integrity checks were performed; no native simulation, tests, or policy execution was run.",
            PBR_RENDERER_NOTICE,
        ]
        recipe = {"schema_version": 1, "profile": "simready-source-hollow-bowl-v1",
            "asset_id": BOWL_ID, "source": metadata, "source_mass_kg": mass,
            "source_bounds": {"low": low.tolist(), "high": high.tolist()},
            "radial_profile_m": profile, "physical_radius_m": radius,
            "radial_boundary_max_vertex_error_m": error,
            "revolved": shape.model_dump(), "limitations": limitations}
        encoded = (json.dumps(recipe, indent=2, allow_nan=False)+"\n").encode()
        digest = hashlib.sha256(encoded).hexdigest()
        recipe_path = cache / ("physics-"+digest+".json")
        if recipe_path.exists() or recipe_path.is_symlink():
            if recipe_path.is_symlink() or recipe_path.read_bytes() != encoded:
                raise ValueError("Dish physics provenance cache changed")
        else:
            recipe_path.write_bytes(encoded)
        visuals = prepare_glb_visuals(decoded.path, cache / "visuals", source_notice=notice,
            source_metadata=metadata, timeout=remaining(), cancelled=cancelled)
        body_id = prefix+"_body"
        body = SceneObject(id=body_id, label="Original SimReady bowl with source-derived hollow collision",
            kind="revolved", position=np.asarray(position)+(0, 0, (high[2]-low[2])/2),
            size=(2*radius, 2*radius, high[2]-low[2]), revolved=shape,
            movable=True, mass=mass, friction=.7, visible=False,
            source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path))
        if collision_count(body) > 200:
            raise ValueError("Dish exceeds its bounded convex contact component budget")
        appearance = visuals.objects(prefix=prefix, parents={p.node_index: body_id for p in primitives})
        for obj in appearance:
            obj.position = np.asarray(obj.position)+offset
        result = ImportedAsset(objects=[body, *appearance],
            body_ids={"portable_bowl": body_id}, geom_ids={"bowl_body": body_id},
            source_files={**visuals.source_files,
                "optimized-source.glb": candidate.metadata["download_sha256"],
                "upstream-license.txt": candidate.metadata["upstream_license_sha256"],
                "upstream-packaging.json": candidate.metadata["upstream_packaging_sha256"],
                "dish-physics.json": digest}, disclosures=limitations)
        check()
        return result

    def unavailable(*args):
        raise ValueError("Source dish import unresolved; no procedural visual replacement")

    return source_asset(BOWL_ID, search=kitchen_dish_asset_search,
                        import_asset=adapt, create_asset=unavailable)


def prepare_kitchen_dish_library(destination: str | Path, *, cache_dir: str | Path | None = None,
                                 timeout=180, cancelled=None) -> Path:
    """Publish one portable source bowl; the input GLB is only 77,228 bytes."""
    destination = Path(destination).expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Library destination already exists; choose a new output")
    cache = Path(cache_dir).expanduser().resolve() if cache_dir else destination.parent / "kitchen-dish-preparation"
    if cache.is_relative_to(destination):
        raise ValueError("Library cache must be outside the destination")
    asset = import_kitchen_dish(cache, timeout=timeout, cancelled=cancelled)
    library = AssetLibrary()
    library.add(BOWL_ID, asset, description="Movable original SimReady ceramic bowl with a source-derived hollow contact cavity; 13 cm wide, native rim grasping unverified")
    return library.save(destination)
