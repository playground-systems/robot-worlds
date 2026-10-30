# Unitree G1 EDU 29-DOF body with Dex1-1 parallel grippers

The MJCF and meshes are copied unmodified from MuJoCo Menagerie:
https://github.com/google-deepmind/mujoco_menagerie/tree/f3b6013629c7b94f8ee93fd977b161fa8350f9bc/unitree_g1

The retained body description has 29 body joints. Its BSD-3-Clause
license, copyright and redistribution conditions are preserved in LICENSE.

The published velocity controller is copied from Unitree Robotics:
https://github.com/unitreerobotics/unitree_rl_mjlab/tree/1425b15f73bd4095f0df53709d7c389c3eb9e790/deploy/robots/g1/config/policy/velocity/v0

policy.onnx and deploy.yaml are shipped with that repository's Apache-2.0 license
in CONTROLLER_LICENSE, copied from its pinned root [LICENCE](https://github.com/unitreerobotics/unitree_rl_mjlab/blob/1425b15f73bd4095f0df53709d7c389c3eb9e790/LICENCE).
The upstream license Git blob is `7b72d429a403d760f5ca0e260ea324dbb35854c9`;
the retained file matches its SHA-256 byte for byte:
`ff28cacaef7b9919a0fb905136cf4ccffba5191522bf5d024c4339932c0edc28`.
The ONNX metadata identifies joint names, action scaling,
default positions, stiffness and damping. The deployment implementation specifying
gait phase and observation semantics is:
https://github.com/unitreerobotics/unitree_rl_mjlab/blob/1425b15f73bd4095f0df53709d7c389c3eb9e790/deploy/include/isaaclab/envs/mdp/observations/observations.h

Robot Worlds runs the 98-input/29-output network locally with ONNX Runtime CPU at
50Hz and MuJoCo physics at 500Hz. Observations are body angular velocity, projected
gravity, commanded velocity, gait phase, joint offsets, joint velocities and
previous actions. No external root forces or base constraints provide balance.

The generated scene removes the Dex3 hand subtrees and attaches genuine Dex1-1
parallel grippers (two prismatic finger joints each). Gripper meshes are byte-for-
byte identical to Unitree's BSD-3-Clause assets at:
https://github.com/unitreerobotics/unitree_ros/tree/3ae893d905d11795ddf1075825ee5010364a1109/robots/g1_description/meshes

DEX1_UPSTREAM_LICENSE retains that license. Mesh Git blob IDs, in filename order:
Dex1_base_link.STL: 36bd5096362394cfcc2fea809932588d7f0e98fd
Dex1_finger_link_1.STL: ab7c48af1d013ee2928d9afa4784c5b50eec275e
Dex1_finger_link_2.STL: e3dda4bef646f27bf6692078c15fd62c389fcc2b
 dex1_col_1.stl: 7066aa69ca07ffc5ec9e4b6f7bc3fbbdeb589f0d
 dex1_col_2.stl: fb49684490facc26bf2ad773ecec7cb50aec84c8

The gripper MJCF conversion and original source URDF are retained as dex1_source.xml
and dex1_source.urdf, from LeRobot revision:
https://huggingface.co/lerobot/unitree-g1-mujoco/tree/68459ed68f6f68e1f661091dfcb6ebce44681aec

The conversion credits HIW-500 revision c69d89d88bb51774fe9a3684b90d9ff1abf801da.
DEX1_LICENSE and DEX1_NOTICE retain its Apache-2.0 attribution; its notice explicitly
does not relicense Unitree meshes. Robot Worlds uses the official -0.020m lower
finger limit, not the conversion's optional -0.023m mesh trim. Fingers use bounded
position servos and the documented 20N per-finger effort limit.

The generated scene derives an in-memory model with policy-matched body servo
gains, controlled fingers, camera and grasp sites. For independent manipulation,
arm targets come from differential IK and torque-limited position servos with
joint bias-force compensation. The locomotion network's commanded-arm state and
previous-action features are explicitly held at their nominal values during that
mode; IMU and leg feedback remain physical. This adaptation allows independent
upper-body control but is not a manipulation-trained whole-body policy or proof
of robustness to arbitrary loads, trajectories, obstacles or terrain.

Physical failures and falls remain visible in tool results and recorded state.
The exact compiled model, controller version and policy SHA-256 accompany reports.

Local validation on MuJoCo 3.17 / Apple silicon covers standing, walking, turning,
arm reaching, a contact grasp of a 30g rigid block, lifting, a 2s hold, carrying while
walking, and release. These bounded tests do not certify arbitrary payloads or
terrain. Controller policy SHA-256:
2a66ca6336eadb3c0b34b557763f3e06d01ff8fcf6260dd4cedbd69d6093fc28
