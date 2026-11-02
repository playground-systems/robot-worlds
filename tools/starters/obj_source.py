"""Bounded, data-only Wavefront source import for the offline scene adapter.

Material libraries and diffuse images must resolve inside the supplied source
root. Polygons are accepted only when planar and simple; ear clipping retains
original winding and independent position/texture/normal corner references.
No upstream code, remote resources, geometry repair or physical inference runs.
"""
from __future__ import annotations

import math
import shlex
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit

import numpy as np

MAX_OBJ_BYTES = 24 * 1024 * 1024
MAX_MTL_BYTES = 2 * 1024 * 1024
MAX_VALUES = 250_000
MAX_TRIANGLES = 500_000
MAX_POLYGON_CORNERS = 256
MAX_MATERIAL_LIBRARIES = 32


@dataclass
class SourcePrimitive:
    vertex: np.ndarray
    vertex_index: np.ndarray
    normal: np.ndarray | None
    normal_index: np.ndarray | None
    texcoordset: tuple
    texcoord_indexset: tuple
    source_color: tuple[float, float, float, float]
    source_texture_path: Path | None
    source_material_files: tuple[Path, ...]
    material_name: str | None
    material: None = None

    def __len__(self):
        return len(self.vertex_index)


def _resource(value: str, base: Path, root: Path) -> Path:
    value = unquote(value)
    if (not value or len(value) > 4096 or "\\" in value or "\x00" in value
            or Path(value).is_absolute() or PureWindowsPath(value).drive or urlsplit(value).scheme):
        raise ValueError(f"Unsafe OBJ/MTL source resource: {value!r}")
    candidate = (base / value).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError(f"Unresolved or escaping OBJ/MTL source resource: {value!r}")
    return candidate


def _text(path: Path, limit: int) -> str:
    if path.stat().st_size > limit:
        raise ValueError(f"Source file exceeds {limit} byte budget: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Source file exceeds {limit} byte budget: {path.name}")
    text = data.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("NUL is not valid OBJ/MTL text")
    return text


def _lines(text: str):
    pending = ""
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        pending += line[:-1] + " " if line.endswith("\\") else line
        if len(pending) > 1024 * 1024:
            raise ValueError("OBJ/MTL source line exceeds its budget")
        if line.endswith("\\"):
            continue
        if pending:
            words = pending.split(None, 1)
            yield words[0], words[1].strip() if len(words) > 1 else ""
        pending = ""
    if pending:
        raise ValueError("Incomplete OBJ/MTL line continuation")


def _numbers(value: str, counts: set[int]) -> list[float]:
    result = [float(item) for item in value.split()]
    if len(result) not in counts or not all(math.isfinite(item) for item in result):
        raise ValueError("Invalid finite OBJ/MTL numeric fields")
    return result


def _materials(path: Path) -> dict:
    materials, current = {}, None
    for keyword, value in _lines(_text(path, MAX_MTL_BYTES)):
        if keyword == "newmtl":
            if not value or value in materials:
                raise ValueError("Missing or duplicate MTL material name")
            current = {"color": [1., 1., 1., 1.], "texture": None, "library": path}
            materials[value] = current
        elif keyword in {"Kd", "d", "Tr", "map_Kd"}:
            if current is None:
                raise ValueError("MTL property precedes its material")
            if keyword == "map_Kd":
                tokens = shlex.split(value)
                if len(tokens) != 1 or tokens[0].startswith("-"):
                    raise ValueError("Diffuse texture options require an explicit reviewed UV adapter")
                current["texture"] = tokens[0]
            else:
                values = _numbers(value, {3} if keyword == "Kd" else {1})
                if any(not 0 <= item <= 1 for item in values):
                    raise ValueError("MTL diffuse color/opacity must be between zero and one")
                if keyword == "Kd":
                    current["color"][:3] = values
                else:
                    current["color"][3] = 1 - values[0] if keyword == "Tr" else values[0]
        # Non-diffuse MTL shading parameters are retained in the source library;
        # the caller discloses its material approximation. They are not code.
    return materials


