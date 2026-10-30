# Ten-scenario generation trial, 2026-10-30

Four of ten single-attempt generations passed the predeclared structural contract and native reset checks. No policy was evaluated on these ten generated scenes, and no task-completion or general scene-fidelity success rate is claimed.

## Protocol

The ten prompts in `representative_tasks.json` supplied explicit object IDs, ordered rubric requirements, station distances, process conditions and final stability requirements. This tests generation against detailed engineering contracts, not unconstrained natural-language understanding. The contracts were fixed before the calls; failures were retained without repair or retry.

Each call used `gpt-6-astra`, low reasoning, at most 6,500 output tokens, one generation attempt, zero repairs, and a $0.50 model budget. Scenes requested 30–80 materialized objects. Generation ran from 22:33:42 to 22:40:47 UTC. `tools/validate_scenario_suite.py` made local HTTP calls using an in-memory credential; native simulation and preview rendering ran sequentially on the temporary remote worker with `MUJOCO_GL=egl`. No credential was transferred to that worker.

The suite accounted for **$3.1323275** against its $5 model cap: **$2.02124** calculated from returned usage and **$1.1110875** retained as unreconciled reservations for three transport failures. These reservations are conservative accounting, not confirmed billing. Temporary-worker charges are separate.

## Results

| Scenario | Objects | Result | Model accounting, USD |
| --- | ---: | --- | ---: |
| Factory three-station transfer | 31 | Contract and native reset passed; grasp/prose caveats below | 0.3475425 |
| Warehouse order picking | 46 | Contract and native reset passed | 0.2592350 |
| Apartment delivery | — | Schema rejected seven parented movable components without joints | 0.3063850 |
| Laboratory sample transfer | 34 | Contract passed; static bounds check rejected explicit floor below bounds; native simulation not run | 0.2758725 |
| Clinic supply courier, G1 | 34 | Contract and native reset passed | 0.2627975 |
| Office service route | — | No usable specification; response error after all 6,500 output tokens were consumed | 0.3365225 |
| Retail restocking | 36 | Contract and native reset passed | 0.2328850 |
| Hotel rigid-tote delivery | — | Transport failure; no response ID/usage; unvalidated | 0.3700750 reserved |
| Recycling identity sort | — | Transport failure; no response ID/usage; unvalidated | 0.3699250 reserved |
| Workshop articulated access | — | Transport failure; no response ID/usage; unvalidated | 0.3710875 reserved |

The original runner retained failure classes but omitted safe transport details, so the three HTTP causes cannot be determined from this evidence. It now retains the client's sanitized failure description for future trials. No retry was made to improve the reported outcome. The office result is consistent with the output cap; its incomplete provider body was not retained by the client. These bounded-attempt failures do not measure the product's default repair workflow or a larger-token protocol.

The four passing immutable world IDs are:

- Factory: `cb22e03504d88209f3896b8b227e362bccf42f1ab5206ab2539985343af790ed`
- Warehouse: `6c49f2c89eb49c0a68e7485b65cd9bd76e151e662a39409f7e015c35dd811c19`
- Clinic: `d80760e69726cc7a7754a182898c9448441266b4f5f1c4a0d69ebacfa6e17f64`
- Retail: `f28eaf47a158e020abd7faed2d298dd55f69dc2ac1e837a79893b25b56d8024b`

## Limits that the passing checks do not settle

The factory's box workpiece is 0.18 × 0.12 × 0.12 m: every principal dimension exceeds the TIAGo tool's nominal 0.09 m full finger opening. Its whole-box parallel grasp is not established. The same screen flags warehouse and retail subjects, so all three passing TIAGo scenes have this unresolved grasp issue; it also flags the statically rejected laboratory subject. A separate descriptor warning now flags such boxes; absence of that warning does not certify reach, collision-free approach, friction, grasp stability or task solvability. G1/Dex1 grasp aperture feasibility is explicitly unassessed by this screen. This warning was added after reviewing the first results and did not change the frozen contracts or their pass/fail outcomes.

The factory's generated assumptions also claim a 0.02 s holding/carry sampling interval; the runtime manifest records 0.05 s. The generated text is preserved as incorrect. Measured grading follows the actual runtime trace. A passing reset and a preview image cannot verify all requested room semantics, machine appearance, accessibility or a complete manipulation mission.

## Evaluator and repair-guard evidence

The focused pure suite passed **89 tests in 0.45 s**, covering all ten synthetic positive traces, wrong-object/order/unstable-placement counterexamples, process identity/interruption, and scene-edit task-binding protections. These are evaluator tests, not generated-world policy rollouts.

`test_process_release_physics.py` additionally passed remotely in **37.66 s**. A real TIAGo gripper held a block in physical support contact for 35 simulated seconds; the 30-second release-required process did not complete. After the fingers opened and the arm retracted, supported release allowed completion. The test assigns no object pose and creates no weld. Its first setup lost support during closing and correctly failed the contact precondition; a measured servo-driven lowering corrected the fixture before the successful run. Both logs are retained.

Repair guards preserve bound region geometry, subject identity/shape, destination and fixture poses, ancestor transforms, and the robot start unless task changes are explicitly permitted for refinement. Direct initial subject-placement repair remains possible and does not prove equal difficulty. The ten-scenario protocol does not test autonomous repair success.

## Evidence locations and reruns

Local private artifacts are under `.robot-worlds/release-validation/scenarios`; the remote copy is under `artifacts/release/scenarios`. Per-scenario files include the frozen contract, budgets and usage, structured raw response when parsing succeeded, typed specification when available, native validation record, and immutable world bundle with manifest and preview when created. Manifests preserve runtime versions and implementation/model fingerprints. `summary.json` contains artifact hashes. No private artifacts are required by the test suite or distributed package.

Use `tools/validate_scenario_suite.py generate --ids ... --output NEW_DIRECTORY`, then transfer only public artifacts to a native worker and invoke its `validate` phase. Generation refuses to overwrite an existing charged scenario. Keep the contract file fixed for a comparison and report any changed model, budget or repair protocol separately.
