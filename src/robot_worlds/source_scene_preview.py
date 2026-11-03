"""Frozen browser export from native source geometry, without native textures.

The original verified bundle is never edited. Texture definitions/bindings
are detached in the derived compilation XML; source images remain byte-exact.
Mesh compilation is serialized to bound transient memory usage.
There is no simulation step, geometry substitution or material inference.
"""

from __future__ import annotations

import copy
import hashlib
import json
import resource
import shutil
import struct
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

from .artifacts import canonical, digest, file_digest, inside
from .source_scene_bundle import SourceSceneBundle, verify_source_scene_bundle


def _xml_shape(element):
    return (
        element.tag,
        sorted(element.attrib.items()),
        (element.text or "").strip(),
        [_xml_shape(child) for child in element],
    )


def _without_texture_rendering(root):
    for parent in root.iter():
        for child in list(parent):
            if (parent.tag == "asset" and child.tag == "texture") or (
                child.tag == "layer" and "texture" in child.attrib
            ):
                parent.remove(child)
        if parent.tag == "material":
            parent.attrib.pop("texture", None)
    return root


def _default_assets(root):
    defaults = {"": {"material": {}, "texture": {}}}

    def visit(node, inherited):
        values = copy.deepcopy(inherited)
        for tag in values:
            for child in node.findall(tag):
                values[tag].update(child.attrib)
        defaults[node.get("class", "")] = values
        for child in node.findall("default"):
            visit(child, values)

    for node in root.findall("default"):
        visit(node, defaults[""])
    return defaults


def prepare_texture_free_scene(bundle_path: str | Path) -> dict[str, Any]:
    """Verify inputs and return derived XML plus original appearance metadata.

    This stage is offline. It does not import MuJoCo or decode texture pixels.
    """
    bundle = verify_source_scene_bundle(bundle_path)
    original = bundle.xml_path.read_bytes()
    root = ET.fromstring(original)
    original_texture_attributes = [dict(node.attrib) for node in root.findall("./asset/texture")]
    references = iter(bundle.manifest["references"])
    for element in root.iter():
        for attribute, value in list(element.attrib.items()):
            if not attribute.startswith("file"):
                continue
            row = next(references)
            if (element.tag, attribute, value) != (
                row["tag"],
                row["attribute"],
                row["bundled_value"],
            ):
                raise ValueError("Verified source reference order changed")
            element.set(attribute, str(inside(bundle.directory, row["bundle_path"])))
    if next(references, None) is not None:
        raise ValueError("Verified source reference inventory changed")
    absolute = copy.deepcopy(root)
    defaults = _default_assets(root)
    textures, materials = [], {}
    for element in root.findall("./asset/texture"):
        resolved = {
            **defaults.get(element.get("class", ""), defaults[""])["texture"],
            **element.attrib,
        }
        name = resolved.get("name")
        if name is None and resolved.get("file"):
            name = Path(resolved["file"]).stem
        textures.append(
            dict(
                id=len(textures),
                name=name,
                original_attributes=original_texture_attributes[len(textures)],
                resolved_attributes=resolved,
            )
        )
    for element in root.findall("./asset/material"):
        name = element.get("name")
        if not name or name in materials:
            raise ValueError("Browser export requires unique named original materials")
        resolved = {
            **defaults.get(element.get("class", ""), defaults[""])["material"],
            **element.attrib,
        }
        materials[name] = dict(
            source_attributes=dict(element.attrib),
            resolved_attributes=resolved,
            layers=[dict(child.attrib) for child in element.findall("layer")],
        )
    edits = dict(
        removed_texture_definitions=[dict(node.attrib) for node in root.findall("./asset/texture")],
        detached_material_bindings=[
            dict(node.attrib) for node in root.iter("material") if "texture" in node.attrib
        ],
        detached_texture_layers=[
            dict(node.attrib) for node in root.iter("layer") if "texture" in node.attrib
        ],
        absolute_asset_references=len(bundle.manifest["references"]),
    )
    derived = _without_texture_rendering(root)
    expected = _without_texture_rendering(copy.deepcopy(absolute))
    if _xml_shape(derived) != _xml_shape(expected):
        raise ValueError("Derived XML changed non-texture source semantics")
    compiler = derived.find("compiler")
    if compiler is None:
        compiler = ET.SubElement(derived, "compiler")
    edits["compiler_execution"] = dict(
        attribute="usethread", original=compiler.get("usethread"), derived="false"
    )
    # Execution scheduling only: retain every source asset and physical parameter.
    compiler.set("usethread", "false")
    xml = ET.tostring(derived, encoding="utf-8")
    return dict(
        bundle=bundle,
        xml=xml,
        textures=textures,
        materials=materials,
        edits=edits,
        source_xml_sha256=hashlib.sha256(original).hexdigest(),
        derived_xml_sha256=hashlib.sha256(xml).hexdigest(),
        non_texture_semantics_sha256=digest(_xml_shape(expected)),
        source_lights=[dict(node.attrib) for node in absolute.iter("light")],
    )


