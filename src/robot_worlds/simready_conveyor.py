"""Reviewed passive conveyor adaptation with original visual meshes.

This is deliberately limited to the pinned conveyor's fixed frame and 29 passive
rollers. It does not import a powered conveyor or certify contact behavior.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import TypeAdapter

from .appearance import PBR_RENDERER_NOTICE
from .artifacts import write_json
from .asset_import import ImportedAsset
from .asset_sourcing import source_asset
from .gltf_decode import decode_glb
from .gltf_geometry import _Z_UP, GLBGeometry, _transform
from .gltf_visuals import prepare_glb_visuals
from .models import Identifier, JointSpec, SceneObject, Vec3
from .simready import download_simready_asset, simready_asset_search

_PHYSICS = "KHR_physics_rigid_bodies"
_LIMITS = [{"linearAxes": [0, 1, 2], "min": 0, "max": 0},
           {"angularAxes": [1, 2], "min": 0, "max": 0}]


@dataclass(frozen=True)
class ConveyorBody:
    node_index: int
    mesh_node: int
    source_name: str
    mass: float
    friction: float
    joint: JointSpec | None


def conveyor_bodies(document: dict) -> list[ConveyorBody]:
    """Cross-check both joint frames and source extras in Z-up world coordinates.

    The first body is the frame, explicitly intended for fixed installation.
    Remaining bodies retain unlimited passive hinges. Damping/frictionloss are
    zero because the source does not provide bearing losses; callers must disclose
    that approximation, as well as any mass/inertia or collision conversion.
    """
    nodes = document.get("nodes", [])
    scenes = document.get("scenes", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 256 or len(scenes) != 1 or document.get("scene", 0) != 0:
        raise ValueError("Conveyor requires one bounded source scene")
    if document.get("animations") or document.get("skins"):
        raise ValueError("Conveyor animation/skinning is unsupported")
    library = document.get("extensions", {}).get(_PHYSICS, {})
    if set(library) != {"physicsMaterials", "physicsJoints"} or library["physicsJoints"] != [{"limits": _LIMITS}]:
        raise ValueError("Conveyor requires passive unlimited X hinges without drives")
    materials = library["physicsMaterials"]
    if not isinstance(materials, list) or len(materials) != 2:
        raise ValueError("Conveyor source must contain its two physics materials")
    transforms, parents = {}, {}
    stack = [(index, _Z_UP, None, 0) for index in scenes[0].get("nodes", [])]
    while stack:
        index, parent_frame, parent, depth = stack.pop()
        if (isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(nodes)
                or index in transforms or depth > 32):
            raise ValueError("Conveyor hierarchy has invalid references, repeated parents or cycles")
        node = nodes[index]
        transform = parent_frame @ _transform(node)
        if (not np.isfinite(transform).all()
                or set(node.get("extensions", {})) - {_PHYSICS}):
            raise ValueError("Unsupported conveyor node transform or extension")
        transforms[index], parents[index] = transform, parent
        stack.extend((child, transform, index, depth+1) for child in node.get("children", []))
    if len(transforms) != len(nodes):
        raise ValueError("Conveyor has unreachable nodes; physics cannot be dropped")
    physics = {i: node.get("extensions", {}).get(_PHYSICS, {}) for i, node in enumerate(nodes)}
    bodies = [i for i, value in physics.items() if "motion" in value]
    joints = [i for i, value in physics.items() if "joint" in value]
    colliders = [i for i, value in physics.items() if "collider" in value]
    if len(bodies) != 30 or len(joints) != 29 or len(colliders) != 30:
        raise ValueError("Conveyor must retain all 30 bodies, 29 joints and 30 colliders")
    if any(set(value) - {"motion", "joint", "collider"} for value in physics.values()):
        raise ValueError("Unsupported conveyor physics feature")
    names = [nodes[i].get("name") for i in bodies]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != 30:
        raise ValueError("Conveyor body names must be unique")
    roots = [i for i in bodies if not nodes[i].get("extras", {}).get("joints")]
    if len(roots) != 1:
        raise ValueError("Conveyor needs one fixed frame and 29 roller joint declarations")
    root = roots[0]
    parsed_joints = {}
    for index in joints:
        definition = physics[index]["joint"]
        if set(definition) != {"connectedNode", "joint"} or type(definition["joint"]) is not int or definition["joint"] != 0:
            raise ValueError("Unsupported conveyor joint definition")
        connected = definition["connectedNode"]
        if type(connected) is not int or connected not in transforms or parents[index] != root:
            raise ValueError("Conveyor joint must connect the frame to a roller")
        body = parents[connected]
        if body not in bodies or body == root or body in parsed_joints:
            raise ValueError("Conveyor roller joint mapping is missing or duplicated")
        first, second = transforms[index], transforms[connected]
        if (not np.allclose(first, second, atol=2e-6, rtol=0)
                or not np.allclose(first[:3, :3].T @ first[:3, :3], np.eye(3), atol=1e-6, rtol=0)
                or not np.isclose(np.linalg.det(first[:3, :3]), 1, atol=1e-6)):
            raise ValueError("Conveyor joint frames disagree or include scale/reflection")
        declarations = nodes[body].get("extras", {}).get("joints", [])
        if len(declarations) != 1:
            raise ValueError("Conveyor roller needs exactly one source joint declaration")
        extra = declarations[0]
        if (set(extra) != {"name", "type", "parent", "pivot", "axis", "unit"}
                or extra["type"] != "revolute" or extra["parent"] != nodes[root]["name"]
                or extra["unit"] != "degrees"):
            raise ValueError("Conveyor joint declaration differs from passive revolute source")
        # Source extras are global Y-up. Frame transforms independently establish
        # the actual axis and pivot; disagreement must not be silently repaired.
        pivot, axis = np.asarray(extra["pivot"], dtype=float), np.asarray(extra["axis"], dtype=float)
        if (pivot.shape != (3,) or axis.shape != (3,)
                or not np.allclose(_Z_UP[:3, :3] @ pivot, first[:3, 3], atol=2e-6, rtol=0)
                or not np.allclose(_Z_UP[:3, :3] @ axis, first[:3, 0], atol=1e-6, rtol=0)):
            raise ValueError("Conveyor source joint declaration disagrees with its frames")
        parsed_joints[body] = JointSpec(type="hinge", axis=first[:3, 0], anchor=first[:3, 3],
                                       range=(-np.pi, np.pi), limited=False,
                                       damping=0, frictionloss=0)
    if set(parsed_joints) != set(bodies)-{root}:
        raise ValueError("Conveyor must retain every roller hinge")
    result, used_colliders = [], set()
    for body in [root]+sorted(parsed_joints):
        meshes = [i for i in colliders if parents[i] == body]
        if len(meshes) != 1:
            raise ValueError("Conveyor body must have one source collision mesh")
        mesh = meshes[0]
        collider = physics[mesh]["collider"]
        material = collider.get("physicsMaterial")
        if (set(collider) != {"geometry", "physicsMaterial"}
                or collider["geometry"] != {"node": mesh, "convexHull": True}
                or "mesh" not in nodes[mesh] or type(material) is not int or not 0 <= material < 2):
            raise ValueError("Conveyor collider differs from the reviewed source")
        used_colliders.add(mesh)
        motion = physics[body]["motion"]
        if set(motion) != {"mass", "centerOfMass", "inertiaDiagonal", "inertiaOrientation"}:
            raise ValueError("Unsupported conveyor motion properties")
        mass, friction = motion["mass"], materials[material].get("dynamicFriction")
        if (type(mass) not in (int, float) or not np.isfinite(mass) or mass <= 0
                or type(friction) not in (int, float) or not np.isfinite(friction) or friction < 0):
            raise ValueError("Conveyor requires finite positive mass and nonnegative friction")
        result.append(ConveyorBody(body, mesh, nodes[body]["name"], mass, friction, parsed_joints.get(body)))
    if used_colliders != set(colliders) or {i for i, node in enumerate(nodes) if "mesh" in node} != used_colliders:
        raise ValueError("Conveyor has unmapped visual or collision geometry")
    return result


def conveyor_frame_envelopes(primitives, *, check=lambda: None):
    """Source-component envelopes, with a recorded 0.1 mm planar skin thickness.

    This source-specific approximation keeps every frame component rather than
    dropping planar source surfaces. It is not a concave decomposition algorithm.
    """
    from .simready_structures import component_bounds

    bounds = component_bounds(primitives, check=check)
    if len(bounds) != 191:
        raise ValueError("Conveyor frame differs from the reviewed 191 components")
    planar = [i for i, (low, high) in enumerate(bounds) if np.any(high-low < 1e-6)]
    if len(planar) != 12:
        raise ValueError("Conveyor frame differs from the reviewed 12 planar surfaces")
    boxes = []
    for low, high in bounds:
        if np.count_nonzero(high-low < 1e-6) > 1:
            raise ValueError("Conveyor contains a degenerate line or point component")
        center, size = (low+high)/2, np.where(high-low < 1e-6, .0001, high-low)
        boxes.append((center-size/2, center+size/2))
    # The usable central bed must stay clear above the rollers. This check is
    # specific to the pinned source dimensions, not a general solvability proof.
    bed_low, bed_high = np.array([.20, -1.93, .771]), np.array([.89, -.07, 1.10])
    if any(np.all(np.minimum(bed_high, high)-np.maximum(bed_low, low) > 1e-6)
           for low, high in boxes):
        raise ValueError("Conveyor frame proxy obstructs the usable roller bed")
    return boxes, planar


def _roller_clearances(boxes, primitives, bodies, *, check=lambda: None):
    """Carve bearing pockets from frame proxies, never from source visuals.

    Each pocket encloses a roller's full swept X-axis volume with 1 mm clearance.
    Hinges supply the bearing constraint; the frame must not collide through a
    rotating shaft merely because a conservative component envelope filled a hole.
    """
    pockets = []
    for body in bodies[1:]:
        check()
        points = np.concatenate([p.positions for p in primitives if p.node_index == body.mesh_node])
        anchor = np.array(body.joint.anchor)
        radius = np.linalg.norm(points[:, 1:]-anchor[1:], axis=1).max()+.001
        low = np.array([points[:, 0].min()-.001, anchor[1]-radius, anchor[2]-radius])
        high = np.array([points[:, 0].max()+.001, anchor[1]+radius, anchor[2]+radius])
        pockets.append((low, high))
        remaining = []
        for a, b in boxes:
            cut_low, cut_high = np.maximum(a, low), np.minimum(b, high)
            if np.any(cut_high <= cut_low):
                remaining.append((a, b))
                continue
            # Disjoint complement of the intersection: at most six slabs.
            start, end = a.copy(), b.copy()
            for axis in range(3):
                if cut_low[axis]-start[axis] > 1e-6:
                    edge = end.copy()
                    edge[axis] = cut_low[axis]
                    remaining.append((start.copy(), edge))
                start[axis] = cut_low[axis]
                if end[axis]-cut_high[axis] > 1e-6:
                    edge = start.copy()
                    edge[axis] = cut_high[axis]
                    remaining.append((edge, end.copy()))
                end[axis] = cut_high[axis]
        boxes = remaining
        if not boxes or len(boxes) > 1024:
            raise ValueError("Conveyor bearing pockets exceed frame collision budget")
    return boxes, pockets

def import_simready_conveyor(cache_dir: str | Path, *, prefix="conveyor", position=(0, 0, 0),
                             timeout=60, cancelled=None, client=None):
    """Prepare the pinned conveyor as a fixed frame with 29 unpowered rollers.

    This preserves articulation, source visuals and source mass/friction values.
    It does not establish native contact stability or powered material transport.
    """
    TypeAdapter(Identifier).validate_python(prefix+"_frame1023")
    position = TypeAdapter(Vec3).validate_python(position)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Conveyor preparation timeout must be finite and in (0, 120]")
    deadline = time.monotonic()+timeout
    cache = Path(cache_dir).resolve()

    def check():
        if cancelled and cancelled():
            raise ValueError("Conveyor preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("Conveyor preparation exceeded its time budget")

    def remaining():
        check()
        return deadline-time.monotonic()

    def adapt(candidate):
        check()
        if candidate.metadata["optimized_manifest_record"]["physics"] != {
                "rigidBodies": 30, "joints": 29, "colliders": 30}:
            raise ValueError("Conveyor catalog differs from the reviewed mechanism")
        source = download_simready_asset("conveyor_belt_a05", cache / "simready", timeout=remaining(),
                                         cancelled=cancelled, client=client)
        decoded = decode_glb(source.glb_path, cache / "gltf-decoded", timeout=remaining(), cancelled=cancelled)
        geometry = GLBGeometry(decoded.path)
        bodies = conveyor_bodies(geometry.document)
        primitives = geometry.primitives()
        check()
        frame = bodies[0]
        boxes, planar = conveyor_frame_envelopes([p for p in primitives if p.node_index == frame.mesh_node], check=check)
        by_node = {body.mesh_node: [p for p in primitives if p.node_index == body.mesh_node] for body in bodies}
        # Source roller colliders request convex hulls. Keep the exact roller
        # triangle mesh as input to that same native collision representation.
        for body in bodies[1:]:
            parts = by_node[body.mesh_node]
            if len(parts) != 1:
                raise ValueError("Conveyor roller must have one source visual/collision primitive")
            points = parts[0].positions
            low, high = points.min(0), points.max(0)
            anchor = np.array(body.joint.anchor)
            if (not np.allclose(body.joint.axis, [1, 0, 0], atol=1e-6, rtol=0)
                    or not .89 < high[0]-low[0] < .92
                    or not np.allclose((low+high)[1:]/2, anchor[1:], atol=.001, rtol=0)
                    or not np.all((high-low)[1:] > .05) or not np.all((high-low)[1:] < .07)):
                raise ValueError("Conveyor roller mesh is misaligned with its source hinge")
        source_boxes = boxes
        boxes, pockets = _roller_clearances(boxes, primitives, bodies, check=check)
        low = np.min([a for a, _ in boxes]+[p.positions.min(0) for p in primitives], axis=0)
        high = np.max([b for _, b in boxes]+[p.positions.max(0) for p in primitives], axis=0)
        if not np.allclose(high-low, [1.1510843, 2.0000837, 1.1664663], atol=.002, rtol=0):
            raise ValueError("Conveyor decoded dimensions differ from the reviewed geometry")
        offset = np.array(position)-np.array([(low[0]+high[0])/2, (low[1]+high[1])/2, low[2]])
        visuals = prepare_glb_visuals(decoded.path, cache / "gltf-visuals", source_notice=source.notice_path,
            source_metadata=json.loads(source.metadata_path.read_text()), timeout=remaining(), cancelled=cancelled)
        root_id = prefix+"_body"
        parents = {body.mesh_node: root_id if body.joint is None else f"{prefix}_roller{i}"
                   for i, body in enumerate(bodies)}
        appearance = visuals.objects(prefix=prefix, parents=parents)
        limitations = [
            "Unpowered conveyor: fixed frame and 29 passive unlimited roller hinges; no belt drive or automated transport.",
            "Frame uses source-component bounding envelopes; 12 zero-thickness source surfaces receive 0.1 mm collision skins. Small holes/details can be filled; central roller bed clearance is checked geometrically. Swept roller volumes plus 1 mm are removed from frame collision proxies to preserve bearing clearance; source visuals remain unchanged.",
            "Roller collision hulls use the original roller visual meshes, matching the source convex-hull declaration.",
            "Source body masses and dynamic friction are retained as estimates; frame mass is distributed by proxy volume. Source inertia, center of mass and restitution are not retained.",
            "Roller bearing damping and friction loss are unspecified by the source and set to zero; torsional/rolling contact friction uses runtime defaults.",
            "Source geometry and material maps are retained. Geometric checks do not establish native contacts, reachability or policy success.",
            PBR_RENDERER_NOTICE,
        ]
        recipe = {"profile": "passive-conveyor-source-proxies-v1", "source": candidate.metadata,
            "decoded_sha256": json.loads(decoded.manifest_path.read_text())["sha256"],
            "source_frame_components": [{"low": a.tolist(), "high": b.tolist()} for a, b in source_boxes],
            "frame_components": [{"low": a.tolist(), "high": b.tolist()} for a, b in boxes],
            "bearing_pockets": [{"low": a.tolist(), "high": b.tolist()} for a, b in pockets],
            "weld_grid_metres": .00001, "planar_components": planar, "planar_skin_metres": .0001,
            "bodies": [{"node": b.node_index, "mesh_node": b.mesh_node, "name": b.source_name,
                        "mass": b.mass, "dynamic_friction": b.friction,
                        "joint": b.joint.model_dump() if b.joint else None} for b in bodies],
            "limitations": limitations}
        encoded = (json.dumps(recipe, indent=2, allow_nan=False)+"\n").encode()
        recipe_path = cache / "conveyor-physics" / (hashlib.sha256(encoded).hexdigest()+".json")
        if recipe_path.exists() or recipe_path.is_symlink():
            if recipe_path.is_symlink() or recipe_path.read_bytes() != encoded:
                raise ValueError("Conveyor collision provenance cache was changed")
        else:
            recipe_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(recipe_path, recipe)
        asset = ImportedAsset(source_files=visuals.source_files, disclosures=limitations)
        asset.source_files["conveyor-physics.json"] = hashlib.sha256(encoded).hexdigest()
        volumes = np.array([np.prod(b-a) for a, b in boxes])
        for index, ((a, b), volume) in enumerate(zip(boxes, volumes)):
            asset.objects.append(SceneObject(id=root_id if index == 0 else f"{prefix}_frame{index}",
                label="Unpowered conveyor frame", kind="box", position=(a+b)/2+offset, size=b-a,
                mass=frame.mass*volume/volumes.sum(), friction=frame.friction, visible=False,
                parent_id=None if index == 0 else root_id,
                source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path)))
        for body in bodies[1:]:
            identifier = parents[body.mesh_node]
            visual = [o for o in appearance if o.parent_id == identifier]
            if len(visual) != 1:
                raise ValueError("Conveyor roller appearance must map to exactly one physical body")
            obj = visual[0]
            joint = body.joint.model_copy(deep=True)
            joint.anchor = np.array(joint.anchor)+offset
            asset.objects.append(SceneObject(id=identifier, label="Passive conveyor roller", kind="mesh",
                mesh_path=obj.mesh_path, position=np.array(obj.position)+offset, size=obj.size,
                parent_id=root_id, joint=joint, mass=body.mass, friction=body.friction, visible=False,
                source_notice=obj.source_notice, source_metadata=str(recipe_path)))
            asset.joint_ids[body.source_name] = identifier
        for body in bodies:
            asset.body_ids[body.source_name] = parents[body.mesh_node]
            asset.geom_ids[geometry.document["nodes"][body.mesh_node]["name"]] = parents[body.mesh_node]
        for obj in appearance:
            obj.position = np.array(obj.position)+offset
            asset.objects.append(obj)
        check()
        return asset

    def no_creation(*args):
        raise ValueError("Conveyor import unresolved; procedural creation is not a fallback")

    return source_asset("conveyor_belt_a05", search=simready_asset_search, import_asset=adapt, create_asset=no_creation)
