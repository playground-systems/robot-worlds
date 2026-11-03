# Native source previews

**Failure pattern:** textured native compilation exceeded the Mac memory cap;
a successful geometry import then exposed free-joint and shader incompatibilities.
**Verified by:** the 2026-11-02 RoboCasa browser export completed under its
600 MiB/60-second watchdog, with 41 focused Python checks and 32 browser-module
checks. Actual app screenshots verified original textures, cabinet/microwave
hinges and a fridge drawer. This establishes source inspection, not task success.

1. Verify the existing source bundle and its author decisions. Use
   `source_scene_bundle` and `docs/native-source-scene-bundles.md`; retain source
   dependency/license bytes. Delegate cameras and appearance decisions to the
   model required by repository instructions.
2. For a bounded preview, use `tools/export_source_scene_preview.py` with a new
   output directory under the server's `source-scenes/`. Keep the original bundle
   immutable. Run full scenes in an external RSS/wall watchdog under the user's
   current limits; the CLI CPU limit alone is insufficient. The verified path
   detaches only native texture allocations and serializes mesh compilation.
3. Check `source-preview.json` hashes, native inventory, derivation edits and
   zero physics-step count. Native compiled geom/body frames already incorporate
   mesh transforms; applying mesh offsets or scales again corrupts placement.
4. Restart the local server after route changes, with no active jobs. Open
   `/?source=<id>` through computer use. Reload after frontend changes and verify
   the actual canvas, not just a successful fetch or static unit test.
5. Use **Hide panels** for the authored 16:9 framing. Verify the DOM canvas aspect,
   capture interior/overview, and return images to the authorized scene author.
   Test one hinge and one slider through the UI and preserve the kinematic label.
   Keep source inspection separate from robot evaluation and physical acceptance.

## What did not work

- Full native texture allocation exceeded the watchdog. Sharing exact duplicate
  texture definitions reduced allocation but still did not fit. Retaining source
  image bytes in the browser avoided changing scene geometry or physics.
- Parallel native mesh compilation produced intermittent memory spikes; serial
  compilation completed the same scene. Preserve its execution-only change in
  the derivation record.
- Free/ball joint references are vectors, not scalar angles. Retain their native
  initial poses and disclose unsupported browser manipulation.
- Three's default Phong diffuse includes 1/pi normalization absent from classic
  source coefficients. Use the reviewed scoped shader correction, preserving
  specular and texture hooks; arbitrary exposure/light boosts are not equivalent.
- Browser range inputs round floating-point endpoints. Clamp UI values to the
  exact original range before invoking strict joint limits; do not widen limits.
- Cached JS hid a loader fix. Static JS/CSS now revalidate; check the real browser
  after reload. A unit-test-only result cannot verify GPU shader compilation.

The accepted kitchen remains an inspection-stage result. Shadows, appliance
material response, specular/reflectance differences, physical contacts and robot
tasks have separate outstanding gates in the author review and evidence records.
