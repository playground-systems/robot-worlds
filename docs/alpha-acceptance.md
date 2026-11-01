# Alpha acceptance evidence

The release target is install → text generation → physical policy execution →
inspect evidence → export/reload. **The candidate is not yet accepted:** final
rendered package verification and warehouse presentation measurements remain open.

| Gate | Verified evidence | Remaining work |
| --- | --- | --- |
| SDK and HTTP | Mocked generation with original assets, native sensors/actions, remote policy errors, cancellation, reporting and portable regrading pass focused checks. | Check the final built artifacts after the latest runtime changes. |
| Physical manipulation | The original bowl was grasped, lifted, carried a short distance, guided into the original microwave, released and supported. All three criteria passed through public SDK actions and after export/reload/regrading. | Capture the rendered installed-wheel run. This engineering controller knows the fixture; the door starts open. It is not autonomous manipulation or a door-opening demonstration. |
| Grading | Native decoy, partial insertion, continued holding and brief recontact checks reject false success. Actual compiled collision vertices avoid rotated-bounds false failures without changing tolerances. | Include the current native cases in the final hosted audit. |
| Live model workflow | Astra via supported Codex sign-in generated a kitchen and two warehouses. One dense warehouse and a scoped edit passed short navigation, reporting and portable regrading. | Sensor-only household manipulation failed within its token budget. API-key account credits remain exhausted; Codex plan usage is separate. |
| Browser | Actual walkthrough covered invalid-key recovery, generation cancellation, preview-to-ready rendering, scoped edit and parent navigation, policy execution/cancellation and evidence. | Capture final manipulation report and interior views. |
| Installation | Earlier 0.1.0a1 wheel/source archives installed outside the checkout on Linux. The wheel also passed a fresh Apple silicon installation and offline native SDK walkthrough. | Repeat against the final candidate artifacts. |
| Presentation | Actual generated NVIDIA warehouse overview, detail and report images are retained. | Improve reference-level architecture/presentation; measure viewport performance and finalize the reproducible walkthrough. |

## Physical placement

The [headless engineering SDK record](evidence/release-2026-10-31/engineering-placement.json)
contains the complete criterion evidence. The validated fixture executed 37
decisions over 111.82 simulated seconds. Validation, evaluation and portable
reload/regrading took 137.21 wall seconds on this Mac, with 2.09 GB peak process
memory. No model calls were made. These timings precede the latest incremental
trace-checkpoint optimization and do not include sensor/replay rendering.

The controller uses original source geometry, the bowl's documented 0.6 kg mass
estimate, and original actuator limits. It reads the robot base transform to
convert known fixture targets into base-frame commands. It never writes object
poses or creates grasp attachments. Two bounded arm motions reported unreachable
or blocked status; later actions recovered. This is not a collision-free planner.
The reproducible script is `tools/acceptance/engineering_household.py`.

Containment uses bounds measured from the compiled physical mesh vertices. It
excludes appearance-only geometry and empty corners of rotated bounding boxes.
The recorder retains these measurements in private evaluator traces; policies
still receive only the documented sensor contract. Old traces retain conservative
bounds. Historical worlds, replays and scores are unchanged.

## Live generation and editing

- [Household](evidence/release-2026-10-31/live-household.json): 167.5 seconds,
  one generation call and nine sensor-only policy actions, followed by a token
  limit. The task failed; reporting, export/reload and regrading passed.
- [First warehouse](evidence/release-2026-10-31/live-warehouse.json): 24 × 20 m,
  2,135 components, 364 seconds. Physical creation checks passed; navigation
  failed because a zero-tolerance target excluded the settled base by about 1 mm.
  New pre-run validation diagnoses that mismatch instead of changing old scores.
- [Dense alternative](evidence/release-2026-10-31/live-dense-warehouse.json):
  20 × 16 m, 3,046 components, 251.85 seconds. Six racks, 72 cartons, six pallets
  and two passive conveyors retain original source geometry. Generation used
  44,634 tokens; three sensor-only policy calls used 78,809. Navigation passed.
- [Scoped edit](evidence/release-2026-10-31/live-targeted-edit.json): moved one
  seven-part barrier assembly by 25 cm in 140.52 seconds. Exact comparison verified
  all unrelated entities, physical parameters, task, rubric, start and bounds.
  Validation, navigation and portable regrading passed. Edit/policy usage was
  34,098/79,643 tokens. The parent remains available unchanged.

These are warm-cache full-workflow timings. Codex reports plan usage, not an API
dollar charge; none is invented. Separate generation and policy ledgers retain
usage across resets. No new paid compute was started for this acceptance work.

Actual application captures: [warehouse overview](images/dense-warehouse/overview.png),
[rack detail](images/dense-warehouse/rack-detail.png),
[report](images/dense-warehouse/report.png),
[targeted edit](images/dense-warehouse/targeted-edit.png), and
[household inspection](images/live-household/evidence.json).

## Browser recovery

The [browser record](evidence/release-2026-10-31/browser-flow.json) uses an isolated
loopback model fixture, a dummy key, and real HTTP jobs and physics. It makes no
paid requests. The walkthrough exposed and verified fixes for misleading key and
budget labels, stale terminal notices, and loss of the reusable WebGL canvas when
replacing a preview. The replacement now retains an interactive 3D scene.
Cancellation preserves an invalid/interrupted episode rather than counting it as
a valid task failure. A later native regression verifies the explicit stop reason.

## Packages and focused verification

Historical [run 41235518874](https://github.com/playground-systems/robot-worlds/actions/runs/41235518874)
passed 77 checks, built both 0.1.0a1 archives and installed each outside the checkout.
Its 66.3 MB downloaded evidence archive was SHA-256 verified. Exact identity and
archive hashes are in the [release record](evidence/release-2026-10-31/run.json).
The wheel then passed the [fresh Mac native SDK walkthrough](evidence/release-2026-10-31/mac-wheel-native.json)
in 9.43 seconds. The later focused run 41256105862 passed 79 checks. Neither
historical build validates changes made afterward.

The current opt-in household workflow checks the affected SDK, rubric, native
asset, arm and viewer paths, requires 16 native cases without skips, benchmarks
asset preparation, builds both archives, and runs rendered engineering placement
from a clean installed wheel. It uses one Ubuntu runner with a 15-minute cap,
single-threaded numeric libraries and no model key. Current focused local checks
include 69 SDK/rubric/checkpoint tests; changed files pass Ruff.

Normal commits use `[skip ci]`. The dedicated `run-household-acceptance` label
suppresses the existing full-suite and packaging jobs. The unmerged workflow runs
on its explicit label event; remove the label after recording the result. The
user authorized the remaining focused execution on October 31. No merge or
publication is planned during candidate preparation.

## Sources and limits

- [Household assets](household-assets.md): original bowl concavity, appliance
  articulation, attribution and documented adaptations.
- [Warehouse assets](simready-selection.md): pinned sources and collision proxies.
- [Materials](source-materials.md): source channels, renderer differences and batching.
- [SDK contract](sdk.md): onboard sensors, bounded tools and private grading state.
- [Release procedure](releasing.md): package contents, clean installs and review gates.

The deterministic warehouse preview is a development fixture, not evidence of
live generation. A successful reset proves loadability and stability, not task
solvability. Attractive equipment views do not prove powered transport or logistics.