def _png_metadata(path):
    """Read dimensions and MuJoCo's exact PNG-auto sRGB signal, without pixels."""
    width = height = None
    srgb = False
    with path.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Texture .png has an invalid PNG signature")
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("Truncated PNG texture metadata")
            size, kind = struct.unpack(">I4s", header)
            if size > path.stat().st_size:
                raise ValueError("Invalid PNG chunk length")
            if kind in {b"IHDR", b"sRGB"}:
                payload, crc = stream.read(size), stream.read(4)
                if len(crc) != 4 or zlib.crc32(kind + payload) != struct.unpack(">I", crc)[0]:
                    raise ValueError("Invalid PNG metadata checksum")
                if kind == b"IHDR":
                    if size != 13:
                        raise ValueError("Invalid PNG header")
                    width, height = struct.unpack(">II", payload[:8])
                else:
                    if size != 1:
                        raise ValueError("Invalid PNG sRGB metadata")
                    srgb = True
            else:
                stream.seek(size + 4, 1)
            if kind == b"IEND":
                break
    if not width or not height:
        raise ValueError("PNG texture has no valid dimensions")
    return dict(width=width, height=height, png_srgb_chunk=srgb)


def _copy_textures(prepared, stage):
    textures, issues = [], []
    bundle = prepared["bundle"]
    declared_names = set()
    for original in prepared["textures"]:
        record = copy.deepcopy(original)
        attrs = record["resolved_attributes"]
        if record["name"] is not None:
            if record["name"] in declared_names:
                raise ValueError("Original texture names must be unique")
            declared_names.add(record["name"])
        record.update(type=attrs.get("type", "cube"), path=None, colorspace=None, source_files=[])
        for attribute, source_name in attrs.items():
            if not attribute.startswith("file"):
                continue
            source = inside(bundle.directory, str(Path(source_name).relative_to(bundle.directory)))
            if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError(
                    "Browser texture format unsupported; no image conversion is allowed"
                )
            expected = file_digest(source)
            member = "textures/" + expected + source.suffix.lower()
            target = stage / member
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                shutil.copyfile(source, target)
            if file_digest(target) != expected:
                raise ValueError("Original texture changed during copying")
            metadata = _png_metadata(source) if source.suffix.lower() == ".png" else {}
            if not metadata:
                from PIL import Image

                with Image.open(source) as image:
                    metadata = dict(width=image.width, height=image.height)
            record["source_files"].append(
                dict(
                    attribute=attribute,
                    path=member,
                    sha256=expected,
                    byte_length=source.stat().st_size,
                    **metadata,
                )
            )
            if attribute == "file":
                record.update(path=member, byte_length=source.stat().st_size, **metadata)
        declared = attrs.get("colorspace", "auto")
        if declared in {"linear", "srgb", "sRGB"}:
            record["colorspace"] = declared.lower()
        elif declared == "auto" and record.get("png_srgb_chunk") is not None:
            record["colorspace"] = "srgb" if record["png_srgb_chunk"] else "linear"
        elif record["source_files"]:
            issues.append(dict(feature="unresolved_texture_colorspace", texture_id=record["id"]))
        if not record["source_files"]:
            issues.append(
                dict(feature="procedural_texture", texture_id=record["id"], type=record["type"])
            )
        if record["type"] in {"cube", "skybox"} and (
            len(record["source_files"]) != 1 or attrs.get("gridsize", "1 1").split() != ["1", "1"]
        ):
            issues.append(dict(feature="complex_cube_texture_mapping", texture_id=record["id"]))
        textures.append(record)
    return textures, issues


def _name(mj, model, kind, index):
    return mj.mj_id2name(model, getattr(mj.mjtObj, "mjOBJ_" + kind), index)


