# Bundled starter environments

**Failure pattern:** a converted source scene, attractive thumbnail or available
card can conceal missing source objects, blocked openings, invalid physics or an
incomplete package.

**Verified by:** [native preparation run 41492179245](https://github.com/playground-systems/robot-worlds/actions/runs/41492179245)
passed native checks and real offline API/SDK imports for all four scenes. The
same bundles passed actual Mac imports through production spawned workers,
idempotent SDK reopening and application reopening. These checks used no model
calls or robot policy. See `docs/starter-acceptance.md` and its machine-readable
evidence for identities, counts, timings and limits. This is historical evidence,
not certification of later edits. The separate [package-only run 41511111605](https://github.com/playground-systems/robot-worlds/actions/runs/41511111605)
passed offline imports and wheel/source inspection with the committed bundles.

## Procedure

1. Read `docs/starter-environments.md`, `docs/starter-acceptance.md` and the current
   `.github/workflows/starter-environments.yml`. The packaged catalog and
   `tools/starters/` own revisions, licenses, acquisition limits, reviewed repairs
   and conversion options; inspect those sources rather than copying their pins
   or budgets into this skill. Retain original layouts, source visuals/UVs,
   notices, source-instance coverage and per-file provenance through export.
2. For a source/importer/native change, use the authorized Linux preparation
   worker and fresh output directories. Follow the workflow's installed
   dependencies and sequence:

   ```sh
   .venv/bin/python tools/starters/prepare.py --output starter-output --cache /tmp/robot-worlds-starter-sources
   xvfb-run -a .venv/bin/python tools/starters/freeze.py --input starter-output --output starter-bundles --evidence starter-evidence/native
   ```

   Conversion records model-specific failures while diagnosing other scenes;
   partial scenes receive no release manifest. Freeze must pass every required
   native check, verify portable exports and write the complete release catalog.
   Inspect both conversion failures and native evidence: an early shell pipeline
   once hid an exception behind `tee`. Preserve `set -euo pipefail` when logging.
3. Install the complete validated output together, including its catalog,
   archives and thumbnails:

   ```sh
   cp -R starter-bundles/. src/robot_worlds/assets/starters/
   .venv/bin/python tools/starters/check_installed.py
   ```

   The check exercises real bundled bytes through API submission, the normal
   spawned worker, world/preview reads, repeated public SDK selection and app
   reopening. Its default workspace is temporary. Add `--require-current-runtime`
   on the matching release installation; on another installation, retain and
   report the compatibility flag instead of treating successful unpacking as
   local native validation.
4. Verify the customer boundary: adding a starter only verifies and unpacks
   local data. It needs no key, network, conversion or simulation. Evaluation
   still enforces runtime identity. A mismatch leads to **Checks → Validate on
   this computer**, `world.revalidate()` or the CLI revalidation command; native
   checks create an immutable child while preserving scene, task, rubric and
   import profile. Failed checks remain visible and block evaluation.
5. Complete packaging separately with the current build and
   `tools/check_distribution.py` gate. Read `.github/workflows/starter-packages.yml`
   for the narrower verification path when committed bundles are unchanged.
   Inspect its actual outcome for the current candidate before claiming package
   acceptance; native/import success cannot substitute for it. Preparation
   output and diagnostic folders belong outside the distributable package.
6. Inspect actual app selection and source geometry, and record evidence against
   the exact candidate. Imported equipment is fixed navigation scenery, not
   manipulation-certified. Reset/settling/rendering passes establish loadability
   and initial stability, not policy success. Aerial cutaways affect inspection
   visibility only; saved contact geometry and policy cameras retain the scene.

## Proven failure diagnoses

- **Filled openings:** a whole concave mesh hull filled shelves, rooms or the
  U-shaped hospital curtain. Reuse the verified exact-surface collision adapter
  for architecture and cloth. Conservative component envelopes are restricted
  to reviewed static navigation props/hardware, disclose filled concavities, and
  preserve original visuals. Repeated components still count toward scene limits.
- **COLLADA exporter quirks:** pycollada exposes explicit zero-count material
  groups with missing arrays. Skip only proved empty groups and disclose them.
  Gazebo Classic applies authored node transforms and unit scale but ignores
  `up_axis`; hospital Y_UP files already author their Z-up rotations. An extra
  rotation would stand their floors upright. Preserve the reviewed Gazebo semantics.
- **Legacy OBJ geometry:** reviewed Fuel data contains skew nonplanar quads,
  exact zero-area records and wire lines. Use the bounded source adapter and its
  disclosures. A crossed 2D projection is not proof of a 3D intersection; a true
  planar bow-tie must still fail. Retain original 3D corners and UVs rather than
  silently flattening, dropping or reordering real surfaces.
- **Saved pose corruption:** only exact pinned instances receive recorded rigid
  assembly repairs. The source includes both an extremely high bookstore book
  and three hospital props far underground. Keep original poses and adjustments,
  preserve XY/orientation, and retain the importer's upper/lower corruption
  gates. The hospital's ordinary descending staircase is authored below ground
  and remains intact. Warehouse walking-plane alignment translates the entire
  source layout; the planner shares the validator's ground tolerance and stays
  on the reviewed floor footprint.
- **Static-body arena overflow:** many fixed source bodies stressed MuJoCo's
  broadphase even with few degrees of freedom. Use the runtime's bounded arena
  sizing based on body count and inspect recorded allocation/peak usage. Preserve
  contacts and source geometry; disabling collisions is not a valid remedy.

## Ruled-out work

Full conversion, native checks and dense policy trials on the Mac caused resource
pressure and are unnecessary for offline import acceptance. Use small mocked
adapter fixtures locally and the focused authorized Linux native gate for real
scenes. Model retries cannot repair static conversion, source-pose or packaging
failures; these workflows need no model access or credential recovery.
