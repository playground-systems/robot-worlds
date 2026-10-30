# Third-party assets and notices

The repository's original code is MIT licensed. The assets below retain their own terms; the root MIT license does not relicense them. This inventory records pinned sources and retained notices, not a blanket guarantee about every downstream use. Dependency distributions installed by the package manager retain their own licenses.

| Distributed component | Pinned source | Retained terms and provenance |
| --- | --- | --- |
| TIAGo++ MJCF and meshes | [MuJoCo Menagerie `f3b601…/pal_tiago_dual`](https://github.com/google-deepmind/mujoco_menagerie/tree/f3b6013629c7b94f8ee93fd977b161fa8350f9bc/pal_tiago_dual) | Apache-2.0; `assets/robots/tiago/LICENSE` and `PROVENANCE.md` inside the package. Runtime-derived wheel/gripper collision changes are described there. |
| Unitree G1 body MJCF and meshes | [Menagerie `f3b601…/unitree_g1`](https://github.com/google-deepmind/mujoco_menagerie/tree/f3b6013629c7b94f8ee93fd977b161fa8350f9bc/unitree_g1) | BSD-3-Clause; `assets/robots/g1/LICENSE` and `PROVENANCE.md`. Copyright, redistribution conditions and non-endorsement clause are retained. |
| G1 velocity policy and deployment parameters | [Unitree `1425b15…/velocity/v0`](https://github.com/unitreerobotics/unitree_rl_mjlab/tree/1425b15f73bd4095f0df53709d7c389c3eb9e790/deploy/robots/g1/config/policy/velocity/v0) | Upstream root [**LICENCE**](https://github.com/unitreerobotics/unitree_rl_mjlab/blob/1425b15f73bd4095f0df53709d7c389c3eb9e790/LICENCE), Apache-2.0, copied as `assets/robots/g1/CONTROLLER_LICENSE`. It is not a license from the neighboring third-party ONNX Runtime directory. |
| Dex1-1 gripper meshes | [Unitree ROS `3ae893…/g1_description`](https://github.com/unitreerobotics/unitree_ros/tree/3ae893d905d11795ddf1075825ee5010364a1109/robots/g1_description) | BSD-3-Clause; `assets/robots/g1/DEX1_UPSTREAM_LICENSE`. Mesh blob IDs are recorded in G1 provenance. |
| Dex1 MJCF conversion and source URDF | [LeRobot `68459e…`](https://huggingface.co/lerobot/unitree-g1-mujoco/tree/68459ed68f6f68e1f661091dfcb6ebce44681aec), crediting HIW-500 `c69d89…` | Apache-2.0 conversion attribution retained in `DEX1_LICENSE` and `DEX1_NOTICE`; those notices do not relicense the Unitree meshes. |
| Three.js, OrbitControls and OBJLoader | [Three.js r187](https://github.com/mrdoob/three.js/tree/r187), npm `0.187.0` | MIT; `static/vendor/THREE-LICENSE.txt` and `README.md`. Browser modules are vendored locally. |

For the G1 controller, the pinned upstream `LICENCE` Git blob is `7b72d429a403d760f5ca0e260ea324dbb35854c9`. Its SHA-256, matching the retained `CONTROLLER_LICENSE`, is `ff28cacaef7b9919a0fb905136cf4ccffba5191522bf5d024c4339932c0edc28`. The policy itself is sourced from `exported/policy.onnx`; its SHA-256 is recorded in G1 provenance and run manifests. These are evidence of the copied files and repository-level terms, not an endorsement by their authors.

The preserved Dex1 upstream notice also mentions GR00T/SONIC resources and dataset terms in its original project. Robot Worlds does not bundle those GR00T policy files or the dataset. Its shipped locomotion policy comes from the separate Unitree source above.

The optional Airsup source-reference importer writes local development artifacts under ignored `.robot-worlds/references/`; those scenes are not bundled into the wheel or onboarding. The importer retains Airsup's MIT license in its outputs. See [the reference-import record](docs/airsup-reference.md). User-imported assets retain their own source terms; exporting a world does not grant rights in an uploaded asset.

When updating any component, verify its actual pinned source, preserve applicable notices, record modifications, and inspect the built distribution. The [packaging check](tools/check_distribution.py) checks required asset/license presence and excludes local state from release archives.
