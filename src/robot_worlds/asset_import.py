"""Import a checked subset of MJCF without flattening physical articulation.

Only axis-aligned primitive colliders and textured OBJ appearances are supported.
Unsupported physical features fail explicitly; this is not a general MJCF loader.
Source actuators and global simulation settings are deliberately not imported.
"""
from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .models import JointSpec, Region, SceneObject


@dataclass
class ImportedAsset:
    objects: list[SceneObject] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    body_ids: dict[str, str] = field(default_factory=dict)
    geom_ids: dict[str, str] = field(default_factory=dict)
    joint_ids: dict[str, str] = field(default_factory=dict)
    source_files: dict[str, str] = field(default_factory=dict)
    disclosures: list[str] = field(default_factory=lambda: [
        "Imported collision shapes retain source dimensions; density/masses are estimates when unspecified.",
        "Source textures and UVs are retained; renderer material response is an approximation.",
        "Tangential friction is imported; torsional/rolling friction use Robot Worlds runtime defaults.",
        "Source global simulation options and actuators are omitted; the robot must interact physically.",
    ])


def import_mjcf_asset(path: str | Path, *, prefix: str, position=(0, 0, 0), source_notice: str | Path | None = None) -> ImportedAsset:
    """Convert local MJCF to scene parts, preserving supported source geometry.

    Resolve licensing/search provenance before calling this importer and retain
    it alongside source_files. It never fetches remote dependencies or executes
    source code. Return IDs identify original bodies, joints and named colliders.
    """
    path = Path(path).resolve()
    root_dir = path.parent
    notice = Path(source_notice).resolve() if source_notice else root_dir / "ATTRIBUTION.md"
    if not notice.is_file() or notice.suffix.lower() not in {".md", ".txt"} or notice.stat().st_size > 1024 * 1024:
        raise ValueError("Import requires a retained source/license attribution notice")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("MJCF source exceeds import budget")
    source = path.read_bytes()
    if b"<!" in source:
        raise ValueError("MJCF declarations/entities are unsupported")
    tree = ET.fromstring(source)
    if tree.tag != "mujoco" or any(tree.findall(".//" + tag) for tag in ("include", "plugin", "flex", "tendon", "equality", "inertial")):
        raise ValueError("Unsupported MJCF physical feature or external include")
    compiler = tree.find("compiler")
    if compiler is None or compiler.get("angle", "degree") != "radian":
        raise ValueError("Importer currently requires radian MJCF")
    if any(compiler.get(key) for key in ("meshdir", "texturedir", "assetdir")):
        raise ValueError("Use source-relative asset paths; compiler asset directories are unsupported")
    result = ImportedAsset(source_files={path.name: hashlib.sha256(source).hexdigest()})
    offset = np.asarray(position, dtype=float)
    if offset.shape != (3,) or not np.isfinite(offset).all():
        raise ValueError("Asset position must have three finite coordinates")

    def file(name):
        candidate = (root_dir / name).resolve()
        if not candidate.is_relative_to(root_dir) or not candidate.is_file():
            raise ValueError("Missing source file or asset path outside the source directory")
        if candidate.stat().st_size > 24 * 1024 * 1024:
            raise ValueError("Asset file exceeds import budget")
        result.source_files[str(candidate.relative_to(root_dir))] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return candidate

    def vector(value, default, length=3):
        data = np.asarray([float(v) for v in value.split()] if value is not None else default)
        if data.shape != (length,) or not np.isfinite(data).all():
            raise ValueError("Invalid source vector")
        return data

    def axis_aligned(attrs):
        if any(key in attrs for key in ("euler", "axisangle", "xyaxes", "zaxis", "fromto")):
            raise ValueError("Rotated source geometry is not supported by this importer")
        quat = vector(attrs.get("quat"), [1, 0, 0, 0], 4)
        if not np.allclose(np.abs(quat), [1, 0, 0, 0], atol=1e-9):
            raise ValueError("Rotated source geometry is not supported by this importer")

    defaults = {}
    def read_defaults(element, inherited):
        geom = element.find("geom")
        attrs = {**inherited, **(geom.attrib if geom is not None else {})}
        defaults[element.get("class", "")] = attrs
        for child in element.findall("default"):
            read_defaults(child, attrs)
    for default in tree.findall("default"):
        read_defaults(default, {})
    assets = tree.find("asset")
    meshes = {m.get("name"): m for m in assets.findall("mesh")} if assets is not None else {}
    textures = {m.get("name"): m for m in assets.findall("texture")} if assets is not None else {}
    materials = {m.get("name"): m for m in assets.findall("material")} if assets is not None else {}

    def visit(body, origin, parent_id=None):
        axis_aligned(body.attrib)
        if any(key in body.attrib for key in ("mocap", "gravcomp")) or body.find("freejoint") is not None:
            raise ValueError("Unsupported source body dynamics")
        origin = origin + vector(body.get("pos"), [0, 0, 0])
        colliders, visuals, regions = [], [], []
        for geom in body.findall("geom"):
            cls = geom.get("class", body.get("childclass", ""))
            if cls and cls not in defaults:
                raise ValueError("Unresolved source default class")
            attrs = {**defaults.get(cls, {}), **geom.attrib}
            axis_aligned(attrs)
            if int(attrs.get("contype", 1)) or int(attrs.get("conaffinity", 1)):
                if attrs.get("type", "sphere") not in {"box", "cylinder", "sphere"}:
                    raise ValueError("Source collision meshes need a verified convex decomposition importer")
                colliders.append(attrs)
            elif attrs.get("type") == "mesh":
                visuals.append(attrs)
            elif cls == "region" and attrs.get("name") == "reg_int":
                regions.append(attrs)
        joints = body.findall("joint")
        if len(joints) > 1 or ((visuals or joints) and not colliders):
            raise ValueError("Each visual/articulated source body needs primitive collision geometry")
        root_id = None
        for index, attrs in enumerate(colliders):
            oid = f"{prefix}_part{len(result.objects)}"
            if root_id is None:
                root_id = oid
            kind = attrs.get("type", "sphere")
            raw_size = [float(v) for v in attrs.get("size", "").split()]
            if kind == "box" and len(raw_size) == 3:
                size = np.asarray(raw_size) * 2
                volume = float(np.prod(size))
            elif kind == "cylinder" and len(raw_size) == 2:
                size = np.asarray([raw_size[0]*2, raw_size[0]*2, raw_size[1]*2])
                volume = math.pi * raw_size[0]**2 * raw_size[1]*2
            elif kind == "sphere" and len(raw_size) == 1:
                size = np.asarray([raw_size[0]*2]*3)
                volume = 4/3 * math.pi * raw_size[0]**3
            else:
                raise ValueError("Unsupported source collider size")
            joint = None
            if index == 0 and joints:
                j = joints[0]
                if j.get("type", "hinge") not in {"hinge", "slide"} or j.get("springref", "0") != "0":
                    raise ValueError("Unsupported source joint")
                joint = JointSpec(type=j.get("type", "hinge"), axis=vector(j.get("axis"), [0, 0, 1]),
                                  anchor=origin+vector(j.get("pos"), [0, 0, 0]),
                                  range=vector(j.get("range"), [-math.pi, math.pi], 2),
                                  limited=j.get("limited", "true") != "false",
                                  damping=float(j.get("damping", 0)), frictionloss=float(j.get("frictionloss", 0)),
                                  armature=float(j.get("armature", 0)), stiffness=float(j.get("stiffness", 0)))
                if j.get("name"):
                    result.joint_ids[j.get("name")] = oid
            obj = SceneObject(id=oid, label=attrs.get("name", body.get("name", "Asset collision")),
                              kind=kind, position=origin+vector(attrs.get("pos"), [0, 0, 0]), size=size,
                              parent_id=parent_id if index == 0 else root_id, joint=joint, visible=False,
                              mass=float(attrs.get("mass", volume*float(attrs.get("density", 1000)))),
                              friction=float(attrs.get("friction", "1 .005 .0001").split()[0]))
            result.objects.append(obj)
            if attrs.get("name"):
                result.geom_ids[attrs["name"]] = oid
        if body.get("name") and root_id:
            result.body_ids[body.get("name")] = root_id
        for attrs in visuals:
            mesh = meshes.get(attrs.get("mesh"))
            if mesh is None or set(mesh.attrib) - {"name", "file", "scale"} or not np.allclose(vector(mesh.get("scale"), [1, 1, 1]), 1):
                raise ValueError("Unsupported or missing visual mesh definition")
            mesh_path = file(mesh.get("file", ""))
            if mesh_path.suffix.lower() != ".obj":
                raise ValueError("Importer currently requires OBJ visual meshes")
            vertices = np.asarray([[float(x) for x in line.split()[1:4]] for line in mesh_path.read_text().splitlines() if line.startswith("v ")])
            if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
                raise ValueError("Invalid source mesh vertices")
            lower, upper = vertices.min(0), vertices.max(0)
            material = materials.get(attrs.get("material"))
            if attrs.get("material") and material is None:
                raise ValueError("Missing source material")
            rgba = vector(attrs.get("rgba", material.get("rgba") if material is not None else None), [1, 1, 1, 1], 4)
            texture_path = None
            if material is not None and material.get("texture"):
                texture = textures.get(material.get("texture"))
                if texture is None or texture.get("type", "2d") != "2d":
                    raise ValueError("Missing or unsupported source texture")
                texture_path = file(texture.get("file", ""))
                from .mesh_assets import validate_texture
                validate_texture(texture_path)
            result.objects.append(SceneObject(
                id=f"{prefix}_visual{len(result.objects)}", label=body.get("name", "Asset appearance"),
                kind="mesh", position=origin+vector(attrs.get("pos"), [0, 0, 0])+(lower+upper)/2,
                size=upper-lower, mesh_path=str(mesh_path), texture_path=str(texture_path) if texture_path else None,
                parent_id=root_id, collision=False, color=rgba[:3], opacity=rgba[3],
            ))
        for attrs in regions:
            if attrs.get("type") != "box":
                raise ValueError("Unsupported source interior region")
            result.regions.append(Region(id=f"{prefix}_interior{len(result.regions)}", label="Imported appliance cavity",
                                         position=origin+vector(attrs.get("pos"), [0, 0, 0]),
                                         size=vector(attrs.get("size"), [0, 0, 0])*2, interior_of=root_id))
        for child in body.findall("body"):
            visit(child, origin, root_id or parent_id)

    worldbody = tree.find("worldbody")
    if worldbody is None or worldbody.findall("geom"):
        raise ValueError("Source must contain asset bodies, not world geometry")
    for body in worldbody.findall("body"):
        visit(body, offset)
    if not result.objects:
        raise ValueError("Source contains no supported physical asset")
    objects = {obj.id: obj for obj in result.objects}
    for exclusion in tree.findall("./contact/exclude"):
        first = objects.get(result.body_ids.get(exclusion.get("body1")))
        second = objects.get(result.body_ids.get(exclusion.get("body2")))
        if first is not None and second is not None:
            child = (first if first.joint and first.parent_id == second.id else
                     second if second.joint and second.parent_id == first.id else None)
        else:
            child = None
        if child is None or set(exclusion.attrib) - {"name", "body1", "body2"}:
            raise ValueError("Source contact exclusions currently require an adjacent articulated parent/child body pair")
        child.joint.collide_parent = False
    if tree.findall("./contact/exclude"):
        result.disclosures.append("Explicit source adjacent-joint contact exclusions are retained across their parent/child rigid component groups.")
    for obj in result.objects:
        obj.source_notice = str(notice)
        if (root_dir / "source-manifest.json").is_file():
            obj.source_metadata = str(root_dir / "source-manifest.json")
    return result


