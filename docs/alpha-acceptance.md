# Alpha acceptance evidence

The release target is install → text generation → policy execution → evidence →
portable export/reload. The functional scenario is the original bowl and microwave;
the visual scenario is a detailed generated warehouse with an alternative layout.
The release is **not yet accepted**. Mocked, analytical and historical results do
not replace current native, live-model or application evidence.

| Gate | Available evidence | Required work still open |
| --- | --- | --- |
| SDK action loop | The latest focused Linux run passed 77 checks. The built wheel also passed original-asset generation with a mock, native RGB-D/action execution, reporting, export/reload and regrading in a fresh Mac environment outside the checkout. | Successful manipulation remains separate from these integration checks. |
| Physical manipulation | The focused native run passed original-bowl concavity, pre-placed support in the microwave and the no-op failure case. Arm approach presets use the same complete base frame as position targets; feedback checks measured orientation as well as position. | Demonstrate robot approach, grasp, door interaction, transport, insertion, release and stable support with a labeled engineering policy. Run native decoy, partial insertion, held and unstable-placement cases. |
| Trustworthy grading | Synthetic original-asset traces exercise the negative cases, full containment, duration and missing measurements. Holding/release must name a movable root. Timed-process loading accepts fixed child-piece contact while excluding decoys and separately articulated children. | Current physical evidence for these measurements and outcomes. No heating or closure requirement is added to the bowl task. |
| Live generation and editing | Astra through the existing Codex sign-in generated a physically validated original-asset kitchen, then executed nine sensor-only policy actions before its token budget ended. Reporting, export/reload and regrading passed. | Successful manipulation, generated warehouse acceptance and a verified live targeted edit. API-key transport remains blocked by exhausted account credits; Codex plan usage is recorded separately. |
| Warehouse appearance | Rack, pallet, carton, barrier and passive conveyor source adapters retain original visuals and disclose collision approximations. The conveyor retains 29 hinges; portable 0°/90° placements pass static checks. Viewer sharing/batching, texture budgets and failure recovery have CPU-only coverage. | Native contacts/materials, two genuinely generated detailed layouts, actual overview/close-up screenshots, reference comparison and viewport performance/LOD. The classic policy camera differs from browser PBR. |
| Fresh-user journey | Version 0.1.0a1 wheel and source archives installed independently on Linux. The same wheel installed on Apple silicon Python 3.13, passed dependency checks and completed the offline native SDK walkthrough. | Complete the browser walkthrough and recheck any subsequently changed release artifact. |
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
customer latency. Successful manipulation, current warehouse native material
behavior and browser frame rate remain unverified. Detailed measurements and
their limits live in the linked evidence files.

## Current release checks

[Run 41235518874](https://github.com/playground-systems/robot-worlds/actions/runs/41235518874)
passed **77 tests with no failures**, built and checked both 0.1.0a1 archives,
and installed each outside the checkout. The job took 109 seconds. The downloaded
66.3 MB evidence archive was verified against GitHub's SHA-256. Exact commit,
archive hashes, JUnit and installation diagnostics are in the
[release evidence](evidence/release-2026-10-31/run.json).

The built wheel then passed a fresh Apple silicon Python 3.13 installation,
`pip check`, offline diagnostics and the [native SDK walkthrough](evidence/release-2026-10-31/mac-wheel-native.json)
in 9.43 seconds. It rendered real sensor frames, measured depth, drove with wheel
odometry, operated a gripper and preserved the deliberately unsuccessful task
verdict through export/reload and regrading. The script is
`tools/acceptance/offline_household.py`; it makes no model calls.

The [live household run](evidence/release-2026-10-31/live-household.json) completed
in 167.5 seconds using Astra through the supported Codex CLI adapter. Generation
used one model call; the sensor-only policy executed nine actions and then reached
its token budget. It did not grasp or insert the bowl. This establishes the live
workflow and retained evidence, not manipulation success. Both phase ledgers are
retained; Codex plan usage has no inferred API dollar charge. New acceptance runs
use separate generation/policy clients so an episode reset cannot erase generation
usage or reset the full run's API spending allowance.

The current arm planner uses `mj_kinematics` and `mj_comPos` for hypothetical
joint poses, following MuJoCo's [Jacobian pipeline](https://mujoco.readthedocs.io/en/stable/APIreference/APIfunctions.html#mj-jac).
Running full forward dynamics during this search overflowed the contact arena
when candidate poses intersected the original bowl's convex pieces. The same
native fixture now returns an unreachable-target result without advancing live
state or crashing (2.36-second focused test). Actual actuator execution still
uses full physics. Position/Jacobian equivalence and base-frame checks pass;
this fix does not establish a successful grasp.

[Current application screenshots](images/live-household/evidence.json) show the
live-generated kitchen, original bowl, report and containment evidence. The
report now states its stop reason explicitly. Its displayed replay is retained
from the original run; regrading created a new report without regenerating frames.

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
