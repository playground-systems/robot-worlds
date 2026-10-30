# Drift review and bounded hands-on protocol

Research date: 2026-10-30. This note distinguishes **documented** product behavior, **observed** behavior in our session, and **proposed tests**. An authenticated native trial now demonstrates a small physical wheel-drive scene and one independently verified narrow edit. Factory composition and the remaining capability tests are still pending.

## Evidence so far

**Observed:** the official 2.0.1 amd64 package downloaded and extracted successfully. Its CLI stayed silent through bounded help/startup attempts in this Mac's existing Docker x86 emulation; no CLI authentication URL or simulation was reached. The container was removed. Exact environment, package hash, attempts and limitations are in [the runtime check](drift-runtime-check.md).

**Observed remotely, reported by the parent agent:** the existing RTX 4000 was unavailable. A temporary RTX 4090 started at 18:17:54 UTC on 2026-10-30, at $0.89/hour compute plus $0.007/hour disk, within the approved $10 total ceiling. The exact same official 2.0.1 package/hash runs natively: `drift --help` exits successfully, and interactive startup reaches a CLI-specific Google sign-in link. The process uses an isolated non-root user and a separate empty project, without host model credentials. The user completed sign-in, and an authenticated native session generated and ran the small MuJoCo scene described below. The parent agent owns the pod, browser and experiment, including saving results and removing the resource. No identifiers, authentication links or account details are recorded here.

**Observed CLI discrepancy:** `--no-resume` appears in native help, but interactive startup warns that it is unknown and skips it. Treat the effective behavior as unverified rather than assuming the flag works.

This research pass started no local servers, containers, simulations, builds or tests. The earlier emulation failure therefore does not imply a native CLI startup failure.


## Observed small native trial

**Observed pass, limited scope:** Drift generated MJCF for a generic four-wheel robot, three named fixed obstacles, a target marker and an overview camera. The controller applies wheel velocity commands and advances MuJoCo for 3 simulated seconds. The saved baseline contains 76 pose samples (first timestamp 0.002 s, last 3.0 s), 11,939 per-step contact records and an overview PNG. The independent audit measures approximately **0.445853 m** planar displacement. Contact records repeatedly sample persistent contacts; they are not 11,939 distinct collisions. This is a physical drive test, not navigation planning, manipulation, or evaluation of a supported research-robot preset.

**Observed pass, narrow follow-up:** the requested change moved only `obstacle_a` from x = 0.4 m to x = 0.9 m. Our saved-file diff contains exactly that one-line change; the controller SHA-256 is identical before and after. The rerun again contains 76 pose samples over 3 simulated seconds and the same measured displacement. The audit also confirms earlier outputs were preserved. This demonstrates one correct incremental scene edit; it does not establish correctness for arbitrary edits or general reproducibility across machines.

**Observed recovery with a misleading intermediate signal:** according to the parent agent's live transcript inspection, the initial validation command printed “Validated” alongside `No module named mujoco` because it missed the installed virtual environment. Drift subsequently used the correct environment and completed the real run. A positive status word alone was therefore insufficient evidence of validation.

**Critical verification boundary:** the parent agent observed Drift's own `git diff` attempt fail because the directory was not a Git repository, followed by an echoed, manually constructed diff. That printed diff is not an independent verifier. The correctness claim above rests on our comparison of the saved baseline and edited files, controller hashes and retained outputs. A convincing narrative can accompany a correct edit without being valid evidence of it.

**Measurement limits:** the saved controller reads cached body transforms after `mj_step`, so pose/contact state can lag the logged integration timestamp by one 2 ms timestep. Its floor plane has visually bounded extents but physically infinite plane collision; the apparent 6 × 4 m floor is not a bounded enclosure. An empty numerical-warning list would establish only the checks that controller actually performs, not complete stability, collision safety or task success.

### Local evidence inventory

These paths are relative to the project root under the ignored `.robot-worlds/drift-trial/` directory. They are local trial artifacts, not promised public-download links. Images are preserved without embedding links that would break in a public checkout.

