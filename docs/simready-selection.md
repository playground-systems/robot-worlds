# Warehouse source selection

The user's warehouse screenshots set the visual target: detailed racks, pallets,
cartons, conveyors, barriers, structural members, floor markings and coherent
lighting. Asset availability does not prove this appearance in Robot Worlds.

The [CodePawl GLB collection](https://huggingface.co/datasets/codepawl/simready-glb/tree/e52172290393dde80e8d383861db94b8d0e92ec3)
derives from [NVIDIA SimReady](https://huggingface.co/datasets/nvidia/simready-assets/tree/4763fbfaa4f8e97658e68b57d01b4c86659e08b5).
Its publisher documents meshopt-compressed geometry, WebP textures, Y-up
coordinates, physics extras and convex-hull collisions that fill hollow shapes.
These are conversion inputs, not verified MuJoCo physics. The inspected manifest
does not declare its exact upstream derivation commit.

The [selection manifest](simready-selection.manifest.json) records five
candidates: rack, pallet, carton, barrier and conveyor. All five upstream package
declarations identify CC BY 4.0; their license files share the recorded SHA-256.
It retains upstream and optimized revisions separately, source URLs, packaging
records and publisher-reported sizes/bounds. Geometry has not been downloaded or
verified by this selection step.

Import must preserve materials and explicitly transform axes, units and joint
frames. Racks need open shelf collisions; pallets need usable fork openings.
Conveyor moving parts require validation or an explicit nonfunctional label.
Do not use the optimized convex hulls as proof of these features. Reuse the
existing pinned household assets for bowl-and-microwave acceptance.

Next implementation: bounded per-file acquisition and content verification,
meshopt decoding, conversion into the existing source-backed asset library,
separate interaction-aware collisions, and native validation when permitted.
The browser and policy camera must depict the same physical scene. No full
catalog download or new asset design is needed for these candidates.
