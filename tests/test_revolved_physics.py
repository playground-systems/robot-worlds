"""Native curved-cavity checks; no model calls and no autonomous policy trial."""
import io

import pytest
from PIL import Image
from test_appliance_generation import appliance_spec
from test_revolved import bowl

from robot_worlds import generate, load
from robot_worlds.models import SceneObject
from robot_worlds.runtime import Runtime


def curved_scene():
    spec = appliance_spec()
    spec.objects = [o for o in spec.objects if not o.id.startswith("bowl")]+[bowl()]
    spec.objects.append(SceneObject(id="marble", label="Small sphere inside bowl", kind="sphere",
                                   position=(1, 0, .63), size=(.012, .012, .012), movable=True, mass=.002))
    return spec


def test_small_sphere_settles_inside_revolved_bowl_without_filled_hull():
    runtime = Runtime(curved_scene(), render=False)
    try:
        state = runtime.snapshot()
        assert state["objects"]["marble"]["position"][2] < .63
        assert state["objects"]["marble"]["position"][2] > .60
        assert "bowl" in state["objects"]["marble"]["contacts"]
        assert runtime.model.body_mass[runtime._object_bodies["bowl"]] == pytest.approx(.08)
        assert sum(oid == "bowl" for oid in runtime._geom_objects.values()) == 128
        runtime.execute({"tool": "wait", "arguments": {"seconds": .2}})
        assert runtime.snapshot()["objects"]["marble"]["position"][2] < .63
    finally:
        runtime.close()


def test_generated_curved_appliance_round_trips_and_renders(tmp_path, native_rendering):
    class MockGeneration:
        def complete_json(self, *args):
            return curved_scene().model_dump(mode="json")

    world = generate("A hollow curved bowl beside a microwave", client=MockGeneration(),
                     max_repairs=0, output=tmp_path / "worlds")
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    restored = load(world.path)
    assert next(o for o in restored.spec.objects if o.id == "bowl").revolved.segments == 32
    runtime = Runtime(restored.spec, asset_root=restored.path)
    try:
        with Image.open(io.BytesIO(runtime.render(view="overview"))) as image:
            assert image.size == (960, 720)
            assert len(set(image.resize((40, 30)).getdata())) > 20
    finally:
        runtime.close()
