# Alpha acceptance evidence

The active release target is the customer path from installation through generated
environment, policy execution, evidence inspection and portable export. Bowl and
microwave manipulation is the functional scenario; a detailed generated warehouse
is the visual scenario. This checklist does not replace missing live evidence with
mocked tests.

| Gate | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| SDK action loop | `test_household_sdk_flow.py` adds original-asset generation with a mocked model, onboard RGB-D, depth queries, wheel motion, gripper commands, feedback, replay, export/reload and regrading through in-process and real loopback HTTP policies. It also covers HTTP timeout/disconnection, malformed actions and cancellation. | Native execution remains unverified; CI cancellation requested at the user's direction. |
| Physical manipulation and grading | Existing native tests check bowl concavity and pre-placed support inside the original microwave. Synthetic original-asset traces reject no-op, wrong object, partial insertion, continued holding and interrupted support. Fixed/visual subjects are rejected for gripper predicates. | Actual grasp, transport, door interaction, insertion and release; native task-specific negative cases. The new action-loop test intentionally does not attempt insertion. |
| Live generation and evaluation | Earlier assisted factory episodes are development evidence, not current sensor-only acceptance. | Complete a bounded paid generation-to-report run with the current sensor contract; preserve actual outcome and spending. |
| Warehouse assets and appearance | Five pinned source assets now register in portable libraries: rack, pallet, sealed carton, barrier and passive conveyor. The conveyor preserves 29 hinges; its original and quarter-turned placements pass static checks. Original visual data and explicit collision approximations pass geometric checks for shelves, fork channels and rail openings. Repeated viewer assets share geometry, materials and maps with lightweight lifecycle coverage. See `simready-selection.md` and `source-materials.md`. | Verify native contacts/materials, verify conveyor bearing/contact behavior, address renderer differences, generate two layouts and inspect actual screenshots/performance, including LOD needs. |
| Fresh installation and recovery | Existing hosted clean-install jobs and offline diagnostics. Warehouse preparation now has an SDK/CLI installer, subset selection, progress, bounded imports and atomic library publication; failure/cancellation/worker-handoff checks pass. The actual installer ran in 2.31 s with cached sources, with a 0.2333 s warm repeat. | Run the built release-candidate journey, including cold setup, native execution and recovery evidence. |
| UI and release | Existing workbench, reports, export, attribution and draft PR. | Current browser acceptance, reference comparison, versioned artifacts and reconciled walkthrough. |

The action-loop probe uses only public sensor observations. Its deliberately
unsuccessful bowl placement must remain a valid failed task, while transport or
policy errors must be classified separately. Privileged trace assertions occur
only in the test evaluator to verify that the robot physically moved.

As of October 31, the user requested no further CI runs. Keep verification to
focused lightweight local checks; do not move heavy native tests onto the Mac.
Native acceptance remains pending. Do not trigger workflows through routine
pushes while this instruction is in effect. Paid model requests are reserved for
bounded full acceptance runs, not individual development probes. No new paid
compute, merge or publication is authorized.

Prepared assets and mesh assemblies now support all four quarter-turn placements
without rewriting source files. Focused checks cover original microwave parts,
hinge/cavity frames, mocked library generation and typed edits, portable worlds,
off-center native XML geometry and browser transforms. These are analytical and
serialization checks; rotated native contact/render acceptance remains open.

Prepared repetition now verifies a source once per group, expands independent
copies with stable IDs, and rejects budget/ID failures before publishing the
group. Mocked planning with actual carton assets produced two 960-component
arrangements in under 0.3 s each. This is static expansion evidence, not the
required live generated-warehouse visual acceptance.

The layout viewer now batches compatible opaque source meshes by spatial cell.
CPU checks cover picking, transforms, material/visibility separation, cleanup and
failure recovery. Actual carton geometry yielded 28 batches from 840 visual mesh
objects in a harness without rendering or real image decoding. Frame rate, GPU
memory, screenshots and visual acceptance remain unverified.

Focused grading checks use fabricated traces with the original bowl and microwave
bounds. They verify object identity, full-rim containment, sampled release/support
duration and missing-measurement handling. They are evaluator regressions, not
robot manipulation evidence. Holding/released criteria and process prerequisites
must reference a movable workpiece root; fixed child meshes cannot prove release.

The generation catalog now supplies local component geometry and rigid-assembly
bounds for named references, while the rack importer names all six shelf panels.
Thirty-one focused catalog/library checks passed, including mocked generation
with a quarter-turned shelf target. Actual bowl/microwave/rack/conveyor metadata
occupies 39,511 characters, below the existing 200,000-character planning cap.
This does not establish a live generated scene or physical shelf placement.

Arm approach presets now use the same complete base-link rotation as depth and
position targets. Previously TIAGo used fixed world directions, and G1 only
partially accounted for base rotation. Measured arm feedback checks orientation
as well as position before reporting `reached`. Focused synthetic checks cover
both hands, rotated/tilted bases, feedback and the public action boundary; no
robot rollout was executed. This runtime change requires fresh validation of
release-candidate worlds; historical recordings remain untouched.

Process loading now recognizes contacts on fixed collision pieces of the intended
workpiece, rather than requiring the root piece itself to touch the fixture.
Seventeen focused evaluator/monitor checks pass, including original bowl geometry
with synthetic child-piece contact and root-level holding. Decoy and articulated
child contacts cannot substitute for the rigid workpiece. This fixes the existing
optional process abstraction; it adds no heating requirement to the bowl task and
does not constitute physical placement evidence.

## Proposed one-off hosted household check — not run

`.github/workflows/household-acceptance.yml` selects 33 tests from four files:
`test_household_sdk_flow.py`, `test_sourced_household_physics.py`,
`test_household_grading.py` and `test_arm_orientation.py`. It uses one Ubuntu
runner, a 15-minute job limit, single-threaded numeric libraries, mocked models
and an empty model API key. It performs no package build, catalog conversion,
live model request or publication. Reports, traces, JUnit and runtime/checkout
identity are retained for seven days. This is a subset check, not full release
acceptance or successful bowl manipulation.

The workflow has only explicit label/manual triggers. Normal commits, PR updates
and label removal do not trigger it. The label `run-household-acceptance` also
suppresses the existing full-suite, packaging and conversion jobs while present.
GitHub documents label activity types in its [workflow trigger reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request).

**Keep CI off until the user authorizes this run.** This proposal is needed because
the goal forbids heavy native work on the Mac and new paid compute, while the
later user instruction disabled CI. Preparing this workflow is not authorization
to trigger it.

For this unmerged draft, use the label event after authorization rather than
assuming a new manual-dispatch workflow is already registered on `main`:

1. Apply the dedicated label while the current head still has `[skip ci]`.
2. Push one empty commit without a skip marker. The existing jobs stay suppressed
   by the label; the household workflow does not listen for commit updates.
3. Remove and reapply that label once to request the single household job.
4. Record the resulting run ID and remove the label. Keep subsequent commits
   marked `[skip ci]`; any additional native attempt requires a concrete diagnosed
   change and authorization covering another run.

`tools/check_household_evidence.py` audits nine required native cases and rejects
missing, skipped, duplicate or failing evidence. It always reports full release
acceptance as false because this subset cannot establish successful manipulation,
live generation, warehouse screenshots or clean installation. Twelve lightweight
auditor checks pass. Collection found all 33 selected tests without executing
the native cases. Workflow YAML parses; actual hosted execution is unverified.
