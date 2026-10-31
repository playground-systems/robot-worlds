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
not prepared assets by themselves. Reviewed rigid adapters now cover the rack,
pallet, sealed carton and barrier. The conveyor remains source-only.

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

The world schema, portable library and browser now support [explicit PBR material
maps](source-materials.md); the current classic policy camera has declared
rendering limitations. This is infrastructure for source conversion, not a
completed warehouse import.

`prepare_glb_visuals` now writes immutable OBJ/PNG visual bundles from decoded
geometry. It retains source attribution and the glTF document, composes source
transforms, writes normals/UVs, converts images without resizing, and records
normal-map Y inversion and ORM packing. Uniform vertex colors are folded into
the material factor. Conflicting UV/sampler settings, nonuniform vertex colors
and unsupported material features fail explicitly. They do not authorize
procedural substitutes.

The returned `GLBVisuals` requires an explicit mapping from every source mesh
node to a physical parent before producing `SceneObject` children. It cannot
register a decorative rack as a physically usable asset by itself. A synthetic
fixture proves the source-backed library/world/export path survives deletion of
the original conversion cache. This is persistence evidence, not rack physics.

The actual rack produced three OBJ parts and nine PNG material maps (about
11.9 MB including source metadata/notices). Fifteen small conversion checks
cover image channels, normal-map orientation, source preservation, cache
corruption/cancellation and portable-library handoff. The preparation is cached
by source, notice, metadata and converter identity; warm reuse hashes files
without repeating conversion.
One actual rack preparation took 0.8462 seconds, followed by a 0.0084-second
verified warm repeat. These are visual-artifact preparation timings, not native
validation, rendering or complete environment generation.

`import_simready_rack` now joins those stages with a pinned static-fixture
adapter. It welds source vertices on a 10-micrometre grid for connectivity only,
then bounds each connected component using original coordinates. The actual
rack yields 120 conservative collision boxes and three separate appearance
meshes. These preserve its three shelf openings; a single filled hull is rejected.
Source mass (1,650.4048 kg) and dynamic friction (0.3) are retained as source
estimates, not calibrated measurements. The fixed assembly distributes mass by
proxy volume; source inertia and small structural holes are not retained.

Seven lightweight checks cover shelf geometry, rejection of filled openings or
articulation, cancellation, placement and portable reuse at two positions. The
actual cached rack passed static scene checks and library registration in
0.1565 seconds. Native contact, reach, rendering and generated-layout acceptance
remain pending. The adapter records these limits in the asset and exported
collision provenance; registration does not certify task solvability.

`import_simready_structure` extends the same pipeline to three more sources:

| Source | Physical adaptation | Retained source mass / dynamic friction |
| --- | --- | --- |
| `pallet_a01` | Movable rigid assembly; 19 board/block envelopes preserve four fork channels | 50.731724 kg / 0.5 |
| `box_corrugated_brown_b20` | Movable sealed carton; one enclosing box, original seven visual parts and material maps | 1.151625 kg / 0.7 |
| `barrier_fence_metal_a02` | Fixed fixture; six source-derived foot/post/rail envelopes preserve two rail openings | 45.41036 kg / 0.4 |

These source masses/frictions are estimates, not measurements. Proxy volume
determines mass distribution; source inertia, center of mass and restitution
are not retained. The carton has no openable flaps or deformation. Its six faces
are sampled geometrically to reject an open source; this is not a general
watertightness test. Every barrier triangle must fit a structural envelope.
Pallet/rail clearances stay in the provenance record, rather than becoming
world-fixed task regions attached to movable assets.

Eight additional synthetic checks cover these profiles, open/fill rejection,
zero-index articulation, source preservation, registration and portable placement.
The actual source preparations and registrations took 0.2843 s (pallet),
0.8538 s (carton) and 0.1910 s (barrier), using already acquired source bytes.
A combined four-asset library contains 158 physical/visual components and about
23.8 MB of referenced data; a static layout check passed after save/reopen.
Warm preparation/registration took 0.0146, 0.0275 and 0.0124 s respectively.
These are local asset-processing measurements, not live generation or native
physics/rendering evidence. No full catalog or source-scene import was needed.

