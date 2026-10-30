# Drift hands-on review

Research date: 2026-10-30. This note distinguishes **documented** product behavior, **observed** behavior in our session, and **proposed tests**. An authenticated native trial now demonstrates a small physical wheel-drive scene and one independently verified narrow edit. Factory composition exposed physical and layout failures despite a reported pass; one targeted repair round passed an independent follow-up audit.

## Evidence so far

**Observed:** the official 2.0.1 amd64 package downloaded and extracted successfully. Its CLI stayed silent through bounded help/startup attempts in this Mac's existing Docker x86 emulation; no CLI authentication URL or simulation was reached. The container was removed. Exact environment, package hash, attempts and limitations are in [the runtime check](drift-runtime-check.md).

**Observed remotely:** the existing RTX 4000 was unavailable. A temporary RTX 4090 started at 18:17:54 UTC on 2026-10-30, at $0.89/hour compute plus $0.007/hour disk, within the approved $10 total ceiling. The exact same official 2.0.1 package/hash runs natively: `drift --help` exits successfully, and interactive startup reaches a CLI-specific Google sign-in link. The process uses an isolated non-root user and a separate empty project, without host model credentials. The user completed sign-in, and an authenticated native session generated and ran the small MuJoCo scene described below. The trial includes saving results and removing the temporary resource. No identifiers, authentication links or account details are recorded here. After backups, the temporary pod was stopped and removed at approximately 19:16 UTC; the provider returned 404 and the console no longer listed it. Compute plus disk was less than $0.88 by a conservative elapsed-time calculation, not a final invoice. Existing pods were unchanged.

**Observed CLI discrepancy:** `--no-resume` appears in native help, but interactive startup warns that it is unknown and skips it. Treat the effective behavior as unverified rather than assuming the flag works.

This research pass started no local servers, containers, simulations, builds or tests. The earlier emulation failure therefore does not imply a native CLI startup failure.


## Observed small native trial

**Observed pass, limited scope:** Drift generated MJCF for a generic four-wheel robot, three named fixed obstacles, a target marker and an overview camera. The controller applies wheel velocity commands and advances MuJoCo for 3 simulated seconds. The saved baseline contains 76 pose samples (first timestamp 0.002 s, last 3.0 s), 11,939 per-step contact records and an overview PNG. The independent audit measures approximately **0.445853 m** planar displacement. Contact records repeatedly sample persistent contacts; they are not 11,939 distinct collisions. This is a physical drive test, not navigation planning, manipulation, or evaluation of a supported research-robot preset.

**Observed pass, narrow follow-up:** the requested change moved only `obstacle_a` from x = 0.4 m to x = 0.9 m. Our saved-file diff contains exactly that one-line change; the controller SHA-256 is identical before and after. The rerun again contains 76 pose samples over 3 simulated seconds and the same measured displacement. The audit also confirms earlier outputs were preserved. This demonstrates one correct incremental scene edit; it does not establish correctness for arbitrary edits or general reproducibility across machines.

**Observed recovery with a misleading intermediate signal:** the initial validation command printed “Validated” alongside `No module named mujoco` because it missed the installed virtual environment. Drift subsequently used the correct environment and completed the real run. A positive status word alone was therefore insufficient evidence of validation.

**Critical verification boundary:** Drift's own `git diff` attempt failed because the directory was not a Git repository, and was followed by an echoed, manually constructed diff. That printed diff is not an independent verifier. The correctness claim above rests on our comparison of the saved baseline and edited files, controller hashes and retained outputs. A convincing narrative can accompany a correct edit without being valid evidence of it.

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

## Observed factory trial

**Generated and rendered, but failed physical/layout requirements:** the request specified a 20 × 12 m factory with six connected workcells, two storage rows, inspection, tools, shipping, a connected 1.5 m route, movable parts and a two-second settling check. Drift created 86 bodies, 106 geometries and four free bodies using native primitives. It also produced a checker, asset-provenance notes and a 1280 × 720 overview. An initial rendering failure was repaired by increasing the offscreen buffer. The result is a useful block layout, not Airsup-level visual fidelity.

Drift reported `OVERALL_OK: True`. Its checker looked for finite state, warnings and deep contacts at the end of settling. Our independent audit of a frozen source revision found:

| Check | Measured result |
| --- | --- |
| Initial placement | The inspection tote starts 0.42 m inside the desk. |
| Settling trajectory | The tote moves 4.91 m in two seconds, reaching 8.32 m/s. |
| Geometry type | The cart, inspection tote and shipping box default to spheres because their shared default omits `type="box"`. Three size values do not establish box geometry. |
| Shipping placement | The shipping box starts 0.20 m inside pallet cargo. |
| Connected route | A continuous row of benches leaves only 0.25 m gaps to the walls, ruling out the requested in-room 1.5 m passage. |
| Simulator warnings | None, despite the physical/layout failures. |