def _geometry(model, stream, np):
    records = []

    def write(values, item_size):
        array = np.asarray(values, dtype="<f4").reshape(-1, item_size)
        if not np.isfinite(array).all():
            raise ValueError("Compiled geometry contains nonfinite values")
        result = dict(offset=stream.tell(), count=len(array), item_size=item_size)
        stream.write(array.tobytes(order="C"))
        return result

    for mid in range(model.nmesh):
        start, count = int(model.mesh_faceadr[mid]), int(model.mesh_facenum[mid])
        faces = model.mesh_face[start : start + count]
        normal_faces = model.mesh_facenormal[start : start + count]
        uv_faces = model.mesh_facetexcoord[start : start + count]
        if (faces < 0).any() or (normal_faces < 0).any():
            raise ValueError("Compiled mesh lacks explicit face/normal corner indices")
        positions = model.mesh_vert[int(model.mesh_vertadr[mid]) + faces]
        normals = model.mesh_normal[int(model.mesh_normaladr[mid]) + normal_faces]
        uv = None
        if int(model.mesh_texcoordadr[mid]) >= 0:
            if (uv_faces < 0).any():
                raise ValueError("Compiled mesh has incomplete texture corner indices")
            uv = model.mesh_texcoord[int(model.mesh_texcoordadr[mid]) + uv_faces]
        records.append(
            dict(
                id=mid,
                position=write(positions, 3),
                normal=write(normals, 3),
                uv=None if uv is None else write(uv, 2),
            )
        )
    return records


def _materials(prepared, model, mj, textures):
    names = {texture["name"]: texture["id"] for texture in textures if texture["name"] is not None}
    result = []
    for mid in range(model.nmat):
        name = _name(mj, model, "MATERIAL", mid)
        original = prepared["materials"].get(name)
        if original is None:
            raise ValueError("Compiled material has no original named definition")
        bindings = {}
        attribute = original["resolved_attributes"].get("texture")
        if attribute:
            if attribute not in names:
                raise ValueError("Original material references an unknown texture")
            bindings["rgb"] = names[attribute]
        for layer in original["layers"]:
            if layer.get("texture") not in names:
                raise ValueError("Original material layer references an unknown texture")
            bindings[layer.get("role", "rgb")] = names[layer["texture"]]
        record = dict(
            id=mid,
            name=name,
            rgba=model.mat_rgba[mid].tolist(),
            specular=float(model.mat_specular[mid]),
            shininess=float(model.mat_shininess[mid]),
            emission=float(model.mat_emission[mid]),
            reflectance=float(model.mat_reflectance[mid]),
            metallic=float(model.mat_metallic[mid]),
            roughness=float(model.mat_roughness[mid]),
            texrepeat=model.mat_texrepeat[mid].tolist(),
            texuniform=bool(model.mat_texuniform[mid]),
            texture_id=bindings.get("rgba", bindings.get("rgb")),
            texture_roles=bindings,
            **original,
        )
        result.append(record)
    return result


def _bodies(model, data, mj):
    joint_types = {
        int(getattr(mj.mjtJoint, "mjJNT_" + name.upper())): name
        for name in ["hinge", "slide", "free", "ball"]
    }
    records = []
    for bid in range(model.nbody):
        joints = []
        start, count = int(model.body_jntadr[bid]), int(model.body_jntnum[bid])
        for jid in range(start, start + count):
            kind = joint_types[int(model.jnt_type[jid])]
            address = int(model.jnt_qposadr[jid])
            reference = (
                model.qpos0[address : address + (7 if kind == "free" else 4)].tolist()
                if kind in {"free", "ball"}
                else float(model.qpos0[address])
            )
            joints.append(
                dict(
                    id=jid,
                    name=_name(mj, model, "JOINT", jid),
                    type=kind,
                    axis=model.jnt_axis[jid].tolist(),
                    position=model.jnt_pos[jid].tolist(),
                    reference=reference,
                    inspection_supported=kind in {"hinge", "slide"},
                    range=model.jnt_range[jid].tolist(),
                    limited=bool(model.jnt_limited[jid]),
                )
            )
        position = model.body_pos[bid].tolist()
        quaternion = model.body_quat[bid].tolist()
        if any(not joint["inspection_supported"] for joint in joints):
            parent = int(model.body_parentid[bid])
            position = (
                data.xmat[parent].reshape(3, 3).T @ (data.xpos[bid] - data.xpos[parent])
            ).tolist()
            # Inverse parent world quaternion multiplied by child world quaternion.
            a, b, c, d = data.xquat[parent] * [1, -1, -1, -1]
            w, x, y, z = data.xquat[bid]
            quaternion = [
                float(a * w - b * x - c * y - d * z),
                float(a * x + b * w + c * z - d * y),
                float(a * y - b * z + c * w + d * x),
                float(a * z + b * y - c * x + d * w),
            ]
        records.append(
            dict(
                id=bid,
                name=_name(mj, model, "BODY", bid),
                parent_id=None if bid == 0 else int(model.body_parentid[bid]),
                position=position,
                quaternion=quaternion,
                model_position=model.body_pos[bid].tolist(),
                model_quaternion=model.body_quat[bid].tolist(),
                world_position=data.xpos[bid].tolist(),
                world_quaternion=data.xquat[bid].tolist(),
                joints=joints,
            )
        )
    return records


