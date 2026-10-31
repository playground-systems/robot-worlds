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

`decode_glb(source.glb_path, "asset-cache/decoded")` now decompresses embedded
meshopt buffer views using the bundled MIT-licensed meshoptimizer 1.3.0 decoder.
The first decode requires Node.js; verified cache reuse does not. The worker has
an input/output size limit, deadline and cancellation, receives no model keys,
and leaves source files untouched. Embedded textures, materials, node transforms
and physical metadata remain in the intermediate GLB. No physics is inferred.

Sixteen small Node checks verify decoded triangle data, metadata preservation and
malformed input rejection. Ten Python checks cover the bounded worker, cleanup,
integrity, cancellation and reuse without Node. Decoding the actual 870,796-byte
rack took 0.0593 seconds and produced a 1,733,736-byte intermediate with its three
materials and nine images. This was a byte-decompression check without rendering,
simulation or collision generation; appearance and contact remain unverified.
All nine embedded image byte sequences and the scene/material/joint metadata
matched the source. A verified warm repeat took 0.0039 seconds.

The internal `GLBGeometry` reader now extracts decoded triangle primitives with
composed node transforms, normalized quantized attributes, corrected normals and
mirrored winding. Output positions/normals use metres and Z-up; retained raw
attributes and the full document preserve source-coordinate data for subsequent
material/joint conversion. Texture-info transforms are applied before optional
OBJ V inversion. Embedded image bytes and samplers remain available without
rendering or texture recompression.

Thirty analytical checks cover hierarchy, matrices, signed normalization,
nonuniform scale, reflections, multiple mesh instances, UV transforms and invalid
references/resources. The actual rack reader returned 48,864 vertices and 53,128
triangles across three materials in 0.0123 seconds. Its Z-up bounds match the
publisher's Y-up bounds after axis conversion: approximately 4.000 × 2.067 ×
3.010 metres. This verifies geometry interpretation, not appearance or collision.

Next implementation: write the material/visual artifacts into the existing
source-backed asset library, add separate interaction-aware collisions, and
perform native validation when permitted. PBR maps, vertex colors and physical
metadata must not be silently discarded by that next step.
The browser and policy camera must depict the same physical scene. No full
catalog download or new asset design is needed for these candidates.
