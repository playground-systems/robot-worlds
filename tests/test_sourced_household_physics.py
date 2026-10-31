"""Native original-asset checks on CI. No model request or autonomous placement."""
import pytest
from test_prepared_assets import prepared_plan
from test_sourced_household import sourced_scene

from robot_worlds import generate, load
from robot_worlds.models import SceneObject
from robot_worlds.rubric import measure_predicate
from robot_worlds.runtime import Runtime


def test_sphere_settles_in_scanned_bowl_not_on_a_filled_collision_hull():
    spec, bowl, _ = sourced_scene()
    visual = bowl.objects[-1]
    bottom = visual.position[2]-visual.size[2]/2
    spec.objects.append(SceneObject(id="marble", label="Cavity test sphere", kind="sphere", size=(.012,)*3,
                                   position=(1, 0, bottom+.05), mass=.002, movable=True))
    runtime = Runtime(spec, render=False)
    try:
        runtime._step(.2)
        state = runtime.snapshot()["objects"]
        current_bottom = state[visual.id]["position"][2]-visual.size[2]/2
        assert current_bottom > .595
        assert current_bottom+.009 < state["marble"]["position"][2] < current_bottom+.035
        assert set(state["marble"]["contacts"]) & {o.id for o in bowl.objects if o.collision}
        mass = sum(runtime.model.body_mass[runtime._object_bodies[o.id]] for o in bowl.objects)
        assert mass == pytest.approx(.6)
    finally:
        runtime.close()


def test_original_bowl_fits_and_stays_supported_inside_original_appliance():
    spec, _, _ = sourced_scene(inside=True)
    runtime = Runtime(spec, render=False)
    try:
        for _ in range(3):
            runtime._step(.2)
            for criterion in spec.rubric:
                result = measure_predicate(spec, criterion, runtime.snapshot())
                assert result["satisfied"] is True, (criterion.id, result)
    finally:
        runtime.close()


def test_sdk_runs_original_assets_from_mocked_generation_through_export(tmp_path, native_rendering):
    class MockModel:
        def complete_json(self, *args):
            return prepared_plan()

    class NoOp:
        def reset(self, context):
            pass

        def act(self, observation):
            return {"tool": "finish", "arguments": {}}

    world = generate("Put the porcelain bowl inside the microwave", client=MockModel(), max_repairs=0, output=tmp_path / "worlds")
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    world = load(world.path)
    report = world.evaluate(NoOp(), max_steps=1, output=tmp_path / "runs")
    assert report.summary["valid"] == 1
    assert report.summary["successes"] == 0  # A no-op does not place the bowl.
    assert report.regrade(world, output=tmp_path / "regraded").summary["successes"] == 0
    assert world.export(tmp_path / "world.zip").is_file()