def import_convex_mesh_asset(mesh_path: str | Path, collision_manifest: str | Path, *,
                             prefix: str, mass: float, source_notice: str | Path,
                             texture_path: str | Path | None = None,
                             position=(0, 0, 0), movable=True, friction=.7) -> ImportedAsset:
    """Use original appearance with verified, source-bound convex components.

    Conversion is an offline step; importing never invokes a decomposer. Each
    component's checksum, closed surface and convexity are checked. Mass is
    distributed by component volume and remains a caller-supplied estimate.
    ``position`` translates the original mesh coordinates without recentering.
    """
    from .mesh_assets import (
        MAX_CONVEX_COMPONENTS,
        read_triangle_obj,
        validate_texture,
        verified_convex_obj,
    )

    mesh_path, collision_manifest, notice = (Path(p).resolve() for p in (mesh_path, collision_manifest, source_notice))
    if not notice.is_file() or notice.suffix.lower() not in {".md", ".txt"} or notice.stat().st_size > 1024 * 1024:
        raise ValueError("Import requires a retained source/license attribution notice")
    if isinstance(mass, bool) or not math.isfinite(mass) or mass <= 0:
        raise ValueError("Total asset mass must be finite and positive")
    offset = np.asarray(position, dtype=float)
    if offset.shape != (3,) or not np.isfinite(offset).all():
        raise ValueError("Asset position must have three finite coordinates")
    vertices, _ = read_triangle_obj(mesh_path)
    visual_lower, visual_upper = vertices.min(0), vertices.max(0)
    if collision_manifest.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Collision manifest exceeds import budget")
    manifest = json.loads(collision_manifest.read_text())
    source_hash = hashlib.sha256(mesh_path.read_bytes()).hexdigest()
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("source_sha256") != source_hash:
        raise ValueError("Collision conversion does not match the original source mesh")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_CONVEX_COMPONENTS:
        raise ValueError(f"Collision manifest must contain 1–{MAX_CONVEX_COMPONENTS} verified components")
    result = ImportedAsset(disclosures=[
        "Original textured visual surface is retained; collision geometry is an approximate convex decomposition.",
        "Collision components are checksum-bound to the source and checked for closed convex surfaces; native cavity/contact checks are still required.",
        "Total mass and friction are caller-supplied estimates; component masses are distributed by volume.",
    ])
    result.source_files["visual/"+mesh_path.name] = source_hash
    result.source_files["collision/manifest.json"] = hashlib.sha256(collision_manifest.read_bytes()).hexdigest()
    result.source_files["notice/"+notice.name] = hashlib.sha256(notice.read_bytes()).hexdigest()
    if texture_path is not None:
        texture_path = Path(texture_path).resolve()
        validate_texture(texture_path)
        result.source_files["texture/"+texture_path.name] = hashlib.sha256(texture_path.read_bytes()).hexdigest()
    verified, seen = [], set()
    for record in parts:
        if not isinstance(record, dict):
            raise ValueError("Invalid collision component record")
        name = record.get("file")
        if not isinstance(name, str) or Path(name).name != name or name in seen:
            raise ValueError("Collision files must have unique local filenames")
        seen.add(name)
        path = (collision_manifest.parent / name).resolve()
        if not path.is_relative_to(collision_manifest.parent):
            raise ValueError("Collision file escapes its conversion directory")
        digest = record.get("sha256")
        lower, upper, volume = verified_convex_obj(path, digest)
        if np.any(lower < visual_lower-.005) or np.any(upper > visual_upper+.005):
            raise ValueError("Collision conversion expands more than 5 mm outside source bounds")
        verified.append((path, lower, upper, volume))
        result.source_files["collision/"+name] = digest
    if len(parts) == 1 and parts[0]["sha256"] == source_hash:
        result.disclosures[:2] = [
            "Original mesh was verified closed and convex; collision geometry uses the unchanged source surface.",
            "Original texture and UVs are retained; native contact checks are still required.",
        ]
    total_volume = sum(part[3] for part in verified)
    root_id = prefix + "_body"
    result.body_ids["object"] = root_id
    for index, (path, lower, upper, volume) in enumerate(verified):
        oid = root_id if index == 0 else f"{prefix}_collision{index}"
        result.objects.append(SceneObject(
            id=oid, label="Imported convex collision component", kind="mesh", mesh_path=str(path),
            source_notice=str(notice), source_metadata=str(collision_manifest), position=offset+(lower+upper)/2, size=upper-lower,
            mass=mass*volume/total_volume, friction=friction,
            movable=movable if index == 0 else False, parent_id=None if index == 0 else root_id,
            visible=False,
        ))
    result.objects.append(SceneObject(
        id=prefix+"_visual", label="Original source appearance", kind="mesh", mesh_path=str(mesh_path),
        texture_path=str(texture_path) if texture_path else None, source_notice=str(notice), source_metadata=str(collision_manifest),
        position=offset+(visual_lower+visual_upper)/2, size=visual_upper-visual_lower,
        color=(1, 1, 1), parent_id=root_id, collision=False,
    ))
    return result
