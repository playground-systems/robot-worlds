"""Tiny metadata-only fixtures; no model, simulator, renderer or downloads."""
from copy import deepcopy

import numpy as np
import pytest

from robot_worlds.simready_conveyor import conveyor_bodies

PHYSICS = "KHR_physics_rigid_bodies"


def source():
    nodes = []
    for index in range(30):
        body, mesh = 2*index, 2*index+1
        point = [0, 0, index*.07]
        nodes.append({"name": f"body{index}", "translation": point, "children": [mesh],
                      "extensions": {PHYSICS: {"motion": {
                          "mass": 100 if index == 0 else .2, "centerOfMass": [0, 0, 0],
                          "inertiaDiagonal": [1, 1, 1], "inertiaOrientation": [0, 0, 0, 1],
                      }}}})
        nodes.append({"name": f"mesh{index}", "mesh": index, "extensions": {PHYSICS: {
            "collider": {"geometry": {"node": mesh, "convexHull": True}, "physicsMaterial": int(index == 0)},
        }}})
        if index:
            nodes[body]["extras"] = {"joints": [{
                "name": f"hinge{index}", "type": "revolute", "parent": "body0",
                "pivot": point, "axis": [1, 0, 0], "unit": "degrees",
            }]}
    for index in range(1, 30):
        frame, connected = len(nodes), len(nodes)+1
        nodes.append({"translation": [0, 0, index*.07], "extensions": {PHYSICS: {
            "joint": {"connectedNode": connected, "joint": 0},
        }}})
        nodes.append({})
        nodes[0]["children"].append(frame)
        nodes[2*index]["children"].append(connected)
    return {"nodes": nodes, "scenes": [{"nodes": list(range(0, 60, 2))}], "extensions": {
        PHYSICS: {"physicsMaterials": [{"dynamicFriction": .47}, {"dynamicFriction": .4}],
                  "physicsJoints": [{"limits": [
                      {"linearAxes": [0, 1, 2], "min": 0, "max": 0},
                      {"angularAxes": [1, 2], "min": 0, "max": 0},
                  ]}]},
    }}


def test_every_passive_roller_keeps_its_source_identity_mass_and_axis():
    doc = source()
    unchanged = deepcopy(doc)
    bodies = conveyor_bodies(doc)
    assert doc == unchanged
    assert len(bodies) == 30
    assert bodies[0].joint is None
    assert bodies[0].mass == 100
    for index, body in enumerate(bodies[1:], 1):
        assert body.node_index == 2*index
        assert body.mesh_node == 2*index+1
        assert body.source_name == f"body{index}"
        assert body.mass == .2
        assert body.friction == .47
        assert body.joint.type == "hinge"
        assert body.joint.limited is False
        assert body.joint.axis == (1, 0, 0)
        assert body.joint.anchor == pytest.approx((0, -index*.07, 0))
        assert body.joint.damping == body.joint.frictionloss == 0


def test_joint_frames_transform_before_comparison_with_source_declaration():
    doc = source()
    # Rotate the first roller around its X axis. Its child frame inversely rotates
    # so the physical connection stays aligned with the fixed frame.
    q = [np.sin(.2), 0, 0, np.cos(.2)]
    doc["nodes"][2]["rotation"] = q
    doc["nodes"][61]["rotation"] = [-q[0], 0, 0, q[3]]
    assert conveyor_bodies(doc)[1].joint.anchor == pytest.approx((0, -.07, 0))


