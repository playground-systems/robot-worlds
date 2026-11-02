"""Small source-geometry proofs; no native simulation or scene conversion."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("trimesh")
pytest.importorskip("scipy")

from robot_worlds.mesh_assets import convex_volume

_SCRIPT = Path(__file__).resolve().parents[1] / "tools/starters/geometry.py"
_spec = importlib.util.spec_from_file_location("starter_geometry", _SCRIPT)
geometry = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = geometry
_spec.loader.exec_module(geometry)


def box():
    # Source fixture: one ordinary solid cube, independent of the adapter.
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
    faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                      [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                      [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return vertices, faces


def test_closed_convex_source_keeps_solid_volume_and_repairs_winding():
    vertices, faces = box()
    faces[3] = faces[3, ::-1]
    result = geometry.prepare_static_collision(vertices, faces)
    assert result.solid_components == 1 and result.surface_prisms == 0
    assert result.repaired_components == 1
    points, triangles = result.parts[0]
    assert convex_volume(points, triangles) == pytest.approx(1)
    assert {tuple(point) for point in points} == {tuple(point) for point in vertices}


def test_coplanar_quad_merges_and_bounds_contact_error():
    vertices = np.array([[0., 0., 0.], [2, 0, 0], [2, 1, 0], [0, 1, 0]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    result = geometry.prepare_static_collision(vertices, faces)
    assert result.surface_prisms == 1 and result.solid_components == 0
    points, triangles = result.parts[0]
    assert convex_volume(points, triangles) == pytest.approx(2 * 0.0005)
    np.testing.assert_allclose(points.min(axis=0), [0, 0, -0.00025])
    np.testing.assert_allclose(points.max(axis=0), [2, 1, 0.00025])
    assert "0.0005 m total thickness" in result.disclosure


def test_l_shaped_wall_does_not_close_its_opening():
    vertices = np.array([[0., 0., 0.], [2, 0, 0], [2, 1, 0],
                         [1, 1, 0], [1, 2, 0], [0, 2, 0]])
    faces = np.array([[0, 1, 3], [1, 2, 3], [0, 3, 5], [3, 4, 5]])
    result = geometry.prepare_static_collision(vertices, faces)
    assert result.surface_prisms >= 2
    total_volume = sum(convex_volume(v, f) for v, f in result.parts)
    assert total_volume == pytest.approx(3 * 0.0005)
    # Probe the entire missing square against each actual convex polyhedron.
    for x in (1.1, 1.5, 1.9):
        for y in (1.1, 1.5, 1.9):
            for points, _ in result.parts:
                planes = geometry.ConvexHull(points).equations
                assert np.max(planes[:, :3] @ [x, y, 0] + planes[:, 3]) > 1e-8


def test_separate_convex_components_are_not_joined_across_free_space():
    vertices, faces = box()
    result = geometry.prepare_static_collision(
        np.vstack((vertices, vertices + [4, 0, 0])), np.vstack((faces, faces + len(vertices))))
    assert result.solid_components == 2
    assert sum(convex_volume(v, f) for v, f in result.parts) == pytest.approx(2)
    assert all(np.ptp(v[:, 0]) == pytest.approx(1) for v, _ in result.parts)


def test_piece_budget_and_invalid_source_fail_without_omitting_geometry():
    vertices, faces = box()
    with pytest.raises(ValueError, match="more than 1"):
        geometry.prepare_static_collision(np.vstack((vertices, vertices + [4, 0, 0])),
                                          np.vstack((faces, faces + len(vertices))), max_parts=1)
    with pytest.raises(ValueError, match="Invalid bounded"):
        geometry.prepare_static_collision(vertices, np.array([[0, 1, 100]]))
    with pytest.raises(ValueError, match="backing"):
        geometry.prepare_static_collision(vertices, faces, thickness=0.1)


def test_exact_duplicates_and_zero_area_are_disclosed():
    vertices, faces = box()
    result = geometry.prepare_static_collision(vertices, np.vstack((faces, faces[:1], [0, 0, 1])))
    assert result.removed_duplicate_faces == 1 and result.removed_degenerate_faces == 1
    assert result.solid_components == 1
    assert "1 zero-area and 1 duplicate" in result.disclosure


def test_planar_visual_backing_preserves_per_corner_uv_and_normal():
    vertices = np.array([[0., 0., 0.], [1, 0, 0], [0, 1, 0]])
    faces = np.array([[0, 1, 2]])
    uv = np.array([[[0., 0.], [1, 0], [0, 1]]])
    normals = np.array([[[0., 0., 1.]] * 3])
    points, triangles, texcoords, directions, changed = geometry.thicken_planar_visual(
        vertices, faces, uv, normals)
    assert changed and len(points) == 6 and len(triangles) == 2
    np.testing.assert_allclose(points[:3, :2], vertices[:, :2])
    np.testing.assert_allclose(points[:3, 2], 0.00005)
    np.testing.assert_allclose(points[3:, 2], -0.00005)
    np.testing.assert_array_equal(texcoords[0], uv[0])
    np.testing.assert_array_equal(texcoords[1], uv[0, ::-1])
    np.testing.assert_array_equal(directions[0], normals[0])
    np.testing.assert_array_equal(directions[1], -normals[0, ::-1])


def test_oblique_planar_visual_gets_backing_but_solid_does_not():
    vertices = np.array([[0., 0., 0.], [1, 1, 0], [0, 1, 1]])
    result = geometry.thicken_planar_visual(vertices, np.array([[0, 1, 2]]))
    assert result[-1] and np.linalg.matrix_rank(result[0] - result[0].mean(axis=0)) == 3
    vertices, faces = box()
    result = geometry.thicken_planar_visual(vertices, faces)
    assert not result[-1]
    np.testing.assert_array_equal(result[0], vertices)


def test_small_source_hole_is_not_filled_by_submesh_repair_or_convexification():
    vertices = np.array([[float(x), float(y), 0.] for y in range(4) for x in range(4)])
    faces = []
    for y in range(3):
        for x in range(3):
            if (x, y) == (1, 1):
                continue
            a = y * 4 + x
            faces.extend(((a, a + 1, a + 5), (a, a + 5, a + 4)))
    result = geometry.prepare_static_collision(vertices, np.array(faces))
    assert result.solid_components == 0
    assert sum(convex_volume(v, f) for v, f in result.parts) == pytest.approx(8 * 0.0005)
    for points, _ in result.parts:
        planes = geometry.ConvexHull(points).equations
        assert np.max(planes[:, :3] @ [1.5, 1.5, 0] + planes[:, 3]) > 1e-8
