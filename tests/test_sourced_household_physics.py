"""Native original-asset checks on CI. No model request or autonomous placement."""
import pytest
from test_prepared_assets import prepared_plan
from test_sourced_household import sourced_scene

from robot_worlds import generate, load
from robot_worlds.models import SceneObject
from robot_worlds.rubric import measure_predicate
from robot_worlds.runtime import Runtime
from robot_worlds.sensors import local_point


def test_original_bowl_arm_search_does_not_overflow_contact_solver():
    # This source bowl has 158 convex pieces. Full forward dynamics during
    # random IK candidates overflowed MuJoCo's arena on this unreachable pose.
    spec, bowl, _ = sourced_scene()
    spec.robot_start = (.35, .25, 0)
    spec.objects[0].size = (.4, .5, .6)
    for obj in bowl.objects:
        obj.position = (obj.position[0]-.16, *obj.position[1:])
    runtime = Runtime(spec, render=False, max_sim_seconds=15)
    try:
        runtime.execute({'tool': 'gripper', 'arguments': {'hand': 'right', 'opening': .045}})
        start = float(runtime.data.time)
        before = runtime.data.qpos.copy()
        result = runtime.execute({'tool': 'move_arm', 'arguments': {
            'hand': 'right', 'position': local_point([.755, 0, .85], *runtime._base_transform()),
            'approach': 'forward'}})
        assert result['status'] == 'unreachable'
        assert runtime.data.time == start
        assert runtime.data.qpos == pytest.approx(before)
    finally:
        runtime.close()


def test_original_bowl_lifts_with_two_hands_and_public_torso_actuation():
    spec, bowl, _ = sourced_scene()
    spec.robot_start = (.35, 0, 0)
    spec.objects[0].size = (.4, .5, .6)
    members = {obj.id for obj in bowl.objects}
    for obj in spec.objects:
        if obj.id in members:
            obj.position = (obj.position[0]-.1, *obj.position[1:])
    runtime = Runtime(spec, render=False, max_sim_seconds=60)
    try:
        def move(hand, target):
            result = runtime.execute({'tool': 'move_arm', 'arguments': {
                'hand': hand, 'position': local_point(target, *runtime._base_transform()),
                'approach': 'down'}})
            assert result['status'] == 'reached', result

        def grip(hand, opening):
            return runtime.execute({'tool': 'gripper', 'arguments': {'hand': hand, 'opening': opening}})

        # Privileged fixed-fixture engineering sequence, not a sensor-only policy.
        # Poses are commanded through the same public actuator interface.
        grip('left', .045)
        move('left', [.9, .094, .8])
        grip('right', .045)
        move('right', [.9, -.094, .8])
        move('left', [.9, .094, .655])
        grip('left', 0)
        move('right', [.9, -.094, .655])
        grip('right', 0)
        before = runtime.snapshot()['objects']
        result = runtime.execute({'tool': 'move_torso', 'arguments': {'extension': .345}})
        assert result['status'] == 'reached', result
        assert result['measured_extension_m'] == pytest.approx(.345, abs=.01)
        runtime.execute({'tool': 'wait', 'arguments': {'seconds': 1}})
        after = runtime.snapshot()['objects']
        # Every attached bowl piece must rise, and none may retain counter support.
        assert min(after[oid]['position'][2]-before[oid]['position'][2] for oid in members) > .15
        assert all('source_counter' not in after[oid]['contacts'] for oid in members)
        assert after[bowl.body_ids['object']]['held_by'] in {'left', 'right'}
    finally:
        runtime.close()


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
