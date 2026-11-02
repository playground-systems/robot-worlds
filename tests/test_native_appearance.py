"""Display changes preserve authored geometry and do not invent physical materials."""
import xml.etree.ElementTree as ET

from robot_worlds.appearance import NOTICE, add_appearance, finish_name
from robot_worlds.models import SceneObject


def test_lighting_and_materials_preserve_physical_xml():
    root = ET.fromstring('<mujoco><asset/><worldbody><body name="part" pos="1 2 3">'
                         '<joint type="free"/><geom type="box" size=".1 .2 .3" mass=".2" '
                         'friction=".6 .01 .001" rgba=".8 .1 .1 1"/></body></worldbody></mujoco>')
    before = ET.tostring(root.find("worldbody/body"))
    add_appearance(root)
    assert ET.tostring(root.find("worldbody/body")) == before
    assert len(root.findall("worldbody/light")) == 3
    assert len(root.findall("asset/material")) == 9
    assert root.find("asset/texture[@name='rw_floor_texture']").get("builtin") == "checker"
    assert "not measured" in NOTICE


def test_name_estimates_preserve_color_and_distinguish_paint_from_bare_metal():
    obj = SceneObject(id="steel_bench", label="steel workbench", kind="box",
                      position=(0, 0, .5), size=(1, 1, .1), color=(.5, .5, .5))
    before = obj.model_dump()
    assert finish_name(obj) == "metal"
    assert obj.model_dump() == before
    assert finish_name(obj.model_copy(update={"color": (.1, .2, .8)})) == "painted"
    assert finish_name(obj.model_copy(update={"label": "rubber pad"})) == "rubber"
