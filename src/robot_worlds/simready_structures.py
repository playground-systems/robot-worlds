"""Reviewed rigid warehouse assets with separate source visuals and collisions.

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
PROFILES = {
    RACK_ID: ("rack", [3.9999777, 2.0667025, 3.0102069], False),
    "pallet_a01": ("pallet", [1.2042018, .8003512, .2085955], True),
    "box_corrugated_brown_b20": ("carton", [.2989821, .6215816, .3020173], True),
    "barrier_fence_metal_a02": ("barrier", [1.2298000, .2299940, 1.0719345], False),
}
COLLISION_NOTICES = {
    "rack": "Conservative envelopes follow connected source components; small holes and detail can be filled.",
    "pallet": "Conservative envelopes follow separate source boards and blocks; fork channels remain open. Splinters and small gaps are simplified.",
    "carton": "Closed shipping carton uses one conservative source-bounds box. It has no openable flaps, interior interaction or deformable cardboard.",
    "barrier": "Fixed barrier uses six conservative source-derived envelopes for feet, posts and rails. Bolt holes and rounded profiles are simplified; rail openings remain open.",
}


def component_bounds(primitives, *, check=lambda: None):
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
    check()
    order = sorted(range(len(ids)), key=lambda i: (*low[i], *high[i]))
    return [(low[i], high[i]) for i in order]


def component_envelopes(primitives, *, check=lambda: None):
    """Rigid recipes require volumetric components; never silently thicken them."""
    boxes = component_bounds(primitives, check=check)
    if any(np.any(high-low < 1e-6) for low, high in boxes):
        raise ValueError("Zero-thickness source components require a different collision adapter")
    return boxes


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


def _clearance(boxes, low, high, name, label):
    low, high = np.array(low, dtype=float), np.array(high, dtype=float)
    for a, b in boxes:
        if np.all(np.minimum(high, b)-np.maximum(low, a) > 1e-6):
            raise ValueError("Structure collision proxy fills a required clearance")
    return {"id": name, "label": label, "low": low, "high": high}


def _closed_carton(primitives, low, high):
    """Sample both faces along each axis; a source with an open face is rejected.

    The pinned source is a sealed carton. These geometric regression samples
    do not prove arbitrary-mesh watertightness or validate contact dynamics.
    """
    triangles = np.concatenate([p.positions[p.triangles] for p in primitives])
    for axis in range(3):
        u, v = [i for i in range(3) if i != axis]
        a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        determinant = (b[:, v]-c[:, v])*(a[:, u]-c[:, u])+(c[:, u]-b[:, u])*(a[:, v]-c[:, v])
        valid = abs(determinant) > 1e-12
        a, b, c, determinant = a[valid], b[valid], c[valid], determinant[valid]
        for fu in (.25, .5, .75):
            for fv in (.25, .5, .75):
                x, y = low[u]+fu*(high[u]-low[u]), low[v]+fv*(high[v]-low[v])
                first = ((b[:, v]-c[:, v])*(x-c[:, u])+(c[:, u]-b[:, u])*(y-c[:, v]))/determinant
                second = ((c[:, v]-a[:, v])*(x-c[:, u])+(a[:, u]-c[:, u])*(y-c[:, v]))/determinant
                hit = (first >= -1e-8) & (second >= -1e-8) & (first+second <= 1+1e-8)
                coordinate = (first*a[:, axis]+second*b[:, axis]+(1-first-second)*c[:, axis])[hit]
                if not (np.any(abs(coordinate-low[axis]) < .006) and np.any(abs(coordinate-high[axis]) < .006)):
                    raise ValueError("Carton source lacks a required closed face; do not fill its interior")


def structure_geometry(asset_id, primitives, *, check=lambda: None):
    """Reviewed collision recipes, not a universal convex-hull fallback."""
    check()
    if asset_id == RACK_ID:
        boxes = component_envelopes(primitives, check=check)
        clearances = [{"id": f"shelf{i}", "label": f"Source rack shelf {i+1} clearance",
                       "low": low, "high": high, "support_z": support}
                      for i, (low, high, support) in enumerate(rack_clearances(boxes))]
    elif asset_id == "pallet_a01":
        boxes = component_envelopes(primitives, check=check)
        clearances = []
        for axis, pairs in ((0, ((-.22, -.1), (.1, .22))), (1, ((-.42, -.1), (.1, .42)))):
            for start, end in pairs:
                low, high = ([-.601, start, .04], [.601, end, .125]) if axis == 0 else ([start, -.399, .04], [end, .399, .125])
                clearances.append(_clearance(boxes, low, high, f"fork{len(clearances)}", "Pallet fork channel clearance"))
    elif asset_id == "box_corrugated_brown_b20":
        vertices = np.concatenate([p.positions for p in primitives])
        low, high = vertices.min(0), vertices.max(0)
        _closed_carton(primitives, low, high)
        boxes, clearances = [(low, high)], []
    elif asset_id == "barrier_fence_metal_a02":
        vertices = np.concatenate([p.positions for p in primitives])
        x, z = vertices[:, 0], vertices[:, 2]
        masks = [(x < .2) & (z < .08), (x > .8) & (z < .08),
                 (x > -.06) & (x < .06), (x > .94) & (x < 1.06),
                 (z > .3) & (z < .42), z > .95]
        boxes = []
        for mask in masks:
            points = vertices[mask]
            if len(points) < 4:
                raise ValueError("Barrier source is missing a reviewed foot, post or rail")
            boxes.append((points.min(0), points.max(0)))
        # Every whole source triangle must fit one envelope, not just its vertices.
        triangles = np.concatenate([p.positions[p.triangles] for p in primitives])
        low, high = triangles.min(1), triangles.max(1)
        covered = np.zeros(len(triangles), dtype=bool)
        for a, b in boxes:
            covered |= np.all(low >= a-1e-6, axis=1) & np.all(high <= b+1e-6, axis=1)
        if not covered.all():
            raise ValueError("Barrier envelopes do not cover all source triangles")
        clearances = [_clearance(boxes, [.1, -.06, low], [.9, .06, high], f"opening{i}",
                                 "Barrier rail clearance; not a container")
                      for i, (low, high) in enumerate(((.08, .29), (.43, .93)))]
    else:
        raise ValueError("No reviewed collision recipe for this source")
    check()
    return boxes, clearances


def import_simready_structure(asset_id: str, cache_dir: str | Path, *, prefix="fixture", position=(0, 0, 0),
                              timeout=60, cancelled=None, client=None):
    """Adapt one of four reviewed rigid sources for portable generation libraries.

    Source geometry/materials remain separate from disclosed collision proxies.
    Performs geometric checks, not native contact or policy-success validation.
    Unsupported sources and articulation fail instead of becoming decorative props.
    """
    if asset_id not in PROFILES:
        raise ValueError("No reviewed rigid adapter for this source; articulation requires a different importer")
    kind, expected_size, movable = PROFILES[asset_id]
    TypeAdapter(Identifier).validate_python(prefix+"_collision255")
    position = TypeAdapter(Vec3).validate_python(position)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Structure preparation timeout must be finite and in (0, 120]")
    deadline = time.monotonic()+timeout
    cache = Path(cache_dir).resolve()

    def check():
        if cancelled and cancelled():
            raise ValueError("Structure preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("Structure preparation exceeded its time budget")

    def remaining():
        check()
        return deadline-time.monotonic()

    def adapt(candidate):
        check()
        if candidate.metadata["optimized_manifest_record"]["physics"]["joints"] != 0:
            raise ValueError("Articulated source requires an articulated importer")
        source = download_simready_asset(asset_id, cache / "simready", timeout=remaining(),
                                         cancelled=cancelled, client=client)
        decoded = decode_glb(source.glb_path, cache / "gltf-decoded", timeout=remaining(), cancelled=cancelled)
        visuals = prepare_glb_visuals(decoded.path, cache / "gltf-visuals", source_notice=source.notice_path,
            source_metadata=json.loads(source.metadata_path.read_text()), timeout=remaining(), cancelled=cancelled)
        geometry = GLBGeometry(decoded.path)
        doc = geometry.document
        if (doc.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("physicsJoints")
                or any(node.get("extras", {}).get("joints")
                       or "joint" in node.get("extensions", {}).get("KHR_physics_rigid_bodies", {})
                       for node in doc.get("nodes", []))):
            raise ValueError("Articulated source cannot be flattened into a rigid structure")
        primitives = geometry.primitives()
        boxes, clearances = structure_geometry(asset_id, primitives, check=check)
        lower = np.min([low for low, _ in boxes], axis=0)
        upper = np.max([high for _, high in boxes], axis=0)
        if not np.allclose(upper-lower, expected_size, atol=.002, rtol=0):
            raise ValueError("Structure source dimensions differ from the reviewed asset")
        motions = [node.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("motion")
                   for node in doc.get("nodes", [])]
        motions = [motion for motion in motions if motion is not None]
        physics_materials = doc.get("extensions", {}).get("KHR_physics_rigid_bodies", {}).get("physicsMaterials", [])
        if len(motions) != 1 or len(physics_materials) != 1:
            raise ValueError("Structure source physics differs from the reviewed rigid adaptation")
        mass = motions[0].get("mass")
        friction = physics_materials[0].get("dynamicFriction")
        if (not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass <= 0
                or not isinstance(friction, (int, float)) or not math.isfinite(friction) or friction < 0):
            raise ValueError("Structure source mass/friction is missing or invalid")
        low = np.min([low for low, _ in boxes], axis=0)
        high = np.max([high for _, high in boxes], axis=0)
        origin = np.array([(low[0]+high[0])/2, (low[1]+high[1])/2, low[2]])
        offset = np.array(position)-origin
        supports = {}
        if kind == "rack":
            for item in clearances:
                # Expose the same source panels used by the shelf-clearance
                # check, so planning can name a real on_top target.
                indices = [i for i, (a, b) in enumerate(boxes)
                           if abs(b[2]-item["support_z"]) < .001 and a[1] <= 0 <= b[1]
                           and any(a[0] <= x <= b[0] for x in (-1., 1.))]
                if not indices:
                    raise ValueError("Rack shelf has no addressable support component")
                supports.update({f"{item['id']}_support{j}": i for j, i in enumerate(indices)})
        recipe = {"profile": f"{kind}-source-proxies-v1", "asset_id": asset_id, "source": candidate.metadata,
            "decoded_sha256": json.loads(decoded.manifest_path.read_text())["sha256"],
            "weld_grid_metres": .00001 if kind in {"rack", "pallet"} else None,
            "mobility": "movable rigid assembly" if movable else "explicitly fixed warehouse fixture",
            "components": [{"low": a.tolist(), "high": b.tolist()} for a, b in boxes],
            "clearances": [{**item, "low": item["low"].tolist(), "high": item["high"].tolist()} for item in clearances],
            "support_components": supports,
            "source_total_mass_kg": mass, "source_dynamic_friction": friction,
            "limitations": [COLLISION_NOTICES[kind],
                            "Source inertia, center of mass and restitution are not retained; source total mass is distributed by proxy volume.",
                            "Geometric checks are not native contact, reachability or policy-success evidence.", PBR_RENDERER_NOTICE]}
        encoded = (json.dumps(recipe, indent=2, allow_nan=False)+"\n").encode()
        recipe_dir = cache / "structure-physics"
        recipe_dir.mkdir(parents=True, exist_ok=True)
        recipe_path = recipe_dir / (hashlib.sha256(encoded).hexdigest()+".json")
        if recipe_path.exists() or recipe_path.is_symlink():
            if recipe_path.is_symlink() or recipe_path.read_bytes() != encoded:
                raise ValueError("Structure collision provenance cache was changed")
        else:
            # write_json publishes atomically; its formatting matches encoded.
            write_json(recipe_path, recipe)
            if recipe_path.read_bytes() != encoded:
                raise ValueError("Structure collision provenance serialization changed")
        root_id = prefix+"_body"
        volumes = np.array([np.prod(b-a) for a, b in boxes])
        asset = ImportedAsset(source_files=visuals.source_files,
            disclosures=recipe["limitations"]+["Original geometry/materials are retained as separate source-derived appearance meshes."])
        asset.source_files["structure-physics.json"] = hashlib.sha256(recipe_path.read_bytes()).hexdigest()
        for index, ((a, b), volume) in enumerate(zip(boxes, volumes)):
            oid = root_id if index == 0 else f"{prefix}_collision{index}"
            asset.objects.append(SceneObject(id=oid, label=f"Source {kind} collision envelope", kind="box",
                position=(a+b)/2+offset, size=b-a, mass=mass*volume/volumes.sum(), friction=friction,
                parent_id=None if index == 0 else root_id, visible=False, movable=movable and index == 0,
                source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path)))
        parents = {primitive.node_index: root_id for primitive in primitives}
        for obj in visuals.objects(prefix=prefix, parents=parents):
            obj.position = np.array(obj.position)+offset
            asset.objects.append(obj)
        # Fork/rail openings are geometry evidence, not stationary task targets.
        # In particular, a movable pallet must not export world-fixed regions.
        for item in clearances if kind == "rack" else []:
            a, b = item["low"], item["high"]
            asset.regions.append(Region(id=f"{prefix}_{item['id']}", label=item["label"],
                position=(a+b)/2+offset, size=b-a, interior_of=root_id))
        asset.geom_ids.update({name: root_id if index == 0 else f"{prefix}_collision{index}"
                               for name, index in supports.items()})
        asset.body_ids[("movable_" if movable else "fixed_")+kind] = root_id
        check()
        return asset

    def no_creation(*args):
        raise ValueError("Structure import unresolved; procedural creation is not a fallback")

    return source_asset(asset_id, search=simready_asset_search, import_asset=adapt, create_asset=no_creation)


def import_simready_rack(cache_dir: str | Path, *, prefix="rack", position=(0, 0, 0),
                         timeout=60, cancelled=None, client=None):
    """Backward-compatible convenience importer for the reviewed static rack."""
    return import_simready_structure(RACK_ID, cache_dir, prefix=prefix, position=position,
                                    timeout=timeout, cancelled=cancelled, client=client)
