"""Portable OBJ/PBR visuals from decoded GLB, without inventing collision bodies."""
from __future__ import annotations

import hashlib
import io
import json
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .gltf_geometry import GLBGeometry
from .models import ASSET_FILE_FIELDS, SceneObject, SurfaceMaterial

_MAX_TOTAL = 128 * 1024 * 1024
_MAX_METADATA = 2 * 1024 * 1024
_WRAPS = {10497: "repeat", 33071: "clamp", 33648: "mirror"}


def _record_digest(record):
    return hashlib.sha256(json.dumps({k: v for k, v in record.items() if k != "payload_sha256"},
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()


def _read_manifest(directory):
    path = directory / "manifest.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_METADATA:
        raise ValueError("Visual cache manifest missing or unsafe")
    record = json.loads(path.read_text())
    if not isinstance(record, dict) or record.get("payload_sha256") != _record_digest(record):
        raise ValueError("Visual cache manifest failed integrity verification")
    return record


@dataclass(frozen=True)
class GLBVisuals:
    path: Path
    cache_hit: bool

    def objects(self, *, prefix: str, parents: dict[int, str]) -> list[SceneObject]:
        """Attach visuals only after a physics importer has mapped source nodes."""
        manifest = _read_manifest(self.path)
        objects = []
        for index, part in enumerate(manifest["parts"]):
            node = part["node_index"]
            if node not in parents:
                raise ValueError("Every visual source node needs an explicit physical parent")
            payload = dict(part["object"], id=f"{prefix}_visual{index}", parent_id=parents[node])
            for field in ASSET_FILE_FIELDS:
                if payload.get(field):
                    payload[field] = str(self.path / payload[field])
            objects.append(SceneObject.model_validate(payload))
        return objects

    @property
    def source_files(self):
        manifest = _read_manifest(self.path)
        return {"decoded-source.glb": manifest["identity"]["source_sha256"],
                **{name: value["sha256"] for name, value in manifest["files"].items()}}


def _color(value, count):
    result = np.asarray(value, dtype=float)
    if result.shape != (count,) or not np.isfinite(result).all() or np.any(result < 0) or np.any(result > 1):
        raise ValueError("Material color must contain normalized finite components")
    return result


def _material(geometry, primitive, check):
    material = geometry.material(primitive.material_index) if primitive.material_index is not None else {}
    if material.get("extensions"):
        raise ValueError("Material extensions require a separate conversion")
    pbr = material.get("pbrMetallicRoughness", {})
    factor = _color(pbr.get("baseColorFactor", [1, 1, 1, 1]), 4)
    colors = primitive.attributes.get("COLOR_0")
    if colors is not None:
        if colors.shape[1] not in (3, 4) or not np.allclose(colors, colors[0], rtol=0, atol=1e-8):
            raise ValueError("Nonuniform vertex colors require baking; they cannot be discarded")
        color = _color(colors[0], colors.shape[1])
        factor[:len(color)] *= color
    slots = {"color": pbr.get("baseColorTexture"), "mr": pbr.get("metallicRoughnessTexture"),
             "normal": material.get("normalTexture"), "ao": material.get("occlusionTexture"),
             "emissive": material.get("emissiveTexture")}
    # A quarter-turn repeated texture can be baked losslessly into its pixels.
    # Keep the original UV basis, including the normal-map tangent directions,
    # instead of rotating UVs and silently changing the inferred tangent frame.
    normal_transform = (slots["normal"] or {}).get("extensions", {}).get("KHR_texture_transform", {})
    image_turn = None
    if normal_transform.get("rotation", 0) != 0:
        angle = normal_transform["rotation"]
        if not isinstance(angle, (float, int)) or not math.isfinite(angle):
            raise ValueError("Invalid normal-map rotation")
        turns = round(angle / (math.pi / 2))
        if abs(angle-turns*math.pi/2) > 1e-7:
            raise ValueError("Rotated/reflected normal-map UVs require tangent-frame baking")
        for info in slots.values():
            if info is None:
                continue
            transform = info.get("extensions", {}).get("KHR_texture_transform", {})
            scale = np.asarray(transform.get("scale", [1, 1]), dtype=float)
            offset = np.asarray(transform.get("offset", [0, 0]), dtype=float)
            if (transform.get("rotation", 0) != angle or scale.shape != (2,) or offset.shape != (2,)
                    or not np.allclose(scale, [1, 1], atol=1e-7, rtol=0)
                    or not np.isfinite(offset).all() or not np.allclose(offset, np.round(offset), atol=1e-7, rtol=0)):
                raise ValueError("Quarter-turn normal-map baking requires matching transforms, unit scale and integer offsets")
        image_turn = turns % 4
    images, uv, wrapping = {}, None, None
    decoded_bytes = 0
    for role, info in slots.items():
        if info is None:
            continue
        check()
        if set(info.get("extensions", {})) - {"KHR_texture_transform"}:
            raise ValueError("Unsupported texture-info extension")
        if role == "normal":
            transform = info.get("extensions", {}).get("KHR_texture_transform", {})
            if image_turn is None and (transform.get("rotation", 0) != 0 or any(x <= 0 for x in transform.get("scale", [1, 1]))):
                raise ValueError("Rotated/reflected normal-map UVs require tangent-frame baking")
        coordinate_info = info
        if image_turn is not None:
            transform = info.get("extensions", {}).get("KHR_texture_transform", {})
            coordinate_info = {"texCoord": transform.get("texCoord", info.get("texCoord", 0))}
        coordinates = geometry.texture_coordinates(primitive, coordinate_info, obj=True)
        if uv is None:
            uv = coordinates
        elif not np.allclose(uv, coordinates, atol=1e-9, rtol=0):
            raise ValueError("Material maps use different UV sets/transforms; baking is required")
        mime, encoded, sampler = geometry.texture_image(info)
        if sampler.get("magFilter", 9729) != 9729 or sampler.get("minFilter", 9987) != 9987:
            raise ValueError("Source texture filtering is not supported by the prepared material contract")
        try:
            wraps = (_WRAPS[sampler.get("wrapS", 10497)], _WRAPS[sampler.get("wrapT", 10497)])
        except KeyError as error:
            raise ValueError("Unsupported source sampler wrap mode") from error
        if image_turn is not None and wraps != ("repeat", "repeat"):
            raise ValueError("Quarter-turn normal-map baking requires repeat wrapping")
        if wrapping is not None and wrapping != wraps:
            raise ValueError("Material maps need matching sampler wraps before conversion")
        wrapping = wraps
        with Image.open(io.BytesIO(encoded)) as image:
            expected_format = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[mime]
            if image.format != expected_format or image.width*image.height > 4096*4096:
                raise ValueError("Texture format or decoded image budget mismatch")
            decoded_bytes += image.width*image.height*4
            if decoded_bytes > 64*1024*1024 or image.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
                raise ValueError("Material exceeds decoded texture budget or supported bit depth")
            images[role] = np.array(image.convert("RGBA"))
            if image_turn is not None:
                images[role] = np.rot90(images[role], image_turn).copy()
    normal_strength = (slots["normal"] or {}).get("scale", 1)
    if not isinstance(normal_strength, (int, float)) or not math.isfinite(normal_strength) or normal_strength < 0:
        raise ValueError("Invalid source normal strength")
    surface = SurfaceMaterial(metallic=pbr.get("metallicFactor", 1), roughness=pbr.get("roughnessFactor", 1),
        emissive=material.get("emissiveFactor", [0, 0, 0]), normal_scale=(normal_strength, normal_strength),
        double_sided=material.get("doubleSided", False), alpha_mode=material.get("alphaMode", "OPAQUE"),
        alpha_cutoff=material.get("alphaCutoff", .5),
        wrap_s=(wrapping or ("repeat", "repeat"))[0], wrap_t=(wrapping or ("repeat", "repeat"))[1])
    maps = {}
    if "color" in images:
        maps["texture_path"] = images["color"]
    if "normal" in images:
        normals = images["normal"][:, :, :3].copy()
        # OBJ uses V-up. Flipping the normal map's tangent Y preserves the
        # source surface direction when UV V is inverted for OBJ.
        normals[:, :, 1] = 255 - normals[:, :, 1]
        maps["normal_texture_path"] = normals
    if "emissive" in images:
        maps["emissive_texture_path"] = images["emissive"]
    if "mr" in images or "ao" in images:
        base = images.get("mr", images.get("ao"))
        orm = np.full((*base.shape[:2], 3), 255, dtype=np.uint8)
        if "mr" in images:
            orm[:, :, 1:] = images["mr"][:, :, 1:3]
        if "ao" in images:
            if images["ao"].shape[:2] != base.shape[:2]:
                raise ValueError("Occlusion and roughness/metallic textures need matching resolution")
            strength = (slots["ao"] or {}).get("strength", 1)
            if not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
                raise ValueError("Invalid occlusion strength")
            orm[:, :, 0] = np.rint(255*(1-strength) + strength*images["ao"][:, :, 0]).astype(np.uint8)
        maps["orm_texture_path"] = orm
    return surface, factor[:3], 1. if surface.alpha_mode == "OPAQUE" else float(factor[3]), uv, maps


def prepare_glb_visuals(source: str | Path, cache_dir: str | Path, *, source_notice: str | Path,
                        source_metadata: dict, timeout=60, cancelled=None) -> GLBVisuals:
    """Prepare visuals with source evidence. No collision/joint bodies are created.

Use inside a source_asset importer after acquiring/decoding a pinned source. A
physical adapter must explicitly attach each source node before registration.
Unsupported appearance requirements fail instead of degrading silently.
"""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("Visual preparation timeout must be finite and in (0, 300]")
    deadline = time.monotonic() + timeout

    def check():
        if cancelled and cancelled():
            raise ValueError("GLB visual preparation cancelled")
        if time.monotonic() >= deadline:
            raise ValueError("GLB visual preparation exceeded its time budget")

    check()
    source, notice_path = Path(source), Path(source_notice)
    if not source.is_file() or source.stat().st_size > _MAX_TOTAL:
        raise ValueError("Decoded GLB exceeds source budget")
    if not notice_path.is_file() or notice_path.stat().st_size > 1024*1024 or notice_path.suffix.lower() not in {".md", ".txt"}:
        raise ValueError("Visual conversion needs a bounded source attribution notice")
    notice = notice_path.read_bytes()
    if not notice.strip() or not isinstance(source_metadata, dict) or not source_metadata:
        raise ValueError("Visual conversion needs source attribution and metadata")
    def hash_source():
        digest, count = hashlib.sha256(), 0
        with source.open("rb") as stream:
            while chunk := stream.read(65536):
                check()
                count += len(chunk)
                if count > _MAX_TOTAL:
                    raise ValueError("Decoded GLB exceeds source budget")
                digest.update(chunk)
        return digest.hexdigest()

    source_hash = hash_source()
    metadata_bytes = json.dumps(source_metadata, sort_keys=True, allow_nan=False).encode()
    if len(metadata_bytes) > _MAX_METADATA:
        raise ValueError("Source metadata exceeds budget")
    identity = {"profile": 1, "source_sha256": source_hash,
        "notice_sha256": hashlib.sha256(notice).hexdigest(), "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "implementation_sha256": hashlib.sha256(b"".join(Path(__file__).with_name(name).read_bytes()
            for name in ("gltf_visuals.py", "gltf_geometry.py", "models.py"))).hexdigest()}
    root = Path(cache_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def verify(directory, cache_hit):
        check()
        manifest = directory / "manifest.json"
        if directory.is_symlink() or manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > _MAX_METADATA:
            raise ValueError("Visual cache is missing or unsafe")
        record = _read_manifest(directory)
        if record.get("identity") != identity or not isinstance(record.get("files"), dict):
            raise ValueError("Visual cache provenance mismatch")
        files = record["files"]
        if not 2 <= len(files) <= 4096 or {p.name for p in directory.iterdir()} != {*files, "manifest.json"}:
            raise ValueError("Visual cache file set mismatch")
        total = 0
        for name, expected in files.items():
            if not name or Path(name).name != name or "\\" in name or name in {".", ".."}:
                raise ValueError("Unsafe visual cache member")
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["bytes"] or path.stat().st_size > 24*1024*1024:
                raise ValueError("Visual cache member was changed")
            total += path.stat().st_size
            if total > _MAX_TOTAL:
                raise ValueError("Visual cache exceeds size budget")
            check()
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
                raise ValueError("Visual cache member failed integrity verification")
        if not isinstance(record.get("parts"), list) or not 1 <= len(record["parts"]) <= 1000:
            raise ValueError("Visual cache has invalid primitive records")
        for part in record["parts"]:
            obj = SceneObject.model_validate(dict(part["object"], id="check", parent_id="physical"))
            if obj.collision or obj.movable or obj.joint or any(getattr(obj, field) not in files for field in ASSET_FILE_FIELDS if getattr(obj, field)):
                raise ValueError("Invalid visual cache object references")
        return GLBVisuals(directory, cache_hit)

    if target.exists() or target.is_symlink():
        return verify(target, True)
    geometry = GLBGeometry(source)
    primitives = geometry.primitives()
    check()
    # Recheck after reading; never bind changed source bytes to an earlier hash.
    if hash_source() != source_hash:
        raise ValueError("GLB source changed during preparation")
    source_record = {"source": source_metadata, "decoded_source_sha256": source_hash,
        "gltf_document": geometry.document,
        "conversion": {"coordinates": "metres, Z-up, composed hierarchy transforms", "uv": "OBJ V-up",
            "normal_map": "tangent Y channel inverted for OBJ V inversion; matching quarter-turn/unit-scale/integer-offset REPEAT transforms are baked into all material images, preserving the original UV tangent basis", "textures": "decoded to PNG without resizing",
            "orm": "RGB occlusion/roughness/metallic; absent occlusion is white", "physics": "not imported or validated"}}
    source_bytes = (json.dumps(source_record, indent=2, allow_nan=False) + "\n").encode()
    if len(source_bytes) > _MAX_METADATA:
        raise ValueError("Retained source metadata exceeds budget")
    with tempfile.TemporaryDirectory(prefix=".visual-", dir=root) as scratch:
        stage = Path(scratch) / "output"
        stage.mkdir()
        files, parts, total = {}, [], 0

        def publish_file(name, data, limit=16*1024*1024):
            nonlocal total
            check()
            if len(data) > limit or total + len(data) > _MAX_TOTAL:
                raise ValueError("Converted visual files exceed budget")
            (stage / name).write_bytes(data)
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            total += len(data)

        publish_file("NOTICE.md", notice + b"\n\nRobot Worlds adaptation: decoded GLB visuals converted to Z-up OBJ and PNG material maps, without mesh simplification. UV, normal-map and ORM channel changes are recorded in source.json. Collision and articulation are not supplied by this visual conversion.\n")
        publish_file("source.json", source_bytes)
        for index, primitive in enumerate(primitives):
            check()
            surface, color, opacity, uv, maps = _material(geometry, primitive, check)
            mesh_name = f"part-{index}.obj"
            mesh_hash, mesh_bytes = hashlib.sha256(), 0
            with (stage / mesh_name).open("wb") as mesh:
                def line(text):
                    nonlocal mesh_bytes, total
                    data = text.encode()
                    mesh_bytes += len(data)
                    total += len(data)
                    if mesh_bytes > 24*1024*1024 or total > _MAX_TOTAL:
                        raise ValueError("Converted visual files exceed budget")
                    mesh_hash.update(data)
                    mesh.write(data)

                line("# Derived from retained GLB source; see source.json and NOTICE.md.\n")
                for tag, values in (("v", primitive.positions), ("vn", primitive.normals), ("vt", uv)):
                    if values is not None:
                        for row_index, row in enumerate(values):
                            if row_index % 4096 == 0:
                                check()
                            line(tag + " " + " ".join(format(float(x), ".10g") for x in row) + "\n")
                for row_index, triangle in enumerate(primitive.triangles):
                    if row_index % 4096 == 0:
                        check()
                    refs = []
                    for vertex in triangle:
                        v = str(int(vertex)+1)
                        if primitive.normals is not None:
                            refs.append(f"{v}/{v if uv is not None else ''}/{v}")
                        else:
                            refs.append(f"{v}/{v}" if uv is not None else v)
                    line("f " + " ".join(refs) + "\n")
            files[mesh_name] = {"sha256": mesh_hash.hexdigest(), "bytes": mesh_bytes}
            paths = {}
            for field, pixels in maps.items():
                stream = io.BytesIO()
                Image.fromarray(pixels).save(stream, format="PNG")
                data = stream.getvalue()
                name = hashlib.sha256(data).hexdigest() + ".png"
                if name not in files:
                    publish_file(name, data)
                paths[field] = name
            low, high = primitive.positions.min(0), primitive.positions.max(0)
            obj = dict(label=f"Source mesh node {primitive.node_index}, material {primitive.material_index}", kind="mesh",
                       position=((low+high)/2).tolist(), size=np.maximum(high-low, 1e-6).tolist(),
                       mesh_path=mesh_name, color=color.tolist(), opacity=opacity, collision=False,
                       surface=surface.model_dump(mode="json"), source_notice="NOTICE.md", source_metadata="source.json", **paths)
            parts.append({"node_index": primitive.node_index, "mesh_index": primitive.mesh_index,
                          "primitive_index": primitive.primitive_index, "object": obj})
        record = {"identity": identity, "files": files, "parts": parts, "status": "visuals_only_requires_physical_parent_mapping"}
        record["payload_sha256"] = _record_digest(record)
        encoded = (json.dumps(record, indent=2) + "\n").encode()
        if len(encoded) > _MAX_METADATA:
            raise ValueError("Visual manifest exceeds budget")
        (stage / "manifest.json").write_bytes(encoded)
        verify(stage, False)
        check()
        try:
            stage.rename(target)
        except OSError:
            if target.exists() or target.is_symlink():
                return verify(target, True)
            raise
        return verify(target, False)
