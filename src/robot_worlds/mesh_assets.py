"""Bounded asset geometry checks and reusable verification results."""
import hashlib
from collections import OrderedDict
from pathlib import Path
from threading import Lock

import numpy as np
from PIL import Image

MAX_TEXTURE_BYTES = 16 * 1024 * 1024
MAX_TEXTURE_PIXELS = 4096 * 4096
MAX_CONVEX_COMPONENTS = 256
_verified_parts = OrderedDict()
_verification_lock = Lock()


def validate_texture(path: str | Path) -> None:
    path = Path(path)
    if path.suffix.lower() != ".png" or not path.is_file():
        raise ValueError("Texture must be an existing PNG file")
    if path.stat().st_size > MAX_TEXTURE_BYTES:
        raise ValueError("Texture exceeds 16 MiB")
    try:
        with Image.open(path) as image:
            if image.format != "PNG" or image.width * image.height > MAX_TEXTURE_PIXELS:
                raise ValueError("Texture must be PNG with at most 16 megapixels")
            image.verify()
    except Image.DecompressionBombError as exc:
        raise ValueError("Texture exceeds the decoded image budget") from exc


def read_triangle_obj(path: str | Path):
    """Read bounded geometry only; callers retain original OBJ/MTL/UV files."""
    return _triangle_obj(_obj_bytes(path))


def _obj_bytes(path):
    path = Path(path)
    if path.suffix.lower() != ".obj" or path.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("Expected an OBJ of at most 24 MiB")
    with path.open("rb") as stream:
        data = stream.read(24 * 1024 * 1024 + 1)
    if len(data) > 24 * 1024 * 1024:
        raise ValueError("Expected an OBJ of at most 24 MiB")
    return data


def obj_bounds(path: str | Path):
    """Source-coordinate bounds without compiling a throwaway native model.

    Faces/UVs stay untouched and are validated by the final MuJoCo compiler.
    Accept polygon faces here, unlike the triangle-only collision verifier.
    """
    vertices = []
    for line in _obj_bytes(path).decode().splitlines():
        fields = line.split()
        if fields and fields[0] == "v":
            if len(fields) != 4:
                raise ValueError("Expected three-coordinate OBJ vertices")
            vertices.append([float(v) for v in fields[1:]])
            if len(vertices) > 100_000:
                raise ValueError("Mesh exceeds the geometry import budget")
    vertices = np.asarray(vertices, dtype=float)
    if vertices.ndim != 2 or len(vertices) < 4 or not np.isfinite(vertices).all():
        raise ValueError("Invalid OBJ vertices")
    return vertices.min(0), vertices.max(0)


def verified_convex_obj(path: str | Path, expected_digest: str):
    """Recheck bytes each time; reuse expensive geometry proofs by SHA-256.

    Cache only immutable bounds/volume for at most 512 parts, never file bytes
    or mutable scene objects. Changed files cannot reuse an earlier verdict.
    """
    data = _obj_bytes(path)
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_digest:
        raise ValueError("Converted collision file was modified")
    with _verification_lock:
        result = _verified_parts.get(digest)
        if result is not None:
            _verified_parts.move_to_end(digest)
    if result is None:
        vertices, faces = _triangle_obj(data)
        volume = convex_volume(vertices, faces)
        result = (tuple(vertices.min(0)), tuple(vertices.max(0)), volume)
        with _verification_lock:
            _verified_parts[digest] = result
            _verified_parts.move_to_end(digest)
            while len(_verified_parts) > 512:
                _verified_parts.popitem(last=False)
    lower, upper, volume = result
    return np.array(lower), np.array(upper), volume


def _triangle_obj(data):
    vertices, faces = [], []
    for line in data.decode().splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "v":
            if len(fields) != 4:
                raise ValueError("Expected three-coordinate OBJ vertices")
            vertices.append([float(v) for v in fields[1:]])
        elif fields[0] == "f":
            if len(fields) != 4:
                raise ValueError("Collision conversion requires a triangulated OBJ")
            face = [int(v.split("/")[0]) for v in fields[1:]]
            if 0 in face:
                raise ValueError("OBJ vertex indices cannot be zero")
            faces.append([v-1 if v > 0 else len(vertices)+v for v in face])
        if len(vertices) > 100_000 or len(faces) > 200_000:
            raise ValueError("Mesh exceeds the geometry import budget")
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int32)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 4
            or faces.ndim != 2 or faces.shape[1] != 3 or not np.isfinite(vertices).all()
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("Invalid triangle mesh")
    return vertices, faces


def convex_volume(vertices, faces) -> float:
    """Verify closed convex hull geometry before trusting a collision part.

    Check the actual faces/vertices, not a filename or a claimed converter tag.
    Bounds keep the plane-side check inexpensive for runtime imports.
    """
    if len(vertices) > 2048 or len(faces) > 4096:
        raise ValueError("Convex part exceeds the verification budget")
    edges = {}
    for face in faces:
        for a, b in zip(face, np.roll(face, -1)):
            key = tuple(sorted((int(a), int(b))))
            count, direction = edges.get(key, (0, 0))
            edges[key] = (count+1, direction + (1 if a < b else -1))
    if any(count != 2 or direction != 0 for count, direction in edges.values()):
        raise ValueError("Convex collision part must be closed with consistent face winding")
    points = vertices[faces]
    volume = float(np.einsum("ij,ij->i", points[:, 0], np.cross(points[:, 1], points[:, 2])).sum()/6)
    if not np.isfinite(volume) or abs(volume) < 1e-12 or np.any(np.ptp(vertices, axis=0) < 1e-8):
        raise ValueError("Convex collision part has no usable volume")
    normals = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths < 1e-14):
        raise ValueError("Degenerate triangle in convex collision part")
    normals *= (1 if volume > 0 else -1) / lengths[:, None]
    for start in range(0, len(normals), 64):
        n, p = normals[start:start+64], points[start:start+64, 0]
        if np.max(vertices @ n.T - np.einsum("ij,ij->i", p, n)) > 1e-7:
            raise ValueError("Collision component is not convex")
    return abs(volume)
