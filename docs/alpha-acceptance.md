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
| Warehouse assets and appearance | Pinned five NVIDIA/CodePawl models and per-asset license evidence. SDK acquisition/cache has 25 checks; decoding has 16 Node and 10 Python checks. Actual rack acquisition, offline reuse and bounded decompression verified. See `simready-selection.md`. | Convert decoded visuals, add usable collision geometry, physically validate the subset, generate two layouts and inspect actual screenshots/performance. |
| Fresh installation and recovery | Existing hosted clean-install jobs and offline diagnostics. | Run documented release-candidate journey and reconcile failures/recovery evidence. |
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
