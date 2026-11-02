"""Bounded offline adapters for static source collision surfaces.

Gazebo static triangle meshes can be concave or open. MuJoCo mesh colliders are
convex. This adapter preserves verified solid convex components and represents
other surfaces as thin, individually verified convex prisms. It never fills a
room, doorway, shelf opening, or hole with the source object's convex hull.
The original visual geometry remains the caller's responsibility.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.spatial import ConvexHull, QhullError

from robot_worlds.mesh_assets import convex_volume


@dataclass(frozen=True)
class StaticCollision:
    parts: tuple[tuple[np.ndarray, np.ndarray], ...]
    solid_components: int
    surface_prisms: int
    repaired_components: int
    removed_degenerate_faces: int
    removed_duplicate_faces: int
    thickness_m: float

    @property
    def disclosure(self) -> str:
        changes = [f"{self.solid_components} verified solid convex components"]
        if self.surface_prisms:
            changes.append(
                f"{self.surface_prisms} convex surface prisms of {self.thickness_m:g} m "
                "total thickness (half on each side of the source surface); source "
                "openings and original visual meshes retained"
            )
        if self.repaired_components:
            changes.append(f"face winding repaired on {self.repaired_components} components")
        if self.removed_degenerate_faces or self.removed_duplicate_faces:
            changes.append(
                f"removed {self.removed_degenerate_faces} zero-area and "
                f"{self.removed_duplicate_faces} duplicate source triangles"
            )
        return "; ".join(changes)


def _repair_winding(mesh):
    """Orient adjacent triangles without filling holes or adding dependencies."""
    faces = np.asarray(mesh.faces).copy()
    edge_faces = {}
    for face_id, face in enumerate(faces):
        for a, b in zip(face, np.roll(face, -1)):
            key = (min(int(a), int(b)), max(int(a), int(b)))
            edge_faces.setdefault(key, []).append((face_id, 1 if a < b else -1))
    neighbors = [[] for _ in faces]
    for pair in edge_faces.values():
        if len(pair) == 2:
            (a, ad), (b, bd) = pair
            neighbors[a].append((b, -ad * bd))
            neighbors[b].append((a, -ad * bd))
    orientation = np.zeros(len(faces), dtype=np.int8)
    for start in range(len(faces)):
        if orientation[start]:
            continue
        orientation[start] = 1
        pending = [start]
        while pending:
            current = pending.pop()
            for other, relative in neighbors[current]:
                expected = orientation[current] * relative
                if orientation[other] and orientation[other] != expected:
                    raise ValueError("Source collision surface has non-orientable topology")
                if not orientation[other]:
                    orientation[other] = expected
                    pending.append(other)
    flipped = orientation == -1
    if flipped.any():
        faces[flipped] = faces[flipped, ::-1]
        mesh.faces = faces
    return bool(flipped.any())


def _convex(vertices, faces):
    # Translation avoids cancellation in the volume integral for offset source
    # meshes. Adapter.add_mesh centers these same vertices before serialization.
    try:
        convex_volume(vertices - vertices.mean(axis=0), faces)
    except ValueError:
        return False
    return True


def _prism(points, normal, thickness):
    """A closed convex polygon extrusion; points follow its surface normal."""
    n = len(points)
    vertices = np.vstack((points - normal * thickness / 2, points + normal * thickness / 2))
    faces = []
    for i in range(1, n - 1):
        faces.extend(((0, i + 1, i), (n, n + i, n + i + 1)))
    for i in range(n):
        j = (i + 1) % n
        faces.extend(((i, j, n + j), (i, n + j, n + i)))
    faces = np.asarray(faces, dtype=np.int32)
    if not _convex(vertices, faces):
        raise ValueError("Source surface is too small or ill-conditioned for a verified collision prism")
    return vertices, faces


def _surface_parts(mesh, face_indices, thickness):
    """Merge a coplanar patch only when its triangles exactly cover the hull.

    The area check prevents convexification of L-shaped walls and rings. For a
    concave patch, connected triangles are greedily merged when their union is
    convex. Failed merges keep their triangles; no source surface is omitted.
    """
    indices = np.asarray(face_indices, dtype=np.int64)
    triangles = np.asarray(mesh.triangles)[indices]
    normal = np.asarray(mesh.face_normals)[indices[0]]
    origin = triangles[0, 0]
    u = triangles[0, 1] - origin
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    basis = np.vstack((u, v))
    points = np.asarray(mesh.vertices)
    planar_error = np.max(np.abs((triangles.reshape(-1, 3) - origin) @ normal))
    if planar_error > 1e-8:
        if len(indices) == 1:
            raise ValueError("Source triangle cannot be projected to its own surface")
        return [part for index in indices for part in _surface_parts(mesh, [index], thickness)]
    projected = (points - origin) @ basis.T

    def polygon(group):
        faces = np.asarray(mesh.faces)[list(group)]
        selected = np.unique(faces)
        coords = projected[selected]
        try:
            hull = ConvexHull(coords)
        except QhullError:
            return None
        # The source patch is connected. Its triangles must cover the hull,
        # rather than merely have vertices around an opening.
        tri = projected[faces]
        a, b = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        area = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2
        if abs(hull.volume - area) > max(1e-12, float(area) * 1e-10):
            return None
        edges = np.sort(np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1)
        unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
        if np.any(counts > 2):
            return None
        boundary_vertices = np.unique(unique_edges[counts == 1])
        # Every boundary of a convex polygon is on its hull. An interior hole
        # or concave notch has boundary vertices strictly inside that hull.
        signed = projected[boundary_vertices] @ hull.equations[:, :2].T + hull.equations[:, 2]
        if len(boundary_vertices) and np.any(np.max(signed, axis=1) < -1e-9):
            return None
        if len(hull.vertices) > 1024:
            return None
        return origin + coords[hull.vertices] @ basis

    whole = polygon(indices)
    if whole is not None:
        return [_prism(whole, normal, thickness)]

    # No unbounded geometric decomposition or stochastic worker: at most eight
    # passes over the patch adjacency. A missed merge only adds exact prisms.
    index_set = set(map(int, indices))
    edges = [(int(a), int(b)) for a, b in mesh.face_adjacency if a in index_set and b in index_set]
    roots = {int(index): int(index) for index in indices}
    groups = {int(index): {int(index)} for index in indices}

    def root(index):
        while roots[index] != index:
            roots[index] = roots[roots[index]]
            index = roots[index]
        return index

    for _ in range(8):
        changed = False
        for left, right in edges:
            left, right = root(left), root(right)
            if left == right:
                continue
            combined = groups[left] | groups[right]
            if polygon(combined) is not None:
                roots[right] = left
                groups[left] = combined
                del groups[right]
                changed = True
        if not changed:
            break
    parts = []
    for group in groups.values():
        outline = polygon(group)
        if outline is None:
            raise ValueError("Source surface group lost its convex coverage proof")
        parts.append(_prism(outline, normal, thickness))
    return parts


def prepare_static_collision(vertices, faces, *, thickness=0.0005, max_parts=4096):
    """Return verified convex pieces for a fixed source mesh, in source units.

    Input must already be expressed in metres. The maximum geometric change for
    a planar source contact surface is thickness / 2 plus 1e-8 m projection
    tolerance. This is for fixed architecture, never movable objects or joints.
    The piece budget is explicit; exceeding it fails rather than dropping parts.
    """
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 3
            or len(vertices) > 100_000 or not np.isfinite(vertices).all()
            or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or len(faces) > 200_000 or faces.dtype.kind not in "iu"
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("Invalid bounded source triangle mesh")
    if not np.isfinite(thickness) or not 0.0001 <= thickness <= 0.002:
        raise ValueError("Static surface backing must be between 0.1 and 2 mm")
    if not isinstance(max_parts, int) or not 1 <= max_parts <= 20_000:
        raise ValueError("Invalid static collision piece budget")

    # Weld only exactly identical coordinates. Near points are not quantized or
    # collapsed here, because they can represent real seams and narrow openings.
    vertices, inverse = np.unique(vertices, axis=0, return_inverse=True)
    faces = inverse[faces]
    triangles = vertices[faces]
    area_twice = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                         triangles[:, 2] - triangles[:, 0]), axis=1)
    good = area_twice > 0
    removed_degenerate = int((~good).sum())
    faces = faces[good]
    if not len(faces):
        raise ValueError("Source collision has no nondegenerate surface")
    _, unique = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    removed_duplicate = len(faces) - len(unique)
    faces = faces[np.sort(unique)]
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    parts = []
    solid_count = surface_count = repaired_count = 0
    # Trimesh's default submesh repair fills small holes. Disable it explicitly:
    # a source opening must remain an opening, however few triangles border it.
    for component in mesh.split(only_watertight=False, repair=False):
        repaired_count += int(_repair_winding(component))
        component_vertices, component_faces = np.asarray(component.vertices), np.asarray(component.faces)
        if component.is_watertight and _convex(component_vertices, component_faces):
            parts.append((component_vertices, component_faces))
            solid_count += 1
        else:
            assigned = set()
            patches = []
            for facet in component.facets:
                patches.append(facet)
                assigned.update(map(int, facet))
            patches.extend([index] for index in range(len(component.faces)) if index not in assigned)
            for patch in patches:
                patch_parts = _surface_parts(component, patch, thickness)
                parts.extend(patch_parts)
                surface_count += len(patch_parts)
                if len(parts) > max_parts:
                    raise ValueError(f"Static source needs more than {max_parts} convex collision pieces")
        if len(parts) > max_parts:
            raise ValueError(f"Static source needs more than {max_parts} convex collision pieces")
    if not parts:
        raise ValueError("Source collision produced no verified pieces")
    return StaticCollision(tuple(parts), solid_count, surface_count, repaired_count,
                           removed_degenerate, removed_duplicate, float(thickness))


def thicken_planar_visual(vertices, faces, uv=None, normals=None, *, thickness=0.0001):
    """Back a truly planar visual by 0.1 mm, retaining its UVs and normals.

    Returns ``(vertices, faces, uv, normals, changed)``. UVs and normals, when
    present, are per-triangle corner arrays, as consumed by prepare.obj_bytes.
    A volume mesh is returned unchanged. A planar mesh gets its source triangles
    on the front and reversed copies on the back, offset half the stated total
    thickness in each direction. This is visual geometry, never a collider.
    """
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 3
            or not np.isfinite(vertices).all() or faces.ndim != 2 or faces.shape[1] != 3
            or not len(faces) or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("Invalid source visual triangles")
    if not np.isfinite(thickness) or not 0.0001 <= thickness <= 0.002:
        raise ValueError("Visual backing must be between 0.1 and 2 mm")
    if uv is not None and np.asarray(uv).shape != (len(faces), 3, 2):
        raise ValueError("Expected per-corner source texture coordinates")
    if normals is not None and np.asarray(normals).shape != (len(faces), 3, 3):
        raise ValueError("Expected per-corner source normals")
    centered = vertices - vertices.mean(axis=0)
    _, singular, axes = np.linalg.svd(centered, full_matrices=False)
    if singular[1] < 1e-10:
        raise ValueError("Source visual has no usable two-dimensional surface")
    normal = axes[-1]
    if np.max(np.abs(centered @ normal)) > 1e-8:
        return vertices, faces, uv, normals, False
    source_normals = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                              vertices[faces[:, 2]] - vertices[faces[:, 0]])
    nonzero = np.flatnonzero(np.linalg.norm(source_normals, axis=1) > 1e-14)
    if not len(nonzero):
        raise ValueError("Source visual contains no usable triangle")
    if np.dot(normal, source_normals[nonzero[0]]) < 0:
        normal = -normal
    backed = np.vstack((vertices + normal * thickness / 2, vertices - normal * thickness / 2))
    faces = np.vstack((faces, faces[:, ::-1] + len(vertices)))
    if uv is not None:
        uv = np.asarray(uv)
        uv = np.concatenate((uv, uv[:, ::-1]), axis=0)
    if normals is not None:
        normals = np.asarray(normals)
        normals = np.concatenate((normals, -normals[:, ::-1]), axis=0)
    return backed, faces, uv, normals, True
