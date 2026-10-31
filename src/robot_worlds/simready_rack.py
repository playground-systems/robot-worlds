"""Pinned rack adaptation with separate structural envelopes and open shelves.

Conservative collision proxies, not a general concave-mesh physics importer.
The original detailed visuals stay separate. Native contact checks remain needed.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from pydantic import TypeAdapter

from .appearance import PBR_RENDERER_NOTICE
from .artifacts import write_json
from .asset_import import ImportedAsset
from .asset_sourcing import source_asset
from .gltf_decode import decode_glb
from .gltf_geometry import GLBGeometry
from .gltf_visuals import prepare_glb_visuals
from .models import Identifier, Region, SceneObject, Vec3
from .simready import download_simready_asset, simready_asset_search

RACK_ID = "rack_large_empty_a02"


def component_envelopes(primitives, *, check=lambda: None):
    """Find welded connected surfaces; use original coordinates for box bounds.

Welding uses a recorded 10-micrometre coordinate grid only for connectivity.
It does not round the actual source bounds. Small holes inside each connected
component are conservatively filled, but disconnected components stay separate.
"""
    vertices = np.concatenate([part.positions for part in primitives])
    if not 4 <= len(vertices) <= 1_000_000 or not np.isfinite(vertices).all():
        raise ValueError("Structural source exceeds vertex budget")
    faces, offset = [], 0
    for part in primitives:
        faces.append(part.triangles + offset)
        offset += len(part.positions)
    triangles = np.concatenate(faces)
    if not 1 <= len(triangles) <= 1_000_000:
        raise ValueError("Structural source exceeds triangle budget")
    check()
    unique, inverse = np.unique(np.round(vertices, 5), axis=0, return_inverse=True)
    parents = list(range(len(unique)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, (a, b, c) in enumerate(inverse[triangles]):
        if index % 4096 == 0:
            check()
        a, b, c = root(int(a)), root(int(b)), root(int(c))
        parents[b] = a
        parents[c] = a
    roots = np.array([root(i) for i in range(len(unique))])[inverse]
    ids, slots = np.unique(roots, return_inverse=True)
    if not 1 <= len(ids) <= 256:
        raise ValueError("Structural source exceeds component budget")
    low = np.full((len(ids), 3), np.inf)
    high = np.full((len(ids), 3), -np.inf)
    np.minimum.at(low, slots, vertices)
    np.maximum.at(high, slots, vertices)
    if np.any(high-low < 1e-6):
        raise ValueError("Zero-thickness source components require a different collision adapter")
    check()
    order = sorted(range(len(ids)), key=lambda i: (*low[i], *high[i]))
    return [(low[i], high[i]) for i in order]


def rack_clearances(boxes):
    """Pinned-source shelf support/clearance checks, independent of native physics."""
    lower = np.min([low for low, _ in boxes], axis=0)
    upper = np.max([high for _, high in boxes], axis=0)
    if not np.allclose(upper-lower, [3.9999777, 2.0667025, 3.0102069], atol=.002, rtol=0):
        raise ValueError("Rack source dimensions differ from the reviewed asset")
    result = []
    for expected_support, ceiling in ((.5059, 1.48), (1.63, 2.23), (2.3813, 2.98)):
        supports = []
        for x in (-1., 1.):
            candidates = [high[2] for low, high in boxes if low[0] <= x <= high[0]
                          and low[1] <= 0 <= high[1] and abs(high[2]-expected_support) < .001]
            if not candidates:
                raise ValueError("Rack shelf is missing its source-derived support")
            supports.append(max(candidates))
        if max(supports)-min(supports) > .001:
            raise ValueError("Rack shelf support panels are misaligned")
        support = max(supports)
        low = np.array([-1.8, -.85, support-.005])
        high = np.array([1.8, .85, ceiling])
        for part_low, part_high in boxes:
            penetration = np.minimum(high, part_high)-np.maximum(low, part_low)
            if np.all(penetration > .0051):
                raise ValueError("Rack collision proxy fills a required shelf opening")
        result.append((low, high, support))
    return result


def import_simready_rack(cache_dir: str | Path, *, prefix="rack", position=(0, 0, 0),
                         timeout=60, cancelled=None, client=None):
    """Acquire/prepare the reviewed rack and return a provenance-backed static asset.

