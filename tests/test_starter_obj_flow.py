"""Tiny OBJ source through the real SDF adapter, without native simulation."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from robot_worlds.mesh_assets import convex_volume, read_triangle_obj
from robot_worlds.models import WorldSpec

pytest.importorskip("collada")
pytest.importorskip("trimesh")
pytest.importorskip("scipy")

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "tools/starters"
# Match the directly executable release tool's sibling-module resolution.
for name in ("geometry", "obj_source"):
    location = _SCRIPT_DIR / (name + ".py")
    if location.exists():
        spec = importlib.util.spec_from_file_location(name, location)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
_prepare_spec = importlib.util.spec_from_file_location("starter_obj_flow_prepare", _SCRIPT_DIR / "prepare.py")
prepare = importlib.util.module_from_spec(_prepare_spec)
sys.modules[_prepare_spec.name] = prepare
sys.path.insert(0, str(_SCRIPT_DIR))
try:
    _prepare_spec.loader.exec_module(prepare)
finally:
    sys.path.pop(0)


def test_sdf_obj_mtl_texture_import_keeps_scale_attributes_and_source_hashes(tmp_path):
    source = tmp_path / "source"
    model = source / "models" / "fixture_cube"
    (model / "meshes").mkdir(parents=True)
    (source / "worlds").mkdir()
    (source / "LICENSE").write_text("MIT-0 synthetic adapter fixture; no upstream assets used.\n")
    (source / "acquisition.json").write_text(json.dumps({"archive_sha256": "1" * 64}))
    entry = {"id": "fixture", "name": "OBJ adapter fixture", "description": "Unit test only",
             "source_url": "https://example.invalid/source", "repo": "test/fixture",
             "revision": "2" * 40, "license": "MIT-0", "world": "worlds/fixture.world"}
    mesh = model / "meshes" / "cube.obj"
    points = np.array([[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0],
                       [0, 0, 100], [100, 0, 100], [100, 100, 100], [0, 100, 100]])
    faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
             [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
             [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    text = "mtllib cube.mtl\n" + "".join("v " + " ".join(map(str, point)) + "\n" for point in points)
    # Deliberately independent corner indices preserve texture seams. A fixed
    # source normal direction makes the authored SDF rotation easy to verify.
    text += "vt 0.1 0.2\nvt 0.8 0.2\nvt 0.8 0.9\nvn 1 0 0\nusemtl Paint\n"
    text += "".join("f " + " ".join(f"{vertex+1}/{corner+1}/1" for corner, vertex in enumerate(face)) + "\n" for face in faces)
    mesh.write_text(text)
    material = model / "meshes" / "cube.mtl"
    material.write_text("newmtl Paint\nKd 0.8 0.4 0.2\nd 0.6\nmap_Kd original.png\n")
    texture = model / "meshes" / "original.png"
    Image.new("RGB", (4, 4), (20, 120, 200)).save(texture)
    (model / "model.sdf").write_text('''<sdf version="1.6"><model name="fixture_cube"><link name="body">
      <collision name="collision"><geometry><mesh><uri>model://fixture_cube/meshes/cube.obj</uri>
        <scale>.01 .01 .01</scale></mesh></geometry></collision>
      <visual name="visual"><geometry><mesh><uri>model://fixture_cube/meshes/cube.obj</uri>
        <scale>.01 .01 .01</scale></mesh></geometry></visual>
      </link></model></sdf>''')
    world = source / entry["world"]
    world.write_text('''<sdf version="1.6"><world name="fixture">
      <model name="rotated"><pose>4 4 .25 0 0 1.5707963267948966</pose>
        <include><uri>model://fixture_cube</uri></include></model>
      <model name="unrotated"><pose>-4 -4 .25 0 0 0</pose>
        <include><uri>model://fixture_cube</uri></include></model>
      </world></sdf>''')
    output, cache = tmp_path / "output", tmp_path / "cache"
    cache.mkdir()
    adapter = prepare.Adapter(entry, source, output, cache)
    adapter.input(source / "LICENSE")
    adapter.input(world)
    for instance in prepare.xml(world).find("world").findall("model"):
        adapter.model(instance, np.eye(4), instance.get("name"))
    adapter.finish()

    spec = WorldSpec.model_validate_json((output / "spec.json").read_text())
    physical = [obj for obj in spec.objects if obj.kind == "mesh" and obj.collision]
    visuals = [obj for obj in spec.objects if obj.kind == "mesh" and not obj.collision]
    assert len(physical) == len(visuals) == 2
    np.testing.assert_allclose(np.array(physical[0].position)-physical[1].position, [7, 8, 0])
    for index, (body, visual) in enumerate(zip(physical, visuals)):
        assert body.size == pytest.approx([1, 1, 1])
        np.testing.assert_allclose(body.position, visual.position)
        assert visual.parent_id == body.id and not body.visible and visual.visible
        assert visual.color == pytest.approx([.8, .4, .2])
        assert visual.opacity == pytest.approx(.6)
        assert visual.surface.alpha_mode == "BLEND"
        vertices, triangles = read_triangle_obj(output / body.mesh_path)
        assert convex_volume(vertices, triangles) == pytest.approx(1)
        converted = sys.modules["obj_source"].read_obj(output / visual.mesh_path, root=output)[0]
        np.testing.assert_allclose(converted.texcoordset[0][converted.texcoord_indexset[0]],
                                   np.tile([[[.1, .2], [.8, .2], [.8, .9]]], (12, 1, 1)))
        expected_normal = [0, 1, 0] if index == 0 else [1, 0, 0]
        np.testing.assert_allclose(converted.normal[converted.normal_index],
                                   np.tile(expected_normal, (12, 3, 1)), atol=1e-12)
        with Image.open(output / visual.texture_path) as image:
            assert image.size == (4, 4) and image.getpixel((0, 0)) == (20, 120, 200)
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["instances"] == 2
    for original in (mesh, material, texture, source / "LICENSE", world, model / "model.sdf"):
        assert provenance["source_files"][str(original.relative_to(source))] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert len([name for name in provenance["source_files"] if name.endswith("cube.mtl")]) == 1
    assert "MIT-0 synthetic adapter fixture" in (output / "NOTICE.txt").read_text()
    manifest = json.loads((output / "manifest.json").read_text())
    for relative, checksum in manifest["files"].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == checksum
