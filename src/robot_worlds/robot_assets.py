"""Pinned, licensed robot descriptions and their demonstrated runtime capabilities."""

from pathlib import Path

ASSET_ROOT = Path(__file__).parent / "assets" / "robots"
MENAGERIE_REVISION = "f3b6013629c7b94f8ee93fd977b161fa8350f9bc"

ROBOT_MANIFESTS = {
    "tiago++": {
        "name": "PAL Robotics TIAGo++ with parallel grippers",
        "source": "https://github.com/google-deepmind/mujoco_menagerie/tree/"
        + MENAGERIE_REVISION
        + "/pal_tiago_dual",
        "revision": MENAGERIE_REVISION,
        "license": "Apache-2.0",
        "model": "tiago_dual_position.xml",
        "capabilities": [
            "wheel_drive_forward_yaw",
            "dual_arm_position_control",
            "contact_grippers",
        ],
        "limitations": [
            "Wheel collisions use explicit simplified passive rollers; lateral driving is not exposed.",
            "Finger collision hulls are replaced by shaft/pad primitives with estimated friction.",
            "Ideal semantic RGB-D and proprioception; no real sensor noise model.",
            "Cartesian arm control uses local IK and does not guarantee collision-free paths.",
        ],
    },
    "g1": {
        "name": "Unitree G1 EDU 29-DOF body with Dex1-1 parallel grippers",
        "source": "https://github.com/google-deepmind/mujoco_menagerie/tree/"
        + MENAGERIE_REVISION
        + "/unitree_g1",
        "revision": MENAGERIE_REVISION,
        "license": "BSD-3-Clause",
        "gripper_source": "https://github.com/unitreerobotics/unitree_ros/tree/3ae893d905d11795ddf1075825ee5010364a1109/robots/g1_description",
        "gripper_adapter_revision": "68459ed68f6f68e1f661091dfcb6ebce44681aec",
        "controller_license": "Apache-2.0",
        "capabilities": [
            "learned_contact_locomotion",
            "contact_dex1_grippers",
            "joint_servo_arm_control",
        ],
        "limitations": [
            "Velocity policy validated on flat ground; arbitrary terrain and payloads are not certified.",
            "Independent arm mode masks nominal upper-body policy features; balance can still fail.",
        ],
    },
}


def robot_asset_path(robot: str) -> Path:
    if robot not in ("tiago++", "g1"):
        raise ValueError(f"Robot {robot!r} has no validated execution controller in this version")
    return ASSET_ROOT / ("tiago" if robot == "tiago++" else "g1")
