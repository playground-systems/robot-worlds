"""Small OBJ/MTL import proofs; no upstream conversion, native or model calls."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "tools/starters/obj_source.py"
_spec = importlib.util.spec_from_file_location("starter_obj_source", _SCRIPT)
obj_source = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = obj_source
_spec.loader.exec_module(obj_source)


def parse(tmp_path, text):
    path = tmp_path / "source.obj"
    path.write_text(text)
    return obj_source.read_obj(path, root=tmp_path)


def test_material_groups_keep_independent_uv_normals_and_compact_positions(tmp_path):
    (tmp_path / "red.png").write_bytes(b"source texture bytes; parser never decodes them")
    (tmp_path / "source.mtl").write_text("""newmtl Red
Kd 0.8 0.2 0.1
d 0.75
map_Kd red.png
newmtl Blue
Kd 0.1 0.2 0.9
Tr 0.2
""")
    primitives = parse(tmp_path, """mtllib source.mtl
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 100 100 100
vt 0 0
vt 1 0
vt 1 1
vt 0 1
vt 0.3 0.4
vn 0 0 1
vn 0 0 -1
usemtl Red
f 1/5/1 2/2/1 3/3/1
usemtl Blue
f 1/1/2 3/3/2 4/4/2
""")
    red, blue = primitives
    assert len(red) == 1 and red.material is None
    assert red.source_color == (.8, .2, .1, .75)
    assert blue.source_color == (.1, .2, .9, .8)
    assert red.source_texture_path == tmp_path / "red.png"
    assert blue.source_texture_path is None
    assert red.source_material_files == (tmp_path / "source.mtl",)
    assert red.material_name == "Red" and blue.material_name == "Blue"
    np.testing.assert_allclose(red.texcoordset[0][red.texcoord_indexset[0]], [[[.3, .4], [1, 0], [1, 1]]])
    np.testing.assert_allclose(blue.texcoordset[0][blue.texcoord_indexset[0]], [[[0, 0], [1, 1], [0, 1]]])
    np.testing.assert_allclose(blue.normal[blue.normal_index], [[[0, 0, -1]] * 3])
    assert red.vertex.max() == 1  # An unreferenced source vertex cannot inflate this material's bounds.


def test_relative_indices_bind_at_the_face_declaration_and_forward_indices_work(tmp_path):
    primitives = parse(tmp_path, """v 0 0 0
