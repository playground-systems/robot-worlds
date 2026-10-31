# Source material preservation

Mesh objects can carry an explicit `surface` (`SurfaceMaterial`) with metallic,
roughness, emissive, normal-scale, sidedness, alpha and wrapping parameters.
`color` and `opacity` remain the base color factors. These fields do not set mass,
friction, contact or motion properties.

The image slots share the prepared mesh UV coordinates:

| Field | Interpretation | Browser color space |
| --- | --- | --- |
| `texture_path` | Base color, with optional alpha | sRGB |
| `normal_texture_path` | Tangent-space normal | Linear data |
| `orm_texture_path` | Red occlusion, green roughness, blue metallic | Linear data |
| `emissive_texture_path` | Emission color | sRGB |

Maps are bounded local PNG assets. Source importers must convert embedded images
and texture-coordinate transforms before registering an asset; declaring a path
does not make unsupported glTF features work. Different UV sets or sampler
settings across source texture slots need conversion or an explicit rejection,
not silent substitution. Nonuniform vertex colors still need preservation during
visual artifact conversion.

Asset libraries verify map hashes alongside mesh hashes. Frozen worlds copy all
four maps into their own content-addressed asset directory, so source removal
does not break loading/export. Changed map bytes invalidate a saved world's
integrity checks. Targeted surface edits retain existing dynamics and require
normal world revalidation because appearance affects policy observations.

Within a displayed world, repeated OBJ instances share parsed geometry,
materials and decoded maps. Position, size, rotation and selection stay per
object. Texture sharing respects color space and wrapping, and different
material factors keep separate materials. Changing worlds aborts pending loads
and releases the scene-owned resources. The viewer still starts at most four
object loads at once. Once loading finishes, compatible opaque source meshes are
instanced within 8 m spatial cells. Selection still resolves the individual
source object. Cells retain useful culling for close views; transparent parts,
cutaway shells, hidden/reflected meshes and incompatible rendering settings stay
separate. Instance buffers are released separately from shared geometry/maps.
If batch construction fails, the original meshes remain available. LODs and
actual viewport frame-rate measurements remain open.

Mesh placement supports `mesh_quarter_turns` (0–3) around Z without rewriting
source files. `size` describes the full extents after that turn. Prepared assets
and assembly instances set this field while rotating their physical parts and
joint/cavity frames together. Browser and native XML apply the same source
centering, scale and rotation; analytical checks cover off-center source meshes
and composed body poses. Native contact/render validation remains pending.

The browser uses the PBR map channels. MJCF export retains material layers and
coefficients, with shared texture/material definitions. The current runtime uses
MuJoCo's classic renderer; it approximates highlights and does not reproduce all
PBR maps, alpha masking, emission or sampler settings. Frozen-world assumptions
state this difference. MuJoCo also provides a separate [Filament rendering
API](https://mujoco.readthedocs.io/en/3.17.0/python.html#filament-rendering), which
is not yet integrated into the policy camera or replay path.

Verification so far is lightweight: schema validation, library/world persistence,
source removal, hash tampering, scoped edits, XML material sharing, channel/color
space selection and texture cleanup. A small real-Three test loads 64 repeated
fixtures with one OBJ request and three map requests, checks independent bounds
and selection IDs, and verifies disposal on replacement or late completion.
Batch tests also cover ray selection, transformed parents, quarter turns,
spatial/material separation, transparency and construction failure. A CPU-only
harness using the actual carton geometry reduced 840 source mesh objects to
28 spatial batches; geometry loading took 109 ms and batching took 15 ms.
That harness mocked image decoding from PNG dimensions and created no renderer,
so these timings exclude image decoding, GPU upload and frame rendering.
This is CPU-side evidence, not measured GPU memory or viewport frame rate.
No new browser screenshot, native material compilation or PBR policy-camera
parity is established by these checks. The
warehouse visual acceptance gate remains open.

For normal-mapped GLBs, matching quarter-turn texture transforms with unit scale,
integer offsets and REPEAT wrapping can be baked into all material images. This
keeps the original UV tangent basis. Other rotated/reflected normal-map transforms
remain unsupported and fail explicitly. Conversion metadata retains the original
material definitions and records this pixel-permutation step (float transform
rounding tolerance: 1e-7). No maps are silently dropped.

Bundled OBJ/PNG responses are now read through byte-limited streams (24 MiB for
OBJ, 16 MiB for PNG), including when Content-Length is absent or incorrect. PNG
signature/IHDR dimensions are checked before image decoding; this viewer accepts
at most 4096 pixels per edge. A world reserves up to 512 MiB of estimated RGBA
texels plus mipmaps across distinct texture/color-space/wrap combinations.
Concurrent loads reserve before decoding, shared instances reserve once, and
failure/cancellation/disposal releases reservations. Exceeding the scene budget
shows an explicit unavailable-mesh marker; it does not remove source maps or
change exported assets. This is not a total browser/GPU memory cap: retained
bitmaps, geometry, network buffers, renderer allocations and driver overhead are
separate. Adaptive texture/geometry LOD remains future work.

The five selected warehouse sources contain 44 distinct 1024×1024 PNG payloads:
176 MiB of RGBA pixels, or approximately 234.7 MiB with mipmaps before any
color-space/wrapping duplication. These are estimates from asset dimensions,
not measured GPU usage. Twenty-six focused viewer/resource checks cover stream
limits, predecode rejection, concurrent reservations, cancellation, cache sharing,
scene lifecycle and batching. Decoder fixtures and CPU-only Three objects do not
establish rendered quality or frame rate.

Failed viewer resources remain failed for the current displayed world, preventing
one unavailable source from being fetched again for every repeated object.
Reloading the world retries them; `SceneAssets.retryFailures()` lets an internal
caller clear failures before explicitly requesting another load while retaining
successful shared resources. Unavailable objects keep selectable wireframe bounds
with their declared pose. These markers share geometry/materials and can batch;
failed wall shells still obey cutaway. Repeated error labels are grouped, with
at most 16 source labels and one overflow summary. Source geometry, physics and
saved results are unchanged. Twenty-five focused CPU-only checks cover this
failure behavior, retry, selection, batching and cleanup; rendered acceptance
remains open.
