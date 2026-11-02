"""Small source-metadata/geometry regressions; no native runtime or downloads."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from robot_worlds.kitchen_counter_library import (
    _counter_bodies,
    _hinge_slices,
    kitchen_counter_asset_search,
)
from robot_worlds.kitchen_dish_library import kitchen_dish_asset_search
from robot_worlds.living_room_cabinet import _bodies
from robot_worlds.living_room_library import LIVING_ROOM_ASSETS, living_room_asset_search
from robot_worlds.workbench_library import workbench_asset_search

PHYSICS = "KHR_physics_rigid_bodies"
FIXTURES = Path(__file__).parent / "fixtures/simready-joint-documents"


def source_document(name):
    return json.loads((FIXTURES / (name+".json")).read_text())["document"]


@pytest.mark.parametrize("name,parse,count", [
    ("fixture_cabinet_b01", _bodies, 3),
    ("fixture_counter_a01", _counter_bodies, 9),
])
def test_pinned_source_bodies_keep_all_joints_frames_and_collision_policy(name, parse, count):
    document = source_document(name)
    original = deepcopy(document)
    bodies = parse(document)
    assert document == original
    assert len(bodies) == count
    assert bodies[0]["joint"] is None
    joints = [part["joint"] for part in bodies[1:]]
    assert len(joints) == count-1
    assert all(joint.collide_parent is False for joint in joints)
    assert all(joint.damping == joint.frictionloss == joint.stiffness == 0 for joint in joints)
    assert all(part["mass"] > 0 and part["friction"] == .4 for part in bodies)
    if name == "fixture_cabinet_b01":
        assert [joint.type for joint in joints] == ["slide", "slide"]
        assert joints[0].range == (0, .46218)
        assert joints[1].range == (-.000004, .462176)
        assert all(joint.axis == (1, 0, 0) for joint in joints)
    else:
        assert [joint.type for joint in joints] == ["slide"]*4+["hinge"]+["slide"]*3
        assert joints[4].axis == pytest.approx((0, 0, 1))
        assert joints[4].range == pytest.approx((-np.pi/2, 0))
        assert joints[0].range == (0, .460681)
        assert joints[5].range == (-.0001, .4)


@pytest.mark.parametrize("name,parse", [
    ("fixture_cabinet_b01", _bodies), ("fixture_counter_a01", _counter_bodies),
])
@pytest.mark.parametrize("mutation", ["frame", "axis", "drives", "mass"])
def test_inconsistent_source_physics_is_rejected_instead_of_flattened(name, parse, mutation):
    document = source_document(name)
    if mutation == "frame":
        frame = next(node for node in document["nodes"]
            if "joint" in node.get("extensions", {}).get(PHYSICS, {}))
        frame["translation"][0] += .01
    elif mutation == "axis":
        body = next(node for node in document["nodes"] if node.get("extras", {}).get("joints"))
        body["extras"]["joints"][0]["axis"] = [0, 0, 1]
    elif mutation == "drives":
        document["extensions"][PHYSICS]["physicsJoints"][0]["drives"] = [{"stiffness": 10}]
    else:
        body = next(node for node in document["nodes"]
            if "motion" in node.get("extensions", {}).get(PHYSICS, {}))
        body["extensions"][PHYSICS]["motion"]["mass"] = -1
    with pytest.raises(ValueError):
        parse(document)


@pytest.mark.parametrize("search,asset_id", [
    *[(living_room_asset_search, name) for name in LIVING_ROOM_ASSETS],
    (kitchen_counter_asset_search, "fixture_counter_a01"),
    (kitchen_dish_asset_search, "kitchenware_bowl_b01"),
    (workbench_asset_search, "table_packing_heavyduty_c01"),
])
def test_selected_sources_are_pinned_simready_with_checked_per_package_licenses(search, asset_id):
    report = search(asset_id)
    assert report.completed and report.query == asset_id
    assert "nvidia/simready-assets" in report.sources
    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.id == "simready:"+asset_id
    assert candidate.license == candidate.metadata["upstream_packaging"]["license"] == "CC-BY-4.0"
    assert candidate.source_url == (
        "https://huggingface.co/datasets/codepawl/simready-glb/resolve/"
        +candidate.revision+"/glb/"+asset_id+".glb")
    assert len(candidate.revision) == 40
    assert candidate.metadata["upstream_packaging_url"].startswith(
        "https://huggingface.co/datasets/nvidia/simready-assets/resolve/"
        +candidate.metadata["upstream_checked_revision"]+"/"+asset_id+"/")
    assert hashlib.sha256(candidate.metadata["upstream_license_text"].encode()).hexdigest() == candidate.metadata["upstream_license_sha256"]


def test_source_joint_filter_claim_retains_inspected_ascii_layers_and_scope():
    for search, name in ((living_room_asset_search, "fixture_cabinet_b01"),
                         (kitchen_counter_asset_search, "fixture_counter_a01")):
        policy = search(name).evidence["source_joint_collision_policy"]
        assert "openusd.org" in policy.get("usd_schema_url", "")
        for record in policy["source_files"].values():
            assert "physics:collisionEnabled" not in record["text"]
            assert hashlib.sha256(record["text"].encode()).hexdigest() == record["sha256"]
    assert "not composed or parsed" in kitchen_counter_asset_search("fixture_counter_a01").evidence["source_joint_collision_policy"]["scope"]


def angled_hinge():
    # An angled rectangular source part: its whole AABB overlaps the adjacent
    # front, although the actual low-Y source surface has ample X separation.
    points = np.array([[x+shift, y, z] for y, shift in ((0, 0), (.02, .022))
                       for x in (0, .004) for z in (0, .01)])
    faces = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],
             [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
             [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    return points[np.array(faces)]


def test_angled_hinge_source_clipping_keeps_material_without_neighbor_contact():
    triangles = angled_hinge()
    original = triangles.min((0, 1)), triangles.max((0, 1))
    front = np.array([.025, -.1, -.1]), np.array([.05, .003, .1])
    assert np.all(np.minimum(original[1], front[1])-np.maximum(original[0], front[0]) > 0)
    boxes, evidence = _hinge_slices(triangles, original, front)
    assert len(boxes) == 2
    assert boxes[0][1][1] == boxes[1][0][1] == front[1][1]
    assert evidence["lower_y_band_x_clearance_m"] == pytest.approx(.0177)
    assert evidence["source_triangles"] == len(triangles)
    for low, high in boxes:
        assert not np.all(np.minimum(high, front[1])-np.maximum(low, front[0]) > 1e-7)
    for point in np.concatenate((triangles.reshape(-1, 3), triangles.mean(1))):
        assert any(np.all(point >= low-1e-12) and np.all(point <= high+1e-12) for low, high in boxes)
    bad = (original[0], original[1]+(.001, 0, 0))
    with pytest.raises(ValueError, match="original component bounds"):
        _hinge_slices(triangles, bad, front)
