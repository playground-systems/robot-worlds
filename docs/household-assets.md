# Existing household assets

Inspected 2026-10-30. Prefer the original **Google Scanned Objects porcelain bowl** and **NVIDIA/Lightwheel Microwave079** over procedural substitutes. Both have downloadable geometry and textures. The microwave already has articulated parts; the bowl needs a concave collision decomposition for MuJoCo. No native simulation or model trial was run for this selection.

## Selected sources

| Item | Pinned source | Download and integrity |
| --- | --- | --- |
| White porcelain bowl | GoogleResearch `Threshold_Porcelain_Serving_Bowl_Coupe_White`, Fuel version **1** | [Original ZIP](https://fuel.gazebosim.org/1.0/GoogleResearch/models/Threshold_Porcelain_Serving_Bowl_Coupe_White/1/Threshold_Porcelain_Serving_Bowl_Coupe_White.zip), **3,324,041 bytes**; SHA-256 `9f18958313212a56edb33948cf4ec89eae7bd7392293ab27a6ca50ba7ef14098` |
| Articulated microwave | NVIDIA Kitchen MJCF, revision **420a04af939c34873e6839a586b70844baf28aab**, member `microwaves/Microwave079/` | [Category ZIP](https://huggingface.co/datasets/nvidia/PhysicalAI-Robotics-Manipulation-Objects-Kitchen-MJCF/resolve/420a04af939c34873e6839a586b70844baf28aab/fixtures_lightwheel/microwaves.zip), **35,952,253 bytes**; SHA-256 `17e8f0bebbc59a7289e2ee2789d230e527b055e38a1155dd2361defdac34d74b` |

The bowl's [item metadata](https://fuel.gazebosim.org/1.0/GoogleResearch/models/Threshold_Porcelain_Serving_Bowl_Coupe_White) identifies GoogleResearch, version 1 and CC BY 4.0. Its original `model.config`, `model.sdf`, `metadata.pbtxt` and `meshes/model.mtl` explicitly retain **Copyright 2020 Google LLC.**, author **Google**, and CC BY 4.0. The description explicitly says porcelain; this is not inferred from its appearance.

The microwave's [pinned publisher card](https://huggingface.co/datasets/nvidia/PhysicalAI-Robotics-Manipulation-Objects-Kitchen-MJCF/blob/420a04af939c34873e6839a586b70844baf28aab/README.md) declares **CC BY 4.0**. It is distributed in `fixtures_lightwheel`; credit NVIDIA as distributor and Lightwheel as the collection identifier. The selected member has no separate per-model author or license file. Do not invent an individual creator or claim that provenance is more specific than the release supplies.

Redistribution should retain these notices, source links, identifiers and the [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/), and describe conversion, scaling, collision decomposition and other modifications separately. These data licenses remain separate from Robot Worlds' software license.

## What is actually in the files

**Bowl.** The source has `meshes/model.obj` (507,190 bytes, 2,975 vertices), `materials/textures/texture.png` (7,541,215 bytes), an MTL, SDF and metadata. Direct vertex inspection gives bounds `[-0.104085, -0.104730, -0.000052]` to `[0.104149, 0.104611, 0.068831]` metres: approximately **20.9 cm wide × 6.9 cm tall**. Keep the OBJ geometry, UVs and original texture. Explicitly bind the PNG in MJCF; the MTL uses `texture.png` while the image resides in a different directory. The SDF references the same concave OBJ for visual and collision geometry and supplies no measured mass/inertia. A single MuJoCo convex hull would fill the bowl opening. Generate collision pieces from the source surface, validate the cavity, and label estimated physics properties. This is an import/conversion step, not a newly designed bowl. Sources: the original ZIP's SDF, OBJ, MTL and metadata linked above.

**Microwave079.** The selected directory is **1,098,780 bytes** uncompressed, with `model.xml`, `visuals/Body001.obj`, `door.obj`, `door_Clear.obj`, `Disc001.obj`, and `T_BC001.png`. Its MJCF has four visual meshes, original collision boxes/cylinders, a transparent door panel, and two hinges:

- `microjoint`: door, Z axis, pivot `[-0.22846182, -0.10996792, 0.00382642]`, range **[-π/2, 0] radians**.
- `Disc001_joint`: turntable, Z axis, unlimited rotation; pivot near `[-0.04782203, 0.00771576, -0.12979725]`.

Body dimensions from the mesh are about **48.4 × 27.7 × 29.1 cm**; the handle extends forward. The cavity annotation `reg_int` has centre `[-0.039870335, 0.02759006, 0.007467425]` and half-extents `[0.197924835, 0.11980653, 0.133801895]`. A source collision cylinder named `tray` is static, while a separate disc body rotates. Preserve this distinction. The bowl's diameter is slightly larger than the tray's 20.7 cm diameter but smaller than the cavity: this is a fit candidate, not proof of stable placement or a collision-free insertion path. Sources: the original microwave ZIP's `model.xml` and OBJ files linked above.

Import blockers to handle explicitly:

- MJCF assets contain generic names (`object`, `visual`, `collision`, `region`). Namespace asset, body, joint, default, material and actuator names and every reference.
- Preserve body hierarchy, hinge pivots, ranges and visual/collision separation. Do not flatten the door into the fixed housing.
- `region` geometries are transparent, non-colliding annotations. They must not become physical walls or visible solids.
- Do not import the source's global timestep/gravity over the world's settings. Source motors must not silently grant a robot direct appliance control; physical interaction remains through the robot.
- Do not represent appearance-only geometry as the collision geometry. Validate the bowl opening, microwave cavity, hinge sweep, texture mapping and scaled bounds before calling the imported asset simulation-ready.

## Search before creating

Asset lookup should be an internal generation step, with no per-object user confirmation. The resolver should first use installed pinned assets, then query trusted online catalogs. Record the query, candidates, chosen source, license evidence and rejection reasons. Only create a new asset when no usable existing match is found; a request failure is not evidence that none exists.

Verified endpoints:

1. **Fuel search:** `GET https://fuel.gazebosim.org/1.0/models?q=bowl&per_page=100&page=1`. Returns names, owners, descriptions, licenses and sizes. Filter the returned owner explicitly: even the `/GoogleResearch/models?q=...` route returned models from other owners in this inspection. `search=bowl` did **not** filter results; use `q`. Pagination and counts need defensive handling.
2. **Fuel item:** `GET https://fuel.gazebosim.org/1.0/{owner}/models/{name}` returns `version` and per-item license. URL-encode path components; download `/{version}/{name}.zip`. Save the response and content hash.
3. **NVIDIA category discovery:** [Hugging Face repository metadata](https://huggingface.co/api/datasets/nvidia/PhysicalAI-Robotics-Manipulation-Objects-Kitchen-MJCF) supplies the commit SHA and file paths. [Pinned recursive tree](https://huggingface.co/api/datasets/nvidia/PhysicalAI-Robotics-Manipulation-Objects-Kitchen-MJCF/tree/420a04af939c34873e6839a586b70844baf28aab?recursive=true&limit=1000) supplies archive sizes and LFS hashes. Search category names, then inspect the bounded archive inventory for exact models.

Rank candidates on semantics/material, scale, usable interior, articulation, source license, texture quality and import cost. A decorative closed mesh is not an acceptable substitute for an openable microwave. Cache verified bytes and metadata, reject unsafe archive paths and external references, and cap network/extraction sizes.

## Alternatives inspected

- NVIDIA's `objects_lightwheel/fruit_bowl.zip` contains fruit-display assemblies; inspected variants include fruit in the geometry/texture. It is not a clean replacement for an empty ceramic bowl.
- [RoboCasa's asset release](https://huggingface.co/datasets/robocasa/robocasa-assets/tree/1b92c3d02ca4354984fec961357db0bff7b32166) includes `objaverse/bowl/bowl_1/` in `objaverse.zip`: textured visual geometry and 16 referenced collision meshes, 741,955 bytes including unused files. It is physically convenient, but the archive inspected had no per-item source UID/creator metadata. The publisher declares CC BY 4.0 and [documents Objaverse provenance](https://robocasa.ai/docs/build/html/assets/objects.html); the Google bowl has clearer item-level attribution. HTTP Range requests allowed inspection without downloading the 2.16 GB archive.

## Implementation status

`robot_worlds.asset_sourcing.source_asset` now enforces the ordering for an asset pipeline: completed lookup → import/adapt checked candidates → create only after no suitable candidate remains. Import incompatibilities retain reasons. Network failures, incomplete searches, missing source evidence and unexpected import failures do not authorize procedural fallback. Its tests use mocked providers and make no network or model requests.

The exact downloaded source-file hashes are retained in [the selection manifest](household-assets.manifest.json). Native textured-asset ingestion and automatic use of this coordinator by the environment generator are still being implemented. The current procedural preview has not yet been replaced by these original assets. This note does not claim that the selected bowl or microwave has passed simulation validation.
