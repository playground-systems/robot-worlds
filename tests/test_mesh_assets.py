from pathlib import Path

import numpy as np
import pytest

from robot_worlds import asset_import
from robot_worlds.mesh_assets import convex_volume, read_triangle_obj


def cube():
    vertices = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                         (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], dtype=float)
    faces = np.array([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                      (3, 7, 6), (3, 6, 2), (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5)])
    return vertices, faces


def test_verifies_closed_convex_mesh_in_both_windings():
    vertices, faces = cube()
    assert convex_volume(vertices, faces) == pytest.approx(1)
    assert convex_volume(vertices, faces[:, ::-1]) == pytest.approx(1)


def test_rejects_concave_geometry_mislabeled_as_a_collision_hull():
    vertices, faces = cube()
    vertices[6] = (.4, .4, .4)
    with pytest.raises(ValueError, match="not convex"):
        convex_volume(vertices, faces)


def test_rejects_open_or_inconsistently_wound_parts():
    vertices, faces = cube()
    with pytest.raises(ValueError, match="closed"):
        convex_volume(vertices, faces[:-1])
    faces[0] = faces[0, ::-1]
    with pytest.raises(ValueError, match="consistent"):
        convex_volume(vertices, faces)


def test_original_bowl_geometry_is_read_without_rewriting_visual_data():
    path = Path(asset_import.__file__).parent / "assets/household/porcelain_bowl/meshes/model.obj"
    before = path.read_bytes()
    vertices, faces = read_triangle_obj(path)
    assert len(vertices) == 2975
    assert faces.shape[1] == 3
    assert np.ptp(vertices, axis=0) == pytest.approx([.208234, .209341, .068883])
    assert path.read_bytes() == before


@pytest.mark.parametrize("face", ["f 0 2 3", "f 1 2 50", "f 1 2 3 4"])
def test_invalid_obj_indices_and_untriangulated_faces_fail(tmp_path, face):
    path = tmp_path / "bad.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\n"+face+"\n")
    with pytest.raises(ValueError):
        read_triangle_obj(path)
