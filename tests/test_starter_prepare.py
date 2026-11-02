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
_geometry_spec = importlib.util.spec_from_file_location("geometry", _SCRIPT_DIR / "geometry.py")
_geometry = importlib.util.module_from_spec(_geometry_spec)
sys.modules[_geometry_spec.name] = _geometry
_geometry_spec.loader.exec_module(_geometry)
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
