# TIAGo++ assets

Unmodified robot MJCF and meshes from Google DeepMind MuJoCo Menagerie:
https://github.com/google-deepmind/mujoco_menagerie/tree/f3b6013629c7b94f8ee93fd977b161fa8350f9bc/pal_tiago_dual

Downloaded 2026-10-30. Apache-2.0 license is included as LICENSE.
The upstream README identifies derivation from PAL Robotics' public URDF description.

Robot Worlds builds an in-memory derived scene; the copied source assets above
remain unchanged. The derived scene adds generated geometry, cameras and grasp
sites. It replaces wheel collision cylinders with twelve passive, 45-degree
rollers per wheel and reduces wheel servo gain with acceleration-limited commands.
These are simplified roller geometry and estimated controller parameters, not a
validated hardware digital twin. Lateral driving is not exposed.

Convex collision hulls around the original concave finger meshes caused wedge
contacts and slipping. The runtime retains visual meshes and joint/inertial
properties, but replaces finger collision with narrow shaft and flat pad boxes.
Pad dimensions and friction are explicit approximations. Native contact tests
cover grasp, lift, a holding interval, release and navigation; they do not certify
every shape, mass or motion. Objects are never attached by weld constraints or
teleported during execution.
