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
records and publisher-reported sizes/bounds. Five individual GLBs were downloaded
(4,057,528 bytes total); file sizes, SHA-256 values and GLB headers were checked.
This did not decode meshes, render them or establish physical compatibility.

The packaged `assets/simready/catalog.json` binds those exact source bytes to the
SDK's `simready_asset_catalog`, `simready_asset_search` and
`download_simready_asset` functions. Downloads are per asset, hash-checked,
bounded and cancellable. Verified offline cache reuse makes no network request;
changed bytes or provenance fail without overwriting the cache. Each entry keeps
an attribution notice and source metadata. Unknown IDs are unresolved lookups,
not permission for procedural creation.

```python
from robot_worlds import download_simready_asset

source = download_simready_asset("rack_large_empty_a02", "asset-cache/simready")
print(source.glb_path, source.notice_path, source.metadata_path)
```

This acquires source data only; do not pass its GLB into `WorldSpec.mesh_path`.
The runtime currently consumes OBJ/STL/MSH geometry. The source candidates are
therefore not advertised as prepared assets to the scene-generation model yet.

One measured rack acquisition took 1.1139 seconds, with a 0.0024-second verified
offline repeat for 870,796 bytes. These are source-download/cache timings on the
development Mac, not conversion, rendering or environment generation timings.

Import must preserve materials and explicitly transform axes, units and joint
frames. Racks need open shelf collisions; pallets need usable fork openings.
Conveyor moving parts require validation or an explicit nonfunctional label.
Do not use the optimized convex hulls as proof of these features. Reuse the
existing pinned household assets for bowl-and-microwave acceptance.

Next implementation: meshopt decoding, conversion into the existing source-backed asset library,
separate interaction-aware collisions, and native validation when permitted.
The browser and policy camera must depict the same physical scene. No full
catalog download or new asset design is needed for these candidates.
