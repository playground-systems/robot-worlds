"""Native MuJoCo execution: tool commands control actuators, never object poses.

Only reset/initialization writes generalized positions. IK uses separate scratch
state; its solutions become bounded position-servo targets in the live simulator.
"""

from __future__ import annotations

import copy
import hashlib
import io
import math
import sys
import xml.etree.ElementTree as ET
from importlib import metadata, resources
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from .appearance import add_appearance, finish_name
from .artifacts import digest
from .revolved import MAX_SCENE_COLLIDERS, collision_count, wedges
from .robot_assets import ROBOT_MANIFESTS, robot_asset_path
from .rubric import rigid_members
from .sensors import WheelOdometry, depth_point, local_point, world_point

SENSOR_CONTRACT = {
    "kind": "rgbd_joint_encoders_relative_imu",
    "camera": "robot_rgbd",
    "image_size": [640, 480],
    "coordinate_frame": "robot base: X forward, Y left, Z up; G1 origin at pelvis",
    "depth": "optical-axis metres, 0.05–10 m; query_depth returns calibrated base-frame surface point",
    "object_labels": False,
    "global_localization": False,
    "odometry": "TIAGo wheel-encoder dead reckoning from episode start; unavailable for G1",
    "drive_translation": "TIAGo uses wheel odometry; G1 commands nominal travel by velocity/time with no measured displacement. Neither drive primitive terminates from global position.",
    "noise": "ideal camera, depth, encoders and IMU; no calibrated hardware noise model",
    "vision_requires_render": True,
}


def runtime_implementation_identity(robot: str, action_contract: list[dict]) -> dict:
    """Hash installed package resources, not checkout paths or manual labels.

    Whole-module hashes conservatively invalidate validation after even a
    nonbehavioral edit. Missing resources/version metadata fail closed. These
    hashes identify an implementation; they do not attest to an untampered host.
    """
    modules = ["runtime.py", "reset_diagnostics.py", "rubric.py", "models.py", "robot_assets.py", "artifacts.py", "appearance.py", "live.py", "spectator.py", "sensors.py", "revolved.py", "mesh_assets.py"]
    dependencies = ["mujoco", "numpy", "pillow", "pydantic"]
    if robot == "g1":
        modules.append("g1_runtime.py")
        dependencies.append("onnxruntime")
    elif robot != "tiago++":
        raise ValueError(f"Cannot fingerprint unsupported robot {robot!r}")
    package = resources.files("robot_worlds")
    return {
        "version": 1,
        "sources": {
            name: hashlib.sha256(package.joinpath(name).read_bytes()).hexdigest()
            for name in sorted(modules)
        },
        "dependencies": {name: metadata.version(name) for name in sorted(dependencies)},
        "python": {"implementation": sys.implementation.name, "version": list(sys.version_info[:3])},
        "action_contract": copy.deepcopy(action_contract),
        "sensor_contract": copy.deepcopy(SENSOR_CONTRACT),
    }


def _vec(values):
    return " ".join(str(float(x)) for x in values)


def _tool(name, description, properties, required):
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


NUMBER = {"type": "number"}
HAND = {"type": "string", "enum": ["left", "right"]}
TOOLS = [
    _tool(
        "drive",
        "Drive forward/backward distance in metres, then turn radians. Stops using wheel-encoder odometry and relative IMU heading, not global localization. Wheel slip or obstacles can make actual travel differ from the estimate. Positive turn is left. Maximum distance 2m, turn pi radians.",
        {"distance": NUMBER, "turn": NUMBER},
        ["distance", "turn"],
    ),
    _tool(
        "move_arm",
        "Move a gripper grasp center toward robot-base XYZ metres (X forward, Y left, Z up; origin at the base, pelvis for G1) through joint servos. Local IK can fail or encounter obstacles; inspect actual reached position. Optional approach chooses down, forward, or keep (default). Maximum reach is limited by the robot.",
        {
            "hand": HAND,
            "position": {"type": "array", "items": NUMBER, "minItems": 3, "maxItems": 3},
            "approach": {"type": "string", "enum": ["down", "forward", "keep"]},
        },
        ["hand", "position"],
    ),
    _tool(
        "gripper",
        "Set finger opening in metres per finger: 0 closes, 0.045 opens. Objects are held only by physical friction/contact, and can slip.",
        {"hand": HAND, "opening": NUMBER},
        ["hand", "opening"],
    ),
    _tool(
        "wait",
        "Advance simulation for 0–5 seconds without changing actuator targets.",
        {"seconds": NUMBER},
        ["seconds"],
    ),
    _tool("query_depth", "Read depth at an RGB pixel [u,v] (640x480, origin top-left), returning a measured visible surface point in robot-base coordinates. No object recognition or hidden geometry.",
          {"pixel": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}}, ["pixel"]),
    _tool("look", "Read the robot camera and proprioception without advancing simulation.", {}, []),
]


class SimulationTimeLimit(Exception):
    """The declared simulation budget has been exhausted."""


class RobotPhysicalFailure(Exception):
    """A simulated robot has physically failed (for example, fallen)."""


