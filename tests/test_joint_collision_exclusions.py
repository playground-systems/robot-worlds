"""Precise source joint policy; native coverage uses only a tiny primitive model."""
import xml.etree.ElementTree as ET
from itertools import product

import mujoco
import pytest

from robot_worlds.appearance import add_appearance
from robot_worlds.asset_import import import_mjcf_asset
from robot_worlds.generator import validate_spec
from robot_worlds.joint_collisions import MAX_JOINT_COLLISION_EXCLUSIONS, joint_collision_exclusions
from robot_worlds.models import JointSpec, SceneObject, WorldSpec
from robot_worlds.runtime import Runtime


def part(identifier, *, parent=None, joint=False, collide=True, position=(2, 0, 1), movable=False):
    mechanism = (JointSpec(type="slide", axis=(0, 1, 0), anchor=position, range=(0, 1),
                          collide_parent=collide) if joint else None)
    return SceneObject(id=identifier, label=identifier, kind="box", position=position,
                       size=(.1, .1, .1), parent_id=parent, joint=mechanism, movable=movable)


def grouped_objects():
    return [
        part("frame"), part("rail", parent="frame"), part("mount", parent="rail"),
        part("drawer_a", parent="mount", joint=True, collide=False), part("lip_a", parent="drawer_a"),
        part("latch", parent="lip_a", joint=True), part("knob", parent="latch"),
        part("drawer_b", parent="frame", joint=True, collide=False), part("lip_b", parent="drawer_b"),
        part("workpiece", movable=True), part("unrelated_fixture"),
        SceneObject(id="visual", label="Paint", kind="box", position=(2, 0, 1),
                    size=(.1, .1, .002), parent_id="frame", collision=False),
    ]


def pairs(left, right):
    return {tuple(sorted(pair)) for pair in product(left, right)}


def xml(spec, tmp_path, *, robot=False):
    runtime = object.__new__(Runtime)
    runtime.spec, runtime.asset_root = spec, tmp_path
    runtime._objects = {obj.id: obj for obj in spec.objects}
    root = ET.fromstring('<mujoco><option gravity="0 0 0"/><asset/><worldbody/></mujoco>')
    if robot:
        body = ET.SubElement(root.find("worldbody"), "body", name="robot", pos="2.5 .05 1")
        ET.SubElement(body, "freejoint")
        ET.SubElement(body, "geom", name="robot_geom", type="box", size=".05 .05 .05", mass="1")
    add_appearance(root)
    return ET.fromstring(runtime._append_objects(root))


def scene(objects):
    return WorldSpec(name="Joint contact fixture", description="Engineering test only", task="Open a drawer",
                     bounds=(8, 8, 3), objects=objects,
                     rubric=[dict(id="open", description="Open", predicate="joint_at", object_id="drawer_a", threshold=.8)])


def test_default_is_backward_compatible_and_false_survives_model_serialization():
    joint = JointSpec(type="slide", axis=(0, 1, 0), anchor=(2, 0, 1), range=(0, 1))
    assert joint.collide_parent is True
    joint.collide_parent = False
    assert JointSpec.model_validate(joint.model_dump(mode="json")).collide_parent is False


def test_groups_include_welded_ancestors_siblings_and_descendants_but_stop_at_other_joints():
    objects = grouped_objects()
    original = [obj.model_dump() for obj in objects]
    expected = pairs(["frame", "rail", "mount"], ["drawer_a", "lip_a", "drawer_b", "lip_b"])
    result = joint_collision_exclusions(objects)
    assert set(result) == expected
    assert result == sorted(expected)
    assert all("latch" not in pair and "knob" not in pair and "workpiece" not in pair
               and "visual" not in pair and "unrelated_fixture" not in pair for pair in result)
    assert ("drawer_a", "drawer_b") not in result
    assert [obj.model_dump() for obj in objects] == original
    assert joint_collision_exclusions(list(reversed(objects))) == result


def test_nested_joint_excludes_only_its_adjacent_rigid_groups():
    objects = grouped_objects()
    next(obj for obj in objects if obj.id == "latch").joint.collide_parent = False
    result = set(joint_collision_exclusions(objects))
    assert pairs(["drawer_a", "lip_a"], ["latch", "knob"]) <= result
    assert not pairs(["frame", "rail", "mount"], ["latch", "knob"]) & result
    assert ("drawer_b", "latch") not in result


def test_budget_rejects_instead_of_truncating_and_default_bound_is_forty_thousand():
    objects = grouped_objects()
    assert len(joint_collision_exclusions(objects, max_pairs=12)) == 12
    with pytest.raises(ValueError, match="exceed the 11 body-pair budget"):
        joint_collision_exclusions(objects, max_pairs=11)
    assert MAX_JOINT_COLLISION_EXCLUSIONS == 40_000
    large = [part("frame"), part("drawer_a", parent="frame", joint=True, collide=False)]
    large += [part(f"fixed{i}", parent="frame") for i in range(200)]
    large += [part(f"moving{i}", parent="drawer_a") for i in range(199)]
    with pytest.raises(ValueError, match="40000 body-pair budget; no contacts were discarded"):
        joint_collision_exclusions(large)