| Artifact | Relative path within trial directory | SHA-256 where recorded |
| --- | --- | --- |
| Baseline scene | `baseline/scene.xml` | `efd88383dcb05cea0d27df43fedd0bb023d977da6fbf7f1508fe90bb52050fbc` |
| Edited scene | `edited/scene.xml` | `a4b76253206d5a23e90214e9bcda7def413c0b9cdd412fbf507a2edb280dc545` |
| Unchanged controller | `baseline/run_controller.py`, `edited/run_controller.py` | `b032f9b28775af9f6fc7712ea18f46ccdd5640d28abf5bfaae82931182c7f46e` |
| Baseline overview | `baseline/renders/overview_20261030_182725.png` | `ac2c9949535464ffac5eeb2fac60c4b7d5dd55854f1719aeb0d8c198a62975db` |
| Edited overview | `edited/renders/overview_20261030_183147.png` | `2c244dbcdbb04b6f4e7e42c6d90555877c9b172ddf7c57212c77f718cb7640fc` |
| Independent audit and exact diff | `independent-audit.json`, `scene.diff` | Audit includes pose-log hashes and explicit limitations. |
| Pose/contact records | `baseline/logs/`, `edited/logs/` | CSV headers excluded from counts above. |

**Next trial, result pending:** the parent agent requested a 20 × 12 m factory with six workcells, racks, inspection, tools and shipping areas, a 1.5 m aisle, detailed asset composition, and a 2-second settling run without robots. No factory-generation, aisle-clearance or settling success is claimed here yet. Robot-library selection, reference-image fidelity, resource-guard recovery and independent rubric behavior have not been exercised by the small drive/edit trial.

## What the official sources establish