class Runtime:
    def __new__(cls, spec, **kwargs):
        if cls is Runtime and spec.robot == "g1":
            from .g1_runtime import G1Runtime

            return object.__new__(G1Runtime)
        return object.__new__(cls)

    def __init__(self, spec, *, render=True, asset_root=None, seed=0, max_sim_seconds=None):
        self.spec = spec
        self.asset_root = Path(asset_root).resolve() if asset_root else Path.cwd()
        self.seed = int(seed)
        self._time_limit = max_sim_seconds
        self._process_timers = {}
        self._process_states = {}
        self._fixture_members = {}
        self.asset_path = robot_asset_path(spec.robot)
        self._render_enabled = render
        self._implementation = runtime_implementation_identity(spec.robot, self.tools)
        self._implementation_fingerprint = digest(self._implementation)
        self._renderer = None
        self._overview_renderer = None
        self._live_view = None
        self._last_result = None
        self.trace = []
        self._events = []
        self._last_contact_pairs = set()
        self._last_held = set()
        self._next_sample = 0.0
        self._closed = False
        self._objects = {o.id: o for o in spec.objects}
        self._grasp_roots = {}
        for obj in spec.objects:
            root = obj
            seen = set()
            while root.parent_id in self._objects and not root.joint and not root.movable:
                if root.id in seen:
                    raise ValueError("Cyclic rigid assembly")
                seen.add(root.id)
                root = self._objects[root.parent_id]
            self._grasp_roots[obj.id] = root.id
        for cycle in getattr(spec, "process_cycles", []):
            members = {cycle.fixture_id}
            while True:
                expanded = members | {o.id for o in spec.objects if o.parent_id in members}
                if expanded == members:
                    break
                members = expanded
            self._fixture_members[cycle.fixture_id] = members
        self._xml = self._build_xml()
        normalized_xml = self._xml.replace(str(self.asset_path), "ROBOT_ASSETS").replace(
            str(self.asset_root), "WORLD_ASSETS"
        )
        self._model_fingerprint = hashlib.sha256(normalized_xml.encode()).hexdigest()
        self.model = mujoco.MjModel.from_xml_string(self._xml)
        self.data = mujoco.MjData(self.model)
        self._ik_data = mujoco.MjData(self.model)
        self._object_bodies = {o.id: self.model.body("object_" + o.id).id for o in spec.objects}
        self._geom_objects = {}
        for gid in range(self.model.ngeom):
            bid = self.model.geom_bodyid[gid]
            for oid, obid in self._object_bodies.items():
                if bid == obid:
                    self._geom_objects[gid] = oid
        self._setup_robot()
        self._reset()
        self._check_physics_warnings()
        self._initial_yaw = self._yaw()
        self._odometry = WheelOdometry(self._wheel_angles(), self._yaw()) if spec.robot == "tiago++" else None

    def _setup_robot(self):
        self._finger_bodies = {
            hand: [
                self.model.body(f"gripper_{hand}_{finger}_finger_link").id
                for finger in ("left", "right")
            ]
            for hand in ("left", "right")
        }
        self._arm_joints = {
            hand: [self.model.joint(f"arm_{hand}_{i}_joint").id for i in range(1, 8)]
            for hand in ("left", "right")
        }
        self._sites = {h: self.model.site(h + "_grasp").id for h in ("left", "right")}

    def _build_xml(self):
        root = ET.parse(self.asset_path / "tiago_dual.xml").getroot()
        root.find("compiler").set("meshdir", str(self.asset_path / "assets"))
        root.find("option").set("timestep", "0.002")
        root.find("option").set("gravity", "0 0 -9.81")
        root.find("option").set("iterations", "60")
        root.find("option").set("noslip_iterations", "5")
        position = ET.parse(self.asset_path / "tiago_dual_position.xml").getroot()
        root.append(position.find("default"))
        root.append(position.find("actuator"))
        for actuator in root.findall("./actuator/velocity"):
            actuator.set("kv", "100")
        add_appearance(root)
        world = root.find("worldbody")
        ET.SubElement(
            world,
            "geom",
            name="floor",
            type="plane",
            size="0 0 .1",
            material="rw_floor",
            friction=".8 .01 .001",
            contype="4",
            conaffinity="3",
        )
        base = world.find("body[@name='base_link']")
        ET.SubElement(
            base,
            "camera",
            name="robot_rgbd",
            pos=".30 0 1.35",
            xyaxes="0 -1 0 0.5 0 0.8660254",
            fovy="75",
        )
        for hand in ("left", "right"):
            wrist = root.find(f".//body[@name='arm_{hand}_7_link']")
            # Finger mesh extends ~0.15m past the wrist's gripper mounting point.
            z = -0.27 if hand == "left" else 0.27
            ET.SubElement(
                wrist, "site", name=hand + "_grasp", pos=f"0 0 {z}", size=".006", rgba="0 1 0 .3"
            )
        # Flat rubber fingertip pads supplement upstream convex mesh contacts.
        # Their dimensions/material are an explicit runtime approximation.
        for hand in ("left", "right"):
            for finger in ("left", "right"):
                body = root.find(f".//body[@name='gripper_{hand}_{finger}_finger_link']")
                for original_geom in body.findall("geom"):
                    original_geom.set("contype", "0")
                    original_geom.set("conaffinity", "0")
                shaft_x = -0.004 if finger == "left" else 0.004
                ET.SubElement(
                    body,
                    "geom",
                    name=f"finger_shaft_{hand}_{finger}",
                    type="box",
                    pos=f"{shaft_x} 0 -.138",
                    size=".004 .024 .079",
                    mass=".001",
                    rgba=".12 .12 .12 1",
                    contype="1",
                    conaffinity="1",
                )
                x = 0.003 if finger == "left" else -0.003
                ET.SubElement(
                    body,
                    "geom",
                    name=f"pad_{hand}_{finger}",
                    type="box",
                    pos=f"{x} 0 -.190",
                    size=".003 .016 .027",
                    mass=".003",
                    rgba=".15 .15 .15 1",
                    friction="1.5 .01 .001",
                    condim="4",
                    contype="1",
                    conaffinity="1",
                )
        # Passive roller contacts make the omni base turn through wheel forces.
        # Menagerie's collision cylinders cannot represent the real roller DOFs.
        # This explicit simplified roller geometry is a documented approximation.
        for side in ("front_right", "front_left", "rear_right", "rear_left"):
            wheel_body = root.find(f".//body[@name='wheel_{side}_link']")
            cylinder = wheel_body.find("geom[@type='cylinder']")
            cylinder.set("contype", "0")
            cylinder.set("conaffinity", "0")
            for index in range(12):
                theta = 2 * math.pi * index / 12
                tangent = np.array([-math.sin(theta), math.cos(theta), 0.0])
                # Omni rollers: free lateral rolling, traction around wheel rim.
                handedness = 1 if side in ("front_left", "rear_right") else -1
                axis = (tangent + np.array([0.0, 0.0, handedness])) / math.sqrt(2)
                roller = ET.SubElement(
                    wheel_body,
                    "body",
                    name=f"roller_{side}_{index}",
                    pos=_vec([0.0642 * math.cos(theta), 0.0642 * math.sin(theta), 0]),
                )
                ET.SubElement(
                    roller,
                    "joint",
                    name=f"roller_joint_{side}_{index}",
                    type="hinge",
                    axis=_vec(axis),
                    damping=".0001",
                    frictionloss=".0001",
                )
                ET.SubElement(
                    roller,
                    "geom",
                    name=f"roller_geom_{side}_{index}",
                    type="capsule",
                    fromto=_vec([*list(-0.014 * axis), *list(0.014 * axis)]),
                    size=".012",
                    mass=".015",
                    rgba=".12 .12 .12 1",
                    contype="2",
                    conaffinity="4",
                    condim="3",
                    friction=".9 .001 .0001",
                )
        # Name all robot geoms for inspectable collision records.
        for i, geom in enumerate(world.findall(".//geom")):
            if "name" not in geom.attrib:
                geom.set("name", f"robot_geom_{i}")
        return self._append_objects(root)

    def _append_objects(self, root):
        if sum(collision_count(obj) for obj in self.spec.objects) > MAX_SCENE_COLLIDERS:
            raise ValueError(f"Scene exceeds {MAX_SCENE_COLLIDERS} physical scene colliders")
        if any(obj.kind == "revolved" for obj in self.spec.objects):
            # Many wedges can simultaneously rest on one support. MuJoCo's
            # default arena estimate is too small for this contact pattern.
            size = root.find("size")
            if size is None:
                size = ET.SubElement(root, "size")
            size.set("memory", "128M")
            root.find("option").set("jacobian", "sparse")
        world = root.find("worldbody")
        bodies = {}
        remaining = list(self.spec.objects)
        while remaining:
            progress = False
            for obj in remaining[:]:
                parent_id = getattr(obj, "parent_id", None)
                if parent_id and parent_id not in bodies:
                    continue
                parent = bodies[parent_id] if parent_id else world
                origin = np.array(self._objects[parent_id].position) if parent_id else np.zeros(3)
                body = ET.SubElement(
                    parent,
                    "body",
                    name="object_" + obj.id,
                    pos=_vec(np.array(obj.position) - origin),
                )
                bodies[obj.id] = body
                joint = getattr(obj, "joint", None)
                if joint:
                    j = joint.model_dump() if hasattr(joint, "model_dump") else dict(joint)
                    ET.SubElement(
                        body,
                        "joint",
                        name="joint_" + obj.id,
                        type=j["type"],
                        axis=_vec(j["axis"]),
                        pos=_vec(np.array(j["anchor"]) - np.array(obj.position)),
                        range=_vec(j["range"]),
                        limited=str(j.get("limited", True)).lower(),
                        damping=str(j.get("damping", 2)),
                        frictionloss=str(j.get("frictionloss", .2)),
                        armature=str(j.get("armature", 0)),
                        stiffness=str(j.get("stiffness", 0)),
                    )
                elif obj.movable:
                    if parent_id:
                        raise ValueError(
                            f"Movable object {obj.id} with parent needs an articulated joint"
                        )
                    ET.SubElement(body, "freejoint", name="free_" + obj.id)
                geom = dict(
                    name="geom_" + obj.id,
                    material="rw_" + finish_name(obj),
                    rgba=_vec([*obj.color, obj.opacity if obj.visible else 0]),
                    mass=str(obj.mass if obj.collision else 0),
                    friction=f"{getattr(obj, 'friction', 0.7)} .01 .001",
                    condim="4",
                    contype="1" if obj.collision else "0",
                    conaffinity="7" if obj.collision else "0",
                )
                if obj.texture_path:
                    from .mesh_assets import validate_texture
                    texture_path = Path(obj.texture_path).expanduser()
                    if not texture_path.is_absolute():
                        texture_path = self.asset_root / texture_path
                    validate_texture(texture_path)
                    name = "texture_" + obj.id
                    ET.SubElement(root.find("asset"), "texture", name=name, type="2d", file=str(texture_path.resolve()))
                    ET.SubElement(root.find("asset"), "material", name=name, texture=name, texuniform="false")
                    geom["material"] = name
                size = np.asarray(obj.size, dtype=float)
                if obj.kind == "box":
                    geom.update(type="box", size=_vec(size / 2))
                elif obj.kind == "sphere":
                    if not np.allclose(size, size[0]):
                        raise ValueError(f"Sphere {obj.id} must have equal XYZ extents")
                    geom.update(type="sphere", size=str(size[0] / 2))
                elif obj.kind == "cylinder":
                    if not np.isclose(size[0], size[1]):
                        raise ValueError(f"Cylinder {obj.id} must have equal XY extents")
                    geom.update(type="cylinder", size=_vec([size[0] / 2, size[2] / 2]))
                elif obj.kind == "revolved":
                    pieces = list(wedges(obj.revolved.profile, obj.revolved.segments, size))
                    total_volume = sum(volume for _, volume in pieces)
                    for index, (vertices, volume) in enumerate(pieces):
                        name = f"revolved_{obj.id}_{index}"
                        ET.SubElement(root.find("asset"), "mesh", name=name,
                                      vertex=_vec([v for point in vertices for v in point]))
                        piece = {**geom, "name": f"geom_{obj.id}_{index}", "type": "mesh", "mesh": name,
                                 "mass": str(obj.mass * volume / total_volume)}
                        ET.SubElement(body, "geom", **piece)
                elif obj.kind == "mesh":
                    mesh_path = Path(obj.mesh_path).expanduser()
                    if not mesh_path.is_absolute():
                        mesh_path = self.asset_root / mesh_path
                    mesh_path = mesh_path.resolve()
                    if not mesh_path.is_file():
                        raise ValueError(f"Mesh asset is missing: {mesh_path}")
                    # Normalize visual/collision mesh dimensions to the declared
                    # full extents and center; rubric geometry matches physics.
                    if mesh_path.suffix.lower() == ".obj":
                        from .mesh_assets import obj_bounds
                        lower, upper = obj_bounds(mesh_path)
                    else:
                        # Keep the native decoder for binary STL/MSH formats.
                        probe = ET.Element("mujoco")
                        ET.SubElement(
                            ET.SubElement(probe, "asset"), "mesh", name="probe", file=str(mesh_path)
                        )
                        ET.SubElement(
                            ET.SubElement(probe, "worldbody"), "geom", type="mesh", mesh="probe"
                        )
                        mesh_model = mujoco.MjModel.from_xml_string(
                            ET.tostring(probe, encoding="unicode")
                        )
                        mesh_rotation = np.empty(9)
                        mujoco.mju_quat2Mat(mesh_rotation, mesh_model.geom_quat[0])
                        vertices = (
                            mesh_model.mesh_vert @ mesh_rotation.reshape(3, 3).T
                            + mesh_model.geom_pos[0]
                        )
                        lower, upper = vertices.min(0), vertices.max(0)
                    if np.any(upper - lower < 1e-8):
                        raise ValueError(f"Mesh {obj.id} must have three nonzero dimensions")
                    scale = size / (upper - lower)
                    ET.SubElement(
                        root.find("asset"),
                        "mesh",
                        name="mesh_" + obj.id,
                        file=str(mesh_path),
                        scale=_vec(scale),
                    )
                    geom.update(
                        type="mesh", mesh="mesh_" + obj.id, pos=_vec(-0.5 * (lower + upper) * scale)
                    )
                else:
                    raise ValueError(f"Unsupported physical shape: {obj.kind}")
                if obj.kind != "revolved":
                    ET.SubElement(body, "geom", **geom)
                remaining.remove(obj)
                progress = True
            if not progress:
                raise ValueError("Object parent relationships are missing or cyclic")
        return ET.tostring(root, encoding="unicode")

    def _reset(self):
        mujoco.mj_resetData(self.model, self.data)
        start = getattr(self.spec, "robot_start", [0, 0, 0])
        self.data.qpos[:3] = [start[0], start[1], 0.002]
        self.data.qpos[3:7] = [math.cos(start[2] / 2), 0, 0, math.sin(start[2] / 2)]
        for hand in ("left", "right"):
            # A physically reachable folded rest posture, not an animation pose.
            for joint, value in zip(self._arm_joints[hand], [0.3, -0.8, 0, 1.5, 0, 0, 0]):
                self.data.qpos[self.model.jnt_qposadr[joint]] = value
        for name, val in [
            ("torso_lift_joint", 0.15),
            *[
                (f"gripper_{h}_{f}_finger_joint", 0.04)
                for h in ("left", "right")
                for f in ("left", "right")
            ],
        ]:
            self.data.qpos[self.model.joint(name).qposadr[0]] = val
        for i in range(self.model.nu):
            jid = self.model.actuator_trnid[i, 0]
            if "wheel" not in self.model.actuator(i).name:
                self.data.ctrl[i] = self.data.qpos[self.model.jnt_qposadr[jid]]
        for obj in self.spec.objects:
            joint = getattr(obj, "joint", None)
            if joint:
                self.data.joint("joint_" + obj.id).qpos[0] = getattr(joint, "initial", 0.0)
        mujoco.mj_forward(self.model, self.data)
        # Settling is part of initialization, before task time begins.
        self._settle_reset()
        self.data.time = 0
        self._sample()

    def _settle_reset(self, *, before_step=None):
        from .reset_diagnostics import ResetObserver

        observer = ResetObserver(self)
        for _ in range(300):
            if before_step is not None:
                before_step()
            mujoco.mj_step(self.model, self.data)
            observer.sample()
        self._reset_diagnostics = observer.finish()

    @property
    def reset_diagnostics(self):
        """Privileged initialization evidence, never included in observations."""
        return copy.deepcopy(self._reset_diagnostics)

    def _yaw(self):
        w, x, y, z = self.data.qpos[3:7]
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))

    def set_time_limit(self, seconds):
        if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
            raise ValueError("Simulation time limit must be finite and nonnegative")
        self._time_limit = seconds

    def _halt_command(self):
        self.data.ctrl[:4] = 0

    def _before_step(self):
        pass

    def set_live_observer(self, callback):
        from .live import LiveView
        self._live_view = LiveView(lambda: self.render(view="overview"), callback) if callback else None

    def publish_live(self, *, force=False):
        if self._live_view is not None:
            self._live_view.publish(self.data.time, force=force)

    def _step(self, seconds):
        for _ in range(max(0, round(seconds / self.model.opt.timestep))):
            if (
                self._time_limit is not None
                and self.data.time + self.model.opt.timestep > self._time_limit + 1e-9
            ):
                self._halt_command()
                raise SimulationTimeLimit()
            self._before_step()
            mujoco.mj_step(self.model, self.data)
            self._check_physics_warnings()
            if getattr(self, "_odometry", None) is not None:
                self._odometry.update(self._wheel_angles(), self._yaw())
            if not np.isfinite(self.data.qpos).all() or np.max(np.abs(self.data.qvel)) > 1e4:
                raise RuntimeError("Simulator diverged; trajectory is invalid")
            if getattr(self.spec, "process_cycles", []):
                self._advance_processes()
            if self.data.time + 1e-8 >= self._next_sample:
                self._sample()
                self.publish_live()

    def _check_physics_warnings(self):
        from .reset_diagnostics import INVALIDATING_WARNINGS
        warnings = getattr(self.data, "warning", ())
        for name in sorted(INVALIDATING_WARNINGS):
            index = int(getattr(mujoco.mjtWarning, name))
            if index < len(warnings) and warnings[index].number:
                raise RuntimeError(f"Simulator emitted {name}; physics evidence is invalid. Reduce contact complexity or increase the bounded solver arena.")

    def _contact_state(self):
        pairs, fingers = set(), {h: {} for h in ("left", "right")}
        for contact in self.data.contact[: self.data.ncon]:
            a, b = int(contact.geom1), int(contact.geom2)
            oa, ob = self._geom_objects.get(a), self._geom_objects.get(b)
            if oa and ob and oa != ob:
                pairs.add(tuple(sorted((oa, ob))))
            for obj, robot_geom in ((oa, b), (ob, a)):
                if obj:
                    # Opposing fingers may touch different welded pieces of one bowl.
                    grasp_root = self._grasp_roots[obj]
                    bid = int(self.model.geom_bodyid[robot_geom])
                    for hand, bids in self._finger_bodies.items():
                        if bid in bids:
                            fingers[hand].setdefault(grasp_root, set()).add(bid)
        held = {
            (hand, obj)
            for hand, objects in fingers.items()
            for obj, touching in objects.items()
            if len(touching) == 2 and self._objects[obj].movable
        }
        return pairs, held

    def _sample(self):
        pairs, held = self._contact_state()
        for a, b in pairs - self._last_contact_pairs:
            self._events.append(
                {"type": "contact", "object_id": a, "target_id": b, "time": float(self.data.time)}
            )
        for hand, oid in held - self._last_held:
            self._events.append(
                {
                    "type": "grasp_contact",
                    "object_id": oid,
                    "hand": hand,
                    "time": float(self.data.time),
                }
            )
        for hand, oid in self._last_held - held:
            self._events.append(
                {
                    "type": "grasp_contact_lost",
                    "object_id": oid,
                    "hand": hand,
                    "time": float(self.data.time),
                }
            )
        self._last_contact_pairs, self._last_held = pairs, held
        self.trace.append(self.snapshot())
        self._next_sample = float(self.data.time) + 0.05

    def _process_snapshot(self):
        cycles = self.spec.process_cycles
        # Monitors run every physics step, independently of 20Hz replay samples.
        pairs, held = self._contact_state()
        needed = {
            identifier
            for c in cycles
            for identifier in [
                c.fixture_id,
                *c.workpiece_ids,
                *[p.object_id for p in c.prerequisites],
                *[p.target_id for p in c.prerequisites],
            ]
            if identifier in self._objects
        }
        state = {"time": float(self.data.time), "objects": {}}
        needed = set().union(*(rigid_members(self._objects, oid) for oid in needed))
        for oid in needed:
            obj = self._objects[oid]
            bid = self._object_bodies[oid]
            values = {
                "position": self.data.xpos[bid].tolist(),
                "quaternion": self.data.xquat[bid].tolist(),
                "size": list(obj.size),
                "contacts": [b if a == oid else a for a, b in pairs if oid in (a, b)],
                "held_by": next((hand for hand, obj_id in held if obj_id == oid), None),
            }
            if obj.joint:
                values["joint_position"] = float(self.data.joint("joint_" + oid).qpos[0])
            state["objects"][oid] = values
        return state

    def _advance_processes(self):
        cycles = getattr(self.spec, "process_cycles", [])
        if not cycles:
            return
        from .rubric import measure_predicate

        state = self._process_snapshot()
        for cycle in cycles:
            if all(cycle.id in self._process_states.get(w, {}) for w in cycle.workpiece_ids):
                continue
            # Every workpiece must be a declared prerequisite and physically
            # contact the fixture; time alone cannot process a nearby object.
            grounded = all(
                bool(
                    set(state["objects"][w]["contacts"])
                    & self._fixture_members.get(cycle.fixture_id, {cycle.fixture_id})
                )
                for w in cycle.workpiece_ids
            )
            satisfied = grounded and all(
                measure_predicate(self.spec, p, state)["satisfied"] is True
                for p in cycle.prerequisites
            )
            if not satisfied:
                self._process_timers.pop(cycle.id, None)
                continue
            start = self._process_timers.setdefault(cycle.id, float(self.data.time))
            if self.data.time - start + 1e-8 >= cycle.duration:
                for workpiece in cycle.workpiece_ids:
                    self._process_states.setdefault(workpiece, {})[cycle.id] = cycle.output_state
                    self._events.append(
                        {
                            "type": "process_complete",
                            "object_id": workpiece,
                            "target_id": cycle.id,
                            "time": float(self.data.time),
                            "abstraction": "timed process with physical loading prerequisites checked every physics step",
                        }
                    )

    def snapshot(self):
        objects = {}
        for oid, bid in self._object_bodies.items():
            velocity = np.zeros(6)
            mujoco.mj_objectVelocity(
                self.model, self.data, mujoco.mjtObj.mjOBJ_BODY, bid, velocity, 0
            )
            obj = self._objects[oid]
            state = {
                "position": self.data.xpos[bid].tolist(),
                "quaternion": self.data.xquat[bid].tolist(),
                "size": list(obj.size),
                "velocity": velocity[3:].tolist(),
                "held_by": next((h for h, o in self._last_held if o == oid), None),
                "process_states": dict(self._process_states.get(oid, {})),
                "contacts": [
                    b if a == oid else a for a, b in self._last_contact_pairs if oid in (a, b)
                ],
            }
            if getattr(obj, "joint", None):
                state["joint_position"] = float(self.data.joint("joint_" + oid).qpos[0])
            objects[oid] = state
        return {
            "time": float(self.data.time),
            "objects": objects,
            "robot": {"position": self.data.qpos[:3].tolist(), "yaw": self._yaw()},
            "events": [dict(e) for e in self._events],
            "qpos": self.data.qpos.tolist(),
            "qvel": self.data.qvel.tolist(),
            "model_fingerprint": self._model_fingerprint,
        }

    def execute(self, action):
        if self._closed:
            raise RuntimeError("Runtime is closed")
        name, args = action.get("tool"), action.get("arguments", {})
        start = float(self.data.time)
        initial_yaw = self._yaw()
        initial_distance = self._odometry.distance if self._odometry is not None else None
        try:
            if name == "drive":
                result = self._drive(float(args.get("distance", 0)), float(args.get("turn", 0)))
            elif name == "move_arm":
                result = self._move_arm(
                    args["hand"],
                    world_point(args["position"], *self._base_transform()),
                    args.get("approach", "keep"),
                )
            elif name == "gripper":
                result = self._gripper(**args)
            elif name == "wait":
                seconds = float(args["seconds"])
                if not math.isfinite(seconds) or not 0 <= seconds <= 5:
                    raise ValueError("seconds must be between0 and5")
                self._step(seconds)
                result = {"status": "executed"}
            elif name == "query_depth":
                result = self._query_depth(args["pixel"])
            elif name == "look":
                result = {"status": "executed"}
            else:
                raise ValueError(f"Unsupported tool: {name}")
        except RobotPhysicalFailure as exc:
            result = {"status": "physical_failure", "error": str(exc)}
        except SimulationTimeLimit:
            result = {
                "status": "time_limit",
                "note": "Simulation budget exhausted; task is graded from the recorded state.",
            }
        except (KeyError, ValueError, TypeError) as exc:
            result = {"status": "invalid_action", "error": str(exc)}
        # Controller internals may use world coordinates; none cross the actor boundary.
        if "actual_position" in result:
            result["actual_position"] = local_point(result["actual_position"], *self._base_transform())
            result["frame"] = "base"
        for field in ("position", "yaw", "contact_detected", "note"):
            result.pop(field, None)
        if name == "drive":
            result.pop("distance_traveled", None)
            result.pop("turn_reached", None)
            if result.get("status") in {"reached", "blocked_or_unreached"}:
                result["status"] = "executed"
            result["measured_turn_rad"] = math.atan2(math.sin(self._yaw() - initial_yaw), math.cos(self._yaw() - initial_yaw))
            if initial_distance is not None:
                result["encoder_distance_m"] = self._odometry.distance - initial_distance
        self._last_result = {
            "tool": name,
            **result,
            "simulated_seconds": float(self.data.time) - start,
        }
        if not self.trace or self.data.time > self.trace[-1]["time"]:
            self._sample()
        return self._last_result

    def _gripper(self, hand, opening):
        opening = float(opening)
        if hand not in self._arm_joints or not 0 <= opening <= 0.045:
            raise ValueError("hand must be left/right; opening must be between0 and0.045 metres")
        for finger in ("left", "right"):
            self.data.ctrl[
                self.model.actuator(f"gripper_{hand}_{finger}_finger_joint_position").id
            ] = opening
        self._step(0.8)
        return {
            "status": "executed",
            "finger_positions": [
                float(self.data.joint(f"gripper_{hand}_{f}_finger_joint").qpos[0])
                for f in ("left", "right")
            ],
            "contact_detected": any(h == hand for h, _ in self._last_held),
            "note": "Bilateral finger contact does not guarantee a stable grasp.",
        }

    def _wheel_command(self, target, seconds):
        self.data.ctrl[:4] += np.clip(target - self.data.ctrl[:4], -2 * seconds, 2 * seconds)
        self._step(seconds)

    def _drive(self, distance, turn):
        if (
            not all(math.isfinite(x) for x in [distance, turn])
            or abs(distance) > 2
            or abs(turn) > math.pi
        ):
            raise ValueError("drive distance must be within±2m and turn within±pi radians")
        if self._odometry is None:
            raise RuntimeError("Wheel drive requires encoder odometry")
        beginning = self._odometry.position.copy()
        initial_yaw = self._odometry.yaw
        direction = np.array([math.cos(initial_yaw), math.sin(initial_yaw)])
        elapsed = 0
        while elapsed < abs(distance) / 0.13 + 2:
            traveled = float((self._odometry.position - beginning) @ direction)
            error = distance - traveled
            if abs(error) < 0.015:
                break
            speed = float(np.clip(error * 2, -0.18, 0.18))
            heading_error = math.atan2(
                math.sin(initial_yaw - self._odometry.yaw), math.cos(initial_yaw - self._odometry.yaw)
            )
            yaw_rate = float(np.clip(heading_error * 3, -0.5, 0.5))
            correction = yaw_rate * 0.223175 / 0.0762
            self._wheel_command(
                np.full(4, speed / 0.0762)
                + np.array([correction, -correction, correction, -correction]),
                0.04,
            )
            elapsed += 0.04
        for _ in range(25):
            self._wheel_command(np.zeros(4), 0.04)
        yaw_start = self._odometry.yaw
        elapsed = 0
        while elapsed < abs(turn) / 0.2 + 2:
            turned = math.atan2(
                math.sin(self._odometry.yaw - yaw_start), math.cos(self._odometry.yaw - yaw_start)
            )
            error = turn - turned
            if abs(error) < 0.025:
                break
            omega = float(np.clip(error * 2, -0.35, 0.35))
            wheel = omega * 0.223175 / 0.0762
            self._wheel_command(np.array([wheel, -wheel, wheel, -wheel]), 0.04)
            elapsed += 0.04
        for _ in range(25):
            self._wheel_command(np.zeros(4), 0.04)
        traveled = float((self._odometry.position - beginning) @ direction)
        return {
            "status": "executed",
            "translation_feedback": "wheel_odometry",
            "estimated_distance_m": traveled,
        }

    def _arm_controls(self, hand):
        actuators = [self.model.actuator(f"arm_{hand}_{i}_joint_position").id for i in range(1, 8)]
        return self.data.ctrl[actuators].copy()

    def _command_arm(self, hand, target):
        actuators = [self.model.actuator(f"arm_{hand}_{i}_joint_position").id for i in range(1, 8)]
        self.data.ctrl[actuators] = target

    def _orientation_target(self, hand, approach, orientation):
        if approach == "down":
            orientation = np.diag([-1.0, 1.0, -1.0]) if hand == "right" else np.eye(3)
        elif approach == "forward":
            z = np.array([1.0, 0.0, 0.0]) * (1 if hand == "right" else -1)
            x = np.array([0.0, 1.0, 0.0])
            orientation = np.column_stack([x, np.cross(z, x), z])
        elif approach != "keep":
            raise ValueError("approach must be down, forward, or keep")
        return orientation

    def _move_arm(self, hand, target, approach="keep"):
        if hand not in self._arm_joints or target.shape != (3,) or not np.isfinite(target).all():
            raise ValueError(
                "hand must be left/right and position must be three finite coordinates"
            )
        if np.linalg.norm(target - self.data.qpos[:3]) > 2:
            raise ValueError("Target is outside this robot's local reach; drive closer first")
        joints = self._arm_joints[hand]
        qids = np.array([self.model.jnt_qposadr[j] for j in joints])
        vids = np.array([self.model.jnt_dofadr[j] for j in joints])
        scratch = self._ik_data
        scratch.qpos[:] = self.data.qpos
        mujoco.mj_forward(self.model, scratch)
        site = self._sites[hand]
        start_orientation = scratch.site_xmat[site].reshape(3, 3).copy()
        start_orientation = self._orientation_target(hand, approach, start_orientation)
        jp, jr = np.zeros((3, self.model.nv)), np.zeros((3, self.model.nv))
        best = None
        rng = np.random.default_rng(0)
        initial_q = scratch.qpos[qids].copy()
        low, high = self.model.jnt_range[joints, 0], self.model.jnt_range[joints, 1]
        for attempt in range(10):
            if attempt == 0:
                scratch.qpos[qids] = initial_q
            elif attempt == 1:
                scratch.qpos[qids] = (low + high) / 2
            else:
                scratch.qpos[qids] = rng.uniform(low, high)
            for _ in range(240):
                mujoco.mj_forward(self.model, scratch)
                error = target - scratch.site_xpos[site]
                mujoco.mj_jacSite(self.model, scratch, jp, jr, site)
                rotation = scratch.site_xmat[site].reshape(3, 3)
                rotation_delta = start_orientation @ rotation.T
                quaternion = np.empty(4)
                mujoco.mju_mat2Quat(quaternion, rotation_delta.ravel())
                if quaternion[0] < 0:
                    quaternion *= -1
                sin_half = np.linalg.norm(quaternion[1:])
                orientation_error = quaternion[1:] * (
                    2 * math.atan2(sin_half, quaternion[0]) / max(sin_half, 1e-12)
                )
                score = np.linalg.norm(error) + 0.2 * np.linalg.norm(orientation_error)
                if best is None or score < best[0]:
                    best = (
                        score,
                        scratch.qpos[qids].copy(),
                        float(np.linalg.norm(error)),
                        float(np.linalg.norm(orientation_error)),
                    )
                if np.linalg.norm(error) < 0.006 and np.linalg.norm(orientation_error) < 0.04:
                    break
                jac = np.vstack([jp[:, vids], 0.2 * jr[:, vids]])
                residual = np.concatenate([error, 0.2 * orientation_error])
                delta = jac.T @ np.linalg.solve(jac @ jac.T + 0.001 * np.eye(6), residual)
                scratch.qpos[qids] = np.clip(
                    scratch.qpos[qids] + np.clip(delta, -0.12, 0.12), low, high
                )
            if best[2] < 0.012 and best[3] < 0.08:
                break
        scratch.qpos[qids] = best[1]
        mujoco.mj_forward(self.model, scratch)
        predicted_error = best[2]
        if predicted_error > 0.04 or best[3] > 0.2:
            return {
                "status": "unreachable",
                "ik_error_m": predicted_error,
                "orientation_error_rad": best[3],
                "actual_position": self.data.site_xpos[site].tolist(),
            }
        initial = self._arm_controls(hand)
        goal = scratch.qpos[qids].copy()
        duration = max(1.0, float(np.max(np.abs(goal - initial))) / 0.45)
        steps = math.ceil(duration / 0.02)
        for i in range(steps):
            self._command_arm(hand, initial + (goal - initial) * ((i + 1) / steps))
            self._step(0.02)
        self._step(0.4)
        actual_error = float(np.linalg.norm(target - self.data.site_xpos[site]))
        return {
            "status": "reached" if actual_error < 0.05 else "blocked_or_unreached",
            "error_m": actual_error,
            "actual_position": self.data.site_xpos[site].tolist(),
        }

    def _get_renderer(self, *, overview=False):
        if not self._render_enabled:
            raise RuntimeError("Camera rendering is disabled for this runtime")
        if overview:
            if self._overview_renderer is None:
                self._overview_renderer = mujoco.Renderer(self.model, height=720, width=960)
            return self._overview_renderer
        if self._renderer is None:
            self._renderer = mujoco.Renderer(self.model, height=480, width=640)
        return self._renderer

    def _spectator_obstruction(self, origin, direction, maximum):
        """Ray-test actual scene surfaces, stepping past robot visual/collision geoms."""
        traveled = 0.0
        hit = np.empty(1, dtype=np.int32)
        for _ in range(64):
            distance = mujoco.mj_ray(self.model, self.data, origin+traveled*direction,
                                    direction, None, True, -1, hit)
            if distance < 0 or traveled+distance > maximum:
                return None
            traveled += distance
            if int(hit[0]) in self._geom_objects or self.model.geom(int(hit[0])).name == "floor":
                return traveled
            traveled += 1e-5
        return 0.0  # An unresolved dense ray is not evidence of clearance.

    def render(self, *, view="robot", azimuth=135, elevation=-25, distance=4, lookat=None):
        renderer = self._get_renderer(overview=view != "robot")
        renderer.disable_depth_rendering()
        renderer.disable_segmentation_rendering()
        camera = "robot_rgbd"
        if view != "robot":
            camera = mujoco.MjvCamera()
            camera.type = mujoco.mjtCamera.mjCAMERA_FREE
            camera.lookat[:] = lookat if lookat is not None else [*self.data.qpos[:2], 0.8]
            from .spectator import clear_camera
            near = self.model.vis.map.znear * self.model.stat.extent
            clearance = max(.25, near*math.tan(math.radians(self.model.vis.global_.fovy)/2)*5/3)
            camera.azimuth, camera.elevation, camera.distance = clear_camera(
                camera.lookat, distance, azimuth, elevation, self._spectator_obstruction,
                clearance=clearance)
        renderer.update_scene(self.data, camera=camera)
        pixels = renderer.render().copy()
        output = io.BytesIO()
        Image.fromarray(pixels).save(output, format="PNG")
        return output.getvalue()

    def _base_transform(self):
        bid = int(self.model.jnt_bodyid[0])
        return self.data.xpos[bid].copy(), self.data.xmat[bid].reshape(3, 3).copy()

    def _wheel_angles(self):
        return [float(self.data.joint(f"wheel_{end}_{side}_joint").qpos[0])
                for end in ("front", "rear") for side in ("right", "left")]

    def _camera_calibration(self):
        camera = self.model.camera("robot_rgbd").id
        origin, rotation = self._base_transform()
        focal = 240 / math.tan(math.radians(self.model.cam_fovy[camera]) / 2)
        position = np.asarray(local_point(self.data.cam_xpos[camera], origin, rotation))
        orientation = rotation.T @ self.data.cam_xmat[camera].reshape(3, 3)
        return focal, position, orientation

    def _depth(self):
        renderer = self._get_renderer()
        renderer.enable_depth_rendering()
        try:
            renderer.update_scene(self.data, camera="robot_rgbd")
            return renderer.render().copy()
        finally:
            renderer.disable_depth_rendering()

    def _query_depth(self, pixel):
        if not self._render_enabled:
            return {"status": "sensor_unavailable"}
        if not isinstance(pixel, (list, tuple)) or len(pixel) != 2:
            raise ValueError("pixel must be [u,v]")
        return depth_point(self._depth(), pixel, *self._camera_calibration())

    def _finger_positions(self, hand):
        return [
            float(self.data.joint(f"gripper_{hand}_{f}_finger_joint").qpos[0])
            for f in ("left", "right")
        ]

    @property
    def tools(self):
        return TOOLS

    def observe(self):
        origin, rotation = self._base_transform()
        yaw = self._yaw() - self._initial_yaw
        observation = {
            "time": float(self.data.time),
            "robot": {
                "frame": "base",
                "relative_heading_rad": math.atan2(math.sin(yaw), math.cos(yaw)),
                "gravity_direction": (rotation.T @ np.array([0., 0., -1.])).tolist(),
                "odometry": self._odometry.observation() if self._odometry is not None else None,
                "hands": {
                    h: {"position": local_point(self.data.site_xpos[s], origin, rotation),
                        "arm_joint_positions": self.data.qpos[self.model.jnt_qposadr[self._arm_joints[h]]].tolist(),
                        "finger_positions": self._finger_positions(h)}
                    for h, s in self._sites.items()
                },
            },
            "tools": self.tools,
            "last_tool_result": self._last_result,
            "sensors": {"model": SENSOR_CONTRACT["kind"],
                        "camera_available": self._render_enabled,
                        "noise": SENSOR_CONTRACT["noise"]},
        }
        if self._render_enabled:
            observation["image"] = self.render()
            focal, position, orientation = self._camera_calibration()
            observation["sensors"]["camera"] = {
                "width": 640, "height": 480, "fx": focal, "fy": focal, "cx": 319.5, "cy": 239.5,
                "frame": "base", "position": position.tolist(), "rotation": orientation.tolist(),
                "axes": "camera X right, Y up, looks along -Z",
            }
            depth = self._depth()[10::20, 10::20]
            observation["sensors"]["depth"] = {
                "units": "optical-axis metres", "pixel_origin": [10, 10], "pixel_stride": 20,
                "values": [[round(float(z), 3) if np.isfinite(z) and .05 <= z <= 10 else None for z in row] for row in depth],
                "missing": "null means outside supported depth range; query_depth samples a full-resolution pixel",
            }
        return observation

    @property
    def manifest(self):
        return {
            "engine": "mujoco",
            "engine_version": mujoco.__version__,
            "timestep": float(self.model.opt.timestep),
            "robot": ROBOT_MANIFESTS[self.spec.robot],
            "trace_interval": 0.05,
            "controller": "tiago-encoder-odometry-pd-local-ik-v2",
            "clock": "stepped",
            "seed": self.seed,
            "model_fingerprint": self._model_fingerprint,
            "action_contract": self.tools,
            "sensor_contract": copy.deepcopy(SENSOR_CONTRACT),
            "implementation": copy.deepcopy(self._implementation),
            "implementation_fingerprint": self._implementation_fingerprint,
        }

    def restore_snapshot(self, snapshot):
        """Restore recorded state for rendering only; never exposed to policy tools."""
        if snapshot.get("model_fingerprint") != self.manifest["model_fingerprint"]:
            raise ValueError("Replay state belongs to a different compiled model")
        qpos, qvel = np.asarray(snapshot["qpos"]), np.asarray(snapshot["qvel"])
        if qpos.shape != (self.model.nq,) or qvel.shape != (self.model.nv,):
            raise ValueError("Invalid replay state dimensions")
        self.data.qpos[:] = qpos
        self.data.qvel[:] = qvel
        self.data.time = snapshot["time"]
        mujoco.mj_forward(self.model, self.data)

    def close(self):
        self._live_view = None
        if self._overview_renderer is not None:
            self._overview_renderer.close()
            self._overview_renderer = None
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
        self._closed = True
