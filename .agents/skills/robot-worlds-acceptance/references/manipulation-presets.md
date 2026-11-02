# SimReady manipulation presets

**Failure pattern:** source-looking props, a ready base world or plausible task
ordering can hide lost source contacts, stale packaging inputs or false grading.
**Verified by:** `docs/evidence/manipulation-presets-2026-11-02.json` records strict
native checks for kitchen, factory and living room, all 44 task reset checks,
keyless SDK reopening, and actual app selection followed by native revalidation.
It records zero policy runs; physical task completion remains unverified.

## Procedure

1. Read `docs/manipulation-presets.md` and the dated evidence before repeating
   work. Keep the current session's CI and Mac resource limits; the verified
   session kept CI off and local work bounded. Reuse evidence matching the
   candidate runtime. Keep older AWS static starters and their identities intact.
2. Prepare the existing source adapters with `tools/prepare_*_library.py`.
   Catalogs under `src/robot_worlds/assets/simready/` own source pins, licenses
   and disclosed adaptations. Furniture and props must come from prepared
   SimReady assets; room architecture and paint may use fixed primitives.
   Use `online_assets=False` and an explicit `allowed_asset_ids` catalog.
   Inspect the raw plan too: an asset allowlist alone cannot establish that
   direct geometry represents architecture. Retain source visuals/materials.
3. Preserve source physics before composing scenes. The cabinet and counter
   adapters retain source joints and `collide_parent=False` where the inspected
   USD layers leave joined-body collision disabled. `joint_collisions.py` expands
   only the connected parent/child rigid groups, including welded component
   bodies and stopping at other joints or movable roots. Static validation and
   MuJoCo use the same bounded pair list. Retain other-drawer, robot and loose
   object contacts and the original reset tolerances. Reuse counter preparation
   v3: its angled hinge proxies clip actual source triangles into bands at the
   neighboring pullout edge, retaining clearance and every visual triangle.
4. Have Astra author the complete layout, named tasks and semantic rationale
   together. The proven compositions are in
   `tools/acceptance/astra_authored_scene_plans.py`; the Astra subagent authored
   these compositions, then replayed them deterministically for native checks.
   Persist `generation-plan.json`, `task-suite.json`,
   `semantic-plan.md` and the selected `asset-library/` before validation.
   Freeze these inputs for the run. Each scene needs at least 12 distinct tasks
   sharing identical reset geometry; the recorded release has 16/15/13.
5. Validate every task reference before native work. Publish the initial native
   world with `World.from_spec(..., task_catalog=TaskCatalog(...))` so identity
   version 4 includes the catalog without an unpublished parent dependency.
   Replay saved inputs into a fresh creation directory:

   ```sh
   .venv/bin/python tools/acceptance/create_manipulation_presets.py \
     --prompt tools/acceptance/prompts/manipulation-kitchen.txt \
     --library "$authored/asset-library" --saved-plan "$authored/generation-plan.json" \
     --task-suite "$authored/task-suite.json" --semantic-plan "$authored/semantic-plan.md" \
     --output "$creation"
   ```

   Set `authored` to the saved scene directory and `creation` to a new output
   directory. Require completed status, strict schema/geometry/settling/reset/
   initial-rubric/render passes, and exactly one passing reset check per task.
   Saved replay makes no validation model calls; retain separate authoring usage.
6. Freeze only that checked world into a new destination:

   ```sh
   .venv/bin/python tools/acceptance/freeze_manipulation_preset.py \
     --creation "$creation" --authored "$authored" \
     --id simready-kitchen --destination "$destination"
   ```

   Match the creation world ID, all four saved-input hashes (plan, tasks,
   semantic plan and library manifest), and task sidecar against `world.tasks`.
   Thumbnail runtime model/implementation fingerprints must match validation.
   Re-export with `world.export()`; a stale creation ZIP is not authoritative.
   Publish the bundle, thumbnail, plan, tasks, semantic plan, preparation evidence
   and release entry together. Distribution checks require all seven files.
7. Verify keyless `add_starter()`, load/export and `world.tasks`. Select through
   `world.with_task(id)` and the actual workbench Task selector; confirm unchanged
   physical identity, objects and poses. Selection creates a preview instruction
   version, then **Checks → Validate on this computer** or `revalidate()` creates
   the checked child. Successful app selection must leave the Task inspector
   visible; failed selection keeps its error visible. Record these separately
   from robot execution and never infer reachability or policy success from them.
   For full CUA evidence images, save the returned `.screenshot` from
   `getAXStateAndScreenshot({disableDiffing:true})`; repeated screenshots may be cropped diffs.

## What didn't work

- A Codex CLI request at high reasoning timed out after 360 seconds without a
  candidate. Use the actual Astra-authored saved plans for reproducible native
  replay; distinguish replay usage from the cost of authoring.
- Separate welded component bodies lost the source joined-body collision policy;
  broad angled hinge boxes also filled source-empty clearance. Use precise rigid
  groups and source-clipped counter v3 proxies, rather than global collision
  filtering, deleting sibling contacts or relaxing reset penetration limits.
- Strict `support → release` ordering rejected a natural simultaneous set-down
  and release; a timed grasp witness also demanded an artificial pause before
  motion. Use a zero-duration grasp witness, then held `carried_distance`, then
  parallel final support and release with both depending on carry. Keep the three
  saved grading cases in `tests/test_astra_transfer_rubric.py` and
  `tests/fixtures/astra_transfer_traces.json`: immediate release and fast carry
  pass; release before carry fails. These synthetic traces verify grading only.
