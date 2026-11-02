"""Source appearance persistence/XML checks without native rendering or physics."""
import hashlib
import shutil
import xml.etree.ElementTree as ET

import pytest
from PIL import Image
from test_asset_library import fixture_import
from test_generator import valid

from robot_worlds import AssetLibrary, World, load
from robot_worlds.appearance import PBR_RENDERER_NOTICE, add_source_material
from robot_worlds.editing import apply_patch
from robot_worlds.models import TEXTURE_FIELDS, SceneObject, SurfaceMaterial, WorldSpec


def material_asset(tmp_path):
    sourced = fixture_import(tmp_path)
    for i, field in enumerate(TEXTURE_FIELDS):
        path = tmp_path / "source" / f"{field}.png"
        Image.new("RGBA", (2, 2), (40+i*30, 80, 120, 255)).save(path)
        sourced.asset.source_files[field] = hashlib.sha256(path.read_bytes()).hexdigest()
        for obj in sourced.asset.objects:
            if obj.kind == "mesh":
                obj.surface = SurfaceMaterial(metallic=.8, roughness=.25, emissive=(.1, .2, .3), normal_scale=(1, -1), double_sided=True)
                setattr(obj, field, str(path))
    return sourced


def test_material_maps_survive_library_freeze_export_and_source_removal(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda _: {"fixture": "no native robot load"})
    library = AssetLibrary().add("fixture", material_asset(tmp_path), description="Synthetic material fixture")
    library.save(tmp_path / "library")
    shutil.rmtree(tmp_path / "source")
    restored = AssetLibrary.open(tmp_path / "library").load("fixture", prefix="fixture").asset
    spec = WorldSpec.model_validate(valid())
    spec.objects.extend(restored.objects)
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    assert PBR_RENDERER_NOTICE in world.spec.assumptions
    for obj in world.spec.objects:
        if obj.surface:
            assert obj.surface.normal_scale == (1, -1)
            for field in TEXTURE_FIELDS:
                assert getattr(obj, field).startswith("assets/")
                assert (world.path / getattr(obj, field)).is_file()
    world.export(tmp_path / "world.zip")
    shutil.rmtree(tmp_path / "library")
    assert load(world.path).id == world.id
    normal = next(obj.normal_texture_path for obj in world.spec.objects if obj.surface)
    (world.path / normal).write_bytes(b"changed")
    with pytest.raises(ValueError, match="asset was modified"):
        load(world.path)


def test_library_rejects_changed_material_maps(tmp_path):
    sourced = material_asset(tmp_path)
    (tmp_path / "source/orm_texture_path.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed since import"):
        AssetLibrary().add("fixture", sourced, description="Synthetic material fixture")


def test_native_material_xml_preserves_roles_and_reuses_sources_without_dynamics_changes(tmp_path):
    asset = material_asset(tmp_path).asset
    obj = next(obj for obj in asset.objects if obj.kind == "mesh")
    original = obj.model_dump()
    root = ET.Element("mujoco")
    textures, materials = {}, {}
    name = add_source_material(root, obj, tmp_path, textures, materials)
    assert add_source_material(root, obj, tmp_path, textures, materials) == name
    material = root.find("asset/material")
    assert material.get("metallic") == "0.8" and material.get("roughness") == "0.25"
    assert {layer.get("role") for layer in material.findall("layer")} == {"rgb", "rgba", "normal", "orm", "emissive"}
    assert len(root.findall("asset/texture")) == 5
    layers = {layer.get("role"): layer.get("texture") for layer in material.findall("layer")}
    source_textures = {texture.get("name"): texture for texture in root.findall("asset/texture")}
    rgb, rgba = (source_textures[layers[role]] for role in ("rgb", "rgba"))
    assert rgb.get("file") == rgba.get("file") == obj.texture_path
    assert rgb.get("nchannel") == "3" and rgba.get("nchannel") == "4"
    assert len(root.findall("asset/material")) == 1
    changed = obj.model_copy(deep=True)
    changed.surface.roughness = .8
    assert add_source_material(root, changed, tmp_path, textures, materials) != name
    assert len(root.findall("asset/texture")) == 5
    assert obj.model_dump() == original
    assert root.find("worldbody") is None  # Only appearance assets were authored.


@pytest.mark.parametrize("change", [
    {"surface": {"metallic": 2}}, {"surface": {"roughness": -1}},
    {"normal_texture_path": "normal.png"},
    {"kind": "box", "mesh_path": None, "surface": {}},
    {"surface": {"normal_scale": [float("nan"), 1]}},
])
def test_invalid_surface_contract_rejected(change):
    raw = dict(id="fixture", label="Fixture", kind="mesh", mesh_path="test.obj", position=[0, 0, 1], size=[1, 1, 1])
    raw.update(change)
    with pytest.raises(ValueError):
        SceneObject.model_validate(raw)


def test_targeted_surface_edit_preserves_mass_and_friction(tmp_path):
    asset = material_asset(tmp_path).asset
    spec = WorldSpec.model_validate(valid())
    spec.objects.extend(asset.objects)
    obj = next(obj for obj in spec.objects if obj.surface)
    original = obj.model_dump()
    edited = apply_patch(spec, {"operations": [{"op": "update_object", "id": obj.id,
        "fields": ["surface"], "values": {"surface": {"metallic": .2, "roughness": .4}}}]})
    updated = next(item for item in edited.objects if item.id == obj.id)
    assert updated.surface.metallic == .2
    assert updated.mass == original["mass"] and updated.friction == original["friction"]
    assert updated.model_dump(exclude={"surface"}) == obj.model_dump(exclude={"surface"})
