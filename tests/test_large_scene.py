"""Generated assembly expansion and real large-world runtime boundaries."""
import random
import time

import pytest

from robot_worlds.generator import _intersects, generate_spec, validate_spec
from robot_worlds.large_scene import candidate_pairs, expand_assemblies
from robot_worlds.models import SceneObject, WorldSpec


def box(oid, position, size, **kwargs):
    return {"id": oid, "label": oid, "kind": "box", "position": position, "size": size, **kwargs}


def large_spec():
    # This is an internal engineering fixture, not a user-facing default scene
    # or a bundled controller. Every production template comes from the model.
    rack = [box(f"leg{i}", [x, y, .8], [.1, .1, 1.6])
            for i, (x, y) in enumerate([(-.95, -.45), (-.95, .45), (.95, -.45), (.95, .45)])]
    rack += [box("shelf0", [0, 0, .2], [1.8, .8, .08]),
             box("shelf1", [0, 0, .8], [1.8, .8, .08]),
             box("top", [0, 0, 1.64], [2, 1, .08])]
    table = [box(f"leg{i}", [x, y, .4], [.1, .1, .8])
             for i, (x, y) in enumerate([(-.7, -.35), (-.7, .35), (.7, -.35), (.7, .35)])]
    table += [box("top", [0, 0, .85], [1.6, .9, .1])]
    bin_parts = [box("base", [0, 0, .05], [.8, .6, .1]),
                 box("left", [-.425, 0, .3], [.05, .7, .6]),
                 box("right", [.425, 0, .3], [.05, .7, .6]),
                 box("front", [0, -.325, .3], [.8, .05, .6]),
                 box("back", [0, .325, .3], [.8, .05, .6])]
    return WorldSpec.model_validate({
        "name": "Large physical engineering fixture", "description": "Connected storage, workbench and container zones",
        "task": "Carry the part from receiving to the nearby output region", "bounds": [100, 60, 5],
        "objects": [box("workpiece", [2, 0, .1], [.2, .2, .2], movable=True)],
        "assembly_templates": [{"id": "rack", "label": "rack", "parts": rack},
                               {"id": "bench", "label": "workbench", "parts": table},
                               {"id": "bin", "label": "bin", "parts": bin_parts}],
        "assembly_instances": [
            {"id": "storage", "template_id": "rack", "position": [-42, -24, 0], "repeat": [8, 5, 1], "spacing": [12, 4, 0]},
            {"id": "assembly", "template_id": "bench", "position": [-42, 4, 0], "repeat": [8, 5, 1], "spacing": [12, 4, 0]},
            {"id": "receiving", "template_id": "bin", "position": [-42, 26, 0], "repeat": [8, 2, 1], "spacing": [12, 2, 0]},
        ],
        "regions": [{"id": "output", "label": "output", "position": [5, 0, .2], "size": [1, 1, .4]}],
        "rubric": [{"id": "deliver", "description": "Deliver exact workpiece", "predicate": "in_region", "object_id": "workpiece", "target_id": "output"}],
    })


def test_expansion_materializes_561_unique_physical_objects_idempotently():
    original = large_spec()
    expanded = expand_assemblies(original)
    assert len(original.objects) == 1
    assert len(expanded.objects) == 561
    assert len({o.id for o in expanded.objects}) == 561
    assert next(o for o in expanded.objects if o.id == "storage_7_4_0_top").position == (42, -8, 1.64)
    assert expand_assemblies(expanded) == expanded
    assert validate_spec(expanded) == []


def test_assembly_budget_checked_before_large_expansion():
    spec = large_spec()
    with pytest.raises(ValueError, match="budget"):
        expand_assemblies(spec, max_objects=100)
    spec.assembly_instances[0].repeat = (1000, 1000, 1000)
    with pytest.raises(ValueError, match="budget"):
        expand_assemblies(spec)