`robot-worlds prepare-warehouse` and `prepare_warehouse_library` now install this
subset into a new portable library. The actual command installed all four into
the workbench data directory in 2.31 s (2.67 s process wall time), with about
146 MB maximum resident memory. Source downloads/decodes were already cached;
this is not a completely cold setup measurement. A warm prepare/copy to a
temporary destination took 0.2333 s and produced the same library identity.
That temporary copy was removed. The installed planning catalog includes all
four warehouse assets plus the two bundled household assets.

The saved four-asset library also passes static checks after whole-asset 90°
and 270° placement: all 158 components, including 12 visual meshes, retain
aligned collision bounds. These are SDK placement probes, not model-generated
warehouse layouts or native contact/render acceptance.

Compact repetition was also exercised through `generate_spec` with a mocked
planner and the actual installed carton source. Both a 12×10 layout and a rotated
10×12 layout produced 120 independent rigid roots and 960 components, passing
static validation in 0.2986 and 0.1699 s. Each used one 154-character asset
reference; the expanded object data was about 1.48 million characters before
freezing paths. These measure local expansion and static checks, not model
latency, native stability, rendered appearance or a complete warehouse demo.

The pinned conveyor now passes the internal `conveyor_bodies` articulation
check: 30 source bodies, 30 collider mappings, 29 unlimited passive hinges and
112.524278 kg total declared source mass. Both connection frames and the
independent source joint declarations agree after Y-up → Z-up conversion. The
source has 37 visual primitives / 228,964 triangles. These counts were read from
the hash-verified source above, not inferred from the catalog alone.

Eighteen metadata-only checks reject missing/duplicate mappings, disagreeing
frames, changed axes/pivots, drives, unsupported motion, unreachable geometry
and malformed mass/collider references. These metadata checks are preparation evidence, not evidence of physical operation. The intended
adaptation is a fixed frame with passive rollers; no motor/controller or powered
transport is provided. Zero bearing damping/friction loss would be an explicit
estimate because the inspected source does not specify those properties.

The conveyor adapter now retains all 191 connected frame components. Twelve
planar surfaces receive an explicit 0.1 mm collision thickness. It removes each
roller's swept volume plus 1 mm from frame proxies so their conservative boxes
do not fill bearing openings. This produces 701 frame boxes, 29 articulated
roller collision meshes and 37 source visual meshes (767 components). Original
visual geometry stays unchanged; no contact filter or task criterion was relaxed.
Frame mass is distributed by final proxy volume. Source inertia, center of mass
and restitution are not retained; bearing friction/damping are set to zero.

The brushed-aluminum material uses a quarter-turn normal-map UV transform.
Matching unit-scale, integer-offset REPEAT transforms are now baked by permuting
all material images, keeping the original UV tangent basis and the existing OBJ
normal Y conversion. Tests compare independent UV sampling against output pixels
and reject non-equivalent transforms. See the [Khronos texture-transform
specification](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_texture_transform).

Actual preparation from cached, hash-verified source bytes took 6.118 s on its
first successful visual conversion. A warm installer run took 0.538 s and wrote
a separate 33,089,715-byte portable conveyor library. Reopening that library and
placing it at 0° and 90° retained all hinges and passed static scene checks.
These are local preparation/geometry results, not cold downloads, live generation,
native contacts or rendered appearance. Seventy-one focused lightweight checks
cover the affected source adapters, installer, materials and rejection paths.

The SDK exposes `import_simready_conveyor`; `prepare-warehouse` now includes this
fifth source or accepts `--asset conveyor_belt_a05` alone. Existing libraries
are never overwritten; choose a new output directory for an updated subset.
The conveyor is explicitly unpowered and still needs native contact and visual
acceptance. No new supported robot or logistics controller is introduced.

Next: verify native contacts/materials and generated layouts when permitted.
The viewer now shares source geometry/materials/maps
across repeated fixtures; real warehouse frame rate, GPU memory and LOD remain
unmeasured.
The browser and policy camera must depict the same physical scene. No full
catalog download or new asset design is needed for these candidates.
