"""Pure arena planning/XML checks; these never compile or step a native model."""
import xml.etree.ElementTree as ET

import pytest

from robot_worlds.appearance import add_appearance
from robot_worlds.models import WorldSpec
from robot_worlds.runtime import (
    Runtime,
    _configure_scene_arena,
    _scene_arena_bytes,
)

MIB = 1024 * 1024


def test_bookstore_collision_stack_failure_gets_bounded_headroom():
    # Hosted failure 41484443711 requested this first pair array before any
    # contacts existed; the default 18 MiB arena could not allocate it.
    bodies, observed_pair_bytes = 3318, 22_011_612
    assert observed_pair_bytes == 4 * bodies * (bodies - 1) // 2
    allocation = _scene_arena_bytes(bodies)
    assert allocation == 128 * MIB
    assert allocation > 2 * observed_pair_bytes + 32 * MIB


def test_hospital_preflight_failure_fits_without_allocating_a_blanket_gibibyte():
    # Hosted run 41487009435 converted all 9,198 components, then correctly
    # stopped at the old 512 MiB guard before making any native state.
    allocation = _scene_arena_bytes(9273)
    assert allocation == 688 * MIB
    assert allocation < 1024 * MIB
    # The supported full object budget plus this robot's bodies also fits.
    assert _scene_arena_bytes(10_000 + 75) == 816 * MIB


def test_small_scene_defaults_and_curved_contact_minimum_are_preserved():
    assert _scene_arena_bytes(75) is None
    root = ET.fromstring('<mujoco><option jacobian="dense"/><size nstack="2048" nconmax="4096"/></mujoco>')
    _configure_scene_arena(root, 75)
    assert root.find("size").attrib == {"nstack": "2048", "nconmax": "4096"}
    assert root.find("option").get("jacobian") == "dense"
    _configure_scene_arena(root, 75, revolved=True)
    assert int(root.find("size").get("memory")) == 128 * MIB
    assert root.find("size").get("nstack") is None
    assert root.find("size").get("nconmax") == "4096"
    assert root.find("option").get("jacobian") == "sparse"


def test_large_scene_memory_does_not_change_solver_or_contact_limits():
    root = ET.fromstring('<mujoco><option jacobian="dense" solver="Newton"/><size njmax="1000" nstack="2048" nconmax="4096"/></mujoco>')
    _configure_scene_arena(root, 3318)
    assert root.find("option").attrib == {"jacobian": "dense", "solver": "Newton"}
    assert root.find("size").attrib == {"nconmax": "4096", "memory": str(128 * MIB)}


def test_large_scene_memory_is_monotonic_bounded_and_rejects_instead_of_clamping():
    values = [_scene_arena_bytes(count) for count in (512, 1500, 3318, 5000, 7000, 9273, 10_075)]
    assert values == sorted(values)
    assert all(0 < value <= 1024 * MIB and value % (16 * MIB) == 0 for value in values)
    with pytest.raises(ValueError, match="20000 native bodies.*exceeding the 1024 MiB budget"):
        _scene_arena_bytes(20_000)


def test_appearance_only_bodies_count_toward_collision_workspace(tmp_path):
    # A collision-count-only policy misses these bodies even though MuJoCo
    # reserves its first broadphase array before rejecting contact-free pairs.
    spec = WorldSpec.model_validate({
        "name": "Appearance body fixture", "description": "XML-only test", "task": "Drive ahead",
        "bounds": [20, 20, 3], "objects": [
            {"id": f"paint_{index}", "label": "Test floor marking", "kind": "box", "collision": False,
             "position": [2, 2, .001], "size": [.1, .1, .002]} for index in range(520)
        ],
        "regions": [{"id": "arrival", "label": "Arrival", "position": [-2, 0, .5], "size": [1, 1, 1]}],
        "rubric": [{"id": "arrive", "description": "Arrive", "predicate": "robot_in_region", "target_id": "arrival"}],
    })
    original = spec.model_dump()
    runtime = object.__new__(Runtime)
    runtime.spec, runtime.asset_root = spec, tmp_path
    runtime._objects = {obj.id: obj for obj in spec.objects}
    root = ET.fromstring('<mujoco><option jacobian="dense"/><asset/><worldbody><body name="robot"><geom type="box" size=".1 .1 .1"/></body></worldbody></mujoco>')
    add_appearance(root)
    result = ET.fromstring(runtime._append_objects(root))
    assert int(result.find("size").get("memory")) == _scene_arena_bytes(522)
    assert result.find("option").get("jacobian") == "dense"
    assert len(result.findall(".//body")) == 521
    for geom in result.findall(".//geom[@name]"):
        assert geom.get("contype") == "0" and geom.get("conaffinity") == "0"
    assert spec.model_dump() == original
