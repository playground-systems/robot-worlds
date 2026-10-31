"""Import a checked subset of MJCF without flattening physical articulation.

Only axis-aligned primitive colliders and textured OBJ appearances are supported.
Unsupported physical features fail explicitly; this is not a general MJCF loader.
Source actuators and global simulation settings are deliberately not imported.
"""
from __future__ import annotations

import hashlib
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
    for obj in result.objects:
        obj.source_notice = str(notice)
    return result
