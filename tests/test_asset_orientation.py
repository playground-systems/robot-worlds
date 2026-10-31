"""Quarter-turn source placement: geometry/XML checks, no native simulation."""
import hashlib
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from test_asset_library import fixture_library
from test_generator import Client, valid
from test_mesh_assets import cube

from robot_worlds import World, load, load_prepared_asset
from robot_worlds.editing import apply_patch
from robot_worlds.generator import generate_spec, validate_spec
from robot_worlds.large_scene import expand_assemblies
from robot_worlds.models import SceneObject, WorldSpec


def rotation(turns):
    angle = turns*math.pi/2
    return np.array([[math.cos(angle), -math.sin(angle), 0],
                     [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_original_microwave_rotates_all_parts_joints_and_cavity_without_rewriting_source(turns):
    original = load_prepared_asset("nvidia-microwave079", prefix="oven").asset
    paths = {Path(o.mesh_path) for o in original.objects if o.mesh_path}
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    moved = load_prepared_asset("nvidia-microwave079", prefix="oven", position=(2, -1, .7),
                                quarter_turns=turns, joint_initials=[dict(name="microjoint", initial=-.4)]).asset
    r, offset = rotation(turns), np.array([2, -1, .7])
    for a, b in zip(original.objects, moved.objects):
        assert b.position == pytest.approx(r@a.position+offset)
        assert b.size == pytest.approx(np.abs(r)@a.size)
        assert b.mass == a.mass and b.friction == a.friction
        assert b.parent_id == a.parent_id and b.mesh_path == a.mesh_path
        if a.kind == "mesh":
            assert b.mesh_quarter_turns == turns
        if a.joint:
            assert b.joint.anchor == pytest.approx(r@a.joint.anchor+offset)
            assert b.joint.axis == pytest.approx(r@a.joint.axis)
            assert b.joint.range == a.joint.range
    assert moved.regions[0].position == pytest.approx(r@original.regions[0].position+offset)
    assert moved.regions[0].size == pytest.approx(np.abs(r)@original.regions[0].size)
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_native_xml_rotates_off_center_source_and_shares_mesh_without_compilation(tmp_path):
    from robot_worlds.runtime import Runtime
    vertices, faces = cube()
    vertices = vertices*[1, 2, 3]+[2, 3, 4]
    path = tmp_path / "fixture.obj"
    path.write_text("".join("v " + " ".join(map(str, row)) + "\n" for row in vertices)
                    + "".join("f " + " ".join(str(i+1) for i in row) + "\n" for row in faces))
    objects = [SceneObject(id=f"part{i}", label="Fixture", kind="mesh", mesh_path=str(path),
        mesh_quarter_turns=i, size=(.4, .1, .9) if i % 2 else (.1, .4, .9), position=(i, 0, 1)) for i in range(4)]
    raw = valid()
    raw["objects"] = [o.model_dump(mode="json") for o in objects]
    raw["rubric"] = [dict(id="present", description="Approach", predicate="robot_in_region", target_id=raw["regions"][0]["id"])]
    runtime = object.__new__(Runtime)  # Bypass robot construction, compilation, settling and rendering.
    runtime.spec = WorldSpec.model_validate(raw)
    runtime._objects = {o.id: o for o in objects}
    runtime.asset_root = tmp_path
    root = ET.fromstring('<mujoco><asset/><worldbody/></mujoco>')
    result = ET.fromstring(runtime._append_objects(root))
    assert len(result.findall('./asset/mesh')) == 1
    scale = np.fromstring(result.find('./asset/mesh').get('scale'), sep=' ')
    assert scale == pytest.approx([.1, .2, .3])
    for i, obj in enumerate(objects):
        geom = result.find(f'.//geom[@name="geom_{obj.id}"]')
        q = np.fromstring(geom.get('quat'), sep=' ')
        assert q == pytest.approx([math.cos(i*math.pi/4), 0, 0, math.sin(i*math.pi/4)])
        actual = (vertices*scale)@rotation(i).T+np.fromstring(geom.get('pos'), sep=' ')+obj.position
        assert actual.min(0) == pytest.approx(np.array(obj.position)-np.array(obj.size)/2)
        assert actual.max(0) == pytest.approx(np.array(obj.position)+np.array(obj.size)/2)


def test_rotated_library_generation_edit_and_export_keep_orientation(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda _: {"fixture": "no native robot loading"})
    library = fixture_library(tmp_path)
    plan = valid()
    plan["prepared_assets"] = [dict(id="station", asset_id="inspection-station", position=(-2, -2, 0), quarter_turns=1)]
    spec = generate_spec("Face the inspection station sideways", client=Client([plan]), asset_library=library)
    assert validate_spec(spec) == []
    meshes = [o for o in spec.objects if o.kind == "mesh"]
    assert meshes and all(o.mesh_quarter_turns == 1 for o in meshes)
    revised = apply_patch(spec, {"operations": [{"op": "add_prepared_asset", "asset": {
        "id": "second", "asset_id": "inspection-station", "position": (2, -2, 0), "quarter_turns": 3}}]}, asset_library=library)
    assert any(o.id.startswith('second_') and o.mesh_quarter_turns == 3 for o in revised.objects)
    world = World.from_spec(revised, tmp_path / "worlds", validate=False)
    assert world.export(tmp_path / "world.zip").is_file()
    restored = load(world.path)
    assert [o.mesh_quarter_turns for o in restored.spec.objects] == [o.mesh_quarter_turns for o in revised.objects]
    # Reusing the registered original must not inherit another instance's turn.
    fresh = load_prepared_asset("inspection-station", prefix="fresh", asset_library=library).asset
    assert all(o.mesh_quarter_turns == 0 for o in fresh.objects)


def test_mesh_assembly_turns_compose_and_expand_idempotently(tmp_path):
    raw = valid()
    raw['assembly_templates'] = [dict(id='source', label='Source fixture', parts=[dict(
        id='part', label='Part', kind='mesh', mesh_path=str(tmp_path/'not_loaded.obj'),
        mesh_quarter_turns=3, size=(2, 1, 3), position=(1, 0, 1.5))])]
    raw['assembly_instances'] = [dict(id='turned', template_id='source', position=(2, 2, 0), quarter_turns=1)]
    expanded = expand_assemblies(WorldSpec.model_validate(raw))
    part = next(o for o in expanded.objects if o.id == 'turned_0_0_0_part')
    assert part.mesh_quarter_turns == 0 and part.size == (1, 2, 3) and part.position == (2, 3, 1.5)
    assert expand_assemblies(expanded) == expanded


def test_task_bound_mesh_rotation_cannot_change_without_task_authorization(tmp_path):
    raw = valid()
    raw['objects'][0].update(kind='mesh', mesh_path=str(tmp_path/'not_loaded.obj'))
    spec = WorldSpec.model_validate(raw)
    patch = {'operations': [dict(op='update_object', id=spec.objects[0].id,
        fields=['mesh_quarter_turns'], values={'mesh_quarter_turns': 2})]}
    with pytest.raises(ValueError, match='task-bound object mesh_quarter_turns'):
        apply_patch(spec, patch)
    assert apply_patch(spec, patch, allow_task_changes=True).objects[0].mesh_quarter_turns == 2
    with pytest.raises(ValueError, match='only valid for mesh'):
        SceneObject(id='bad', label='Bad', kind='box', position=(0,0,0), size=(1,1,1), mesh_quarter_turns=1)
