# Alpha acceptance evidence

The release target is install → text generation → policy execution → evidence →
portable export/reload. The functional scenario is the original bowl and microwave;
the visual scenario is a detailed generated warehouse with an alternative layout.
The release is **not yet accepted**. Mocked, analytical and historical results do
not replace current native, live-model or application evidence.

| Gate | Available evidence | Required work still open |
| --- | --- | --- |
| SDK action loop | The [focused Linux run](#one-off-hosted-household-check--passed) passed mocked generation, original assets, public RGB-D/depth observations, wheel/gripper actions, feedback, in-process and HTTP policies, timeout/disconnection/invalid actions, cancellation, replay, export/reload and regrading. | This integration scope is verified at `9273a3e`. Its policy deliberately does not insert the bowl; manipulation and live acceptance remain separate. |
| Physical manipulation | The focused native run passed original-bowl concavity, pre-placed support in the microwave and the no-op failure case. Arm approach presets use the same complete base frame as position targets; feedback checks measured orientation as well as position. | Demonstrate robot approach, grasp, door interaction, transport, insertion, release and stable support with a labeled engineering policy. Run native decoy, partial insertion, held and unstable-placement cases. |
| Trustworthy grading | Synthetic original-asset traces exercise the negative cases, full containment, duration and missing measurements. Holding/release must name a movable root. Timed-process loading accepts fixed child-piece contact while excluding decoys and separately articulated children. | Current physical evidence for these measurements and outcomes. No heating or closure requirement is added to the bowl task. |
| Live generation and editing | Prepared assets, compact repetition, scoped edits, immutable versions and source reuse have focused analytical/persistence coverage. Catalog geometry exposes component bounds and six actual rack shelf supports. | A bounded paid prompt-to-sensor-policy-to-report/export/reload run, with actual outcome and spending; usable scene/clearance checks and a verified targeted edit. Earlier assisted factory episodes are not current sensor-only acceptance. |
| Warehouse appearance | Rack, pallet, carton, barrier and passive conveyor source adapters retain original visuals and disclose collision approximations. The conveyor retains 29 hinges; portable 0°/90° placements pass static checks. Viewer sharing/batching, texture budgets and failure recovery have CPU-only coverage. | Native contacts/materials, two genuinely generated detailed layouts, actual overview/close-up screenshots, reference comparison and viewport performance/LOD. The classic policy camera differs from browser PBR. |
| Fresh-user journey | Installer, cache verification, subset selection, cancellation, atomic publication, CLI library selection and workbench handoff have focused checks. Historical clean-install jobs exist. | Build the versioned candidate and test documented commands in a clean environment: diagnostics, BYO key, progress/errors, policy connection, cancellation, saved-result recovery and portable artifacts. Verify supported Mac operation. |
| Application and release | Workbench, selection, edit/progress controls, reports, replay and export are implemented. | Current application walkthrough: inspection, validation/error state, targeted edit, policy execution/cancellation, replay and evidence navigation; camera framing without obstruction. Capture warehouse, bowl/interior, interaction and report screenshots. Reconcile release docs/artifacts and reproducible walkthrough. |

## Execution constraints

On October 31 the user resumed all remaining release work and authorized the
execution needed to complete it, superseding the earlier one-job CI restriction
and compute blocker. Use focused CI and bounded live runs; keep heavy work hosted
where practical and local diagnostics serial, headless and low priority. Commits
use `[skip ci]` unless intentionally triggering a necessary acceptance job.
No merge or publication is planned until the candidate is actually accepted.
Paid model requests are reserved for bounded full acceptance runs, not development
probes. Historical worlds and recordings are not rewritten. Runtime changes require
fresh candidate validation; older green CI does not validate the current code.

The [viz3d-guided warehouse preview](viz3d-rendering.md) now has actual application
overview and detail screenshots, with original NVIDIA asset textures. Its
deterministic layout and static checks do not establish live text generation,
native contacts, reference-level visual quality or the complete browser acceptance
journey. Those gates remain open.

## Evidence details

- [Household sources and native-test limits](household-assets.md): original geometry,
  attribution, convex bowl pieces and articulated appliance.
- [Warehouse source/adaptation evidence](simready-selection.md): pinned sources,
  collision approximations, cached preparation timings and portable placement.
- [Material and viewer evidence](source-materials.md): source channels, renderer
  differences, batching and resource/error handling. Latest focused loader checks:
  36 passing; transfer timeout covers headers/body, not decoding/rendering.
- `test_prepared_assets.py`, `test_simready_rack.py`, `test_asset_library.py`: 31
  focused checks for catalog references, rotated support targets and persistence.
  Actual bowl/microwave/rack/conveyor metadata occupies 39,511 characters.
- `test_arm_orientation.py`, `test_sensors.py`: 25 focused mathematical/action-boundary
  checks; no physical rollout. `test_eval_adversarial.py`: 17 evaluator/monitor
  checks, including synthetic original-bowl child-piece support.

Reported local timings distinguish cached preparation/static expansion from live
customer latency. Actual model latency, successful manipulation, current native
material behavior, browser frame rate and release-candidate installation remain
unverified. Detailed measurements and their limits live in the linked evidence
files rather than being repeated here.

## One-off hosted household check — passed

[Run 41228261769](https://github.com/playground-systems/robot-worlds/actions/runs/41228261769)
passed **33 tests, zero skips, errors or failures** on October 31. Tests took
78.093 seconds; the single Ubuntu job took 1 minute 43 seconds against a 15-minute
cap. Head `9273a3e828cc3def0f3c8019a2c8f97d15ecf9c3` was tested through GitHub's
merge checkout `76c9619f06b26b46adc5d1a428452859a085ba98`, using Python 3.13.17
and MuJoCo 3.17.0. No paid model calls ran. Other CI jobs were skipped, and the
trigger label was removed.

- Both public-interface policies completed five steps and physically moved the
  robot 0.0760 m. Exported worlds ran again after the original directories were
  deleted; regrading preserved the results.
- Timeout, disconnect and invalid-action episodes retained evidence as
  `policy_error`; cancellation remained `cancelled`. None counted as a valid
  task failure. Normal movement-only and no-op episodes correctly failed placement.
- The nine required native cases passed the evidence audit. Bowl concavity and
  pre-placed support passed, but neither demonstrates robot insertion.

The [committed run record](evidence/household-2026-10-31/run.json),
[JUnit](evidence/household-2026-10-31/junit.xml) and
[audit](evidence/household-2026-10-31/acceptance.json) retain exact identity,
results and limits. The [full artifact](https://github.com/playground-systems/robot-worlds/actions/runs/41228261769/artifacts/13052338207)
contains reports, traces and recorded frames with seven-day hosted retention;
a local copy is retained under `.robot-worlds/acceptance/household-41228261769`.
These recorded simulation frames are not application screenshots.

`.github/workflows/household-acceptance.yml` selects 33 tests from four files:
`test_household_sdk_flow.py`, `test_sourced_household_physics.py`,
`test_household_grading.py` and `test_arm_orientation.py`. It uses one Ubuntu
runner, a 15-minute job limit, single-threaded numeric libraries, mocked models
and an empty model API key. No package build, conversion, live model request or
publication runs. JUnit, reports, traces and checkout/runtime identity are retained
for seven days. The nine required native cases cover SDK integration, concavity
and pre-placed support; they cannot prove successful manipulation or full release
readiness. The evidence auditor rejects skipped/missing/duplicate/failing cases;
its 12 lightweight checks pass. All 33 selected tests executed successfully.

The original one-off run is complete. The later authorization above now permits
further focused execution for the remaining gates; do not repeat unchanged tests
or confuse this earlier result with later revisions.

Normal commits, PR updates and label removals do not trigger this workflow. The
`run-household-acceptance` label suppresses the existing full-suite, packaging and
conversion jobs while present. For the unmerged draft, use the explicit label
event after authorization ([GitHub trigger reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)):

1. Apply the dedicated label while the head still has `[skip ci]`.
2. Push one empty commit without a skip marker. Existing jobs remain suppressed;
   the household workflow does not listen for commit updates.
3. Remove/reapply the label once to request the single household job.
4. Record the run ID and remove the label. Subsequent commits retain `[skip ci]`.
   Another attempt needs a diagnosed change and authorization covering that run.
