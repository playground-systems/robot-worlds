"""Pure scene-XML checks; no native compiler or renderer is started here."""
import copy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from robot_worlds import mesh_assets
from robot_worlds.appearance import add_appearance
from robot_worlds.models import SceneObject, WorldSpec
from robot_worlds.runtime import Runtime


def repeated_mesh_scene(tmp_path):
    # An offset tetrahedron exposes accidental scale/centering changes. Test
    # geometry only; this is not a production asset or generation fallback.
    mesh = tmp_path / "fixture.obj"
    mesh.write_text("v 1 2 3\nv 2 2 3\nv 1 3 3\nv 1 2 4\n"
                    "vt 0 0\nvt 1 0\nvt 0 1\nvt 1 1\n"
                    "f 1/1 3/3 2/2\nf 1/1 2/2 4/4\nf 1/1 4/4 3/3\nf 2/2 3/3 4/4\n")
    texture = tmp_path / "fixture.png"
    image = Image.new("RGB", (2, 2))
    image.putdata([(255, 255, 255), (20, 30, 70), (40, 120, 30), (240, 200, 60)])
    image.save(texture)
    objects = [SceneObject(id=f"part{i}", label="Test part", kind="mesh", mesh_path="fixture.obj",
                           texture_path="fixture.png", position=(1+i*.5, 0, .3),
                           size=(.2, .2, .2) if i < 2 else (.3, .2, .4),
                           mass=.1*(i+1), movable=True, color=(.8, .2*(i+1), .3)) for i in range(3)]
    return WorldSpec(name="Shared-asset fixture", description="Test only", task="Move the parts",
                     bounds=(8, 6, 3), objects=objects,
                     rubric=[dict(id="near", description="Parts together", predicate="near",
                                  object_id="part0", target_id="part1", threshold=.1)])


def scene_xml(spec, asset_root):
    runtime = object.__new__(Runtime)
    runtime.spec, runtime.asset_root = spec, Path(asset_root)
    runtime._objects = {obj.id: obj for obj in spec.objects}
    root = ET.fromstring('<mujoco><option timestep=".002"/><asset/><worldbody>'
                         '<geom name="floor" type="plane" size="5 5 .1"/></worldbody></mujoco>')
    add_appearance(root)
    return ET.fromstring(runtime._append_objects(root))


def per_geom_assets(root):
    """Equivalent unshared baseline: one file asset/material per mesh geom."""
    root = copy.deepcopy(root)
    assets = root.find("asset")
    original = {(node.tag, node.get("name")): node for node in list(assets)}
    for geom in root.findall(".//geom[@mesh]"):
        mesh = copy.deepcopy(original["mesh", geom.get("mesh")])
        mesh.set("name", geom.get("name") + "_unique_mesh")
        assets.append(mesh)
        geom.set("mesh", mesh.get("name"))
        material = copy.deepcopy(original["material", geom.get("material")])
        if not material.get("texture"):
            continue
        texture = copy.deepcopy(original["texture", material.get("texture")])
        texture.set("name", geom.get("name") + "_unique_texture")
        material.set("name", geom.get("name") + "_unique_material")
        material.set("texture", texture.get("name"))
        assets.extend([texture, material])
        geom.set("material", material.get("name"))
    # Remove the now-unreferenced shared file assets, retaining the lighting palette.
    for node in list(assets):
        if node in original.values() and (node.tag == "mesh" or node.get("file")
                                          or (node.tag == "material" and node.get("texture", "").startswith("texture_"))):
            assets.remove(node)
    return root


def test_shared_sources_are_prepared_once_without_merging_physical_instances(tmp_path, monkeypatch):
    spec = repeated_mesh_scene(tmp_path)
    before = spec.model_dump()
    bounds, texture = mesh_assets.obj_bounds, mesh_assets.validate_texture
    calls = {"bounds": 0, "texture": 0}

    def read_bounds(path):
        calls["bounds"] += 1
        return bounds(path)

    def check_texture(path):
        calls["texture"] += 1
        return texture(path)

    monkeypatch.setattr(mesh_assets, "obj_bounds", read_bounds)
    monkeypatch.setattr(mesh_assets, "validate_texture", check_texture)
    root = scene_xml(spec, tmp_path)
    assert calls == {"bounds": 1, "texture": 1}
    assert len(root.findall("asset/mesh")) == 2  # Different scale needs its own native mesh.
    assert len(root.findall("asset/texture[@file]")) == 1
    geoms = [root.find(f"worldbody/body[@name='object_{obj.id}']/geom") for obj in spec.objects]
    assert geoms[0].get("mesh") == geoms[1].get("mesh") != geoms[2].get("mesh")
    assert len({geom.get("material") for geom in geoms}) == 1
    assert len(root.findall("worldbody/body/freejoint")) == 3
    for obj, geom in zip(spec.objects, geoms):
        assert float(geom.get("mass")) == obj.mass
        assert [float(v) for v in geom.get("rgba").split()][:3] == list(obj.color)
        assert geom.get("name") == "geom_" + obj.id
        assert geom.get("contype") == "1"
    assert spec.model_dump() == before


def test_reuse_is_per_build_and_observes_replaced_source_files(tmp_path):
    spec = repeated_mesh_scene(tmp_path)
    first = scene_xml(spec, tmp_path)
    mesh = tmp_path / "fixture.obj"
    mesh.write_text(mesh.read_text().replace("v 2 2 3", "v 3 2 3"))
    second = scene_xml(spec, tmp_path)
    assert first.find("asset/mesh").get("scale") != second.find("asset/mesh").get("scale")
    (tmp_path / "fixture.png").write_bytes(b"corrupt")
    with pytest.raises((ValueError, OSError)):
        scene_xml(spec, tmp_path)


def test_different_source_paths_do_not_share_assets_or_hide_missing_files(tmp_path):
    spec = repeated_mesh_scene(tmp_path)
    (tmp_path / "other.obj").write_bytes((tmp_path / "fixture.obj").read_bytes())
    (tmp_path / "other.png").write_bytes((tmp_path / "fixture.png").read_bytes())
    spec.objects[1].mesh_path = "other.obj"
    spec.objects[1].texture_path = "other.png"
    root = scene_xml(spec, tmp_path)
    assert len(root.findall("asset/mesh")) == 3
    assert len(root.findall("asset/texture[@file]")) == 2
    (tmp_path / "other.obj").unlink()
    with pytest.raises(ValueError, match="missing"):
        scene_xml(spec, tmp_path)


def test_unshared_baseline_preserves_untextured_transparent_materials(tmp_path):
    spec = repeated_mesh_scene(tmp_path)
    spec.objects[0].texture_path = None
    spec.objects[0].opacity = .3
    root = scene_xml(spec, tmp_path)
    separate = per_geom_assets(root)
    original = root.find("worldbody/body[@name='object_part0']/geom")
    baseline = separate.find("worldbody/body[@name='object_part0']/geom")
    assert baseline.get("material") == original.get("material")
    assert baseline.get("rgba") == original.get("rgba")
    assert len(separate.findall("asset/mesh")) == 3
    assert len(separate.findall("asset/texture[@file]")) == 2
