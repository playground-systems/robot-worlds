# Alpha acceptance evidence

The release target is install → text generation → policy execution → evidence →
portable export/reload. The functional scenario is the original bowl and microwave;
the visual scenario is a detailed generated warehouse with an alternative layout.
The release is **not yet accepted**. Mocked, analytical and historical results do
not replace current native, live-model or application evidence.

| Gate | Available evidence | Required work still open |
| --- | --- | --- |
| SDK action loop | `test_household_sdk_flow.py` covers mocked generation, original assets, public RGB-D/depth observations, wheel/gripper actions, feedback, in-process and HTTP policies, timeout/disconnection/invalid actions, cancellation, replay, export/reload and regrading. | Execute this native suite on the release candidate. Its policy deliberately does not insert the bowl. |
| Physical manipulation | Historical native tests check bowl concavity and pre-placed support in the microwave. Arm approach presets now use the same complete base frame as position targets; feedback checks measured orientation as well as position. | Demonstrate robot approach, grasp, door interaction, transport, insertion, release and stable support with a labeled engineering policy. Run native no-op, decoy, partial insertion, held and unstable-placement cases. |
| Trustworthy grading | Synthetic original-asset traces exercise the negative cases, full containment, duration and missing measurements. Holding/release must name a movable root. Timed-process loading accepts fixed child-piece contact while excluding decoys and separately articulated children. | Current physical evidence for these measurements and outcomes. No heating or closure requirement is added to the bowl task. |
| Live generation and editing | Prepared assets, compact repetition, scoped edits, immutable versions and source reuse have focused analytical/persistence coverage. Catalog geometry exposes component bounds and six actual rack shelf supports. | A bounded paid prompt-to-sensor-policy-to-report/export/reload run, with actual outcome and spending; usable scene/clearance checks and a verified targeted edit. Earlier assisted factory episodes are not current sensor-only acceptance. |
| Warehouse appearance | Rack, pallet, carton, barrier and passive conveyor source adapters retain original visuals and disclose collision approximations. The conveyor retains 29 hinges; portable 0°/90° placements pass static checks. Viewer sharing/batching, texture budgets and failure recovery have CPU-only coverage. | Native contacts/materials, two genuinely generated detailed layouts, actual overview/close-up screenshots, reference comparison and viewport performance/LOD. The classic policy camera differs from browser PBR. |
| Fresh-user journey | Installer, cache verification, subset selection, cancellation, atomic publication, CLI library selection and workbench handoff have focused checks. Historical clean-install jobs exist. | Build the versioned candidate and test documented commands in a clean environment: diagnostics, BYO key, progress/errors, policy connection, cancellation, saved-result recovery and portable artifacts. Verify supported Mac operation. |
| Application and release | Workbench, selection, edit/progress controls, reports, replay and export are implemented. | Current application walkthrough: inspection, validation/error state, targeted edit, policy execution/cancellation, replay and evidence navigation; camera framing without obstruction. Capture warehouse, bowl/interior, interaction and report screenshots. Reconcile release docs/artifacts and reproducible walkthrough. |

## Execution constraints

The user requested no further CI on October 31. Commits use `[skip ci]`; do not
trigger workflows without the requested exception being approved. Heavy native
work must stay off the Mac. No new paid compute, merge or publication is authorized.
Paid model requests are reserved for bounded full acceptance runs, not development
probes. Historical worlds and recordings are not rewritten. Runtime changes require
fresh candidate validation; older green CI does not validate the current code.

Current browser acceptance is also missing. No CPU-only viewer check, mocked
bitmap decoder, source-scene import or prior screenshot establishes it.

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

## Proposed one-off hosted household check — not run

`.github/workflows/household-acceptance.yml` selects 33 tests from four files:
`test_household_sdk_flow.py`, `test_sourced_household_physics.py`,
`test_household_grading.py` and `test_arm_orientation.py`. It uses one Ubuntu
runner, a 15-minute job limit, single-threaded numeric libraries, mocked models
and an empty model API key. No package build, conversion, live model request or
publication runs. JUnit, reports, traces and checkout/runtime identity are retained
for seven days. The nine required native cases cover SDK integration, concavity
and pre-placed support; they cannot prove successful manipulation or full release
readiness. The evidence auditor rejects skipped/missing/duplicate/failing cases;
its 12 lightweight checks pass. Collection found all 33 selected tests, and the
workflow YAML parses. Hosted execution remains unverified.

**Keep CI off until the user authorizes this run.** The goal forbids heavy Mac
runs and new paid compute, while the later CI-off instruction removed the original
hosted execution path. Preparation is not authorization to run it.

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
