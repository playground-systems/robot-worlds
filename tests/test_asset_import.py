import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from robot_worlds import World, import_mjcf_asset, load
from robot_worlds import asset_import as module
from robot_worlds.generator import validate_spec
from robot_worlds.models import SceneObject, WorldSpec

SOURCE = Path(module.__file__).parent / "assets/household/microwave079"


def imported_scene():
    asset = import_mjcf_asset(SOURCE / "model.xml", prefix="microwave", position=(2, 0, .8))
    spec = WorldSpec(name="Imported Microwave079", description="Original textured articulated asset",
                     task="Open the microwave door.", bounds=(8, 6, 3), objects=asset.objects,
                     regions=asset.regions, assumptions=asset.disclosures,
                     rubric=[dict(id="open_door", description="Door opened", predicate="joint_at",
                                  object_id=asset.joint_ids["microjoint"], threshold=-1.2)])
    return asset, spec


def test_import_keeps_original_visuals_collision_parts_joints_and_file_hashes():
    asset, spec = imported_scene()
    assert validate_spec(spec) == []
    assert len(asset.objects) == 20
    visuals = [o for o in asset.objects if not o.collision]
    assert len(visuals) == 4
    assert len([o for o in visuals if o.texture_path]) == 3
    assert all(o.parent_id and o.visible for o in visuals)
    assert all(not o.visible for o in asset.objects if o.collision)
    door = next(o for o in asset.objects if o.id == asset.joint_ids["microjoint"])
    assert door.joint.range == pytest.approx((-1.5707963267948966, 0))
    assert door.joint.anchor == pytest.approx((1.77153818, -.10996792, .80382642))
    assert door.joint.frictionloss == 1
    assert door.joint.armature == .01
    assert door.joint.stiffness == .05
    disc = next(o for o in asset.objects if o.id == asset.joint_ids["Disc001_joint"])
    assert disc.joint.limited is False
    assert asset.regions[0].interior_of == asset.body_ids["object"]
    for record in json.loads((SOURCE / "source-manifest.json").read_text())["files"]:
        data = (SOURCE / record["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]


def test_sdk_freezes_texture_and_attribution_and_export_survives_source_removal(tmp_path):
    local = tmp_path / "source"
    shutil.copytree(SOURCE, local)
    asset, spec = imported_scene()
    imported = import_mjcf_asset(local / "model.xml", prefix="microwave", position=(2, 0, .8))
    spec.objects = imported.objects
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    assert world.manifest["validation"]["status"] != "ready"
    shutil.rmtree(local)
    restored = load(world.path)
    textures = {o.texture_path for o in restored.spec.objects if o.texture_path}
    notices = {o.source_notice for o in restored.spec.objects}
    assert len(textures) == len(notices) == 1
    for path in textures | notices:
        assert path.startswith("assets/")
        assert path in restored.manifest["assets"]
    notice = (restored.path / next(iter(notices))).read_text()
    assert "CC BY 4.0" in notice and "Lightwheel" in notice
    with zipfile.ZipFile(restored.export(tmp_path / "world.zip")) as archive:
        assert textures | notices <= set(archive.namelist())
        archive.extractall(tmp_path / "restored")
    assert load(tmp_path / "restored").id == world.id
    (restored.path / next(iter(textures))).write_bytes(b"changed texture")
    with pytest.raises(ValueError, match="asset was modified"):
        load(restored.path)


@pytest.mark.parametrize("change", ["rotation", "freejoint", "missing_texture", "escape", "missing_notice"])
def test_unsupported_source_features_fail_instead_of_silently_flattening(tmp_path, change):
    local = tmp_path / "source"
    shutil.copytree(SOURCE, local)
    path = local / "model.xml"
    data = path.read_text()
    if change == "rotation":
        data = data.replace('<body name="door">', '<body name="door" euler="0 0 1">')
    elif change == "freejoint":
        data = data.replace('<body name="door">', '<body name="door"><freejoint/>')
    elif change == "missing_texture":
        (local / "visuals/T_BC001.png").unlink()
    elif change == "escape":
        data = data.replace('file="visuals/Body001.obj"', 'file="../outside.obj"')
        (tmp_path / "outside.obj").write_bytes((SOURCE / "visuals/Body001.obj").read_bytes())
    else:
        (local / "ATTRIBUTION.md").unlink()
    path.write_text(data)
    with pytest.raises(ValueError):
        import_mjcf_asset(path, prefix="microwave")


def test_appearance_only_geometry_cannot_be_a_free_physical_object():
    with pytest.raises(ValidationError, match="fixed children"):
        SceneObject(id="ghost", label="Ghost", kind="mesh", mesh_path="bowl.obj",
                    position=(0, 0, 1), size=(1, 1, 1), collision=False, movable=True)


def test_hidden_collider_requires_visible_geometry_and_filled_cavity_still_fails():
    asset, spec = imported_scene()
    spec.objects = [o for o in spec.objects if o.collision]
    assert any("hidden collision geometry" in error for error in validate_spec(spec))
    asset, spec = imported_scene()
    cavity = spec.regions[0]
    spec.objects.append(SceneObject(id="filled_cavity", label="Incorrect solid cavity", kind="box",
                                    parent_id=asset.body_ids["object"], position=cavity.position, size=cavity.size))
    assert any("usable cavity intersects solid" in error for error in validate_spec(spec))