The caller can register this result with AssetLibrary, then use it in arbitrary
generated layouts. This function performs no native simulation or model call.
Collision is an explicit per-component box approximation, with geometric shelf
checks. It does not declare the rack or a complete task simulation-ready.
"""
    TypeAdapter(Identifier).validate_python(prefix+"_collision255")
    position = TypeAdapter(Vec3).validate_python(position)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Rack preparation timeout must be finite and in (0, 120]")
    deadline = time.monotonic()+timeout
    cache = Path(cache_dir).resolve()

    def check():
        if cancelled and cancelled():
            raise ValueError("Rack preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("Rack preparation exceeded its time budget")

    def remaining():
        check()
        return deadline-time.monotonic()

    def adapt(candidate):
        check()
        if candidate.metadata["optimized_manifest_record"]["physics"]["joints"] != 0:
            raise ValueError("Articulated source requires an articulated importer")
        source = download_simready_asset(RACK_ID, cache / "simready", timeout=remaining(),
                                         cancelled=cancelled, client=client)
        decoded = decode_glb(source.glb_path, cache / "gltf-decoded", timeout=remaining(), cancelled=cancelled)
        visuals = prepare_glb_visuals(decoded.path, cache / "gltf-visuals", source_notice=source.notice_path,
            source_metadata=json.loads(source.metadata_path.read_text()), timeout=remaining(), cancelled=cancelled)
        geometry = GLBGeometry(decoded.path)
        doc = geometry.document
        if (doc.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("physicsJoints")
                or any(node.get("extras", {}).get("joints")
                       or node.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("joint")
                       for node in doc.get("nodes", []))):
            raise ValueError("Articulated source cannot be flattened into a static rack")
        primitives = geometry.primitives()
        boxes = component_envelopes(primitives, check=check)
        shelves = rack_clearances(boxes)
        motions = [node.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("motion")
                   for node in doc.get("nodes", [])]
        motions = [motion for motion in motions if motion is not None]
        physics_materials = doc.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("physicsMaterials", [])
        if len(motions) != 1 or len(physics_materials) != 1:
            raise ValueError("Rack source physics differs from the reviewed static adaptation")
        mass = motions[0].get("mass")
        friction = physics_materials[0].get("dynamicFriction")
        if (not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass <= 0
                or not isinstance(friction, (int, float)) or not math.isfinite(friction) or friction < 0):
            raise ValueError("Rack source mass/friction is missing or invalid")
        low = np.min([low for low, _ in boxes], axis=0)
        high = np.max([high for _, high in boxes], axis=0)
        origin = np.array([(low[0]+high[0])/2, (low[1]+high[1])/2, low[2]])
        offset = np.array(position)-origin
        recipe = {"profile": "rack-connected-component-boxes-v1", "source": candidate.metadata,
            "decoded_sha256": json.loads(decoded.manifest_path.read_text())["sha256"],
            "weld_grid_metres": .00001, "mobility": "explicitly fixed warehouse fixture",
            "components": [{"low": a.tolist(), "high": b.tolist()} for a, b in boxes],
            "shelves": [{"low": a.tolist(), "high": b.tolist(), "support_z": z} for a, b, z in shelves],
            "source_total_mass_kg": mass, "source_dynamic_friction": friction,
            "limitations": ["Each connected source surface uses a conservative axis-aligned collision envelope; small holes/detail can be filled.",
                            "Source inertia is not retained; source total mass is distributed by proxy volume on an explicitly fixed assembly.",
                            "Geometric shelf checks are not native contact, reachability or policy-success evidence.", PBR_RENDERER_NOTICE]}
        encoded = (json.dumps(recipe, indent=2, allow_nan=False)+"\n").encode()
        recipe_dir = cache / "rack-physics"
        recipe_dir.mkdir(parents=True, exist_ok=True)
        recipe_path = recipe_dir / (hashlib.sha256(encoded).hexdigest()+".json")
        if recipe_path.exists() or recipe_path.is_symlink():
            if recipe_path.is_symlink() or recipe_path.read_bytes() != encoded:
                raise ValueError("Rack collision provenance cache was changed")
        else:
            # write_json publishes atomically; its formatting matches encoded.
            write_json(recipe_path, recipe)
            if recipe_path.read_bytes() != encoded:
                raise ValueError("Rack collision provenance serialization changed")
        root_id = prefix+"_body"
        volumes = np.array([np.prod(b-a) for a, b in boxes])
        asset = ImportedAsset(source_files=visuals.source_files,
            disclosures=recipe["limitations"]+["Original rack geometry/materials are retained as separate source-derived appearance meshes."])
        asset.source_files["rack-physics.json"] = hashlib.sha256(recipe_path.read_bytes()).hexdigest()
        for index, ((a, b), volume) in enumerate(zip(boxes, volumes)):
            oid = root_id if index == 0 else f"{prefix}_collision{index}"
            asset.objects.append(SceneObject(id=oid, label="Source rack structural collision envelope", kind="box",
                position=(a+b)/2+offset, size=b-a, mass=mass*volume/volumes.sum(), friction=friction,
                parent_id=None if index == 0 else root_id, visible=False,
                source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path)))
        parents = {primitive.node_index: root_id for primitive in primitives}
        for obj in visuals.objects(prefix=prefix, parents=parents):
            obj.position = np.array(obj.position)+offset
            asset.objects.append(obj)
        for index, (a, b, _) in enumerate(shelves):
            asset.regions.append(Region(id=f"{prefix}_shelf{index}", label=f"Source rack shelf {index+1} clearance",
                position=(a+b)/2+offset, size=b-a, interior_of=root_id))
        asset.body_ids["fixed_rack"] = root_id
        check()
        return asset

    def no_creation(*args):
        raise ValueError("Rack import unresolved; procedural creation is not a fallback")

    return source_asset(RACK_ID, search=simready_asset_search, import_asset=adapt, create_asset=no_creation)