def _index(value: str, count: int) -> int:
    index = int(value)
    if index == 0:
        raise ValueError("OBJ indices are one-based, not zero")
    if index < 0:
        if index < -count:
            raise ValueError("OBJ relative index is outside the declared data")
        return count + index
    # Positive references may address data declared later in the file. Validate
    # their final range before publishing any primitive.
    return index - 1


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _triangulate(points: np.ndarray) -> list[tuple[int, int, int]]:
    count = len(points)
    if count == 3:
        return [(0, 1, 2)]
    if count < 3 or count > MAX_POLYGON_CORNERS:
        raise ValueError("OBJ polygon exceeds the supported corner budget")
    relative = points - points[0]
    extent = float(np.max(np.ptp(relative, axis=0)))
    normal = np.cross(relative, np.roll(relative, -1, axis=0)).sum(axis=0)
    length = float(np.linalg.norm(normal))
    epsilon = max(1e-20, extent * extent * 1e-10)
    if length <= epsilon:
        raise ValueError("OBJ polygon has no stable planar area")
    normal /= length
    if np.max(np.abs(relative @ normal)) > max(1e-9, extent * 1e-6):
        raise ValueError("Nonplanar OBJ polygon needs reviewed source triangulation")
    projected = np.delete(relative, int(np.argmax(np.abs(normal))), axis=1)
    signed_area = sum(_cross([0, 0], a, b) for a, b in zip(projected, np.roll(projected, -1, axis=0))) / 2
    winding = 1 if signed_area > 0 else -1
    linear_epsilon = max(1e-12, extent * 1e-10)

    def on_segment(a, b, p):
        return (abs(_cross(a, b, p)) <= epsilon
                and np.all(p >= np.minimum(a, b)-linear_epsilon)
                and np.all(p <= np.maximum(a, b)+linear_epsilon))

    for i in range(count):
        a, b = projected[i], projected[(i+1) % count]
        if np.linalg.norm(a-b) <= linear_epsilon:
            raise ValueError("OBJ polygon has repeated adjacent corners")
        for j in range(i+1, count):
            if j == i+1 or (i == 0 and j == count-1):
                continue
            c, d = projected[j], projected[(j+1) % count]
            ab_c, ab_d, cd_a, cd_b = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
            if ((ab_c > epsilon and ab_d < -epsilon or ab_c < -epsilon and ab_d > epsilon)
                    and (cd_a > epsilon and cd_b < -epsilon or cd_a < -epsilon and cd_b > epsilon)
                    or any((on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b)))):
                raise ValueError("Self-intersecting OBJ polygon is unsupported")
    remaining, triangles = list(range(count)), []
    while len(remaining) > 3:
        for offset, index in enumerate(remaining):
            previous, following = remaining[offset-1], remaining[(offset+1) % len(remaining)]
            a, b, c = projected[[previous, index, following]]
            if winding * _cross(a, b, c) <= epsilon:
                continue
            others = (point for point in remaining if point not in {previous, index, following})
            if any(all(winding * _cross(x, y, projected[point]) >= -epsilon
                       for x, y in ((a, b), (b, c), (c, a))) for point in others):
                continue
            triangles.append((previous, index, following))
            remaining.pop(offset)
            break
        else:
            raise ValueError("OBJ polygon cannot be triangulated without changing its boundary")
    triangles.append(tuple(remaining))
    area = sum(_cross(*projected[list(triangle)]) / 2 for triangle in triangles)
    if any(winding * _cross(*projected[list(triangle)]) <= epsilon for triangle in triangles) or not math.isclose(area, signed_area, rel_tol=1e-8, abs_tol=epsilon):
        raise ValueError("OBJ polygon triangulation did not preserve source area")
    return triangles


def _compact(values, indices, width):
    indices = np.asarray(indices, dtype=np.int64)
    values = np.asarray(values, dtype=np.float64).reshape(-1, width)
    if indices.min() < 0 or indices.max() >= len(values):
        raise ValueError("OBJ face index is outside the declared data")
    used, inverse = np.unique(indices, return_inverse=True)
    return values[used], inverse.reshape(indices.shape)


