"""Reviewed conveyor articulation, before collision adaptation.

This is deliberately limited to the pinned conveyor's fixed frame and 29 passive
rollers. It does not import a powered conveyor or certify contact behavior.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .gltf_geometry import _Z_UP, _transform
from .models import JointSpec

_PHYSICS = "KHR_physics_rigid_bodies"
_LIMITS = [{"linearAxes": [0, 1, 2], "min": 0, "max": 0},
           {"angularAxes": [1, 2], "min": 0, "max": 0}]


@dataclass(frozen=True)
class ConveyorBody:
    node_index: int
    mesh_node: int
    source_name: str
    mass: float
    friction: float
    joint: JointSpec | None


def conveyor_bodies(document: dict) -> list[ConveyorBody]:
    """Cross-check both joint frames and source extras in Z-up world coordinates.

    The first body is the frame, explicitly intended for fixed installation.
    Remaining bodies retain unlimited passive hinges. Damping/frictionloss are
    zero because the source does not provide bearing losses; callers must disclose
    that approximation, as well as any mass/inertia or collision conversion.
    """
    nodes = document.get("nodes", [])
    scenes = document.get("scenes", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 256 or len(scenes) != 1 or document.get("scene", 0) != 0:
        raise ValueError("Conveyor requires one bounded source scene")
    if document.get("animations") or document.get("skins"):
        raise ValueError("Conveyor animation/skinning is unsupported")
    library = document.get("extensions", {}).get(_PHYSICS, {})
    if set(library) != {"physicsMaterials", "physicsJoints"} or library["physicsJoints"] != [{"limits": _LIMITS}]:
        raise ValueError("Conveyor requires passive unlimited X hinges without drives")
    materials = library["physicsMaterials"]
    if not isinstance(materials, list) or len(materials) != 2:
        raise ValueError("Conveyor source must contain its two physics materials")
    transforms, parents = {}, {}
    stack = [(index, _Z_UP, None, 0) for index in scenes[0].get("nodes", [])]
    while stack:
        index, parent_frame, parent, depth = stack.pop()
        if (isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(nodes)
                or index in transforms or depth > 32):
            raise ValueError("Conveyor hierarchy has invalid references, repeated parents or cycles")
        node = nodes[index]
        transform = parent_frame @ _transform(node)
        if (not np.isfinite(transform).all()
                or set(node.get("extensions", {})) - {_PHYSICS}):
            raise ValueError("Unsupported conveyor node transform or extension")
        transforms[index], parents[index] = transform, parent
        stack.extend((child, transform, index, depth+1) for child in node.get("children", []))
    if len(transforms) != len(nodes):
        raise ValueError("Conveyor has unreachable nodes; physics cannot be dropped")
    physics = {i: node.get("extensions", {}).get(_PHYSICS, {}) for i, node in enumerate(nodes)}
    bodies = [i for i, value in physics.items() if "motion" in value]
    joints = [i for i, value in physics.items() if "joint" in value]
    colliders = [i for i, value in physics.items() if "collider" in value]
    if len(bodies) != 30 or len(joints) != 29 or len(colliders) != 30:
        raise ValueError("Conveyor must retain all 30 bodies, 29 joints and 30 colliders")
    if any(set(value) - {"motion", "joint", "collider"} for value in physics.values()):
        raise ValueError("Unsupported conveyor physics feature")
    names = [nodes[i].get("name") for i in bodies]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != 30:
        raise ValueError("Conveyor body names must be unique")
    roots = [i for i in bodies if not nodes[i].get("extras", {}).get("joints")]
    if len(roots) != 1:
        raise ValueError("Conveyor needs one fixed frame and 29 roller joint declarations")
    root = roots[0]
    parsed_joints = {}
    for index in joints:
        definition = physics[index]["joint"]
        if set(definition) != {"connectedNode", "joint"} or type(definition["joint"]) is not int or definition["joint"] != 0:
            raise ValueError("Unsupported conveyor joint definition")
        connected = definition["connectedNode"]
        if type(connected) is not int or connected not in transforms or parents[index] != root:
            raise ValueError("Conveyor joint must connect the frame to a roller")
        body = parents[connected]
        if body not in bodies or body == root or body in parsed_joints:
            raise ValueError("Conveyor roller joint mapping is missing or duplicated")
        first, second = transforms[index], transforms[connected]
        if (not np.allclose(first, second, atol=2e-6, rtol=0)
                or not np.allclose(first[:3, :3].T @ first[:3, :3], np.eye(3), atol=1e-6, rtol=0)
                or not np.isclose(np.linalg.det(first[:3, :3]), 1, atol=1e-6)):
            raise ValueError("Conveyor joint frames disagree or include scale/reflection")
        declarations = nodes[body].get("extras", {}).get("joints", [])
        if len(declarations) != 1:
            raise ValueError("Conveyor roller needs exactly one source joint declaration")
        extra = declarations[0]
        if (set(extra) != {"name", "type", "parent", "pivot", "axis", "unit"}
                or extra["type"] != "revolute" or extra["parent"] != nodes[root]["name"]
                or extra["unit"] != "degrees"):
            raise ValueError("Conveyor joint declaration differs from passive revolute source")
        # Source extras are global Y-up. Frame transforms independently establish
        # the actual axis and pivot; disagreement must not be silently repaired.
        pivot, axis = np.asarray(extra["pivot"], dtype=float), np.asarray(extra["axis"], dtype=float)
        if (pivot.shape != (3,) or axis.shape != (3,)
                or not np.allclose(_Z_UP[:3, :3] @ pivot, first[:3, 3], atol=2e-6, rtol=0)
                or not np.allclose(_Z_UP[:3, :3] @ axis, first[:3, 0], atol=1e-6, rtol=0)):
            raise ValueError("Conveyor source joint declaration disagrees with its frames")
        parsed_joints[body] = JointSpec(type="hinge", axis=first[:3, 0], anchor=first[:3, 3],
                                       range=(-np.pi, np.pi), limited=False,
                                       damping=0, frictionloss=0)
    if set(parsed_joints) != set(bodies)-{root}:
        raise ValueError("Conveyor must retain every roller hinge")
    result, used_colliders = [], set()
    for body in [root]+sorted(parsed_joints):
        meshes = [i for i in colliders if parents[i] == body]
        if len(meshes) != 1:
            raise ValueError("Conveyor body must have one source collision mesh")
        mesh = meshes[0]
        collider = physics[mesh]["collider"]
        material = collider.get("physicsMaterial")
        if (set(collider) != {"geometry", "physicsMaterial"}
                or collider["geometry"] != {"node": mesh, "convexHull": True}
                or "mesh" not in nodes[mesh] or type(material) is not int or not 0 <= material < 2):
            raise ValueError("Conveyor collider differs from the reviewed source")
        used_colliders.add(mesh)
        motion = physics[body]["motion"]
        if set(motion) != {"mass", "centerOfMass", "inertiaDiagonal", "inertiaOrientation"}:
            raise ValueError("Unsupported conveyor motion properties")
        mass, friction = motion["mass"], materials[material].get("dynamicFriction")
        if (type(mass) not in (int, float) or not np.isfinite(mass) or mass <= 0
                or type(friction) not in (int, float) or not np.isfinite(friction) or friction < 0):
            raise ValueError("Conveyor requires finite positive mass and nonnegative friction")
        result.append(ConveyorBody(body, mesh, nodes[body]["name"], mass, friction, parsed_joints.get(body)))
    if used_colliders != set(colliders) or {i for i, node in enumerate(nodes) if "mesh" in node} != used_colliders:
        raise ValueError("Conveyor has unmapped visual or collision geometry")
    return result
