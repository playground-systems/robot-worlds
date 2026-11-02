"""Pinned SimReady drawer cabinet: source joints and hollow component proxies.

This source-specific adapter checks geometry, not native contact dynamics. All
returned boxes use the same metre/Z-up zero-joint coordinates as GLBGeometry.
"""
from __future__ import annotations

import numpy as np

from .gltf_geometry import _Z_UP, _transform
from .models import JointSpec
from .simready_structures import component_bounds

_PHYSICS = "KHR_physics_rigid_bodies"
_NAMES = ("body_01_obj_00", "part_01_obj_00", "part_02_obj_00")
_RANGES = ((0., .46218), (-.000004, .462176))


def _bodies(document):
    """Cross-check physical hierarchy, both source joint frames and node extras."""
    nodes, scenes = document.get("nodes", []), document.get("scenes", [])
    if (not isinstance(nodes, list) or not 1 <= len(nodes) <= 32 or len(scenes) != 1
            or document.get("scene", 0) != 0 or document.get("animations") or document.get("skins")):
        raise ValueError("Drawer cabinet requires its one bounded undeformed source scene")
    library = document.get("extensions", {}).get(_PHYSICS, {})
    if set(library) != {"physicsMaterials", "physicsJoints"}:
        raise ValueError("Drawer cabinet physics extensions changed")
    materials, definitions = library["physicsMaterials"], library["physicsJoints"]
    if not isinstance(materials, list) or len(materials) != 1 or len(definitions) != 2:
        raise ValueError("Drawer cabinet needs its one material and two source slides")
    friction = materials[0].get("dynamicFriction")
    if type(friction) not in (int, float) or not np.isfinite(friction) or friction < 0:
        raise ValueError("Drawer cabinet source friction is missing")
    transforms, parents = {}, {}
    stack = [(i, _Z_UP, None, 0) for i in scenes[0].get("nodes", [])]
    while stack:
        index, frame, parent, depth = stack.pop()
        if (type(index) is not int or not 0 <= index < len(nodes) or index in transforms or depth > 16):
            raise ValueError("Drawer cabinet has invalid or repeated hierarchy references")
        node = nodes[index]
        if set(node.get("extensions", {})) - {_PHYSICS}:
            raise ValueError("Drawer cabinet has an unsupported node extension")
        transform = frame @ _transform(node)
        if not np.isfinite(transform).all():
            raise ValueError("Drawer cabinet transform is nonfinite")
        transforms[index], parents[index] = transform, parent
        stack.extend((child, transform, index, depth+1) for child in node.get("children", []))
    if len(transforms) != len(nodes):
        raise ValueError("Drawer cabinet contains unreachable source nodes")
    physics = {i: node.get("extensions", {}).get(_PHYSICS, {}) for i, node in enumerate(nodes)}
    if any(set(value)-{"motion", "collider", "joint"} for value in physics.values()):
        raise ValueError("Unsupported drawer cabinet physics feature")
    bodies = [i for i, value in physics.items() if "motion" in value]
    names = {nodes[i].get("name"): i for i in bodies}
    if len(bodies) != 3 or set(names) != set(_NAMES):
        raise ValueError("Drawer cabinet must retain the fixed body and both source drawers")
    root = names[_NAMES[0]]
    if nodes[root].get("extras", {}).get("joints"):
        raise ValueError("Cabinet shell is expected to be the fixed source root")
    joints = [i for i, value in physics.items() if "joint" in value]
    if len(joints) != 2:
        raise ValueError("Both drawer source joints must be retained")
    parsed = {}
    used_definitions = set()
    for index in joints:
        link = physics[index]["joint"]
        if set(link) != {"connectedNode", "joint"}:
            raise ValueError("Unsupported drawer joint connection")
        connected, definition = link["connectedNode"], link["joint"]
        if (type(connected) is not int or connected not in transforms or parents[index] != root
                or type(definition) is not int or not 0 <= definition < 2 or definition in used_definitions):
            raise ValueError("Drawer source joint mapping is missing or duplicated")
        body = parents[connected]
        if body not in bodies or body == root or body in parsed:
            raise ValueError("Drawer source frames must connect cabinet shell to separate drawers")
        first, second = transforms[index], transforms[connected]
        if (not np.allclose(first, second, atol=2e-6, rtol=0)
                or not np.allclose(first[:3, :3], _Z_UP[:3, :3], atol=1e-6, rtol=0)):
            raise ValueError("Drawer joint frames disagree or rotate/scale the reviewed X slide")
        declarations = nodes[body].get("extras", {}).get("joints", [])
        if len(declarations) != 1:
            raise ValueError("Each drawer needs one source joint declaration")
        extra = declarations[0]
        if (set(extra) != {"name", "type", "parent", "pivot", "axis", "min", "max", "unit"}
                or extra["type"] != "prismatic" or extra["parent"] != _NAMES[0]
                or extra["unit"] != "meters"):
            raise ValueError("Drawer source extras differ from the reviewed prismatic joint")
        ordinal = _NAMES.index(nodes[body]["name"])-1
        bounds = (extra["min"], extra["max"])
        if bounds != _RANGES[ordinal]:
            raise ValueError("Drawer source slide limits changed")
        expected_limits = [
            {"linearAxes": [1, 2], "min": 0, "max": 0},
            {"angularAxes": [0], "min": 0, "max": 0},
            {"angularAxes": [1, 2], "min": 0, "max": 0},
            {"linearAxes": [0], "min": bounds[0], "max": bounds[1]},
        ]
        if definitions[definition] != {"limits": expected_limits}:
            raise ValueError("Drawer frame limits disagree with source extras or include drives")
        pivot, axis = np.asarray(extra["pivot"], dtype=float), np.asarray(extra["axis"], dtype=float)
        if (pivot.shape != (3,) or axis.shape != (3,) or not np.isfinite(pivot).all()
                or not np.allclose(_Z_UP[:3, :3] @ pivot, first[:3, 3], atol=2e-6, rtol=0)
                or not np.allclose(_Z_UP[:3, :3] @ axis, first[:3, 0], atol=1e-6, rtol=0)):
            raise ValueError("Drawer source extras disagree with physical joint frames")
        if extra["name"] != f"joint_part_0{ordinal+1}_joint_00":
            raise ValueError("Drawer source joint name changed")
        parsed[body] = JointSpec(type="slide", axis=first[:3, 0], anchor=first[:3, 3],
            range=bounds, damping=0, frictionloss=0, collide_parent=False)
        used_definitions.add(definition)
    if set(parsed) != set(bodies)-{root}:
        raise ValueError("Drawer cabinet lost a source slide")
    colliders = [i for i, value in physics.items() if "collider" in value]
    result, used = [], set()
    for name in _NAMES:
        body = names[name]
        meshes = [i for i in colliders if parents[i] == body]
        if len(meshes) != 1:
            raise ValueError("Each cabinet body requires its one source mesh")
        mesh = meshes[0]
        collider = physics[mesh]["collider"]
        if (set(collider)-{"geometry", "physicsMaterial"}
                or collider.get("geometry") != {"node": mesh, "convexHull": True}
                or collider.get("physicsMaterial", 0) != 0 or "mesh" not in nodes[mesh]):
            raise ValueError("Cabinet source collider differs from the reviewed mesh")
        motion = physics[body]["motion"]
        mass = motion.get("mass")
        if type(mass) not in (int, float) or not np.isfinite(mass) or mass <= 0:
            raise ValueError("Cabinet source mass is missing or nonpositive")
        if nodes[body].get("extras", {}).get("mass") != mass:
            raise ValueError("Cabinet source mass extras and motion disagree")
        result.append({"name": name, "mesh_node": mesh, "mass": mass,
            "friction": friction, "joint": parsed.get(body)})
        used.add(mesh)
    if set(colliders) != used or {i for i, n in enumerate(nodes) if "mesh" in n} != used:
        raise ValueError("Cabinet contains unmapped visual or collision geometry")
    return result


