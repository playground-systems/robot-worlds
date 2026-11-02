"""Pinned kitchen counter with all eight source joints and retained visuals.

Source-specific geometric preparation only; native contact behavior is untested.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
import time
from pathlib import Path

import numpy as np
from pydantic import TypeAdapter

from .appearance import PBR_RENDERER_NOTICE
from .asset_import import ImportedAsset
from .asset_library import AssetLibrary
from .asset_sourcing import AssetCandidate, AssetSearch, source_asset
from .gltf_decode import decode_glb
from .gltf_geometry import _Z_UP, GLBGeometry, _transform
from .gltf_visuals import prepare_glb_visuals
from .living_room_cabinet import _ray_surfaces, _source_empty, _subtract
from .models import Identifier, JointSpec, SceneObject, Vec3
from .simready_structures import component_bounds
from .workbench_library import _source

KITCHEN_COUNTER_ID = "fixture_counter_a01"
_CATALOG = Path(__file__).parent / "assets/simready/kitchen_counter_catalog.json"
_PHYSICS = "KHR_physics_rigid_bodies"
_NAMES = ["body_01_obj_00", *[f"shelf_{i:02}_obj_00" for i in range(1, 9)]]


def kitchen_counter_asset_search(query):
    if query != KITCHEN_COUNTER_ID:
        raise ValueError("Only the reviewed pinned kitchen counter is supported")
    catalog = json.loads(_CATALOG.read_text())
    row = copy.deepcopy(catalog["selected"])
    candidate = AssetCandidate("simready:"+query, row["optimized_url"], catalog["optimized_revision"],
        "CC-BY-4.0", {**row, "upstream_checked_revision": catalog["upstream_checked_revision"],
        "derivation_upstream_revision": catalog["optimized_derivation_upstream_revision"]})
    return AssetSearch(query, tuple(catalog["catalogs"]), (candidate,), True,
        {"scope": "Recorded pinned source lookup; not a fresh online search",
         "queries": catalog["queries"], "manifest_sha256": catalog["manifest_sha256"],
         "source_joint_collision_policy": catalog["joint_collision_policy"]})


def _counter_bodies(document):
    nodes, scenes = document.get("nodes", []), document.get("scenes", [])
    if (not isinstance(nodes, list) or not 1 <= len(nodes) <= 64 or len(scenes) != 1
            or document.get("scene", 0) != 0 or document.get("animations") or document.get("skins")):
        raise ValueError("Kitchen counter requires its one undeformed bounded source scene")
    library = document.get("extensions", {}).get(_PHYSICS, {})
    if set(library) != {"physicsMaterials", "physicsJoints"}:
        raise ValueError("Kitchen counter source physics changed")
    materials, definitions = library["physicsMaterials"], library["physicsJoints"]
    if len(materials) != 1 or len(definitions) != 4:
        raise ValueError("Kitchen counter requires its source material and four joint definitions")
    friction = materials[0].get("dynamicFriction")
    if type(friction) not in (int, float) or not np.isfinite(friction) or friction < 0:
        raise ValueError("Kitchen source friction is missing")
    transforms, parents = {}, {}
    stack = [(i, _Z_UP, None, 0) for i in scenes[0].get("nodes", [])]
    while stack:
        index, frame, parent, depth = stack.pop()
        if type(index) is not int or not 0 <= index < len(nodes) or index in transforms or depth > 16:
            raise ValueError("Kitchen counter has invalid hierarchy references")
        node = nodes[index]
        if set(node.get("extensions", {}))-{_PHYSICS}:
            raise ValueError("Unsupported kitchen counter node extension")
        transformed = frame @ _transform(node)
        if not np.isfinite(transformed).all():
            raise ValueError("Kitchen counter contains nonfinite transforms")
        transforms[index], parents[index] = transformed, parent
        stack.extend((child, transformed, index, depth+1) for child in node.get("children", []))
    if len(transforms) != len(nodes):
        raise ValueError("Kitchen counter has unreachable source nodes")
    physics = {i: n.get("extensions", {}).get(_PHYSICS, {}) for i, n in enumerate(nodes)}
    if any(set(value)-{"motion", "joint", "collider"} for value in physics.values()):
        raise ValueError("Unsupported kitchen counter physics feature")
    bodies = [i for i, value in physics.items() if "motion" in value]
    names = {nodes[i].get("name"): i for i in bodies}
    if len(bodies) != 9 or set(names) != set(_NAMES):
        raise ValueError("Kitchen counter must retain all nine source bodies")
    root = names[_NAMES[0]]
    if nodes[root].get("extras", {}).get("joints"):
        raise ValueError("Kitchen counter shell must remain its fixed root")
    frames = [i for i, value in physics.items() if "joint" in value]
    if len(frames) != 8:
        raise ValueError("Kitchen counter must retain all eight joints")
    parsed = {}
    for index in frames:
        link = physics[index]["joint"]
        if set(link) != {"joint", "connectedNode"}:
            raise ValueError("Kitchen source joint connection changed")
        connected, definition = link["connectedNode"], link["joint"]
        if (type(connected) is not int or connected not in transforms or parents[index] != root
                or type(definition) is not int or not 0 <= definition < len(definitions)):
            raise ValueError("Kitchen joint must connect shell to a retained source part")
        body = parents[connected]
        if body not in bodies or body == root or body in parsed:
            raise ValueError("Kitchen source joint mapping is missing or repeated")
        first, second = transforms[index], transforms[connected]
        if (not np.allclose(first, second, atol=2e-6, rtol=0)
                or not np.allclose(first[:3, :3], _Z_UP[:3, :3], atol=1e-6, rtol=0)):
            raise ValueError("Kitchen source joint frames disagree or change orientation")
        ordinal = _NAMES.index(nodes[body]["name"])
        declarations = nodes[body].get("extras", {}).get("joints", [])
        if len(declarations) != 1:
            raise ValueError("Each kitchen moving body requires its actual joint declaration")
        extra = declarations[0]
        hinge = ordinal == 5
        expected_type, expected_unit = ("revolute", "degrees") if hinge else ("prismatic", "meters")
        bounds = (-90., 0.) if hinge else ((0., .460681) if ordinal == 1
            else ((-.0001, .4) if ordinal == 6 else (0., .4)))
        if (set(extra) != {"name", "type", "parent", "pivot", "axis", "min", "max", "unit"}
                or extra["name"] != f"joint_shelf_{ordinal:02}_joint_00"
                or extra["type"] != expected_type or extra["unit"] != expected_unit
                or extra["parent"] != _NAMES[0] or (extra["min"], extra["max"]) != bounds):
            raise ValueError("Kitchen source joint type, range or parent changed")
        converted = tuple(np.deg2rad(bounds)) if hinge else bounds
        limits = definitions[definition].get("limits", [])
        locked = ([{"linearAxes": [0, 1, 2], "min": 0, "max": 0},
                   {"angularAxes": [0, 2], "min": 0, "max": 0}] if hinge else
                  [{"linearAxes": [1, 2], "min": 0, "max": 0},
                   {"angularAxes": [0], "min": 0, "max": 0},
                   {"angularAxes": [1, 2], "min": 0, "max": 0}])
        if (set(definitions[definition]) != {"limits"} or len(limits) != len(locked)+1
                or limits[:-1] != locked
                or set(limits[-1]) != {"angularAxes" if hinge else "linearAxes", "min", "max"}
                or limits[-1].get("angularAxes" if hinge else "linearAxes") != ([1] if hinge else [0])
                or not np.allclose([limits[-1]["min"], limits[-1]["max"]], converted, atol=1e-6, rtol=0)):
            raise ValueError("Kitchen source frame limits disagree with extras")
        pivot, axis = np.asarray(extra["pivot"], float), np.asarray(extra["axis"], float)
        source_axis = first[:3, 1 if hinge else 0]
        if (pivot.shape != (3,) or axis.shape != (3,) or not np.isfinite(pivot).all()
                or not np.allclose(_Z_UP[:3, :3] @ pivot, first[:3, 3], atol=2e-6, rtol=0)
                or not np.allclose(_Z_UP[:3, :3] @ axis, source_axis, atol=1e-6, rtol=0)):
            raise ValueError("Kitchen source joint extras disagree with independent frames")
        parsed[body] = JointSpec(type="hinge" if hinge else "slide", axis=source_axis,
            anchor=first[:3, 3], range=converted, damping=0, frictionloss=0, collide_parent=False)
    if set(parsed) != set(bodies)-{root}:
        raise ValueError("Kitchen counter lost a source joint")
    colliders = [i for i, value in physics.items() if "collider" in value]
    result, used = [], set()
    for name in _NAMES:
        body = names[name]
        meshes = [i for i in colliders if parents[i] == body]
        if len(meshes) != 1:
            raise ValueError("Each kitchen body requires its one original visual/collision mesh")
        mesh = meshes[0]
        collider = physics[mesh]["collider"]
        if (set(collider)-{"geometry", "physicsMaterial"}
                or collider.get("geometry") != {"node": mesh, "convexHull": True}
                or collider.get("physicsMaterial", 0) != 0):
            raise ValueError("Kitchen source collider changed")
        mass = physics[body]["motion"].get("mass")
        if (type(mass) not in (int, float) or not np.isfinite(mass) or mass <= 0
                or nodes[body].get("extras", {}).get("mass") != mass):
            raise ValueError("Kitchen source motion mass and extras disagree")
        result.append({"name": name, "mesh_node": mesh, "mass": mass,
            "friction": friction, "joint": parsed.get(body)})
        used.add(mesh)
    if used != set(colliders) or used != {i for i, n in enumerate(nodes) if "mesh" in n}:
        raise ValueError("Kitchen counter contains unmapped physical or appearance geometry")
    return result


def _simplify(boxes):
    """Merge only tiny hardware cells, then remove fully contained envelopes."""
    groups, merged, members = {}, [], []
    for i, (a, b) in enumerate(boxes):
        if max(b-a) < .06:
            groups.setdefault(tuple(np.floor(((a+b)/2)/.08).astype(int)), []).append(i)
        else:
            merged.append((a, b))
            members.append([i])
    for indices in groups.values():
        merged.append((np.min([boxes[i][0] for i in indices], 0), np.max([boxes[i][1] for i in indices], 0)))
        members.append(indices)
    kept, mapping = [], {}
    for i in sorted(range(len(merged)), key=lambda i: -float(np.prod(merged[i][1]-merged[i][0]))):
        a, b = merged[i]
        inside = [j for j, (x, y) in enumerate(kept) if np.all(a >= x-1e-7) and np.all(b <= y+1e-7)]
        target = inside[0] if inside else len(kept)
        if not inside:
            kept.append((a, b))
        mapping.update({index: target for index in members[i]})
    return kept, mapping


def _empty_proxy(boxes, low, high):
    if any(np.all(np.minimum(high, b)-np.maximum(low, a) > 1e-6) for a, b in boxes):
        raise ValueError("Kitchen collision fills a reviewed usable cavity")


def _hinge_slices(triangles, box, front):
    """Clip real hinge triangles at the neighboring source front's Y edge.

    A whole angled hinge's AABB fills space next to the tall pullout panel.
    Two conservative clipped envelopes retain all selected source triangles and
    leave their measured separation from that independent moving panel intact.
    """
    low, high = box
    selected = triangles[np.all((triangles >= low-1e-7) & (triangles <= high+1e-7), axis=(1, 2))]
    if (not len(selected) or not np.allclose(selected.min((0, 1)), low, atol=1e-7, rtol=0)
            or not np.allclose(selected.max((0, 1)), high, atol=1e-7, rtol=0)):
        raise ValueError("Source hinge triangles do not retain the original component bounds")
    cut = float(front[1][1])
    if not low[1] < cut < high[1]:
        raise ValueError("Source pullout edge does not cross the reviewed hinge envelope")
    boxes = []
    for lower_band in (True, False):
        vertices = []
        for triangle in selected:
            polygon = []
            for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
                first = a[1] <= cut if lower_band else a[1] >= cut
                second = b[1] <= cut if lower_band else b[1] >= cut
                if first:
                    polygon.append(a)
                if first != second:
                    point = a+(b-a)*((cut-a[1])/(b[1]-a[1]))
                    point[1] = cut
                    polygon.append(point)
            if len(polygon) >= 3:
                vertices.extend(polygon)
        vertices = np.asarray(vertices)
        if len(vertices) < 4:
            raise ValueError("Clipped source hinge band is empty")
        bounds = vertices.min(0), vertices.max(0)
        if np.any(bounds[1]-bounds[0] <= 1e-6):
            raise ValueError("Clipped source hinge band has no contact volume")
        if np.all(np.minimum(bounds[1], front[1])-np.maximum(bounds[0], front[0]) > 1e-7):
            raise ValueError("Source-derived hinge slices still intersect the neighboring pullout front")
        boxes.append(bounds)
    gap = float(front[0][0]-boxes[0][1][0])
    if gap < .006:
        raise ValueError("Source-derived hinge clearance changed")
    return boxes, {"method": "Source triangles clipped into two Y bands at the neighboring source front's exact edge",
        "source_triangles": len(selected), "cut_y_m": cut,
        "original_envelope": {"low": low.tolist(), "high": high.tolist()},
        "neighbor_front": {"low": front[0].tolist(), "high": front[1].tolist()},
        "lower_y_band_x_clearance_m": gap,
        "reason": "The original whole-hinge AABB combined extrema at different Y coordinates and filled source-empty space beside a separate moving pullout; no sibling-joint contacts are excluded."}


def kitchen_counter_parts(document, primitives):
    parts = _counter_bodies(document)
    if {p.node_index for p in primitives} != {part["mesh_node"] for part in parts}:
        raise ValueError("Every kitchen visual primitive needs an actual source body")
    total, pullout_front = 0, None
    for ordinal, part in enumerate(parts):
        selected = [p for p in primitives if p.node_index == part["mesh_node"]]
        source_boxes = component_bounds(selected)
        triangles = np.concatenate([p.positions[p.triangles] for p in selected])
        supports, handles, clearances = {}, {}, []
        source_count = (177, 48, 21, 21, 21, 33, 28, 28, 28)[ordinal]
        if len(source_boxes) != source_count:
            raise ValueError("Kitchen source connected component structure changed")
        if ordinal == 1:
            fronts = [(a, b) for a, b in source_boxes
                if .77 < b[2]-a[2] < .78 and .30 < b[1]-a[1] < .31 and .025 < b[0]-a[0] < .026]
            if len(fronts) != 1:
                raise ValueError("Source tall pullout requires its exact neighboring front edge")
            pullout_front = fronts[0]
        if ordinal == 0:
            x = _ray_surfaces(triangles, 0, [0, -1.36, .7])
            y = _ray_surfaces(triangles, 1, [0, -1.36, .7])
            z = _ray_surfaces(triangles, 2, [0, -1.36, .7])
            if (len(x) != 2 or len(y) != 14 or len(z) != 5
                    or not np.allclose(x, [-.33915950, -.31388424], atol=2e-6, rtol=0)
                    or not np.allclose(z, [.11430883, .13976859, .86357279, .88903254, .92703768], atol=2e-6, rtol=0)):
                raise ValueError("Kitchen counter source panel planes changed")
            pockets = []
            for left, right in ((y[1], y[2]), (y[4], y[5]), (y[7], y[8]), (y[11], y[12])):
                ceiling = .48255280968845826 if left == y[7] else z[2]
                pockets.append((np.array([x[1]+.001, left+.02, z[1]+.001]),
                    np.array([source_boxes[0][1][0]+.01, right-.02, ceiling-.001])))
            pockets.append((np.array([x[1]+.001, y[7]+.02, .5080125635177116+.001]),
                np.array([.2694, y[8]-.02, z[2]-.001])))
            for low, high in pockets:
                _source_empty(triangles, low, high)
            boxes, indices = [], {}
            for i, box in enumerate(source_boxes):
                pieces = [box]
                if i in (0, 2):
                    for pocket in pockets:
                        pieces = [b for piece in pieces for b in _subtract(piece, pocket)]
                indices[i] = list(range(len(boxes), len(boxes)+len(pieces)))
                boxes.extend(pieces)
            supports["countertop"] = indices[1][0]
            supports["counter_top"] = indices[1][0]
            handles = {}
            clearances = [{"id": f"fixed_counter_bay{i}", "low": a.tolist(), "high": b.tolist()}
                          for i, (a, b) in enumerate(pockets)]
            root = supports["countertop"]
        else:
            boxes = source_boxes
            if ordinal == 5:
                root = 11
                handles["cabinet_door_handle"] = 32
            else:
                floor = 22 if ordinal == 1 else 11
                root = floor
                supports[f"drawer{ordinal}_floor"] = floor
                if ordinal == 1:
                    supports["drawer1_upper_floor"] = 23
                    handles["drawer1_handle"] = 47
                    tray_specs = [(22, 16, 19, 20), (23, 17, 18, 21)]
                else:
                    tray_specs = [(11, 8, 9, 10)]
                    if ordinal in (6, 7, 8):
                        handles[f"drawer{ordinal}_handle"] = 27
                    else:
                        # These three real internal drawers have a front lip,
                        # not an invented handle bar.
                        handles[f"drawer{ordinal}_pull_front"] = 20
                for number, (floor, left, right, back) in enumerate(tray_specs):
                    a, b = boxes[floor]
                    low = np.array([boxes[back][1][0]+.005, boxes[left][1][1]+.003, b[2]+.003])
                    high = np.array([b[0]-.005, boxes[right][0][1]-.003,
                        min(boxes[left][1][2], boxes[right][1][2])-.003])
                    if np.any(high-low <= .02):
                        raise ValueError("Kitchen drawer source has no useful reviewed pocket")
                    _source_empty(triangles, low, high)
                    clearances.append({"id": f"drawer{ordinal}_pocket{number}", "low": low.tolist(),
                        "high": high.tolist(), "moves_with": part["name"]})
        simplified, mapping = _simplify(boxes)
        extras, refinements = [], []
        if ordinal == 5:
            overlapping = [i for i, (a, b) in enumerate(simplified)
                if np.all(np.minimum(b, pullout_front[1])-np.maximum(a, pullout_front[0]) > 1e-7)]
            if len(overlapping) != 2:
                raise ValueError("Source hinged door requires its two reviewed angled hinge refinements")
            for index in overlapping:
                slices, refinement = _hinge_slices(triangles, simplified[index], pullout_front)
                simplified[index] = slices[0]
                extras.extend(slices[1:])
                refinements.append({"component_index_before_root_order": index, **refinement})
        for item in clearances:
            # Body bay pockets describe where source material was carved; small
            # guide boxes can remain near their margins. Drawer pockets are the
            # actual useful collision clearances and must be completely empty.
            if ordinal:
                _empty_proxy(simplified, np.array(item["low"]), np.array(item["high"]))
        root = mapping[root]
        order = [root, *[i for i in range(len(simplified)) if i != root]]
        remap = {old: new for new, old in enumerate(order)}
        part.update(boxes=[simplified[i] for i in order],
            support_components={name: remap[mapping[i]] for name, i in supports.items()},
            handle_components={name: remap[mapping[i]] for name, i in handles.items()},
            clearances=clearances, extra_boxes=extras, source_hinge_refinements=refinements)
        total += len(part["boxes"])+len(extras)
    if total > 200:
        raise ValueError("Kitchen counter exceeds its reviewed 200-component proxy budget")
    return parts


def _visual_batch(geometry, mesh_node, cache):
    """Copy exact raw source channels into bounded triangle/vertex subsets.

    Some source material primitives reference the entire body's vertex buffer.
    Writing all those unused vertices once per material blows up OBJ output.
    This changes indexing and file partitioning only: every original triangle,
    raw attribute value, transform, material and encoded texture is retained.
    """
    original = geometry.document
    document = copy.deepcopy(original)
    binary, views, accessors = bytearray(), [], []

    def append(data):
        binary.extend(b"\0"*((-len(binary)) % 4))
        index = len(views)
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary.extend(data)
        return index

    for image in document.get("images", []):
        _, encoded = geometry.view(image["bufferView"])
        image["bufferView"] = append(bytes(encoded))
    types = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
    components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def attribute(index, selected):
        source = original["accessors"][index]
        view = original["bufferViews"][source["bufferView"]]
        dtype = np.dtype(types[source["componentType"]])
        width = components[source["type"]]
        array = np.ndarray((source["count"], width), dtype=dtype, buffer=geometry.binary,
            offset=view.get("byteOffset", 0)+source.get("byteOffset", 0),
            strides=(view.get("byteStride", dtype.itemsize*width), dtype.itemsize))
        values = array[selected].copy()
        target = {k: copy.deepcopy(v) for k, v in source.items()
                  if k not in {"bufferView", "byteOffset", "min", "max", "count"}}
        target.update(bufferView=append(values.tobytes()), count=len(values))
        if "min" in source:
            target["min"] = values.min(0).tolist()
        if "max" in source:
            target["max"] = values.max(0).tolist()
        result = len(accessors)
        accessors.append(target)
        return result

    primitives, triangle_count = [], 0
    mesh = original["meshes"][original["nodes"][mesh_node]["mesh"]]
    for primitive in mesh["primitives"]:
        if "indices" not in primitive or primitive.get("mode", 4) != 4 or primitive.get("extensions"):
            raise ValueError("Kitchen visual partition requires decoded indexed triangles")
        indices = geometry.accessor(primitive["indices"]).reshape(-1)
        if len(indices) % 3:
            raise ValueError("Kitchen source triangle indexing changed")
        triangle_count += len(indices)//3
        for start in range(0, len(indices), 60_000):
            selected, remapped = np.unique(indices[start:start+60_000], return_inverse=True)
            compact = copy.deepcopy(primitive)
            compact["attributes"] = {name: attribute(index, selected)
                for name, index in primitive["attributes"].items()}
            compact["indices"] = len(accessors)
            accessors.append({"bufferView": append(remapped.astype("<u4").tobytes()),
                "componentType": 5125, "count": len(remapped), "type": "SCALAR",
                "min": [0], "max": [len(selected)-1]})
            primitives.append(compact)
    for index, node in enumerate(document["nodes"]):
        if index == mesh_node:
            node["mesh"] = 0
        else:
            node.pop("mesh", None)
    # The source Geometry parent is identity. Keep the original body and its
    # transform so each visual retains its original global rest coordinates.
    parents = {child: i for i, node in enumerate(original["nodes"]) for child in node.get("children", [])}
    body = parents[mesh_node]
    parent = parents.get(body)
    if parent is not None and not np.allclose(_transform(original["nodes"][parent]), np.eye(4), atol=1e-10, rtol=0):
        raise ValueError("Kitchen visual partition requires the reviewed identity Geometry parent")
    document.update(scenes=[{"nodes": [body]}], scene=0, meshes=[{"primitives": primitives}],
        bufferViews=views, accessors=accessors, buffers=[{"byteLength": len(binary)}])
    encoded_json = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    encoded_json += b" "*((-len(encoded_json)) % 4)
    binary.extend(b"\0"*((-len(binary)) % 4))
    size = 28+len(encoded_json)+len(binary)
    encoded = (struct.pack("<4sII", b"glTF", 2, size)+struct.pack("<II", len(encoded_json), 0x4e4f534a)
        +encoded_json+struct.pack("<II", len(binary), 0x004e4942)+binary)
    if size > 128*1024*1024:
        raise ValueError("Kitchen visual source partition exceeds its bounded budget")
    path = cache / f"visual-source-node{mesh_node}.glb"
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != encoded:
            raise ValueError("Kitchen visual partition cache changed")
    else:
        path.write_bytes(encoded)
    return path, {"mesh_node": mesh_node, "source_triangles": triangle_count,
        "output_primitives": len(primitives), "raw_source_attribute_copy": True,
        "geometry_simplified": False, "sha256": hashlib.sha256(encoded).hexdigest()}


def import_kitchen_counter(cache_dir, *, prefix="counter", position=(0, 0, 0), timeout=240, cancelled=None):
    TypeAdapter(Identifier).validate_python(prefix+"_collision199")
    position = TypeAdapter(Vec3).validate_python(position)
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("Kitchen preparation timeout must be in (0, 300]")
    deadline, cache = time.monotonic()+timeout, Path(cache_dir).expanduser().resolve()

    def check():
        if cancelled and cancelled():
            raise ValueError("Kitchen counter preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("Kitchen counter preparation exceeded its time budget")

    def remaining(limit=120):
        check()
        return min(limit, deadline-time.monotonic())

    def adapt(candidate):
        glb, notice, metadata = _source(candidate, cache, check=check, remaining=remaining)
        decoded = decode_glb(glb, cache / "decoded", timeout=remaining(), cancelled=cancelled)
        geometry = GLBGeometry(decoded.path)
        primitives = geometry.primitives()
        parts = kitchen_counter_parts(geometry.document, primitives)
        check()
        visual_batches, partition_records = {}, []
        for part in parts:
            check()
            partition, record = _visual_batch(geometry, part["mesh_node"], cache)
            batch = prepare_glb_visuals(partition, cache / "visuals", source_notice=notice,
                source_metadata={**metadata, "visual_partition": record},
                timeout=remaining(240), cancelled=cancelled)
            visual_batches[part["mesh_node"]] = batch
            partition_records.append(record)
        if sum(r["source_triangles"] for r in partition_records) != sum(len(p.triangles) for p in primitives):
            raise ValueError("Kitchen visual partition lost source triangles")
        low = np.min([a for p in parts for a, _ in p["boxes"]], 0)
        high = np.max([b for p in parts for _, b in p["boxes"]], 0)
        offset = np.array(position)-np.array([(low[0]+high[0])/2, (low[1]+high[1])/2, low[2]])
        limitations = [
            "Original kitchen counter meshes and material maps retain all nine source body visuals; seven real slides and the actual hinged door retain source frames and limits.",
            "Visual geometry is partitioned by physical source body into triangle batches with exact raw source attribute values and unused vertices removed; every original triangle and material is retained, without simplification.",
            "Source connected components use conservative boxes; tiny hardware under 6 cm is grouped in 8 cm cells, and boxes already fully contained in another envelope are removed. Small holes and rounded details may be filled.",
            "The two angled door hinges use conservative envelopes of exact source triangles clipped at the neighboring tall pullout front's Y edge, retaining over 6 mm of measured X clearance. Existing component IDs and all original visual geometry are retained; sibling moving parts still collide.",
            "Source-empty cabinet pockets are carved after triangle/box separation checks; drawer bottoms, sides, fronts and actual handle bars remain separate source-derived components.",
            "Source body mass is distributed by proxy volume; inertia, center of mass and restitution are not retained. Missing collider material links explicitly use the source's sole recorded friction material (0.4).",
            "Official USD drive definitions are retained as source evidence, but active drives and their target positions are not implemented. Runtime passive damping and frictionloss are explicitly zero; source drive damping is not silently treated as a bearing loss.",
            "Inspected official Physics, base and MuJoCo enabled layers do not author joint physics:collisionEnabled; USD fallback false is retained for each connected counter/moving-part rigid group only. Robot, prop, moving-part-to-moving-part and other environment contacts remain enabled. The binary root layer was not composed or parsed.",
            "The three inner drawers have source front lips rather than handle bars; pull-front mappings identify those original front panels.",
            "Drawer cavity bounds describe their rest pose and move with the drawer; no stationary moving-drawer Region is emitted.",
            "Geometric checks only: native contacts, opening, pulling, reachability and task success remain unvalidated.", PBR_RENDERER_NOTICE]
        recipe = {"schema_version": 1, "profile": "kitchen-counter-source-panels-v1", "source": metadata,
            "decoded_sha256": json.loads(decoded.manifest_path.read_text())["sha256"],
            "weld_grid_metres": .00001, "tiny_hardware_cell_metres": .08,
            "visual_partitions": partition_records,
            "source_joint_collision_policy": json.loads(_CATALOG.read_text())["joint_collision_policy"],
            "limitations": limitations, "parts": [{**{k: v for k, v in p.items() if k not in {"boxes", "joint", "extra_boxes"}},
                "joint": p["joint"].model_dump(mode="json") if p["joint"] else None,
                "extra_components": [{"low": a.tolist(), "high": b.tolist()} for a, b in p["extra_boxes"]],
                "components": [{"low": a.tolist(), "high": b.tolist()} for a, b in p["boxes"]]} for p in parts]}
        encoded = (json.dumps(recipe, indent=2, allow_nan=False)+"\n").encode()
        recipe_path = cache / ("kitchen-counter-physics-"+hashlib.sha256(encoded).hexdigest()+".json")
        if recipe_path.exists() or recipe_path.is_symlink():
            if recipe_path.is_symlink() or recipe_path.read_bytes() != encoded:
                raise ValueError("Kitchen counter physics provenance changed")
        else:
            recipe_path.write_bytes(encoded)
        files = {f"node{node}/{name}": checksum for node, batch in visual_batches.items()
            for name, checksum in batch.source_files.items()}
        asset = ImportedAsset(source_files=files, disclosures=limitations)
        asset.source_files.update({"optimized-source.glb": candidate.metadata["download_sha256"],
            "upstream-license.txt": candidate.metadata["upstream_license_sha256"],
            "upstream-packaging.json": candidate.metadata["upstream_packaging_sha256"],
            "upstream-physics.usda": candidate.metadata["upstream_physics_sha256"],
            "upstream-base.usda": candidate.metadata["upstream_base_sha256"],
            "kitchen-counter-physics.json": hashlib.sha256(encoded).hexdigest()})
        roots, visual_parents, extra_objects = {}, {}, []
        for ordinal, part in enumerate(parts):
            visuals = visual_batches[part["mesh_node"]]
            root = f"{prefix}_body{ordinal}"
            roots[ordinal] = root
            volume = np.array([np.prod(b-a) for a, b in [*part["boxes"], *part["extra_boxes"]]])
            for i, (a, b) in enumerate(part["boxes"]):
                joint = part["joint"].model_copy(deep=True) if i == 0 and part["joint"] else None
                if joint:
                    joint.anchor = np.array(joint.anchor)+offset
                asset.objects.append(SceneObject(id=root if i == 0 else f"{prefix}_body{ordinal}_collision{i}",
                    label=f"Source kitchen {part['name']} component", kind="box", position=(a+b)/2+offset,
                    size=b-a, mass=part["mass"]*volume[i]/volume.sum(), friction=part["friction"],
                    movable=bool(joint), joint=joint, visible=False,
                    parent_id=(roots[0] if ordinal else None) if i == 0 else root,
                    source_notice=str(visuals.path / "NOTICE.md"), source_metadata=str(recipe_path)))
            for i, (a, b) in enumerate(part["extra_boxes"]):
                extra_objects.append(SceneObject(id=f"{prefix}_body{ordinal}_extra{i}",
                    label=f"Source kitchen {part['name']} clipped hinge component", kind="box",
                    position=(a+b)/2+offset, size=b-a,
                    mass=part["mass"]*volume[len(part["boxes"])+i]/volume.sum(), friction=part["friction"],
                    visible=False, parent_id=root, source_notice=str(visuals.path / "NOTICE.md"),
                    source_metadata=str(recipe_path)))
            asset.body_ids[part["name"]] = root
            if part["joint"]:
                asset.joint_ids[f"joint_shelf_{ordinal:02}_joint_00"] = root
            for name, i in {**part["support_components"], **part["handle_components"]}.items():
                asset.geom_ids[name] = root if i == 0 else f"{prefix}_body{ordinal}_collision{i}"
            visual_parents[part["mesh_node"]] = root
        for node, batch in visual_batches.items():
            for obj in batch.objects(prefix=f"{prefix}_mesh{node}", parents=visual_parents):
                obj.position = np.array(obj.position)+offset
                asset.objects.append(obj)
        # Append new contact components after existing visuals, preserving every
        # previous prepared-asset part ID and all task/support/joint references.
        asset.objects.extend(extra_objects)
        check()
        return asset

    def unavailable(*args):
        raise ValueError("Kitchen source import unresolved; procedural replacement is not a fallback")

    return source_asset(KITCHEN_COUNTER_ID, search=kitchen_counter_asset_search,
        import_asset=adapt, create_asset=unavailable)


def prepare_kitchen_counter_library(destination, *, cache_dir=None, timeout=240, cancelled=None):
    destination = Path(destination).expanduser()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Kitchen counter library destination already exists")
    destination = destination.resolve()
    cache = Path(cache_dir).expanduser().resolve() if cache_dir is not None else destination.parent / "kitchen-counter-preparation"
    if cache.is_relative_to(destination):
        raise ValueError("Kitchen counter cache must be outside its library destination")
    sourced = import_kitchen_counter(cache, timeout=timeout, cancelled=cancelled)
    library = AssetLibrary().add(KITCHEN_COUNTER_ID, sourced,
        description="Source NVIDIA kitchen counter with countertop, seven real drawer/pullout slides, one hinged cabinet door and hollow storage; original visuals; native contact validation pending")
    return library.save(destination)