The audit source, frozen MJCF, compiled geometry, contacts, trajectories and initial/final images are saved under `factory-audit/9447550199c9a260/`; the audit script is in its parent directory. The snapshot hash is recorded in its README. These findings apply to that revision; a later rendering-buffer change did not repair geometry. A separate `factory-repaired/` request preserves the original and asks for corrected types, supported initial poses, a connected cross-aisle and checks throughout settling. The independent follow-up audit passed those four targeted repairs:

- The three intended movable box geoms now compile as boxes; no initial solver penetration exceeds 1 mm.
- Inspection tote and shipping box drift about 0.08 mm by the end of two seconds, with maximum excursion about 0.165 mm and peak speed 0.0196 m/s. The cart settles downward about 2 cm; its wheel-floor transient penetration peaks at 4.15 mm.
- A swept 0.75 m radius route, including turns, clears all collision AABBs and walls in both initial and settled states. Minimum clearance is exactly 0.75 m at the benches: the 1.5 m route has no extra margin.
- The original source hash is unchanged. The repaired source, copied locally and independently audited, has hash `b470d95d36de0871197cbf7c148a6de10eae8e1f418d272034ae11ef9a670167`.

This is a successful targeted repair, not complete scene validity. Fixed rack solids still overlap stored totes, so general storage/manipulation fidelity remains unresolved. The repaired render also retains a distant camera and a front wall that obscures some equipment. Evidence is saved under `factory-repaired/` and `factory-audit/b470d95d36de0871/`.

Robot-library selection, reference-image reconstruction, resource-guard overload recovery and independently executable task-rubric generation were not exercised.

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

## Trial boundaries

The trial used one disposable native Linux project, an isolated non-root account, short headless MuJoCo runs and explicit file/command review. It covered small scene generation, one narrow edit and one factory repair request. It did not test robot-library staging, image-to-scene reconstruction, learned policies, task-rubric generation or deliberate resource overload. Time spent includes manual approval pauses; it is not a latency benchmark.

Useful future probes are a known-dimension reference floorplan, a supported research robot under physical control, and an executable task grader tested against wrong-object, ordering, unstable-placement and missing-evidence counterexamples. They remain untested, not failed.

## What we should adapt from the actual results

**Implement narrow, entity-specific edits with saved before/after versions.** The obstacle edit preserved the controller and prior outputs; the factory repair preserved its original scene and addressed externally measured defects in a separate version. Our product should expose the requested entity/property change and a computed artifact diff, then retain the rerun evidence. Do not infer that all future edits will preserve invariants without checking them.

**Make verification independent of generated explanations.** Compute hashes and diffs from persisted files in application code. Accept a validation stage only after the expected command succeeds and its structured outputs exist. Display execution environment, command outcome and evidence separately from the agent's text. The misleading validation word and hand-constructed diff directly motivate this requirement.

**Ship a useful evidence bundle for every run.** Scene, controller, timestamped pose/contact data, image and run metadata made this tiny trial inspectable. Add synchronized state semantics and stable entity identities so replay timestamps and measurements refer to the same physical state; label contact samples correctly. An image should complement the state trace, not replace it.

**Keep capability states explicit.** This trial earned “generic robot physically drove” and “one narrow edit preserved unrelated artifacts.” It did not earn “research robot supported,” “factory task solved” or “rubric validated.” Show preview, compile, stepped physics, task checks and missing evidence separately.

**Validate layout and the entire settling trajectory.** Factory composition worked at the primitive-layout level, but checking only the final state hid a severe initial overlap and ejection. Clearance must be measured against physical geometry; a labeled aisle is not evidence of connectivity. Record object types from the compiled model, initial penetration, peak speed/displacement and route checks separately.

**Still candidates, not hands-on findings:** persistent project conventions, reference-image inventories, robot-library staging, and one-viewer/resource cleanup. The official docs motivate these features; additional trials must justify stronger conclusions. Preserve inferred-property disclosures and verify physical clearance independently.

**Keep our evaluation contract:** a frozen world/task/rubric version, actor/evaluator separation, replayable state evidence and counterexample-tested grading. Neither the reviewed documentation nor this drive/edit trial establishes equivalent rubric behavior in Drift.

For each trial phase, report one of: observed pass, observed failure, not exercised, or documented only. Archive prompts/transcripts, source files and diffs, asset provenance, sensor traces, evaluator definition/results, renderings, resource logs and final cleanup confirmation. Exclude account/session tokens and credential directories. Update this note with actual remote results only after they occur.