def _ray_surfaces(triangles, axis, point):
    """Intersect a source mesh with an axis line to identify actual panel faces."""
    u, v = [i for i in range(3) if i != axis]
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    denominator = (b[:, v]-c[:, v])*(a[:, u]-c[:, u])+(c[:, u]-b[:, u])*(a[:, v]-c[:, v])
    valid = abs(denominator) > 1e-12
    a, b, c, denominator = a[valid], b[valid], c[valid], denominator[valid]
    first = ((b[:, v]-c[:, v])*(point[u]-c[:, u])+(c[:, u]-b[:, u])*(point[v]-c[:, v]))/denominator
    second = ((c[:, v]-a[:, v])*(point[u]-c[:, u])+(a[:, u]-c[:, u])*(point[v]-c[:, v]))/denominator
    hit = (first >= -1e-8) & (second >= -1e-8) & (first+second <= 1+1e-8)
    values = first*a[:, axis]+second*b[:, axis]+(1-first-second)*c[:, axis]
    surfaces = []
    for value in sorted(values[hit]):
        if not surfaces or value-surfaces[-1] > 1e-7:
            surfaces.append(float(value))
    return surfaces


def _source_empty(triangles, low, high):
    """Triangle/box SAT: removed open pockets must contain no source surface."""
    center = (low+high)/2
    half = (high-low)/2-1e-7
    vertices = triangles-center
    hit = np.all(vertices.min(1) < half, axis=1) & np.all(vertices.max(1) > -half, axis=1)
    vertices = vertices[hit]
    if not len(vertices):
        return
    axes = [np.cross(vertices[:, 1]-vertices[:, 0], vertices[:, 2]-vertices[:, 0])]
    axes.extend(np.cross(vertices[:, edge]-vertices[:, (edge+1) % 3], basis)
                for edge in range(3) for basis in np.eye(3))
    overlap = np.ones(len(vertices), dtype=bool)
    for axis in axes:
        projection = np.einsum("nij,nj->ni", vertices, axis)
        radius = np.sum(half*np.abs(axis), axis=1)
        overlap &= (projection.min(1) <= radius+1e-12) & (projection.max(1) >= -radius-1e-12)
    if overlap.any():
        raise ValueError("Reviewed cabinet pocket would remove original source material")