def read_obj(path: Path, *, root: Path) -> list[SourcePrimitive]:
    """Read source OBJ/MTL into bounded material primitives; lengths stay unscaled."""
    root, path = Path(root).resolve(), Path(path).resolve()
    if path.suffix.lower() != ".obj" or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Expected a local OBJ inside the source root")
    vertices, normals, texcoords, faces, libraries = [], [], [], [], []
    material_name = None
    for keyword, value in _lines(_text(path, MAX_OBJ_BYTES)):
        if keyword == "v":
            position = _numbers(value, {3, 4})
            if len(position) == 4:
                if position[3] == 0:
                    raise ValueError("OBJ homogeneous position has zero weight")
                position = [item / position[3] for item in position[:3]]
                if not all(math.isfinite(item) for item in position):
                    raise ValueError("OBJ homogeneous position overflow")
            vertices.append(position)
        elif keyword == "vn":
            normal = _numbers(value, {3})
            if not any(normal):
                raise ValueError("OBJ normal must be nonzero")
            normals.append(normal)
        elif keyword == "vt":
            uv = _numbers(value, {1, 2, 3})
            if len(uv) == 3 and uv[2] != 0:
                raise ValueError("Three-dimensional OBJ texture coordinates are unsupported")
            texcoords.append((uv + [0])[:2])
        elif keyword == "mtllib":
            references = shlex.split(value)
            if not references:
                raise ValueError("OBJ material library name is missing")
            libraries.extend(_resource(reference, path.parent, root) for reference in references)
            if len(libraries) > MAX_MATERIAL_LIBRARIES:
                raise ValueError("OBJ exceeds its material-library budget")
        elif keyword == "usemtl":
            if not value:
                raise ValueError("OBJ material name is missing")
            material_name = value
        elif keyword == "f":
            corners = []
            for reference in value.split():
                fields = reference.split("/")
                if len(fields) > 3 or not fields[0]:
                    raise ValueError("Invalid OBJ corner reference")
                corners.append(tuple(_index(field, count) if field else None
                                     for field, count in zip(fields + [""]*(3-len(fields)), (len(vertices), len(texcoords), len(normals)))))
            if len(corners) < 3 or len(corners) > MAX_POLYGON_CORNERS:
                raise ValueError("OBJ polygon exceeds the supported corner budget")
            if any(len({corner[i] is None for corner in corners}) != 1 for i in (1, 2)):
                raise ValueError("Partial texture/normal references within an OBJ face")
            faces.append((material_name, corners))
            if len(faces) > MAX_TRIANGLES:
                raise ValueError("OBJ exceeds its face budget")
        elif keyword not in {"o", "g", "s"}:
            raise ValueError(f"Unsupported OBJ source statement: {keyword}")
        if max(len(vertices), len(normals), len(texcoords)) > MAX_VALUES:
            raise ValueError("OBJ exceeds its vertex/attribute budget")
    if not vertices or not faces:
        raise ValueError("OBJ source needs vertices and faces")
    materials = {}
    for library in dict.fromkeys(libraries):
        parsed = _materials(library)
        if materials.keys() & parsed.keys():
            raise ValueError("Ambiguous material names across OBJ libraries")
        materials.update(parsed)
    grouped, triangle_count = {}, 0
    positions = np.asarray(vertices, dtype=float)
    for name, corners in faces:
        for corner in corners:
            if any(index is not None and not 0 <= index < count
                   for index, count in zip(corner, (len(vertices), len(texcoords), len(normals)))):
                raise ValueError("OBJ face index is outside the declared data")
        indices = _triangulate(positions[[corner[0] for corner in corners]])
        triangle_count += len(indices)
        if triangle_count > MAX_TRIANGLES:
            raise ValueError("OBJ exceeds its triangle budget")
        key = (name, corners[0][1] is not None, corners[0][2] is not None)
        grouped.setdefault(key, []).extend([[corners[i] for i in triangle] for triangle in indices])
    result = []
    for (name, has_uv, has_normal), triangles in grouped.items():
        if name is not None and name not in materials:
            raise ValueError(f"Undefined OBJ material: {name}")
        material = materials.get(name)
        texture = (_resource(material["texture"], material["library"].parent, root)
                   if material and material["texture"] else None)
        if texture and not has_uv:
            raise ValueError("Textured OBJ material has no source UV coordinates")
        indices = np.asarray(triangles, dtype=object)
        points, vertex_indices = _compact(vertices, indices[:, :, 0].astype(int), 3)
        uv, uv_indices = _compact(texcoords, indices[:, :, 1].astype(int), 2) if has_uv else (None, None)
        normal, normal_indices = _compact(normals, indices[:, :, 2].astype(int), 3) if has_normal else (None, None)
        result.append(SourcePrimitive(points, vertex_indices, normal, normal_indices,
                                      (uv,) if has_uv else (), (uv_indices,) if has_uv else (),
                                      tuple(material["color"]) if material else (1., 1., 1., 1.), texture,
                                      (material["library"],) if material else (), name))
    return result
