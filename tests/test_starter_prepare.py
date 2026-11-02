"""Exercise the offline source adapter on tiny actual SDF/COLLADA fixtures."""
import hashlib
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from robot_worlds.mesh_assets import convex_volume, read_triangle_obj
from robot_worlds.models import WorldSpec

collada = pytest.importorskip("collada")
pytest.importorskip("trimesh")
pytest.importorskip("scipy")

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "tools/starters"
# prepare.py is deliberately a directly executable offline release script.
# Resolve its sibling helper exactly as `python tools/starters/prepare.py` does.
for _name in ("geometry", "fuel", "obj_source"):
    _helper_spec = importlib.util.spec_from_file_location(_name, _SCRIPT_DIR / f"{_name}.py")
    _helper = importlib.util.module_from_spec(_helper_spec)
    sys.modules[_name] = _helper
    _helper_spec.loader.exec_module(_helper)
_prepare_spec = importlib.util.spec_from_file_location("starter_prepare", _SCRIPT_DIR / "prepare.py")
prepare = importlib.util.module_from_spec(_prepare_spec)
sys.modules[_prepare_spec.name] = prepare
_prepare_spec.loader.exec_module(prepare)


def cube():
    vertices = np.array([[0., 0., 0.], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
    faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                      [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                      [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return vertices, faces


def write_cube_dae(path):
    """One centimetre-authored cube with source texture and node translation."""
    document = collada.Collada()
    document.assetInfo.unitmeter = 0.01
    document.assetInfo.unitname = "centimeter"
    document.assetInfo.upaxis = "Z_UP"
    vertices, faces = cube()
    vertex_source = collada.source.FloatSource("positions", (vertices * 100).astype(np.float32),
                                               ("X", "Y", "Z"))
    uv = np.array([[0., 0.], [1, 0], [1, 1], [0, 1], [0, 0], [1, 0], [1, 1], [0, 1]])
    uv_source = collada.source.FloatSource("uv", uv.astype(np.float32), ("S", "T"))
    geometry = collada.geometry.Geometry(document, "cube", "cube", [vertex_source, uv_source])
    inputs = collada.source.InputList()
    inputs.addInput(0, "VERTEX", "#positions")
    inputs.addInput(1, "TEXCOORD", "#uv", set="0")
    # Independent position/UV indices exercise source UV seams in OBJ output.
    indices = np.stack((faces, faces), axis=-1).reshape(-1)
    triangles = geometry.createTriangleSet(indices, inputs, "source-material")
    geometry.primitives.append(triangles)
    document.geometries.append(geometry)
    image = collada.material.CImage("texture", "original.png")
    surface = collada.material.Surface("surface", image)
    sampler = collada.material.Sampler2D("sampler", surface)
    effect = collada.material.Effect("effect", [surface, sampler], "phong",
                                      diffuse=collada.material.Map(sampler, "UVSET0"))
    material = collada.material.Material("material", "original", effect)
    document.images.append(image)
    document.effects.append(effect)
    document.materials.append(material)
    bound = collada.scene.MaterialNode("source-material", material, inputs=[("UVSET0", "TEXCOORD", "0")])
    node = collada.scene.Node("node", children=[collada.scene.GeometryNode(geometry, [bound])],
                              transforms=[collada.scene.TranslateTransform(100, 200, 0)])
    scene = collada.scene.Scene("scene", [node])
    document.scenes.append(scene)
    document.scene = scene
    document.write(str(path))
    Image.new("RGB", (4, 4), (40, 100, 180)).save(path.parent / "original.png")


@pytest.fixture
def adapter(tmp_path):
    source = tmp_path / "source"
    (source / "worlds").mkdir(parents=True)
    (source / "models/unit_cube").mkdir(parents=True)
    (source / "LICENSE").write_text("MIT-0 test fixture license\n")
    (source / "acquisition.json").write_text(json.dumps({"archive_sha256": "1" * 64}))
    entry = {"id": "fixture", "name": "Source fixture", "description": "Tiny original source layout.",
             "source_url": "https://example.com/pinned-scene", "repo": "fixture/source", "revision": "2" * 40,
             "world": "worlds/fixture.world"}
    output = tmp_path / "output"
    cache = tmp_path / "cache"
    cache.mkdir()
    return prepare.Adapter(entry, source, output, cache)


def test_centimetre_source_scene_to_spec_retains_layout_texture_and_manifest(adapter):
    model_dir = adapter.source / "models/unit_cube"
    dae = model_dir / "original.dae"
    write_cube_dae(dae)
    (model_dir / "model.sdf").write_text('''<sdf version="1.6"><model name="unit_cube">
      <link name="body"><collision name="collision"><geometry><mesh>
        <uri>model://unit_cube/original.dae</uri></mesh></geometry></collision>
      <visual name="visual"><geometry><mesh><uri>model://unit_cube/original.dae</uri>
        </mesh></geometry></visual></link></model></sdf>''')
    world_file = adapter.source / adapter.entry["world"]
    world_file.write_text('''<sdf version="1.6"><world name="fixture">
      <model name="cube_instance"><pose>4 4 .25 0 0 1.5707963267948966</pose>
        <include><uri>model://unit_cube</uri></include></model>
      <model name="sphere_instance"><pose>-4 -4 .3 0 0 0</pose><link name="body">
        <collision name="collision"><geometry><sphere><radius>.2</radius></sphere></geometry></collision>
        <visual name="visual"><geometry><sphere><radius>.2</radius></sphere></geometry>
          <material><diffuse>.2 .4 .6 1</diffuse></material></visual></link></model>
      </world></sdf>''')
    adapter.input(adapter.source / "LICENSE")
    adapter.input(world_file)
    for model in prepare.xml(world_file).find("world").findall("model"):
        adapter.model(model, np.eye(4), model.get("name"))
    adapter.finish()

    spec = WorldSpec.model_validate_json((adapter.output / "spec.json").read_text())
    physical = [obj for obj in spec.objects if obj.collision]
    assert len(physical) == 2
    source_cube = next(obj for obj in physical if obj.kind == "mesh")
    sphere = next(obj for obj in physical if obj.kind == "sphere")
    visual = next(obj for obj in spec.objects if obj.kind == "mesh" and not obj.collision)
    # Centimetres, the DAE node transform, the world instance pose and yaw all
    # compose before the intentional XY scene recentering.
    np.testing.assert_allclose(source_cube.size, [1, 1, 1])
    np.testing.assert_allclose(np.array(source_cube.position) - sphere.position, [5.5, 9.5, .45])
    np.testing.assert_allclose(visual.position, source_cube.position)
    assert visual.parent_id == source_cube.id
    assert visual.visible and not source_cube.visible
    assert sphere.visible and sphere.color == pytest.approx((.2, .4, .6))
    assert sphere.size == pytest.approx((.4, .4, .4))
    assert visual.texture_path and (adapter.output / visual.texture_path).is_file()
    with Image.open(adapter.output / visual.texture_path) as texture:
        assert texture.size == (4, 4) and texture.getpixel((0, 0)) == (40, 100, 180)
    points, triangles = read_triangle_obj(adapter.output / source_cube.mesh_path)
    assert len(points) == 8  # Indexed source vertices, not triangle expansion.
    assert convex_volume(points, triangles) == pytest.approx(1)

    provenance = json.loads((adapter.output / "provenance.json").read_text())
    assert provenance["instances"] == 2
    assert provenance["source_files"]["models/unit_cube/original.dae"] == hashlib.sha256(dae.read_bytes()).hexdigest()
    assert "models/unit_cube/original.png" in provenance["source_files"]
    assert "MIT-0 test fixture license" in (adapter.output / "NOTICE.txt").read_text()
    manifest = json.loads((adapter.output / "manifest.json").read_text())
    for relative, digest in manifest["files"].items():
        assert hashlib.sha256((adapter.output / relative).read_bytes()).hexdigest() == digest


def test_planar_visual_filters_unused_source_vertices_and_preserves_uv(adapter):
    vertices, faces = cube()
    parent = adapter.add_mesh(vertices, faces, np.eye(4), "Physical source", physical=True)
    planar = np.array([[0., 0., 0.], [1, 0, 0], [0, 1, 0], [9000, 9000, 9000]])
    uv = np.array([[[0., 0.], [1, 0], [0, 1]]])
    normals = np.array([[[0., 0., 1.]] * 3])
    adapter.add_mesh(planar, np.array([[0, 1, 2]]), np.eye(4), "Original planar glass",
                     parent=parent, uv=uv, normals=normals, material=([.4, .6, .8], None, .5))
    obj = adapter.objects[-1]
    assert obj["collision"] is False and obj["parent_id"] == parent
    np.testing.assert_allclose(obj["size"], [1, 1, .0001])
    np.testing.assert_allclose(obj["position"], [.5, .5, 0])
    text = (adapter.output / obj["mesh_path"]).read_text()
    assert sum(line.startswith("v ") for line in text.splitlines()) == 6
    assert sum(line.startswith("vt ") for line in text.splitlines()) == 6
    assert "f 1/1/1 2/2/2 3/3/3" in text
    assert "f 6/4/4 5/5/5 4/6/6" in text
    assert obj["surface"]["alpha_mode"] == "BLEND"
    assert any("0.1 mm total" in item for item in adapter.disclosures)


def test_physical_parts_and_visuals_keep_first_assembly_parent_across_links(adapter):
    # Two original collision meshes are kept physical despite sharing a parent;
    # a later visual-only link is attached to the same original assembly.
    dae = adapter.source / "models/unit_cube/original.dae"
    write_cube_dae(dae)
    mesh = "<geometry><mesh><uri>model://unit_cube/original.dae</uri></mesh></geometry>"
    instance = ET.fromstring(f'''<model name="assembly"><link name="a">
      <collision name="one">{mesh}</collision><collision name="two"><pose>2 0 0 0 0 0</pose>{mesh}</collision>
      </link><link name="visual_link"><visual name="visual">{mesh}</visual></link></model>''')
    adapter.model(instance, np.eye(4), "Assembly")
    first, second, visual = adapter.objects
    assert first["collision"] and first["parent_id"] is None
    assert second["collision"] and second["parent_id"] == first["id"]
    assert not visual["collision"] and visual["parent_id"] == first["id"]
    assert len(adapter.collision_cache) == 1


def test_explicit_empty_collada_material_group_is_disclosed_without_losing_faces(adapter):
    import copy

    dae = adapter.source / "models/unit_cube/original.dae"
    write_cube_dae(dae)
    tree = ET.parse(dae)
    ns = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    mesh = tree.getroot().find(".//c:mesh", ns)
    source_triangles = mesh.find("c:triangles", ns)
    empty = copy.deepcopy(source_triangles)
    empty.set("count", "0")
    empty.find("c:p", ns).text = None
    mesh.insert(list(mesh).index(source_triangles) + 1, empty)
    tree.write(dae, encoding="utf-8", xml_declaration=True)
    disclosures = []
    primitives = list(prepare.primitives(dae, disclosures))
    assert len(primitives) == 1 and len(primitives[0][0].vertex_index) == 12
    assert len(disclosures) == 1 and "source count=0" in disclosures[0]
    assert "no source surface removed" in disclosures[0]
    parts = adapter.collisions(dae, np.ones(3))
    assert len(parts) == 1
    assert convex_volume(parts[0][0], parts[0][1]) == pytest.approx(1)
    assert any("source count=0" in item for item in adapter.disclosures)


def test_main_collects_source_failures_and_never_releases_a_partial_scene(tmp_path, monkeypatch):
    entries = [{"id": name, "world": "scene.world"} for name in ("partial", "complete")]
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"environments": entries}))
    source = tmp_path / "source"
    for entry in entries:
        directory = source / entry["id"]
        directory.mkdir(parents=True)
        (directory / "LICENSE").write_text("fixture")
        models = '<model name="bad"/><model name="after_bad"/>' if entry["id"] == "partial" else '<model name="good"/>'
        (directory / "scene.world").write_text(f'<sdf><world name="fixture">{models}</world></sdf>')
    output = tmp_path / "output"
    (output / "partial").mkdir(parents=True)
    (output / "partial/manifest.json").write_text("stale previous success")
    calls = []

    class TinyAdapter:
        def __init__(self, entry, source, output, cache):
            self.entry, self.output = entry, output
            output.mkdir(parents=True, exist_ok=True)

        def input(self, path):
            assert path.is_file()

        def model(self, model, transform, label):
            calls.append((self.entry["id"], label))
            if label == "bad":
                raise ValueError("original source fixture is unsupported")

        def finish(self):
            calls.append((self.entry["id"], "finish"))
            (self.output / "manifest.json").write_text("complete fixture")

    monkeypatch.setattr(prepare, "CATALOG", catalog)
    monkeypatch.setattr(prepare, "Adapter", TinyAdapter)
    monkeypatch.setattr(prepare, "acquire", lambda entry, cache: source / entry["id"])
    monkeypatch.setattr(sys, "argv", ["prepare.py", "--output", str(output), "--cache", str(tmp_path / "cache")])
    with pytest.raises(RuntimeError, match="no partial scene is release-ready"):
        prepare.main()
    assert calls == [("partial", "bad"), ("partial", "after_bad"), ("complete", "good"), ("complete", "finish")]
    assert not (output / "partial/manifest.json").exists()
    assert (output / "complete/manifest.json").read_text() == "complete fixture"
    failures = json.loads((output / "failures.json").read_text())
    assert len(failures) == 1
    assert failures[0]["environment"] == "partial" and failures[0]["source_model"] == "bad"


def test_y_up_gazebo_source_keeps_authored_node_rotation_in_collisions_and_visuals(adapter):
    # Gazebo Classic does not add an asset.up_axis rotation. Its Y_UP hospital
    # meshes already author Rx(+90 deg) nodes; repeating it makes floors vertical.
    dae = adapter.source / "models/unit_cube/original.dae"
    document = collada.Collada()
    document.assetInfo.unitmeter = 0.01
    document.assetInfo.unitname = "centimeter"
    document.assetInfo.upaxis = "Y_UP"
    positions = collada.source.FloatSource("positions", np.array(
        [[0., 0., 0.], [100, 0, 0], [100, 0, -200], [0, 0, -200]], dtype=np.float32), ("X", "Y", "Z"))
    normals = collada.source.FloatSource("normals", np.array([[0., 1., 0.]], dtype=np.float32), ("X", "Y", "Z"))
    mesh = collada.geometry.Geometry(document, "floor", "floor", [positions, normals])
    inputs = collada.source.InputList()
    inputs.addInput(0, "VERTEX", "#positions")
    inputs.addInput(1, "NORMAL", "#normals")
    indices = np.array([[0, 0], [1, 0], [2, 0], [0, 0], [2, 0], [3, 0]]).reshape(-1)
    mesh.primitives.append(mesh.createTriangleSet(indices, inputs))
    document.geometries.append(mesh)
    node = collada.scene.Node("source-floor", children=[collada.scene.GeometryNode(mesh)], transforms=[
        collada.scene.TranslateTransform(100, 200, 300), collada.scene.RotateTransform(1, 0, 0, 90)])
    scene = collada.scene.Scene("scene", [node])
    document.scenes.append(scene)
    document.scene = scene
    document.write(str(dae))
    geometry_xml = "<geometry><mesh><uri>model://unit_cube/original.dae</uri></mesh></geometry>"
    instance = ET.fromstring(f'''<model name="floor"><link name="body">
      <collision name="collision">{geometry_xml}</collision>
      <visual name="visual">{geometry_xml}</visual></link></model>''')
    adapter.model(instance, np.eye(4), "Source floor")
    assert len(adapter.objects) == 2
    collision, visual = adapter.objects
    np.testing.assert_allclose(collision["position"], [1.5, 3, 3], atol=1e-7)
    np.testing.assert_allclose(visual["position"], collision["position"], atol=1e-7)
    np.testing.assert_allclose(collision["size"], [1, 2, .0005], atol=1e-7)
    np.testing.assert_allclose(visual["size"], [1, 2, .0001], atol=1e-7)
    lines = (adapter.output / visual["mesh_path"]).read_text().splitlines()
    normals = np.array([[float(value) for value in line.split()[1:]] for line in lines if line.startswith("vn ")])
    np.testing.assert_allclose(normals[:6], np.tile([0, 0, 1], (6, 1)), atol=1e-7)
    assert any("Y_UP" in item and "no extra up-axis rotation" in item for item in adapter.disclosures)


def test_reviewed_original_photo_remap_is_bounded_and_disclosed(adapter, tmp_path):
    photos = adapter.source / "photos"
    photos.mkdir()
    image = photos / "portrait.png"
    Image.new("RGB", (2, 2), (10, 20, 30)).save(image)
    disclosures = []
    found = prepare.resolve("../../../../photos/portrait.png", adapter.source,
                            adapter.source / "models/unit_cube", disclosures)
    assert found == image.resolve()
    assert len(disclosures) == 1 and "photos/portrait.png" in disclosures[0]
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside fixture")
    with pytest.raises(ValueError, match="Unresolved source resource"):
        prepare.resolve("../../outside.png", adapter.source, adapter.source, disclosures)
    with pytest.raises(ValueError, match="Unresolved source resource"):
        prepare.resolve("../../../../photos/nested/missing.png", adapter.source,
                        adapter.source / "models/unit_cube", disclosures)


def test_source_filename_case_correction_stays_in_existing_source_parent(adapter, monkeypatch):
    directory = adapter.source / "models/unit_cube"
    original = directory / "Actual_Collision.DAE"
    original.write_text("original data")
    requested_name = "actual_collision.dae"
    original_is_file = Path.is_file
    # Exercise the Linux case-sensitive source condition even on a Mac whose
    # filesystem may consider both casings equal.
    monkeypatch.setattr(Path, "is_file", lambda path: False if path.name == requested_name else original_is_file(path))
    disclosures = []
    path = prepare.resolve(f"model://unit_cube/{requested_name}", adapter.source,
                           adapter.source, disclosures)
    assert path == original.resolve()
    assert len(disclosures) == 1 and "exporter path/case correction" in disclosures[0]
    with pytest.raises(ValueError, match="Unresolved source resource"):
        prepare.resolve("model://nonexistent/actual_collision.dae", adapter.source,
                        adapter.source, disclosures)
