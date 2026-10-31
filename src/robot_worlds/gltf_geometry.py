"""Read decoded GLB geometry without dropping material or physical metadata.

This is a conversion input, not a physics importer. Mesh vertices are in metres,
Z-up; source node indices/materials remain available for articulation conversion.
"""
from __future__ import annotations

import copy
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_TYPES = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_Z_UP = np.array([[1., 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
_MAX_BYTES = 128 * 1024 * 1024


def _integer(value, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("Invalid glTF integer")
    return value


def _item(values, index):
    index = _integer(index)
    if not isinstance(values, list) or index >= len(values):
        raise ValueError("Missing glTF reference")
    return values[index]


def _vector(value, count):
    array = np.asarray(value, dtype=float)
    if array.shape != (count,) or not np.isfinite(array).all():
        raise ValueError("Invalid finite glTF vector")
    return array


def _transform(node):
    if "matrix" in node:
        if any(key in node for key in ("translation", "rotation", "scale")):
            raise ValueError("Node cannot have both matrix and TRS")
        matrix = _vector(node["matrix"], 16).reshape((4, 4), order="F")
    else:
        translation = _vector(node.get("translation", [0, 0, 0]), 3)
        scale = _vector(node.get("scale", [1, 1, 1]), 3)
        q = _vector(node.get("rotation", [0, 0, 0, 1]), 4)
        norm = np.linalg.norm(q)
        if abs(norm - 1) > 1e-4:
            raise ValueError("Node quaternion must be normalized")
        x, y, z, w = q / norm
        rotation = np.array([
            [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
        ])
        matrix = np.eye(4)
        matrix[:3, :3] = rotation @ np.diag(scale)
        matrix[:3, 3] = translation
    if (not np.allclose(matrix[3], [0, 0, 0, 1], rtol=0, atol=1e-10)
            or abs(np.linalg.det(matrix[:3, :3])) < 1e-12):
        raise ValueError("Node transform is singular or non-affine")
    return matrix


@dataclass
class GLBPrimitive:
    node_index: int
    mesh_index: int
    primitive_index: int
    material_index: int | None
    positions: np.ndarray
    triangles: np.ndarray
    normals: np.ndarray | None
    attributes: dict[str, np.ndarray]
    transform: np.ndarray


class GLBGeometry:
    """Bounded embedded buffers and source-preserving primitive extraction."""

    def __init__(self, path: str | Path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError("Decoded GLB must be at most 128 MiB")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if (not 28 <= len(raw) <= _MAX_BYTES or raw[:4] != b"glTF"
                or struct.unpack_from("<II", raw, 4) != (2, len(raw))):
            raise ValueError("Invalid decoded GLB header")
        size, kind = struct.unpack_from("<II", raw, 12)
        if kind != 0x4e4f534a or size % 4 or size > 8*1024*1024 or 28+size > len(raw):
            raise ValueError("Invalid decoded GLB JSON")
        self.document = json.loads(raw[20:20+size])
        length, kind = struct.unpack_from("<II", raw, 20+size)
        if kind != 0x004e4942 or length % 4 or 28+size+length != len(raw):
            raise ValueError("Invalid decoded GLB binary")
        self.binary = raw[28+size:]
        doc = self.document
        if doc.get("asset", {}).get("version") != "2.0" or len(doc.get("buffers", [])) != 1:
            raise ValueError("Expected a decoded self-contained glTF 2.0 buffer")
        buffer = doc["buffers"][0]
        count = _integer(buffer["byteLength"])
        if "uri" in buffer or count > length or length-count > 3:
            raise ValueError("Invalid or external binary buffer")
        self.binary = self.binary[:count]
        if doc.get("animations") or doc.get("skins"):
            raise ValueError("Skinning and animation require a separate importer")
        if set(doc.get("extensionsRequired", [])) - {
                "KHR_mesh_quantization", "KHR_texture_transform", "EXT_texture_webp",
                "KHR_physics_rigid_bodies"}:
            raise ValueError("Required GLB extension is unsupported by the geometry reader")
        if any("uri" in image for image in doc.get("images", [])):
            raise ValueError("External image URIs are unsupported")
        if any(len(doc.get(key, [])) > limit for key, limit in (
                ("nodes", 4096), ("meshes", 1000), ("bufferViews", 8192),
                ("accessors", 8192), ("images", 256), ("materials", 1000))):
            raise ValueError("GLB resource count exceeds import budget")

    def view(self, index):
        view = _item(self.document.get("bufferViews", []), index)
        if view.get("buffer") != 0 or view.get("extensions", {}).get("EXT_meshopt_compression"):
            raise ValueError("Run decode_glb before reading compressed geometry")
        start, count = _integer(view.get("byteOffset", 0)), _integer(view["byteLength"])
        if start > len(self.binary) or count > len(self.binary)-start:
            raise ValueError("GLB buffer view exceeds embedded bytes")
        return view, memoryview(self.binary)[start:start+count]

    def accessor(self, index):
        accessor = _item(self.document.get("accessors", []), index)
        if "sparse" in accessor or "bufferView" not in accessor:
            raise ValueError("Sparse or implicit accessors require a separate importer")
        kind = accessor.get("componentType")
        if kind not in _TYPES or accessor.get("type") not in _COMPONENTS:
            raise ValueError("Unsupported glTF accessor type")
        count = _integer(accessor["count"], minimum=1)
        if count > 3_000_000:
            raise ValueError("Accessor exceeds element budget")
        components = _COMPONENTS[accessor["type"]]
        dtype = np.dtype(_TYPES[kind])
        view, data = self.view(accessor["bufferView"])
        start = _integer(accessor.get("byteOffset", 0))
        stride = _integer(view.get("byteStride", components*dtype.itemsize), minimum=1)
        if (stride < components*dtype.itemsize or stride > 252 or stride % dtype.itemsize
                or start % dtype.itemsize or start + (count-1)*stride + components*dtype.itemsize > len(data)):
            raise ValueError("Invalid accessor stride, alignment or range")
        values = np.ndarray((count, components), dtype=dtype, buffer=data,
                            offset=start, strides=(stride, dtype.itemsize)).copy()
        if accessor.get("normalized", False):
            if kind not in {5120, 5121, 5122, 5123}:
                raise ValueError("Unsupported normalized component type")
            info = np.iinfo(dtype)
            values = np.maximum(values.astype(float)/info.max, -1)
        if not np.isfinite(values).all():
            raise ValueError("Accessor contains nonfinite data")
        return values

    def image(self, index):
        image = _item(self.document.get("images", []), index)
        if image.get("mimeType") not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Unsupported embedded image type")
        _, data = self.view(image["bufferView"])
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("Embedded image exceeds 16 MiB")
        return image["mimeType"], bytes(data)

    def primitives(self):
        doc, seen, result = self.document, set(), []
        scenes = doc.get("scenes", [])
        if not scenes or ("scene" not in doc and len(scenes) != 1):
            raise ValueError("GLB must identify one selected scene")
        roots = _item(scenes, doc.get("scene", 0)).get("nodes", [])
        stack = [(index, _Z_UP, 0) for index in reversed(roots)]
        total_vertices = total_triangles = total_attribute_bytes = 0
        while stack:
            index, parent, depth = stack.pop()
            node = _item(doc.get("nodes", []), index)
            if index in seen or depth > 128:
                raise ValueError("GLB nodes contain a cycle, duplicate parent or excessive depth")
            seen.add(index)
            if "skin" in node or "weights" in node:
                raise ValueError("Deformed nodes require a separate importer")
            if set(node.get("extensions", {})) - {"KHR_physics_rigid_bodies"}:
                raise ValueError("Node extensions require a separate importer")
            transform = parent @ _transform(node)
            if not np.isfinite(transform).all():
                raise ValueError("Node hierarchy overflows its transform")
            stack.extend((child, transform, depth+1) for child in reversed(node.get("children", [])))
            if "mesh" not in node:
                continue
            mesh = _item(doc.get("meshes", []), node["mesh"])
            for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
                if len(result) >= 1000 or primitive.get("mode", 4) != 4 or primitive.get("targets"):
                    raise ValueError("Only bounded, undeformed triangle primitives are supported")
                if primitive.get("extensions"):
                    raise ValueError("Primitive extensions require a separate importer")
                references = primitive.get("attributes", {})
                if not isinstance(references, dict) or len(references) > 16:
                    raise ValueError("Primitive exceeds attribute budget")
                for accessor_index in references.values():
                    accessor = _item(doc.get("accessors", []), accessor_index)
                    count = _integer(accessor.get("count"), minimum=1)
                    if count + total_vertices > 1_000_000:
                        raise ValueError("Scene geometry exceeds import budget")
                    # Budget for normalized float64 data before allocating it.
                    total_attribute_bytes += count * _COMPONENTS.get(accessor.get("type"), 4) * 8
                    if total_attribute_bytes > 64 * 1024 * 1024:
                        raise ValueError("Scene exceeds decoded attribute budget")
                attrs = {name: self.accessor(i) for name, i in references.items()}
                positions = attrs.get("POSITION")
                if positions is None or positions.shape[1] != 3 or len(positions) < 3:
                    raise ValueError("Primitive needs 3D vertex positions")
                if any(len(value) != len(positions) for value in attrs.values()):
                    raise ValueError("Primitive attribute counts differ")
                indices = self.accessor(primitive["indices"]) if "indices" in primitive else np.arange(len(positions)).reshape(-1, 1)
                if (indices.shape[1] != 1 or (indices.dtype.kind != "u" and "indices" in primitive)
                        or len(indices) % 3 or indices.min() < 0 or indices.max() >= len(positions)):
                    raise ValueError("Invalid triangle indices")
                triangles = indices.reshape(-1, 3).astype(np.int32)
                linear = transform[:3, :3]
                determinant = np.linalg.det(linear)
                if abs(determinant) < 1e-12:
                    raise ValueError("Hierarchy has singular geometry transform")
                if determinant < 0:
                    triangles = triangles[:, [0, 2, 1]]
                transformed = positions @ linear.T + transform[:3, 3]
                if not np.isfinite(transformed).all():
                    raise ValueError("Transformed positions are nonfinite")
                normals = attrs.get("NORMAL")
                if normals is not None:
                    if normals.shape[1] != 3:
                        raise ValueError("Normals must have three components")
                    normals = normals @ np.linalg.inv(linear)
                    lengths = np.linalg.norm(normals, axis=1)
                    if np.any(lengths < 1e-10):
                        raise ValueError("Normals must have a usable direction")
                    normals /= lengths[:, None]
                material = primitive.get("material")
                if material is not None:
                    _item(doc.get("materials", []), material)
                total_vertices += len(positions)
                total_triangles += len(triangles)
                if total_vertices > 1_000_000 or total_triangles > 1_000_000:
                    raise ValueError("Scene geometry exceeds import budget")
                result.append(GLBPrimitive(index, node["mesh"], primitive_index, material,
                    transformed, triangles, normals, attrs, transform.copy()))
        if not result:
            raise ValueError("Selected scene contains no triangle geometry")
        return result

    def texture_coordinates(self, primitive: GLBPrimitive, texture_info: dict, *, obj=False):
        """Apply glTF texture-info transforms before optional OBJ V inversion."""
        transform = texture_info.get("extensions", {}).get("KHR_texture_transform", {})
        coordinate = _integer(transform.get("texCoord", texture_info.get("texCoord", 0)))
        values = primitive.attributes.get(f"TEXCOORD_{coordinate}")
        if values is None or values.shape[1] != 2:
            raise ValueError("Material texture references missing UV coordinates")
        scale = _vector(transform.get("scale", [1, 1]), 2)
        offset = _vector(transform.get("offset", [0, 0]), 2)
        angle = transform.get("rotation", 0)
        if not isinstance(angle, (int, float)) or not math.isfinite(angle):
            raise ValueError("Invalid texture rotation")
        c, s = math.cos(angle), math.sin(angle)
        uv = (values * scale) @ np.array([[c, s], [-s, c]]) + offset
        if obj:
            uv[:, 1] = 1-uv[:, 1]
        return uv

    def material(self, index):
        return copy.deepcopy(_item(self.document.get("materials", []), index))

    def texture_image(self, texture_info):
        texture = _item(self.document.get("textures", []), texture_info["index"])
        source = texture.get("extensions", {}).get("EXT_texture_webp", {}).get("source", texture.get("source"))
        mime, data = self.image(source)
        sampler = (_item(self.document.get("samplers", []), texture["sampler"])
                   if "sampler" in texture else {})
        return mime, data, copy.deepcopy(sampler)