def _subtract(box, pocket):
    low, high = box
    first, last = np.maximum(low, pocket[0]), np.minimum(high, pocket[1])
    if np.any(last-first <= 1e-8):
        return [box]
    result, low, high = [], low.copy(), high.copy()
    for axis in range(3):
        if first[axis] > low[axis]+1e-8:
            end = high.copy()
            end[axis] = first[axis]
            result.append((low.copy(), end))
            low[axis] = first[axis]
        if last[axis] < high[axis]-1e-8:
            start = low.copy()
            start[axis] = last[axis]
            result.append((start, high.copy()))
            high[axis] = last[axis]
    return result


def _carve(boxes, index, pockets):
    result, indices = [], {}
    for number, box in enumerate(boxes):
        pieces = [box]
        if number == index:
            for pocket in pockets:
                pieces = [part for piece in pieces for part in _subtract(piece, pocket)]
        indices[number] = list(range(len(result), len(result)+len(pieces)))
        result.extend(pieces)
    return result, indices


def cabinet_parts(document, primitives) -> list[dict]:
    """Return fixed shell and both source slides with usable hollow drawers.

    Every connected source component retains a conservative envelope. Only
    reviewed empty pockets are removed from the large shell envelopes; SAT checks
    prohibit removing source surfaces. The actual fronts, handles and slides
    remain separate components. Drawer clearances are metadata in the rest pose,
    never stationary Regions pretending to follow a moving drawer.
    """
    result = _bodies(document)
    if {p.node_index for p in primitives} != {part["mesh_node"] for part in result}:
        raise ValueError("Every cabinet visual must map to a physical source part")
    count = 0
    for ordinal, part in enumerate(result):
        selected = [p for p in primitives if p.node_index == part["mesh_node"]]
        boxes = component_bounds(selected)
        triangles = np.concatenate([p.positions[p.triangles] for p in selected])
        if ordinal == 0:
            if len(boxes) != 13:
                raise ValueError("Cabinet shell connected components changed")
            x = _ray_surfaces(triangles, 0, [0, 0, .5])
            y = _ray_surfaces(triangles, 1, [0, 0, .5])
            z = _ray_surfaces(triangles, 2, [0, 0, .5])
            if (len(x) != 2 or len(y) != 4 or len(z) != 9
                    or not np.allclose(x, [-.33929945, -.32320508], atol=2e-6, rtol=0)
                    or not np.allclose(y, [-.38097979, -.35921840, .35921926, .38098065], atol=2e-6, rtol=0)
                    or not np.allclose(z, [.11500562, .13678118, .27184066, .29360205,
                        .42061435, .44237574, .86931557, .89107696, .92915940], atol=2e-6, rtol=0)):
                raise ValueError("Cabinet source panel planes changed")
            # Keep 1 mm from back/slab faces and a conservative 12.7 mm margin
            # inside the source side walls, retaining guide hardware protrusions.
            pockets = [(np.array([x[1]+.001, -.3465, z[i]+.001]),
                        np.array([boxes[1][1][0]+.01, .3465, z[i+1]-.001])) for i in (1, 3, 5)]
            for low, high in pockets:
                _source_empty(triangles, low, high)
            cooked, indices = _carve(boxes, 1, pockets)
            top = indices[2][0]
            supports = {"cabinet_top": top}
            floor = [i for i, (a, b) in enumerate(cooked) if abs(b[2]-pockets[0][0][2]) < 1e-7
                     and a[0] < 0 < b[0] and a[1] < 0 < b[1]]
            if len(floor) != 1:
                raise ValueError("Cabinet lower storage lacks an addressable floor")
            supports["lower_storage_floor"] = floor[0]
            regions = [{"id": "lower_storage", "low": [-.28, -.30, pockets[0][0][2]+.01],
                        "high": [.20, .30, pockets[0][1][2]-.01]}]
            handles = {}
        else:
            if len(boxes) != (18 if ordinal == 1 else 17):
                raise ValueError("Drawer source connected components changed")
            center_z = (boxes[0][0][2]+boxes[0][1][2])/2
            x = _ray_surfaces(triangles, 0, [0, 0, center_z])
            y = _ray_surfaces(triangles, 1, [0, 0, center_z])
            z = _ray_surfaces(triangles, 2, [0, 0, center_z])
            expected_floor = .73556701 if ordinal == 1 else .59280292
            if (len(x) < 5 or len(y) != 6 or len(z) != 2
                    or not np.allclose(x[:5], [-.30876291, -.29648423, .25802140, .27030008, .29569462], atol=3e-6, rtol=0)
                    or not np.allclose(y, [-.34769838, -.34625656, -.33402439, .33402415, .34625632, .34769813], atol=3e-6, rtol=0)
                    or not np.allclose(z, [expected_floor, expected_floor+.01227868], atol=2e-6, rtol=0)):
                raise ValueError("Drawer source cavity planes changed")
            # The 5 mm front inset retains real inward-projecting handle bolts;
            # 1 mm elsewhere conservatively covers the source rounded corners.
            pockets = [(np.array([x[1]+.001, y[2]+.001, z[1]+.001]),
                        np.array([x[2]-.005, y[-3]-.001, boxes[0][1][2]+.01]))]
            _source_empty(triangles, *pockets[0])
            cooked, indices = _carve(boxes, 0, pockets)
            floor = [i for i in indices[0] if abs(cooked[i][1][2]-pockets[0][0][2]) < 1e-7
                     and cooked[i][0][0] < 0 < cooked[i][1][0]
                     and cooked[i][0][1] < 0 < cooked[i][1][1]]
            handle = [i for i, (a, b) in enumerate(boxes) if b[0] > .335
                      and b[1]-a[1] > .20 and .012 < b[2]-a[2] < .014]
            if len(floor) != 1 or len(handle) != 1:
                raise ValueError("Drawer requires an addressable floor and source handle bar")
            top = floor[0]
            supports = {f"drawer{ordinal}_floor": top}
            handles = {f"drawer{ordinal}_handle": indices[handle[0]][0]}
            regions = []
        # A useful support is the root collision component. All source-frame
        # coordinates stay unchanged; the outer importer attaches the joint.
        order = [top, *[i for i in range(len(cooked)) if i != top]]
        remap = {old: new for new, old in enumerate(order)}
        part.update(boxes=[cooked[i] for i in order],
            support_components={name: remap[i] for name, i in supports.items()},
            handle_components={name: remap[i] for name, i in handles.items()}, regions=regions,
            clearances=[{"id": f"cabinet_pocket{i+1}" if ordinal == 0 else f"drawer{ordinal}_pocket",
                "low": low.tolist(), "high": high.tolist(), "source_part": part["name"],
                "frame": "source zero-joint world coordinates; moves with source part"}
                for i, (low, high) in enumerate(pockets)],
            limitations=[
                "Connected source surfaces use conservative boxes; small holes and rounded detail may be filled.",
                "Empty source pockets are subtracted from shell boxes after triangle/box separation checks; cabinet back/slab margins are 1 mm, side margins retain guide hardware, drawer front margin is 5 mm and other drawer margins are 1 mm.",
                "Both actual X-axis source slide joints and their limits are retained; joint damping and frictionloss are zero because source bearing losses are unspecified.",
                "Official source USD joint layers and MuJoCo overrides do not author physics:collisionEnabled; the USD fallback false is retained as collide_parent=false for each drawer/cabinet rigid body group only. Contacts with the robot, loose props, other fixtures and the other drawer remain enabled.",
                "The second drawer source collider omits a material link; adaptation explicitly uses the source's sole recorded physics material (dynamic friction 0.4).",
                "Drawer cavity coordinates describe the rest pose and must move with the drawer; no world-fixed drawer Region is emitted.",
                "Geometry and metadata checks do not validate native contacts, pulling, reachability or task success.",
            ])
        for low, high in part["boxes"]:
            if np.any(high-low <= 1e-6):
                raise ValueError("Cabinet collision contains a zero-thickness component")
        count += len(part["boxes"])
    if count > 150:
        raise ValueError("Cabinet source proxy exceeds its bounded component budget")
    return result