| Topic | Documented behavior | What remains to verify |
| --- | --- | --- |
| Incremental scene edits | Drift describes edits to existing MJCF, validation and reruns. It also describes composing asset-based worlds and backing up an overwritten original scene. [MuJoCo guide](https://docs.godrift.ai/guides/mujoco-scene) | One obstacle-only edit passed our independent saved-file comparison; broader edits and task preservation remain untested. |
| Robot selection | `/robots` browses category → maker → model, then available format and preview/workspace action. **Preview pauses physics**; staging sets an active robot but does not launch it. [Robot Library](https://docs.godrift.ai/guides/robot-library.md) | Actual model provenance, controller capability and stability when physics advances. |
| Reference images | An absolute image path precedes the prompt. The guide lists PNG, JPEG, GIF and WebP; image context lasts for the session. [Image guide](https://docs.godrift.ai/guides/attaching-images.md) | Measured layout accuracy, uncertainty about hidden geometry, and which inferred properties are disclosed. |
| Persistent context | `/init` produces `DRIFT.md`; project conventions are re-read, and existing agent-context files can also apply. [Project context](https://docs.godrift.ai/guides/project-context.md) | Whether subsequent edits preserve a particular robot, coordinate convention and naming constraint. |
| Diagnostics | The docs describe inspecting simulator output and repairing divergence, contact, actuator-axis and launch issues. [Troubleshooting](https://docs.godrift.ai/reference/troubleshooting) | Whether diagnoses cite actual observations and repairs resolve a controlled fault. |
| Resource guard | Documented behavior includes pre-launch warnings, one on-screen simulator, monitoring, and stopping the simulator then retrying headlessly. The guard is explicitly **fail-open** when hardware cannot be read. [System requirements](https://docs.godrift.ai/reference/system-requirements) | Second-viewer handling and process cleanup are cheaply testable; overload recovery is not established without an actual or supported injected overload. |
| Automation/control | Interactive execution can pause with ESC. The reference says `drift -c` automatically approves its steps; it is not a review-by-default mode. [Commands](https://docs.godrift.ai/reference/commands) | Whether execution logs preserve enough detail to reproduce a scene and understand changes. |

Two useful capability boundaries are stated by Drift itself: its Aloha walkthrough uses approximate joint interpolation, not a learned grasp policy or collision-aware dual-arm planner; its H1 walkthrough is a PD standing setup, not a walking controller. Do not score those demonstrations as evidence of general manipulation or locomotion. [Aloha scope](https://docs.godrift.ai/showcase/dual-arm-aloha), [H1 scope](https://docs.godrift.ai/showcase/humanoid-h1).

The reviewed documentation demonstrates claims about scene checks, control scripts and logs. It does **not establish** a separately versioned, executable task-rubric system with persistent identities, temporal ordering, holding durations and adversarial counterexamples. That is a limitation of the evidence reviewed, not proof that the product has no additional evaluator features. The test below asks for the artifacts explicitly. [MuJoCo workflow](https://docs.godrift.ai/guides/mujoco-scene), [command surface](https://docs.godrift.ai/reference/commands).

A caution when adapting diagnostic recipes: MuJoCo can infer mass/inertia from geometry; an explicit inertial element is not universally mandatory. Also, `condim=1` still represents frictionless **contact**, rather than disabling contact. Treat a tool's diagnostic narrative as a hypothesis to test against simulator state. [MuJoCo inertia inference](https://mujoco.readthedocs.io/en/stable/XMLreference.html#compiler-inertiafromgeom), [contact dimensions](https://mujoco.readthedocs.io/en/stable/XMLreference.html#body-geom-condim).

## Trial protocol and remaining probes

This is the original test design and acceptance criteria. The observations above record the completed baseline and obstacle-edit subset; other steps remain proposed, not passed. Use one disposable project and short headless MuJoCo runs. Record the exact software versions, model/source revisions, commands, timestamps and paid-resource lifetime. A 45–60 minute engineering timebox leaves ample room beneath the approved ceiling at the quoted compute rate; include actual disk and any separately disclosed service charges. Do not buy a subscription or upgrade to continue a failed test. Save useful evidence and remove the pod at the end.

### 1. Reach a real CLI session and preserve a baseline

Follow the documented first-launch flow: launch Drift in the disposable project, have the user complete the CLI-specific Google URL, and confirm the active account with `/profile`. Browser dashboard sign-in alone is not evidence that the CLI authenticated. [Quickstart](https://docs.godrift.ai/getting-started/quickstart).

Record `/version`, the MuJoCo version, CPU/GPU details and initial resource availability. Use interactive mode first. Put trial constraints in project context: MuJoCo only; no ROS/Isaac installation; short headless runs; named entities; no teleportation or welded grasp shortcuts; preserve unrelated files; disclose unsupported requests. Do not place credentials in that file.

**Original test prompt:** “Create a 6 m by 4 m MuJoCo scene with a simple wheeled robot, three separately named stationary obstacles, and a named target floor region. Advance physics for 3 simulated seconds under a modest drive command. Save MJCF, controller, timestamped chassis pose/contact logs, and one overview image. Report actual displacement and any stability warnings.”

Record time to first plan, first loadable scene, first image and first stepped physics result separately. Inspect compiled bodies/actuators and the logged trajectory independently. A still image or an exit code alone does not pass. If setup fails, allow one diagnosis/repair round and preserve the failure evidence.

### 2. Test narrow edits without rebuilding the meaning of the scene

Save a baseline file inventory and checksums. Apply these changes separately, saving a new artifact snapshot and diff after each:

1. Change only the named robot wheel-contact friction to a specified value; rerun the same duration and controls.
2. Move one named obstacle by a specified displacement while preserving the other obstacle poses, robot model, actuator configuration and task.
3. Add a named camera and log its frames without altering the physical experiment.

For each edit, verify requested changes in the compiled model, compare unrelated entity names/poses and inspect the actual trace. Record unexplained regeneration, disappearing objects, renamed targets, invented measurements and newly introduced failures. A successful run after deleting the difficult object is a failed edit test.

### 3. Separate robot discovery from physical capability

Use `/robots`, inspect the offered formats, and stage one small supported manipulator or wheeled model. Record asset license/source revision, files copied, and the declared action/sensor interface. Inspect preview as a usability feature only. Then run full physics for 3–5 seconds and attempt a simple bounded motion with joint/base state logs.

If choosing a humanoid, classify static standing separately from walking. If choosing an arm, classify free-space reaching separately from grasping, carrying and releasing. Preserve any missing-controller or unsupported-policy limitation instead of silently substituting another robot. No claim about popularity follows from library size.

### 4. Use a reference with known ground truth

Provide a small, self-authored PNG floorplan: a 6 × 4 m room, one clearly marked doorway, two labeled tables, a cabinet, a clear aisle and one colored target object. Attach it using the documented path-prefix syntax. Ask Drift to state its inferred inventory and dimensions before constructing the scene, and to mark any unobservable mass/material values as assumptions.

Check relative placement, doorway clearance, object count, labeled dimensions and robot start against the reference. Request a single later change while preserving the remaining layout. Ask a follow-up about the image without reattaching, then explicitly distinguish session memory from information it persisted in files. Do not grade photorealism as proof of collision, inertia or reachability fidelity.

### 5. Probe diagnostics and resource handling without creating an overload

Make a copy of the baseline and introduce one controlled error, such as an invalid include filename. Ask Drift to diagnose it from actual logs, repair only that error, and rerun the original check. Confirm the missing reference and the corrected artifact rather than accepting a generic explanation.

If a display is available, open one lightweight viewer and request a second; record whether Drift asks to replace/cancel instead of accumulating viewers. Inspect its process listing before and after exit. Keep headless runs short. **Do not deliberately exhaust VRAM/RAM or disable the guard.** Record critical-load recovery as “not exercised” unless a safe, supported fault-injection mechanism exists. Hardware telemetry unavailable should remain a visible uncertainty, even though Drift documents fail-open behavior.

### 6. Ask for an evaluator artifact and test its boundaries

Use a task that is actually supported by the chosen robot/controller. For manipulation, request moving a specific named object onto a physical support, released and stable for one second, after visiting a prerequisite station. If no compatible manipulation controller exists, stop that part of the test rather than using pose writes; a simpler navigation-only task can still test identity/order handling.

Ask for a local executable evaluator that takes a saved state/event trace, with explicit entity bindings, ordering/duration rules, missing-evidence behavior and per-check witness timestamps. Freeze its definition before collecting comparison traces. Evaluate independently prepared cases:

| Case | Expected evaluation property |
| --- | --- |
| No actions | Does not earn achievement credit. |
| Correct movement, wrong object | Fails identity binding. |
| Goal reached before prerequisite | Does not acquire retroactive ordering credit. |
| Brief goal contact followed by slip | Fails the required hold duration/final condition. |
| Goal attained then undone | Fails final-state requirements while preserving historical evidence. |
| Needed sensor/state omitted | Reports insufficient evidence, not an invented pass. |
| Plausible successful trace | Passes with inspectable witnesses if all conditions actually hold. |

Do not accept “the agent says it succeeded,” a visually plausible video, or a task-specific script that sets its own success flag as equivalent to an independent evaluator. If Drift supplies only validation prose or a controller log, record exactly that and leave this capability unproven.

## What we should adapt from the actual results

**Implement narrow, entity-specific edits with saved before/after versions.** The obstacle edit preserved the controller and prior outputs in this observed case. Our product should expose the requested entity/property change and a computed artifact diff, then retain the rerun evidence. Do not infer that all future edits will preserve invariants without checking them.

**Make verification independent of generated explanations.** Compute hashes and diffs from persisted files in application code. Accept a validation stage only after the expected command succeeds and its structured outputs exist. Display execution environment, command outcome and evidence separately from the agent's text. The misleading validation word and hand-constructed diff directly motivate this requirement.

**Ship a useful evidence bundle for every run.** Scene, controller, timestamped pose/contact data, image and run metadata made this tiny trial inspectable. Add synchronized state semantics and stable entity identities so replay timestamps and measurements refer to the same physical state; label contact samples correctly. An image should complement the state trace, not replace it.

**Keep capability states explicit.** This trial earned “generic robot physically drove” and “one narrow edit preserved unrelated artifacts.” It did not earn “research robot supported,” “factory task solved” or “rubric validated.” Show preview, compile, stepped physics, task checks and missing evidence separately.

**Still candidates, not hands-on findings:** persistent project conventions, relational factory composition, reference-image inventories, robot-library staging, and one-viewer/resource cleanup. The official docs motivate these features; additional trials must justify stronger conclusions. Preserve inferred-property disclosures and verify physical clearance independently.

**Keep our evaluation contract:** a frozen world/task/rubric version, actor/evaluator separation, replayable state evidence and counterexample-tested grading. Neither the reviewed documentation nor this drive/edit trial establishes equivalent rubric behavior in Drift.

For each trial phase, report one of: observed pass, observed failure, not exercised, or documented only. Archive prompts/transcripts, source files and diffs, asset provenance, sensor traces, evaluator definition/results, renderings, resource logs and final cleanup confirmation. Exclude account/session tokens and credential directories. Update this note with actual remote results only after they occur.
