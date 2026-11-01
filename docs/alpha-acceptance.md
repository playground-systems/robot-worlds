# Alpha acceptance evidence

The release target is install → text generation → policy execution → evidence →
portable export/reload. The functional scenario is the original bowl and microwave;
the visual scenario is a detailed generated warehouse with an alternative layout.
The release is **not yet accepted**. Mocked, analytical and historical results do
not replace current native, live-model or application evidence.

| Gate | Available evidence | Required work still open |
| --- | --- | --- |
| SDK action loop | The second focused Linux run passed 79 checks; subsequent torso and navigation changes still need the final candidate check. The built wheel also passed original-asset generation with a mock, native RGB-D/action execution, reporting, export/reload and regrading in a fresh Mac environment outside the checkout. | Successful manipulation remains separate from these integration checks. |
| Physical manipulation | Original-bowl concavity, pre-placed support and no-op failure passed in hosted checks. A new native engineering test lifts every original bowl component over 15 cm using two physical grippers and the public torso actuator, with no counter contact after a one-second hold. A development probe also carries it through retreat, turn and forward travel. | Complete door interaction, insertion, release and stable support with a labeled engineering policy. Native decoy, partial insertion, continued-holding and brief-recontact failure checks now pass locally; include them in the final hosted evidence. Lift/carry do not establish placement. |
| Trustworthy grading | Synthetic original-asset traces exercise the negative cases, full containment, duration and missing measurements. Holding/release must name a movable root. Timed-process loading accepts fixed child-piece contact while excluding decoys and separately articulated children. | Current native checks reject a decoy in the cavity, partial insertion, a still-held bowl, and momentary tray recontact after a physical disturbance. Final candidate evidence remains to be collected. No heating or closure requirement is added to the bowl task. |
| Live generation and editing | Astra through the existing Codex sign-in generated a physically validated original-asset kitchen, then executed nine sensor-only policy actions before its token budget ended. Reporting, export/reload and regrading passed. | Successful manipulation remains open. A live targeted barrier edit and subsequent navigation/report/export/reload passed, preserving all other entities and requirements. API-key transport remains blocked by exhausted account credits; Codex plan usage is recorded separately. |
| Warehouse appearance | A live-generated 24 × 20 m, 2,135-component warehouse passed its creation-time physical checks and completed policy/report/export/reload. Original NVIDIA assets are visible in actual application screenshots. | The first layout was too sparse and its zero-tolerance navigation rule failed at ground contact. Pre-run validation now rejects that mismatch. A distinct 20 × 16 m, 3,046-component warehouse passed generation and navigation; current app overview/detail/report images are saved. Reference-level presentation and measured viewport performance remain open. |
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

The subsequent `move_torso` action exposes TIAGo's existing source actuator with
a 0.05 m/s target ramp and encoder feedback. G1 excludes it. The original-bowl
two-hand lift test passes locally in 11.24 seconds; this later action is not
covered by the earlier release archives. The fixture changes counter dimensions
and initial placement to provide access, retaining the bowl's original geometry
and 0.6 kg estimated mass. No object pose writes or attachment forces are used.

[Current application screenshots](images/live-household/evidence.json) show the
live-generated kitchen, original bowl, report and containment evidence. The
report now states its stop reason explicitly. Its displayed replay is retained
from the original run; regrading created a new report without regenerating frames.

The [first generated warehouse workflow](evidence/release-2026-10-31/live-warehouse.json)
completed in 364 seconds, including three generation attempts and three policy
decisions. The robot drove two metres, waited two seconds and finished. Its base
origin was inside the XY target but about 1 mm below its zero-tolerance vertical
boundary, so the recorded verdict is failed. New validation diagnoses this
ground-level mismatch before evaluation; it does not silently change tolerances
or rewrite old scores. The screenshots show functional equipment placement,
not reference-level visual acceptance.

Thin fixed box markings can now be appearance-only without invented mesh files.
Native checks verify zero mass/contact flags; rubric checks reject appearance-only
subjects and supports. This prevents painted lanes from becoming physical
obstacles or fake grading fixtures. These later changes require a new candidate
build; earlier archive hashes remain historical evidence.

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

## Dense warehouse and scoped live edit

The [dense alternative](evidence/release-2026-10-31/live-dense-warehouse.json)
completed in 251.85 seconds: one generation call (44,634 tokens) and three
sensor-only policy calls (78,809 tokens). Six racks, 72 cartons, six pallets and
two passive conveyors use original sourced geometry. The navigation criterion
passed without changing it after execution. This does not demonstrate material
handling or autonomous logistics. [Overview](images/dense-warehouse/overview.png),
[rack detail](images/dense-warehouse/rack-detail.png) and
[recorded report](images/dense-warehouse/report.png) are actual application captures.

The [targeted edit](evidence/release-2026-10-31/live-targeted-edit.json) completed in
140.52 seconds: one edit call (34,098 tokens), three policy calls (79,643 tokens).
It translated one seven-part barrier assembly by 25 cm. Exact comparison verified
that every unrelated entity, all physical parameters, task, rubric, robot start
and world bounds were preserved. The new version passed physical validation,
navigation, portable reload and regrading. The parent version and run remain
unchanged. Both workflows use Codex plan usage; no dollar charge is reported by
that transport. These are warm-cache timings, not fresh-download timings.

## Browser recovery and geometry regressions

The isolated loopback model fixture exercises the normal HTTP client, generation,
worker, physics and browser paths with a dummy key. It makes no paid requests.
It reproduced and verified fixes for two UI issues: a configured key was labeled
“Connected” before authentication, and all incomplete generations were described
as budget failures. Invalid-key errors now identify the HTTP failure and next step.
Starting a new world also clears the previous terminal job notice.

The same walkthrough exposed a WebGL context lifecycle defect: disposing the
preview forcibly lost the persistent canvas context before constructing the
validated-world viewer. Disposal now frees renderer resources without destroying
the reusable canvas. A fresh preview-to-ready browser generation retained its
interactive 3D view after this fix.

A native 45°-rotated original-bowl fixture reproduced a containment false negative
from including the appearance mesh's rotated rectangular bounds. Grading now
aggregates every attached physical collision piece and excludes appearance-only
volume. The original tolerance, source collision geometry and duration remain
unchanged. The rotated fixture and four native failure/geometry checks passed;
50 focused rubric, adversarial and evidence-audit checks also passed. Historical
worlds and reports were not rewritten.