@pytest.mark.parametrize("parent", [None, "missing"])
def test_policy_never_treats_implicit_world_as_a_blanket_parent_group(parent):
    with pytest.raises(ValueError, match="requires an explicit scene parent"):
        joint_collision_exclusions([part("drawer_a", parent=parent, joint=True, collide=False)])


def test_policy_rejects_cyclic_parents():
    objects = [part("drawer_a", parent="rail", joint=True, collide=False), part("rail", parent="drawer_a")]
    with pytest.raises(ValueError, match="Cyclic joint parent"):
        joint_collision_exclusions(objects)


def test_xml_excludes_exact_body_pairs_and_keeps_contact_masks(tmp_path):
    spec = scene(grouped_objects())
    root = xml(spec, tmp_path, robot=True)
    actual = {tuple(sorted((node.get("body1").removeprefix("object_"), node.get("body2").removeprefix("object_"))))
              for node in root.findall("contact/exclude")}
    assert actual == set(joint_collision_exclusions(spec.objects))
    assert root.find("option/flag") is None
    for obj in spec.objects:
        geom = root.find(f".//geom[@name='geom_{obj.id}']")
        assert geom.get("contype") == ("1" if obj.collision else "0")
        assert geom.get("conaffinity") == ("7" if obj.collision else "0")
    for obj in spec.objects:
        if obj.joint:
            obj.joint.collide_parent = True
    assert xml(spec, tmp_path).find("contact") is None


def overlap_scene():
    return scene([
        part("frame", position=(2, 0, 1)),
        part("rail", parent="frame", position=(2.5, .05, 1)),
        part("drawer_a", parent="frame", joint=True, collide=False, position=(2.5, 0, 1)),
        part("lip_a", parent="drawer_a", position=(2.5, .05, 1)),
    ])


def test_static_validation_uses_same_policy_without_hiding_drawer_workpiece_contacts():
    spec = overlap_scene()
    assert validate_spec(spec) == []
    spec.objects[2].joint.collide_parent = True
    assert any("rail" in error and "lip_a" in error and "overlap" in error for error in validate_spec(spec))
    spec.objects[2].joint.collide_parent = False
    spec.objects += [part("drawer_b", parent="frame", joint=True, collide=False, position=(2.5, .05, 1)),
                     part("workpiece", movable=True, position=(2.5, .05, 1))]
    errors = validate_spec(spec)
    assert any("drawer_a" in error and "drawer_b" in error and "overlap" in error for error in errors)
    assert any("workpiece" in error and "lip_a" in error and "overlap" in error for error in errors)


def native_contacts(root):
    model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return {tuple(sorted((model.geom(contact.geom1).name, model.geom(contact.geom2).name)))
            for contact in data.contact[:data.ncon]}


def test_tiny_native_model_filters_joint_groups_but_preserves_other_drawer_robot_and_workpiece(tmp_path):
    spec = overlap_scene()
    spec.objects += [part("drawer_b", parent="frame", joint=True, collide=False, position=(2.5, .05, 1)),
                     part("workpiece", movable=True, position=(2.5, .05, 1))]
    contacts = native_contacts(xml(spec, tmp_path, robot=True))
    assert ("geom_lip_a", "geom_rail") not in contacts
    assert ("geom_drawer_b", "geom_rail") not in contacts
    assert ("geom_drawer_b", "geom_lip_a") in contacts
    assert ("geom_lip_a", "geom_workpiece") in contacts
    assert ("geom_lip_a", "robot_geom") in contacts
    spec.objects[2].joint.collide_parent = True
    unfiltered = native_contacts(xml(spec, tmp_path, robot=True))
    assert ("geom_lip_a", "geom_rail") in unfiltered


def test_mjcf_import_preserves_explicit_adjacent_source_body_exclusion(tmp_path):
    (tmp_path / "ATTRIBUTION.md").write_text("Synthetic import regression fixture, no production asset")
    path = tmp_path / "source.xml"
    path.write_text('''<mujoco><compiler angle="radian"/><worldbody>
      <body name="frame"><geom name="frame1" type="box" size=".1 .1 .1"/>
        <geom name="frame2" type="box" size=".1 .1 .1" pos=".3 0 0"/>
        <body name="drawer"><joint name="slide" type="slide" axis="0 1 0" range="0 1"/>
          <geom name="drawer1" type="box" size=".1 .1 .1"/>
          <geom name="drawer2" type="box" size=".1 .1 .1" pos=".3 0 0"/>
        </body>
      </body></worldbody><contact><exclude body1="frame" body2="drawer"/></contact></mujoco>''')
    asset = import_mjcf_asset(path, prefix="fixture")
    drawer = next(obj for obj in asset.objects if obj.id == asset.joint_ids["slide"])
    assert drawer.joint.collide_parent is False
    assert len(joint_collision_exclusions(asset.objects)) == 4
    path.write_text(path.read_text().replace('body2="drawer"', 'body2="missing"'))
    with pytest.raises(ValueError, match="adjacent articulated parent/child"):
        import_mjcf_asset(path, prefix="fixture")
