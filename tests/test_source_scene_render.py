"""No native process is needed to check lossless texture sharing."""
import copy
import xml.etree.ElementTree as ET

import pytest

from robot_worlds.source_scene_render import share_identical_textures


def test_preview_shares_only_identical_file_textures_and_preserves_physics():
    root = ET.fromstring('''<mujoco><asset>
      <texture name="first" type="2d" file="wood.png"/>
      <texture type="2d" name="alias" file="wood.png"/>
      <texture name="flip" type="2d" file="wood.png" vflip="true"/>
      <texture name="cube" type="cube" file="wood.png"/>
      <texture name="generated" type="2d" builtin="checker"/>
      <texture name="generated2" type="2d" builtin="checker"/>
      <texture type="skybox" builtin="gradient"/>
      <material name="wood" texture="alias" rgba="1 .8 .7 1" specular=".2"/>
      <material name="layered"><layer role="rgb" texture="alias"/></material>
      </asset><worldbody><body name="drawer"><joint type="slide"/>
      <geom type="box" material="wood" size=".4 .3 .1"/></body></worldbody></mujoco>''')
    physics = ET.tostring(root.find("worldbody"))
    original = copy.deepcopy(root)
    assert share_identical_textures(root) == {"alias": "first"}
    assert root.find("./asset/material").attrib == {"name": "wood", "texture": "first", "rgba": "1 .8 .7 1", "specular": ".2"}
    assert root.find(".//layer").get("texture") == "first"
    assert [n.get("name") for n in root.findall("./asset/texture")] == ["first", "flip", "cube", "generated", "generated2", None]
    assert ET.tostring(root.find("worldbody")) == physics
    assert len(original.findall("./asset/texture")) == 7
    assert share_identical_textures(root) == {}


def test_ambiguous_texture_names_fail_before_hiding_a_source_error():
    root = ET.fromstring('<mujoco><asset><texture name="t" file="a.png"/><texture name="t" file="a.png"/></asset></mujoco>')
    before = ET.tostring(root)
    with pytest.raises(ValueError, match="unique"):
        share_identical_textures(root)
    assert ET.tostring(root) == before
