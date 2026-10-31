# Alpha acceptance evidence

The active release target is the customer path from installation through generated
environment, policy execution, evidence inspection and portable export. Bowl and
microwave manipulation is the functional scenario; a detailed generated warehouse
is the visual scenario. This checklist does not replace missing live evidence with
mocked tests.

| Gate | Current evidence | Remaining acceptance work |
| --- | --- | --- |
| SDK action loop | `test_household_sdk_flow.py` adds original-asset generation with a mocked model, onboard RGB-D, depth queries, wheel motion, gripper commands, feedback, replay, export/reload and regrading through in-process and real loopback HTTP policies. It also covers HTTP timeout/disconnection, malformed actions and cancellation. | Native execution remains unverified; CI cancellation requested at the user's direction. |
| Physical manipulation and grading | Existing native tests check bowl concavity and pre-placed support inside the original microwave. | Actual grasp, transport, door interaction, insertion and release; task-specific negative cases. The new action-loop test intentionally does not attempt insertion. |
| Live generation and evaluation | Earlier assisted factory episodes are development evidence, not current sensor-only acceptance. | Complete a bounded paid generation-to-report run with the current sensor contract; preserve actual outcome and spending. |
| Warehouse assets and appearance | Four pinned source assets now register in a portable library: rack, pallet, sealed carton and barrier. Original visual data and explicit collision approximations pass geometric checks for shelves, fork channels and rail openings. Repeated viewer assets share geometry, materials and maps with lightweight lifecycle coverage. See `simready-selection.md` and `source-materials.md`. | Verify native contacts/materials, handle the conveyor's articulation, address renderer differences, generate two layouts and inspect actual screenshots/performance, including LOD needs. |
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