def _objects(model, mj, materials, meshes, textures, np):
    types = {
        int(getattr(mj.mjtGeom, "mjGEOM_" + name.upper())): name
        for name in ["mesh", "box", "sphere", "cylinder", "ellipsoid", "capsule", "plane"]
    }
    result, issues = [], []
    for gid in range(model.ngeom):
        if int(model.geom_group[gid]) != 1:
            continue
        kind = types.get(int(model.geom_type[gid]))
        if kind is None:
            raise ValueError("Unsupported original visible native geom type")
        mid = int(model.geom_matid[gid])
        mesh_id = int(model.geom_dataid[gid]) if kind == "mesh" else None
        rgba = model.geom_rgba[gid].tolist()
        effective = (
            rgba
            if mid < 0
            or np.any(model.geom_rgba[gid] != np.array([0.5, 0.5, 0.5, 1], dtype=np.float32))
            else materials[mid]["rgba"]
        )
        size = model.geom_size[gid].tolist()
        visual_size = (
            [size[0]] * 3
            if kind == "sphere"
            else [size[0], size[0], size[1]]
            if kind in {"cylinder", "capsule"}
            else size
        )
        result.append(
            dict(
                id=gid,
                name=_name(mj, model, "GEOM", gid),
                body_id=int(model.geom_bodyid[gid]),
                type=kind,
                mesh_id=mesh_id,
                size=size,
                visual_size=visual_size,
                position=model.geom_pos[gid].tolist(),
                quaternion=model.geom_quat[gid].tolist(),
                material_id=None if mid < 0 else mid,
                rgba=rgba,
                effective_rgba=effective,
            )
        )
        if (
            mid >= 0
            and materials[mid]["texture_id"] is not None
            and textures[materials[mid]["texture_id"]]["type"] == "2d"
            and kind == "mesh"
            and meshes[mesh_id]["uv"] is None
        ):
            issues.append(dict(feature="mesh_without_authored_uv", object_id=gid, material_id=mid))
        if mid >= 0 and materials[mid]["reflectance"] > 0:
            issues.append(
                dict(feature="source_reflectance_not_rendered", object_id=gid, material_id=mid)
            )
    return result, issues


def _lights(prepared, model, data, mj):
    result = []
    for lid in range(model.nlight):
        result.append(
            dict(
                id=lid,
                name=_name(mj, model, "LIGHT", lid),
                body_id=int(model.light_bodyid[lid]),
                position=data.light_xpos[lid].tolist(),
                direction=data.light_xdir[lid].tolist(),
                frame="world",
                ambient=model.light_ambient[lid].tolist(),
                diffuse=model.light_diffuse[lid].tolist(),
                specular=model.light_specular[lid].tolist(),
                directional=int(model.light_type[lid]) == int(mj.mjtLightType.mjLIGHT_DIRECTIONAL),
                type=int(model.light_type[lid]),
                castshadow=bool(model.light_castshadow[lid]),
                active=bool(model.light_active[lid]),
                attenuation=model.light_attenuation[lid].tolist(),
                cutoff=float(model.light_cutoff[lid]),
                exponent=float(model.light_exponent[lid]),
                intensity=float(model.light_intensity[lid]),
                range=float(model.light_range[lid]),
                source_attributes=prepared["source_lights"][lid]
                if lid < len(prepared["source_lights"])
                else None,
            )
        )
    headlight = model.vis.headlight
    return result, dict(
        ambient=headlight.ambient.tolist(),
        diffuse=headlight.diffuse.tolist(),
        specular=headlight.specular.tolist(),
        active=bool(headlight.active),
    )


