"""G1 with Unitree's published velocity policy and independently commanded arms.

The ONNX policy controls balance through joint servos. No root forces, root
kinematics, object attachments, or motion playback are used.
"""

from __future__ import annotations

import copy
import hashlib
import math
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import onnxruntime as ort

from .appearance import add_appearance
from .runtime import HAND, NUMBER, TOOLS, RobotPhysicalFailure, Runtime, _tool


class G1Runtime(Runtime):
    def _build_xml(self):
        self._policy_path = self.asset_path / "policy.onnx"
        self._policy_sha256 = hashlib.sha256(self._policy_path.read_bytes()).hexdigest()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        self._policy = ort.InferenceSession(
            str(self._policy_path), options, providers=["CPUExecutionProvider"]
        )
        meta = self._policy.get_modelmeta().custom_metadata_map
        self._policy_joint_names = meta["joint_names"].split(",")
        self._defaults = np.fromstring(meta["default_joint_pos"], sep=",")
        self._scales = np.fromstring(meta["action_scale"], sep=",")
        stiffness = np.fromstring(meta["joint_stiffness"], sep=",")
        damping = np.fromstring(meta["joint_damping"], sep=",")
        if len(self._policy_joint_names) != 29 or self._policy.get_inputs()[0].shape != [1, 98]:
            raise ValueError(
                "G1 policy contract does not match the pinned 29-joint/98-input controller"
            )
        root = ET.parse(self.asset_path / "g1_with_hands.xml").getroot()
        root.find("compiler").set("meshdir", str(self.asset_path / "assets"))
        root.find("option").set("timestep", "0.002")
        root.find("option").set("iterations", "60")
        root.find("default/default/position").attrib.pop("dampratio", None)
        for index, name in enumerate(self._policy_joint_names):
            actuator = root.find(f"./actuator/position[@joint='{name}']")
            actuator.set("kp", str(stiffness[index]))
            actuator.set("kv", str(damping[index]))
        # Attach the licensed real Dex1 parallel-gripper description while
        # retaining the already-tested Menagerie body and locomotion joint order.
        dex1 = ET.parse(self.asset_path / "dex1_source.xml").getroot()
        asset = root.find("asset")
        for mesh in dex1.findall("./asset/mesh"):
            if "dex1" in mesh.get("file", "").lower():
                mesh = copy.deepcopy(mesh)
                mesh.set("file", str(self.asset_path / mesh.get("file")))
                asset.append(mesh)
        for actuator in list(root.find("actuator")):
            if "hand_" in actuator.get("joint", ""):
                root.find("actuator").remove(actuator)
        for hand in ("left", "right"):
            wrist = root.find(f".//body[@name='{hand}_wrist_yaw_link']")
            source_wrist = dex1.find(f".//body[@name='{hand}_wrist_yaw_link']")
            wrist.remove(wrist.find("inertial"))
            wrist.insert(0, copy.deepcopy(source_wrist.find("inertial")))
            for child in list(wrist):
                if child.tag == "body" or "hand_palm" in child.get("mesh", ""):
                    wrist.remove(child)
            gripper = copy.deepcopy(source_wrist.find(f"body[@name='{hand}_dex1_base_link']"))
            gripper.find("site").set("name", hand + "_grasp")
            wrist.append(gripper)
            for index in (1, 2):
                name = f"{hand}_dex1_finger_joint_{index}"
                gripper.find(f".//joint[@name='{name}']").set("range", "-.020 .0245")
                ET.SubElement(
                    root.find("actuator"),
                    "position",
                    name=name,
                    joint=name,
                    kp="500",
                    kv="5",
                    forcerange="-20 20",
                    ctrlrange="-.020 .0245",
                )
        root.remove(root.find("keyframe"))
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
        )
        torso = root.find(".//body[@name='torso_link']")
        ET.SubElement(
            torso,
            "camera",
            name="robot_rgbd",
            pos=".06 0 .35",
            xyaxes="0 -1 0 .5 0 .8660254",
            fovy="75",
        )
        for index, geom in enumerate(world.findall(".//geom")):
            if "name" not in geom.attrib:
                geom.set("name", f"g1_geom_{index}")
        return self._append_objects(root)

    def _setup_robot(self):
        self._policy_joints = np.array([self.model.joint(n).id for n in self._policy_joint_names])
        self._policy_q = self.model.jnt_qposadr[self._policy_joints]
        self._policy_v = self.model.jnt_dofadr[self._policy_joints]
        self._policy_actuators = np.array(
            [self.model.actuator(n).id for n in self._policy_joint_names]
        )
        self._pelvis = self.model.body("pelvis").id
        self._sites = {h: self.model.site(h + "_grasp").id for h in ("left", "right")}
        suffixes = [
            "shoulder_pitch",
            "shoulder_roll",
            "shoulder_yaw",
            "elbow",
            "wrist_roll",
            "wrist_pitch",
            "wrist_yaw",
        ]
        self._arm_joints = {
            h: [self.model.joint(f"{h}_{s}_joint").id for s in suffixes] for h in ("left", "right")
        }
        self._arm_indices = {
            h: np.array([self._policy_joint_names.index(f"{h}_{s}_joint") for s in suffixes])
            for h in ("left", "right")
        }
        self._finger_joints = {
            h: [self.model.joint(f"{h}_dex1_finger_joint_{i}").id for i in (1, 2)]
            for h in ("left", "right")
        }
        self._finger_bodies = {
            h: [self.model.body(f"{h}_dex1_finger_link_{i}").id for i in (1, 2)]
            for h in ("left", "right")
        }
        self._arm_overrides = {}
        self._command = np.zeros(3)
        self._last_action = np.zeros(29)
        self._phase = 0.0
        self._next_policy_time = 0.0
        self._fallen = False

    def _reset(self):
        mujoco.mj_resetData(self.model, self.data)
        x, y, yaw = self.spec.robot_start
        self.data.qpos[:3] = [x, y, 0.79]
        self.data.qpos[3:7] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
        self.data.qpos[self._policy_q] = self._defaults
        self.data.ctrl[self._policy_actuators] = self._defaults
        for hand in ("left", "right"):
            for jid in self._finger_joints[hand]:
                self.data.qpos[self.model.jnt_qposadr[jid]] = 0.0245
                self.data.ctrl[self.model.actuator(self.model.joint(jid).name).id] = 0.0245
        for obj in self.spec.objects:
            if obj.joint:
                self.data.joint("joint_" + obj.id).qpos[0] = obj.joint.initial
        mujoco.mj_forward(self.model, self.data)
        self._settle_reset(before_step=self._before_step)
        self.data.time = 0
        self._next_policy_time = 0
        self._phase = 0
        self._sample()

    def _before_step(self):
        up = self.data.xmat[self._pelvis].reshape(3, 3)[2, 2]
        if self.data.qpos[2] < 0.4 or up < 0.45:
            self._fallen = True
            raise RobotPhysicalFailure(
                "G1 fell; the physical trajectory is a task failure, not a valid standing rollout"
            )
        if self.data.time + 1e-8 < self._next_policy_time:
            return
        self._phase = (self._phase + 0.02 / 0.6) % 1
        phase = (
            np.array([math.sin(2 * math.pi * self._phase), math.cos(2 * math.pi * self._phase)])
            if np.linalg.norm(self._command) >= 0.1
            else np.zeros(2)
        )
        rotation = self.data.xmat[self._pelvis].reshape(3, 3)
        observation = np.concatenate(
            [
                self.data.qvel[3:6],
                rotation.T @ np.array([0.0, 0.0, -1.0]),
                self._command,
                phase,
                self.data.qpos[self._policy_q] - self._defaults,
                self.data.qvel[self._policy_v],
                self._last_action,
            ]
        ).astype(np.float32)
        # The locomotion network owns lower-body balance. While an arm is
        # independently servoed, hold its policy features at the nominal pose;
        # actual IMU/leg state and physical reaction forces remain in feedback.
        for hand in self._arm_overrides:
            indices = self._arm_indices[hand]
            observation[11 + indices] = 0
            observation[40 + indices] = 0
            observation[69 + indices] = 0
        self._last_action = self._policy.run(None, {"obs": observation[None]})[0][0]
        target = self._defaults + self._scales * self._last_action
        for hand, q in self._arm_overrides.items():
            indices = self._arm_indices[hand]
            actuators = self._policy_actuators[indices]
            dofs = self._policy_v[indices]
            # Feed-forward compensation goes through torque-limited servos;
            # no force is applied to the floating base or objects.
            stiffness = self.model.actuator_gainprm[actuators, 0]
            target[indices] = q + self.data.qfrc_bias[dofs] / stiffness
        self.data.ctrl[self._policy_actuators] = target
        self._next_policy_time = float(self.data.time) + 0.02

    def _halt_command(self):
        self._command[:] = 0

    def _drive(self, distance, turn):
        if (
            not all(math.isfinite(x) for x in (distance, turn))
            or abs(distance) > 2
            or abs(turn) > math.pi
        ):
            raise ValueError("distance must be within±2m and turn within±pi radians")
        # This preset has IMU/encoders but no position estimator. Command
        # nominal travel by velocity and time; never close the loop on qpos XY.
        yaw = self._yaw()
        speed = .25
        translation_seconds = abs(distance) / speed
        elapsed = 0.
        while elapsed + 1e-9 < translation_seconds:
            heading_error = math.atan2(math.sin(yaw - self._yaw()), math.cos(yaw - self._yaw()))
            self._command[:] = [math.copysign(speed, distance), 0, np.clip(heading_error * 1.5, -0.35, 0.35)]
            duration = min(.04, translation_seconds-elapsed)
            self._step(duration)
            elapsed += duration
        self._command[:] = 0
        self._step(0.6)
        turn_start = self._yaw()
        elapsed = 0
        while elapsed < abs(turn) / 0.2 + 3:
            turned = math.atan2(
                math.sin(self._yaw() - turn_start), math.cos(self._yaw() - turn_start)
            )
            error = turn - turned
            if abs(error) < 0.035:
                break
            self._command[:] = [0, 0, math.copysign(max(0.15, min(0.4, abs(error) * 1.5)), error)]
            self._step(0.04)
            elapsed += 0.04
        self._command[:] = 0
        self._step(0.6)
        return {
            "status": "executed",
            "translation_feedback": "unavailable",
            "commanded_translation_seconds": translation_seconds,
        }

    def _arm_controls(self, hand):
        return self._arm_overrides.get(
            hand, self.data.qpos[self.model.jnt_qposadr[self._arm_joints[hand]]]
        ).copy()

    def _command_arm(self, hand, target):
        self._arm_overrides[hand] = target.copy()
        ids = self._policy_actuators[self._arm_indices[hand]]
        stiffness = np.array([60, 60, 40, 40, 20, 15, 15])
        damping = np.array([3, 3, 2, 2, 1, 0.8, 0.8])
        self.model.actuator_gainprm[ids, 0] = stiffness
        self.model.actuator_biasprm[ids, 1] = -stiffness
        self.model.actuator_biasprm[ids, 2] = -damping
        self.data.ctrl[ids] = target

    def _orientation_target(self, hand, approach, orientation):
        if approach == "down":
            orientation = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])
        elif approach == "forward":
            orientation = np.eye(3)
        elif approach == "keep":
            return orientation
        else:
            raise ValueError("approach must be down, forward, or keep")
        return self._base_transform()[1] @ orientation

    def _move_arm(self, hand, target, approach="keep"):
        # Replan against actual base motion from balance; all correction steps
        # still execute through the same bounded joint servos.
        result = super()._move_arm(hand, target, approach)
        for _ in range(3):
            if (
                result["status"] not in ("reached", "blocked_or_unreached")
                or (result.get("error_m", 0) < 0.012 and result.get("orientation_error_rad", 0) < 0.08)
            ):
                break
            result = super()._move_arm(hand, target, approach)
        return result

    def _gripper(self, hand, closure):
        closure = float(closure)
        if hand not in self._finger_joints or not 0 <= closure <= 1:
            raise ValueError("hand must be left/right and closure between0(open) and1(closed)")
        pose = [0.0245 - closure * 0.0445] * 2
        actuator_ids = [
            self.model.actuator(self.model.joint(j).name).id for j in self._finger_joints[hand]
        ]
        initial = self.data.ctrl[actuator_ids].copy()
        goal = np.array(pose)
        duration = max(0.8, float(np.max(np.abs(goal - initial))) / 0.6)
        steps = math.ceil(duration / 0.02)
        for i in range(steps):
            self.data.ctrl[actuator_ids] = initial + (goal - initial) * ((i + 1) / steps)
            self._step(0.02)
        self._step(0.4)
        return {
            "status": "executed",
            "joint_positions": self._finger_positions(hand),
            "contact_detected": any(h == hand for h, _ in self._last_held),
            "note": "Opposing finger contacts do not guarantee a stable grasp.",
        }

    def _finger_positions(self, hand):
        return self.data.qpos[self.model.jnt_qposadr[self._finger_joints[hand]]].tolist()

    @property
    def tools(self):
        tools = [copy.deepcopy(t) for t in TOOLS if t["name"] not in {"gripper", "move_torso", "move_arm_joints"}]
        for tool in tools:
            if tool["name"] == "move_arm":
                tool["parameters"]["properties"]["approach"]["enum"] = ["down", "forward", "keep"]
                tool["description"] = tool["description"].replace(" forward_down (45 degrees forward and down, TIAGo++ only),", "")
        tools[0] = _tool(
            "drive",
            "Walk a nominal distance using a timed 0.25m/s velocity command, then turn using relative IMU heading. Linear displacement is not measured: use RGB-D to assess actual progress. Contact, balance and obstacles can cause failure. Maximum2m and pi radians.",
            {"distance": NUMBER, "turn": NUMBER},
            ["distance", "turn"],
        )
        tools.append(
            _tool(
                "gripper",
                "Set Dex1 parallel-gripper closure:0 opens,1 closes. Uses physical sliding finger contacts, no object attachment.",
                {"hand": HAND, "closure": NUMBER},
                ["hand", "closure"],
            )
        )
        return tools

    @property
    def manifest(self):
        result = super().manifest
        result.update(
            controller="unitree-g1-dex1-timed-velocity-onnx-with-arm-override-v2",
            policy_frequency_hz=50,
            policy_sha256=self._policy_sha256,
            policy_source_revision="1425b15f73bd4095f0df53709d7c389c3eb9e790",
            arm_control="Torque-limited IK servos with bias compensation and nominal commanded-arm policy feature masking; actual IMU and leg feedback control balance",
        )
        return result