@pytest.mark.parametrize("change,error", [
    (lambda d: d["extensions"][PHYSICS]["physicsJoints"][0].update(drives=[{}]), "without drives"),
    (lambda d: d["nodes"][60]["extensions"][PHYSICS]["joint"].update(joint=False), "joint definition"),
    (lambda d: d["nodes"][60]["extensions"][PHYSICS]["joint"].update(connectedNode=999), "connect"),
    (lambda d: d["nodes"][60].update(translation=[0, 0, .08]), "frames disagree"),
    (lambda d: d["nodes"][2]["extras"]["joints"][0].update(axis=[0, 1, 0]), "disagrees"),
    (lambda d: d["nodes"][2]["extras"]["joints"][0].update(pivot=[0, 0, .1]), "disagrees"),
    (lambda d: d["nodes"][2]["extras"]["joints"][0].update(parent="body2"), "declaration differs"),
    (lambda d: d["nodes"][2]["extras"]["joints"][0].update(lower=-90), "declaration differs"),
    (lambda d: d["nodes"][3]["extensions"][PHYSICS]["collider"]["geometry"].update(convexHull=False), "collider differs"),
    (lambda d: d["nodes"][2]["extensions"][PHYSICS]["motion"].update(mass=float("nan")), "positive mass"),
    (lambda d: d["nodes"][2]["extensions"][PHYSICS]["motion"].update(kinematic=True), "motion properties"),
    (lambda d: d["nodes"][2]["extensions"][PHYSICS].update(force=[1, 0, 0]), "physics feature"),
    (lambda d: d["nodes"].append({"mesh": 31}), "unreachable"),
    (lambda d: d["nodes"][0]["children"].append(0), "cycles"),
    (lambda d: d["nodes"][3].pop("extensions"), "30 colliders"),
    (lambda d: d.update(animations=[{}]), "animation"),
])
def test_inconsistent_or_unsupported_source_cannot_lose_physics_silently(change, error):
    doc = source()
    change(doc)
    with pytest.raises(ValueError, match=error):
        conveyor_bodies(doc)


def test_bearing_pockets_preserve_frame_outside_swept_roller_bounds():
    from types import SimpleNamespace

    from robot_worlds.simready_conveyor import _roller_clearances

    bodies = conveyor_bodies(source())
    # Small round cross-section on X; use only one rotor for this geometric test.
    center = np.array(bodies[1].joint.anchor)
    points = center+np.array([[x, y, z] for x in (-.4, .4) for y, z in ((.02, 0), (-.02, 0), (0, .02), (0, -.02))])
    frame = (np.array([-.5, -.2, -.1]), np.array([.5, .2, .1]))
    parts = [SimpleNamespace(node_index=bodies[1].mesh_node, positions=points)]
    boxes, pockets = _roller_clearances([frame], parts, bodies[:2])
    low, high = pockets[0]
    assert len(boxes) == 6
    assert not any(np.all(np.minimum(high, b)-np.maximum(low, a) > 1e-10) for a, b in boxes)
    original_volume = np.prod(frame[1]-frame[0])
    cut_volume = np.prod(np.minimum(high, frame[1])-np.maximum(low, frame[0]))
    assert sum(np.prod(b-a) for a, b in boxes) == pytest.approx(original_volume-cut_volume)
    # Unrelated frame material is retained; the pocket is independent of rotor angle.
    for point in ([.45, 0, 0], [0, .15, 0], [0, 0, .09]):
        assert any(np.all(point >= a) and np.all(point <= b) for a, b in boxes)


def test_planar_skin_is_explicit_and_cannot_fill_loading_bed(monkeypatch):
    from robot_worlds import simready_structures
    from robot_worlds.simready_conveyor import conveyor_frame_envelopes

    boxes = [(np.array([-.1, -2, .1]), np.array([0, -1.9, .2])) for _ in range(179)]
    boxes += [(np.array([-.1, -2, .1]), np.array([0, -1.9, .1])) for _ in range(12)]
    monkeypatch.setattr(simready_structures, "component_bounds", lambda *a, **k: boxes)
    prepared, planar = conveyor_frame_envelopes([])
    assert planar == list(range(179, 191))
    assert all(high[2]-low[2] == pytest.approx(.0001) for low, high in prepared[179:])
    boxes[0] = (np.array([.2, -1, .8]), np.array([.8, -.5, .9]))
    with pytest.raises(ValueError, match="roller bed"):
        conveyor_frame_envelopes([])


def test_cancelled_conveyor_import_never_downloads(tmp_path, monkeypatch):
    from robot_worlds import simready_conveyor

    monkeypatch.setattr(simready_conveyor, "download_simready_asset", lambda *a, **k: pytest.fail("unexpected download"))
    with pytest.raises(ValueError, match="cancelled"):
        simready_conveyor.import_simready_conveyor(tmp_path / "cache", cancelled=lambda: True)
    assert not (tmp_path / "cache").exists()