def export_source_scene_preview(
    bundle_path: str | Path,
    output_dir: str | Path,
    *,
    views: list[dict[str, Any]],
    name: str | None = None,
) -> Path:
    """Compile a texture-free derivative and export zero-step native geometry.

    Run full scenes only inside the caller's approved resource watchdog. No
    renderer, native texture allocation, robot/controller or policy is created.
    """
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Preview output already exists; preserve previous evidence")
    prepared = prepare_texture_free_scene(bundle_path)
    bundle: SourceSceneBundle = prepared["bundle"]
    authored_views = json.loads(canonical(views))
    if not isinstance(authored_views, list):
        raise ValueError("Preview views must be the authored JSON list")
    import mujoco as mj
    import numpy as np

    started = time.monotonic()
    print("Compiling texture-free source geometry; no physics steps", flush=True)
    model = mj.MjModel.from_xml_string(prepared["xml"].decode())
    print("Source geometry compiled; exporting native initial state", flush=True)
    if model.ntex != 0:
        raise ValueError("Derived model unexpectedly allocated native textures")
    data = mj.MjData(model)
    mj.mj_forward(model, data)
    if not np.isfinite(data.qpos).all() or not np.isfinite(data.xpos).all():
        raise ValueError("Native initial state is nonfinite")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".source-preview-", dir=destination.parent
    ) as temporary:
        stage = Path(temporary) / "preview"
        stage.mkdir()
        textures, issues = _copy_textures(prepared, stage)
        with (stage / "geometry.bin").open("wb") as stream:
            meshes = _geometry(model, stream, np)
        materials = _materials(prepared, model, mj, textures)
        bodies = _bodies(model, data, mj)
        objects, object_issues = _objects(model, mj, materials, meshes, textures, np)
        lights, headlight = _lights(prepared, model, data, mj)
        issues.extend(object_issues)
        for body in bodies:
            for joint in body["joints"]:
                if not joint["inspection_supported"]:
                    issues.append(
                        dict(
                            feature="joint_inspection_unsupported",
                            body_id=body["id"],
                            joint_id=joint["id"],
                            type=joint["type"],
                        )
                    )
        skybox = next((texture for texture in textures if texture["type"] == "skybox"), None)
        background = None
        if skybox is not None and "rgb1" in skybox["resolved_attributes"]:
            background = dict(
                color=[float(value) for value in skybox["resolved_attributes"]["rgb1"].split()],
                limitation="Author-approved flat source skybox rgb1 only; gradient/horizon and environment-lighting contribution are not reproduced.",
            )
        clipping = dict(
            near=float(model.vis.map.znear * model.stat.extent),
            far=float(model.vis.map.zfar * model.stat.extent),
            extent=float(model.stat.extent),
            source_znear=float(model.vis.map.znear),
            source_zfar=float(model.vis.map.zfar),
        )
        payload = dict(
            schema_version=1,
            name=name or bundle.xml_path.stem,
            bundle_id=bundle.manifest["id"],
            geometry_file="geometry.bin",
            geometry_byte_length=(stage / "geometry.bin").stat().st_size,
            geometry_sha256=file_digest(stage / "geometry.bin"),
            geometry_dtype="float32",
            geometry_byte_order="little",
            geometry_offset_unit="bytes",
            meshes=meshes,
            objects=objects,
            bodies=bodies,
            materials=materials,
            textures=textures,
            lights=lights,
            headlight=headlight,
            background=background,
            clipping=clipping,
            views=authored_views,
            unsupported_features=issues,
            limitations=[
                "Frozen native initial state; mj_forward only, zero simulation steps.",
                "Browser material/lighting translation and unsupported_features require visual review; exact render equivalence is unclaimed.",
                "Source compilation does not establish settling, contact validity, grasping or task success.",
            ],
            native=dict(
                mujoco_version=mj.__version__,
                bodies=model.nbody,
                joints=model.njnt,
                joint_types=dict(
                    Counter(joint["type"] for body in bodies for joint in body["joints"])
                ),
                geoms=model.ngeom,
                meshes=model.nmesh,
                materials=model.nmat,
                textures=model.ntex,
                physics_steps=0,
                visible_geom_groups=[1],
            ),
            source=dict(
                source_xml_sha256=prepared["source_xml_sha256"],
                derived_xml_sha256=prepared["derived_xml_sha256"],
                non_texture_semantics_sha256=prepared["non_texture_semantics_sha256"],
                derivation_edits=prepared["edits"],
                original_bundle_unchanged=True,
            ),
            evidence=dict(
                elapsed_seconds=time.monotonic() - started,
                peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                * (1 if sys.platform == "darwin" else 1024),
            ),
        )
        (stage / "source-preview-derived.xml").write_bytes(prepared["xml"])
        (stage / "source-preview.json").write_text(
            json.dumps(payload, indent=2, allow_nan=False) + "\n"
        )
        # Recheck pinned bytes after compilation/copying; never alter the bundle.
        verify_source_scene_bundle(bundle.directory)
        stage.rename(destination)
    return destination / "source-preview.json"
