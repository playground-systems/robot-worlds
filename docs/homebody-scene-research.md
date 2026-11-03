# HomeBody kitchen source audit — 2026-11-02

Engineering evidence for Astra high, not a replacement-scene selection or a visual approval. The inspected `main` head is [`2388aa44c875bb0c2ce4603dd557bf3918b7a1a1`](https://github.com/Stanford-TML/homebody/tree/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1). Only text/code and file metadata were read; no GLB, mesh, texture, package installation, model call, native simulation or policy trial was run.

## What is physically interactive

Text inventory of the shipped [`assets/real2sim/src_kitchen/scene.xml`](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/assets/real2sim/src_kitchen/scene.xml) finds **four bodies, four freejoints, zero hinge/slide joints, zero actuators, zero equalities and zero tendons**. All four are rigid portable props. The remaining geometry is attached directly to the static world. This is a furnished reconstructed room with a small pick/place task set, not a kitchen whose drawers, refrigerator doors, controls and containers already articulate.

| Free-body ID | Published description | Source AABB dimensions, metres (map X/Y/Z) |
|---|---|---|
| `juice_carton` | Sideways orange-juice carton | 0.161773 × 0.103552 × 0.079562 |
| `red_white_carton` | Sideways red-and-white folded carton | 0.171156 × 0.210076 × 0.072069 |
| `parcel_left` | Brown wrapped parcel A; README calls the two parcels coffee bags | 0.168285 × 0.168075 × 0.109603 |
| `parcel_corner` | Brown wrapped parcel B | 0.184175 × 0.181568 × 0.112835 |

The exact movable list is in [`task_scene.json`](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/assets/real2sim/src_kitchen/task_scene.json). Each body has one invisible convex-hull collision mesh, separate original visual meshes, `mass="0.15"`, friction `4.0 0.20 0.020`, six-dimensional contact and masks `contype=2/conaffinity=3`. [`physics.json`](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/assets/real2sim/src_kitchen/physics.json) explicitly calls the masses **simulation assumptions**, not measured values. Bags/cartons have no deformation or internal mechanisms. No grasp frame is supplied.

[`semantics.json`](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/assets/real2sim/src_kitchen/semantics.json) contains 64 entities, 285 interaction subparts, 13 areas and 334 relations. Ten entities have `portable_candidate=true`, including cleaning bottle, pitcher, rack, cooler and recycling bin, but **only the four task-listed entities are free bodies**. Refrigerator handles, cabinet pulls and sorting-cabinet openings are semantic/visual parts here. Their names do not imply joints, usable cavities or runtime actions. The [website's drawer-opening and drawer-picking demonstrations](https://tml.stanford.edu/homebody/#skills) are explicitly real-robot skills; the shipped kitchen XML supplies no drawer joint.

Static contact inventory: 549 direct world geoms, of which 536 have collision enabled (521 mesh, 14 box, one plane). The publisher reports convex hulls per source mesh, with thin planar surfaces represented by thin boxes or the floor plane. Two task-authored support boxes reinforce a bin bottom and counter sheet. Concave openings and bin interiors require inspection/contact validation; neither semantic labels nor convex hulls establish their usability. [Source builder](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/tools/build_kitchen.py).

## Original visuals and relationship to the website

The [source declaration](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/assets/real2sim/src_kitchen/source.json) says the kitchen scan was captured by the authors, in `ws_map`, metres, Z-up. Semantic/physics metadata warn that vertices already contain map-frame positions: do not independently recenter or rotate representations. Object AABB axes are not measured object orientations. The source acquisition/calibration for every actual texture is not documented by a per-texture capture manifest in this shipped scene.

The reproducible visual path is **semantic GLB → Trimesh world-transformed meshes → OBJ visual parts + PNG base colors → MuJoCo XML**. The [builder](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/tools/build_kitchen.py) preserves node transforms and UV-bearing geometry, recenters only each free body's mesh relative to that body's retained position, and exports `baseColorFactor`/`baseColorTexture` with fixed `specular=0.1`. It does **not** export PBR normal, roughness or metallic fields. Whether the original GLB contains those maps remains unknown because the GLB was not fetched. The [reconstruction guide](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/guides/real2sim.md) says the builder consumes an already reconstructed PBR GLB; it neither reconstructs the room nor creates joints automatically. The [geometry/RGB guidance](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/.agents/skills/homebody-real2sim/references/geometry-rgb-and-semantics.md) describes projecting registered camera appearance onto accepted geometry, with unresolved appearance tracked separately; this is workflow guidance, not proof every shipped surface is measured.

The XML declares 547 visual parts/materials, 128 PNG texture assets and 551 mesh assets (547 visual meshes plus four portable-object hulls); total geoms are 566. The [pinned file tree](https://github.com/Stanford-TML/homebody/tree/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/assets/real2sim/src_kitchen) and [asset manifest](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/assets/manifest.json) enumerate:

| Artifact | Published bytes |
|---|---:|
| `kitchen_semantic.glb` | 10,121,136 |
| `scene.xml` | 193,542 |
| `physics.json` | 164,421 |
| `semantics.json` | 531,604 |
| `task_scene.json` | 957 |
| `map.npz` | 24,368 |
| `source.json` | 88 |
| 551 OBJ + 128 PNG files in `meshes/` | 40,810,669 |
| Complete scene subtree (686 files) | **51,846,785** |

The [website](https://tml.stanford.edu/homebody/) calls its reconstruction Isaac Sim and labels the instruction-driven 3D rollout illustrative. Its [viewer loader](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/docs/scripts/scene-loader.js) loads `docs/figures/room.glb` (**9,946,964 bytes**), while the [rollout code](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/docs/scripts/site.js) loads a different `deployment.glb` (**27,117,800 bytes**) and plays timed animation. These are different files from `kitchen_semantic.glb`. The browser uses Three.js, ACES tone mapping, a PMREM room environment, directional shadows and ambient occlusion; the rollout also uses contact shadows. **No verified geometry/material/hash conversion chain between these website artifacts, the Isaac comparison renders and the shipped MuJoCo scene was found.** A similar room identity does not prove equal appearance or equal physics, and no visual match in Robot Worlds has been demonstrated.

## License and native runtime

The [README](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/README.md), [root LICENSE](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/LICENSE) and [NOTICE](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/NOTICE) declare the HomeBody release Apache-2.0. [`THIRD_PARTY_NOTICES.md`](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/THIRD_PARTY_NOTICES.md) expressly lists the scanned kitchen as author-captured and not a third-party component. No separate scene-specific LICENSE/NOTICE or noncommercial scene exception appears in the complete kitchen subtree. Thus the published reuse basis for this author-owned scene is the repository's Apache-2.0 declaration; **it is not separately declared CC-BY**. Retain the project license/notices and mark modifications when redistributing a derivative. This is a statement of the publisher's declarations, not independent verification of capture rights.

The full stack has distinct terms: Unitree robot descriptions/meshes are BSD-3-Clause; the notices call AMO walking-policy weights Apache-2.0 **and state the original project releases them for research use only**; SuperOdometry is GPLv3. Do not extend the kitchen/code licensing conclusion to all robot models, weights, website media or deployment dependencies. Importing just the environment does not require taking the walking policy.

HomeBody SIM is native MuJoCo: [`pyproject.toml`](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/pyproject.toml) pins `mujoco==3.11.0`; the [loader](https://github.com/Stanford-TML/homebody/blob/2388aa44c875bb0c2ce4603dd557bf3918b7a1a1/src/homebody/backends/scene.py) appends this scene's assets/world to its G1 model, installs physics/camera settings and initializes the robot over the reconstructed floor. The README explicitly supports Apple-silicon macOS and Linux SIM, CPU walking-policy execution when CUDA is absent and `cgl` rendering on macOS. REAL2SIM/REAL instead require Linux/amd64 and an NVIDIA GPU. These are upstream support statements; nothing was installed or run here.

## Robot Worlds integration gap

The current [local MJCF asset importer](../src/robot_worlds/asset_import.py#L37) does not import this scene unchanged. It requires an explicit radian compiler; this XML has no compiler block. It rejects direct world geoms, freejoint bodies, rotated source geoms and collision meshes. HomeBody uses all of those scene structures, including a transformed source floor plane and mesh-based contacts. Loading only its GLB as a visual asset would lose the four free bodies and the source contact representation.

A faithful engineering integration would need an environment-level MJCF preservation path, dependency/hash/notice closure, retained map frame and original textures, stable body/geom/semantic ID mapping, and robot/camera/task bindings. Contacts, reset stability, object reachability and browser/native visual correspondence would then need actual checks. Additional articulated drawers or controls would be new authoring work for Astra high or Claude Opus 5.5; the source does not supply their joints. This audit makes no import, fidelity, task-success or policy-success claim.
