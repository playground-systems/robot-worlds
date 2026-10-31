# Fuel asset download and import

Inspected 2026-10-30 (local time). This is implementation research, not evidence
that general online assets already work in generation. Read the existing
[household selection](household-assets.md) and [hash manifest](household-assets.manifest.json)
before repeating searches for the bowl or microwave.

## Pinned download contract

The official Fuel client constructs
`/{owner}/models/{name}/{version}/{name}.zip` under its server/API-version URL.
Its `DownloadModel` requests `link=true`, checks the response version header,
and passes the response to `ZipFromResponse`. That helper accepts ZIP bytes or a
`text/plain` body containing another download URL. The latter is a referral in
the response body, not necessarily an HTTP redirect. [Official FuelClient source,
release 11.0.0](https://github.com/gazebosim/gz-fuel-tools/blob/gz-fuel-tools11_11.0.0/src/FuelClient.cc).

Small read-only requests against the already selected
[version-1 bowl ZIP](https://fuel.gazebosim.org/1.0/GoogleResearch/models/Threshold_Porcelain_Serving_Bowl_Coupe_White/1/Threshold_Porcelain_Serving_Bowl_Coupe_White.zip)
confirmed:

| Request | Observed response |
| --- | --- |
| Plain GET, redirects disabled | `200`, `application/zip`, ZIP magic bytes; no redirect |
| GET with `Range: bytes=0-63` | `206`, `Content-Range: bytes 0-63/3324041` |
| GET with `link=true` | `200`, `text/plain`, a signed URL on `gz-fuel-resources-production.s3.amazonaws.com` |

All three responses reported `X-Ign-Resource-Version: 1`. The plain GET reported
`Content-Length: 3324041`; its body was closed after the first four bytes. These
are observations of this pinned model, not a guarantee that every Fuel endpoint
never redirects. Prefer the plain endpoint for the initial bounded downloader.
Reject unimplemented referral/redirect responses explicitly; do not treat them
as ZIP files or follow arbitrary remote URLs.

## Package layout

The official layout places `model.config` and its referenced SDF at the model
root, with meshes, optional textures and thumbnails in subdirectories. Mesh
formats include OBJ, STL and COLLADA. `model.config` selects an SDF filename;
do not assume every package uses exactly `model.sdf`. The tutorial also shows
same-model `model://.../meshes/...` URIs. [Gazebo mesh-to-Fuel tutorial](https://gazebosim.org/api/sim/7/meshtofuel.html).

Inspection of the existing local bowl ZIP, with SHA-256 matching the selection
manifest, found **15 entries, 11 files and 8,087,730 uncompressed bytes**. It has
**no enclosing model-name folder**: `model.config`, `model.sdf`, `metadata.pbtxt`,
`meshes/model.obj`, `meshes/model.mtl`, `materials/textures/texture.png` and
`thumbnails/0.jpg` through `4.jpg` are rooted directly in the archive. These facts
come from the [pinned source ZIP](https://fuel.gazebosim.org/1.0/GoogleResearch/models/Threshold_Porcelain_Serving_Bowl_Coupe_White/1/Threshold_Porcelain_Serving_Bowl_Coupe_White.zip),
not an assumption derived from the tutorial's directory illustration.

## Download cache

`download_fuel_asset` now implements bounded plain downloads, safe extraction,
atomic publication and verified cache reuse. A read-only test of the pinned bowl
measured **9.80 s** for download/extraction and **10.9 ms** for the verified cache
hit, with one network request across both calls. All 11 original files matched
the selection manifest. These timings measure source acquisition on one machine,
not text-to-world generation. See the [SDK usage and limits](sdk.md#search-before-creating-assets).

The downloader does not interpret model roots or SDF references. The restricted
scan importer described below now handles one model root and local scan references. The following are design requirements and recommendations;
the implementation uses absolute extracted-byte limits rather than a separate
compression-ratio cap:

- Construct the trusted HTTPS Fuel URL from checked owner, name and positive
  integer revision. Encode each path segment. Never use `tip` as a cache identity.
- Stream into temporary storage with a total deadline, compressed-byte limit
  and cleanup on failure. Enforce actual bytes even when `Content-Length` exists.
- Before extraction, bound entry count, individual/total uncompressed bytes and
  compression ratio. Reject absolute paths, traversal, backslashes, symlinks,
  duplicate normalized names and file/directory conflicts. Do not execute files.
- Find one unambiguous model root, either the archive root or a single enclosing
  directory. Resolve every referenced file beneath that root. External models,
  network URLs and filesystem paths outside it require a separate supported flow.
- Key the cache by server/owner/name/revision; retain archive SHA-256, file hashes,
  response revision, source URL and license evidence. Reuse verified local files
  without repeated network requests. Publish the manifest last with an atomic
  cache update; incomplete downloads must not look like cache hits.
- Download success means the source was acquired. Import compatibility, physical
  validation and task suitability remain separate outcomes.

## Implemented scan import

`import_sdf_mesh_asset` accepts one model/link with one matching OBJ visual and
collision mesh, unit scale and translation-only poses. `import_fuel_mesh_asset`
adds cache verification, generated attribution and combined source/conversion
metadata. The source's explicit static flag is retained; a missing flag remains
dynamic. The caller supplies an estimated mass and friction, and inertia is
derived from collision geometry. Explicit inertial or surface settings are
rejected rather than silently overridden. These choices follow the source model
and link distinctions in the [official model schema](https://raw.githubusercontent.com/gazebosim/sdformat/sdf15/sdf/1.6/model.sdf)
and [link schema](https://raw.githubusercontent.com/gazebosim/sdformat/sdf15/sdf/1.6/link.sdf).

Without a conversion manifest, the original mesh must pass the closed-convex
verifier. Concave meshes require an existing source-bound decomposition, including
per-part checksums and geometry checks. Import never starts a converter or makes
an articulated object static to get a pass. Unsupported elements/attributes and
external resources fail explicitly. Material support is one MTL material with
one PNG diffuse map, diffuse color and opacity; other renderer coefficients are
retained/disclosed as approximations. Mesh scale/submesh semantics are defined by
the [official mesh schema](https://raw.githubusercontent.com/gazebosim/sdformat/sdf15/sdf/1.6/mesh_shape.sdf);
non-unit scale and submesh selection are currently rejected.

A read-only import from the already cached version-1 Google bowl retained its
original texture, Copyright 2020 Google LLC notice, 158 prepared collision pieces
and one movable root. Saving/opening it as an `AssetLibrary` also passed. This used
no network request, new collision conversion, model call or policy episode.
Synthetic tests cover transforms, fixed/dynamic settings, unsupported physics,
unsafe paths, corruption and download → import → library portability. Native
checks are separate from these source-format tests.

## Scanned-object adapter boundaries

SDF supports nested models, joints, plugins and frame-dependent placement.
`static` defaults to false; when true it makes a model immovable. A single-link
scan is therefore not automatically a static object just because it has no
joints. [SDF model schema](https://github.com/gazebosim/sdformat/blob/sdf15/sdf/1.12/model.sdf).
Links separately describe inertial, collision, visual and sensor data, and may
have kinematic behavior. [SDF link schema](https://github.com/gazebosim/sdformat/blob/sdf15/sdf/1.12/link.sdf).
Meshes can specify scale, submesh selection and convex optimization; an adapter
must preserve or reject these semantics. [SDF mesh schema](https://github.com/gazebosim/sdformat/blob/sdf15/sdf/1.12/mesh_shape.sdf).

For a first limited adapter, explicitly reject unsupported articulation, nested
models, includes, plugins, sensors, poses/frames, submeshes and physics parameters.
Do not silently flatten an appliance into scenery. A caller may explicitly
request a fixed decorative instance, but that conversion must be disclosed and
must not satisfy a task requiring movement or articulation. This is a proposed
scope boundary, not a restriction imposed by Fuel.

The bowl's source uses the same relative OBJ URI for visual and collision,
without explicit mass/inertia or a static flag. Its MTL names `texture.png` while
the PNG lives under `materials/textures/`. Preserve original files and resolve
textures deterministically; reject ambiguity rather than selecting an arbitrary
PNG. Our existing bowl adaptation keeps the original surface and uses prepared
convex collision pieces to preserve the cavity. A successful download does not
make a single convex-hull import adequate for containment tasks. [Original source
and prepared adaptation evidence](household-assets.md).

## Provenance

Retain catalog license evidence and embedded author/copyright notices separately
from software licensing. The bowl credits Google, Copyright 2020 Google LLC,
CC BY 4.0; those declarations are in its original metadata/config/SDF. Do not infer
that every Fuel asset has the same license. [Pinned bowl metadata](https://fuel.gazebosim.org/1.0/GoogleResearch/models/Threshold_Porcelain_Serving_Bowl_Coupe_White).
CC BY 4.0 requires appropriate credit, a license link and indication of changes;
retain supplied notices and identify collision conversion or estimated mass as
our modifications. [Creative Commons license summary](https://creativecommons.org/licenses/by/4.0/).