v 1 0 0
v 0 1 0
vt 0 0
vt 1 0
vt 0 1
vn 0 0 1
f -3/-3/-1 -2/-2/-1 -1/-1/-1
v 99 99 99
f 1 2 5
v 0 0 1
""")
    uv, plain = primitives
    np.testing.assert_array_equal(uv.vertex[uv.vertex_index], [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]])
    np.testing.assert_array_equal(plain.vertex[plain.vertex_index], [[[0, 0, 0], [1, 0, 0], [0, 0, 1]]])
    assert plain.normal is None and plain.normal_index is None
    assert plain.texcoordset == () and plain.texcoord_indexset == ()


@pytest.mark.parametrize("reverse", [False, True])
def test_concave_polygon_ear_clipping_preserves_hole_winding_and_corner_uvs(tmp_path, reverse):
    points = [[0, 0, 0], [2, 0, 0], [2, 1, 0], [1, 1, 0], [1, 2, 0], [0, 2, 0]]
    corners = list(range(1, 7))
    if reverse:
        corners.reverse()
    text = "".join("v " + " ".join(map(str, point)) + "\n" for point in points)
    text += "".join(f"vt {index / 10} {index / 20}\n" for index in range(1, 7))
    text += "f " + " ".join(f"{index}/{index}" for index in corners) + "\n"
    primitive = parse(tmp_path, text)[0]
    triangles = primitive.vertex[primitive.vertex_index]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    assert np.all(cross[:, 2] < 0 if reverse else cross[:, 2] > 0)
    assert np.abs(cross[:, 2]).sum() / 2 == pytest.approx(3)
    assert not np.any((triangles.mean(axis=1)[:, 0] > 1) & (triangles.mean(axis=1)[:, 1] > 1))
    for probe in ([1.2, 1.2], [1.7, 1.3], [1.3, 1.7]):
        for triangle in triangles[:, :, :2]:
            sides = [obj_source._cross(a, b, probe) for a, b in zip(triangle, np.roll(triangle, -1, axis=0))]
            assert not (all(side >= 0 for side in sides) or all(side <= 0 for side in sides))
    # Every triangle corner retains the original source vertex's UV, including
    # vertices shared by multiple newly triangulated faces.
    for face, uv in zip(primitive.vertex_index, primitive.texcoordset[0][primitive.texcoord_indexset[0]]):
        np.testing.assert_allclose(uv[:, 0], (face + 1) / 10)


def test_rotated_and_translated_polygon_preserves_its_actual_plane(tmp_path):
    # A concave face in a vertical/slanted source plane exercises projection
    # selection; triangulating only XY would incorrectly collapse this shape.
    uv = np.array([[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]], dtype=float)
    points = np.c_[np.zeros(6), uv] @ np.array([[1., 0, 0], [0, .6, .8], [0, -.8, .6]])
    points += [1_000_000, -20, 7]
    text = "".join("v " + " ".join(map(str, point)) + "\n" for point in points)
    primitive = parse(tmp_path, text + "f 1 2 3 4 5 6\n")[0]
    triangles = primitive.vertex[primitive.vertex_index]
    area_vectors = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    assert np.linalg.norm(area_vectors, axis=1).sum() / 2 == pytest.approx(3)
    np.testing.assert_allclose(area_vectors[:, 1:], 0, atol=1e-12)
    assert np.all(area_vectors[:, 0] > 0)


@pytest.mark.parametrize("points", [
    [[0, 0, 0], [1, 1, 0], [0, 1, 0], [1, 0, 0]],
    [[0, 0, 0], [1, 0, 0], [1, 1, .1], [0, 1, 0]],
    [[0, 0, 0], [1, 0, 0], [1, 0, 0], [0, 1, 0]],
])
def test_ambiguous_polygons_fail_instead_of_inventing_triangles(tmp_path, points):
    text = "".join("v " + " ".join(map(str, point)) + "\n" for point in points)
    with pytest.raises(ValueError, match="polygon"):
        parse(tmp_path, text + "f 1 2 3 4\n")


@pytest.mark.parametrize("reference", ["0", "9", "-4", "1/0", "1/1/1/1"])
def test_invalid_corner_indices_fail(tmp_path, reference):
    with pytest.raises(ValueError):
        parse(tmp_path, f"v 0 0 0\nv 1 0 0\nv 0 1 0\nf {reference} 2 3\n")


def test_partially_missing_attributes_do_not_silently_lose_uvs(tmp_path):
    with pytest.raises(ValueError, match="Partial texture/normal"):
        parse(tmp_path, "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nf 1/1 2 3\n")


@pytest.mark.parametrize("uri", ["../outside.mtl", "/etc/passwd", "https://example.invalid/material.mtl", "C:/outside.mtl", "%2e%2e/outside.mtl"])
def test_material_resources_cannot_escape_the_source_root(tmp_path, uri):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.mtl").write_text("newmtl outside\nKd 1 0 0\n")
    with pytest.raises(ValueError, match="resource"):
        parse(root, f"mtllib {uri}\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")


def test_symlinked_texture_cannot_read_outside_the_source_root(tmp_path):
    root = tmp_path / "model"
    root.mkdir()
    (tmp_path / "private.png").write_bytes(b"not source data")
    (root / "texture.png").symlink_to(tmp_path / "private.png")
    (root / "source.mtl").write_text("newmtl paint\nmap_Kd texture.png\n")
    with pytest.raises(ValueError, match="escaping.*resource"):
        parse(root, "mtllib source.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nusemtl paint\nf 1/1 2/1 3/1\n")


@pytest.mark.parametrize("setting,source", [
    ("MAX_OBJ_BYTES", "v 0 0 0\n"),
    ("MAX_MTL_BYTES", "mtllib source.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"),
])
def test_mesh_and_material_byte_budgets_are_enforced(tmp_path, monkeypatch, setting, source):
    (tmp_path / "source.mtl").write_text("newmtl source\nKd 1 0 0\n")
    monkeypatch.setattr(obj_source, setting, 5)
    with pytest.raises(ValueError, match="byte budget"):
        parse(tmp_path, source)


@pytest.mark.parametrize("statement", ["Kd nan 0 0", "Kd 2 0 0", "d -0.1", "Tr inf", "map_Kd -s 2 2 2 texture.png"])
def test_unreviewed_or_invalid_material_fields_are_rejected(tmp_path, statement):
    (tmp_path / "source.mtl").write_text("newmtl paint\n" + statement + "\n")
    with pytest.raises(ValueError):
        parse(tmp_path, "mtllib source.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl paint\nf 1 2 3\n")


def test_undefined_and_ambiguous_materials_are_rejected(tmp_path):
    mesh = "v 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl paint\nf 1 2 3\n"
    with pytest.raises(ValueError, match="Undefined OBJ material"):
        parse(tmp_path, mesh)
    (tmp_path / "a.mtl").write_text("newmtl paint\nKd 1 0 0\n")
    (tmp_path / "b.mtl").write_text("newmtl paint\nKd 0 1 0\n")
    with pytest.raises(ValueError, match="Ambiguous material"):
        parse(tmp_path, "mtllib a.mtl b.mtl\n" + mesh)


def test_quoted_nested_materials_and_homogeneous_positions_are_data(tmp_path):
    directory = tmp_path / "materials"
    directory.mkdir()
    (directory / "color map.png").write_bytes(b"original")
    (directory / "source palette.mtl").write_text('newmtl Paint With Spaces\nmap_Kd "color map.png"\n')
    primitive = parse(tmp_path, 'mtllib "materials/source palette.mtl"\n'
                      'v 0 0 0 2\nv 2 0 0 2\nv 0 2 0 2\nvt .1\n'
                      'usemtl Paint With Spaces\nf 1/1 \\\n2/1 3/1\n')[0]
    np.testing.assert_allclose(primitive.vertex, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    np.testing.assert_allclose(primitive.texcoordset[0], [[.1, 0]])
    assert primitive.source_texture_path == directory / "color map.png"