def test_local_parent_joint_axis_and_anchor_rotate_to_persistent_world_ids():
    data = large_spec().model_dump()
    data["assembly_templates"] = [{"id": "mechanism", "label": "door", "parts": [
        box("frame", [0, 0, 1], [.1, 1, 2]),
        box("panel", [.1, 0, 1], [.1, 1, 2], parent_id="frame", joint={"type": "hinge", "axis": [1, 0, 0], "range": [-1, 1], "anchor": [0, .5, 1]})]}]
    data["assembly_instances"] = [{"id": "d", "template_id": "mechanism", "position": [5, 3, 0], "quarter_turns": 1}]
    expanded = expand_assemblies(WorldSpec.model_validate(data))
    panel = next(o for o in expanded.objects if o.id == "d_0_0_0_panel")
    assert panel.parent_id == "d_0_0_0_frame"
    assert panel.position == (5, 3.1, 1)
    assert panel.size == (1, .1, 2)
    assert panel.joint.axis == (0, 1, 0)
    assert panel.joint.anchor == (4.5, 3, 1)


def test_stale_materialized_geometry_cannot_override_template():
    expanded = expand_assemblies(large_spec())
    expanded.objects[1].position = (0, 0, 0)
    with pytest.raises(ValueError, match="conflicts"):
        expand_assemblies(expanded)


def test_broadphase_contains_every_exact_overlap_and_oversized_geometry():
    rng = random.Random(31)
    objects = [SceneObject.model_validate(box(f"o{i}", [rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(0, 3)], [rng.uniform(.1, 2) for _ in range(3)])) for i in range(80)]
    objects.append(SceneObject.model_validate(box("large", [0, 0, 0], [100, 100, 100])))
    actual = {frozenset((a.id, b.id)) for a, b in candidate_pairs(objects)}
    expected = {frozenset((a.id, b.id)) for i, a in enumerate(objects) for b in objects[i+1:] if _intersects(a, b)}
    assert expected <= actual
    assert len(actual) < len(objects)*(len(objects)-1)//2


def test_model_generated_compact_assemblies_expand_before_validation():
    class Client:
        def complete_json(self, instructions, prompt, schema):
            assert "100m by 60m" in prompt
            assert "assembly_templates" in instructions
            return large_spec().model_dump(mode="json")
    spec = generate_spec("A large connected facility", scene_profile="large", client=Client(), max_repairs=0)
    assert len(spec.objects) == 561
    assert spec.source_prompt == "A large connected facility"


def test_real_mujoco_compiles_renders_and_steps_561_geometry_scene(native_rendering):
    from robot_worlds.runtime import Runtime
    start = time.monotonic()
    spec = expand_assemblies(large_spec())
    runtime = Runtime(spec, render=True, max_sim_seconds=.02)
    try:
        image = runtime.render(view="overview", distance=100, lookat=[0, 0, 0])
        assert image.startswith(b"\x89PNG")
        assert runtime.model.ngeom >= 561
        result = runtime.execute({"tool": "wait", "arguments": {"seconds": .02}})
        assert result["status"] in {"executed", "time_limit"}
        assert 0 < runtime.snapshot()["time"] <= .020001
        print(f"561 scene objects / {runtime.model.ngeom} native geoms; compile+reset+overview+step={time.monotonic()-start:.3f}s")
    finally:
        runtime.close()


def test_explicit_minimum_count_triggers_repair_without_prescribing_layout():
    small = large_spec().model_dump(mode="json")
    small["assembly_templates"] = []
    small["assembly_instances"] = []
    class Client:
        def __init__(self):
            self.prompts = []
        def complete_json(self, instructions, prompt, schema):
            self.prompts.append(prompt)
            return small if len(self.prompts) == 1 else large_spec().model_dump(mode="json")
    client = Client()
    result = generate_spec("A large facility", client=client, min_objects=500, max_repairs=1)
    assert len(result.objects) == 561
    assert "explicit minimum is 500" in client.prompts[1]
