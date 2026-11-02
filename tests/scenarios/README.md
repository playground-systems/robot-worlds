# Internal task-integrity scenarios

`representative_tasks.json` contains ten generation prompts and predeclared acceptance contracts. These are engineering inputs, not bundled worlds, onboarding policies, or measured benchmark results. The contracts are written independently of the generated rubric.

| Scenario | Robot | Required distinction |
| --- | --- | --- |
| Factory three-station transfer | TIAGo++ | Same workpiece, separated processing station, physically loaded two-second surrogate |
| Warehouse order picking | TIAGo++ | Requested carton, distractor rejection, separate packing destination |
| Apartment delivery | TIAGo++ | Kitchen-to-living-room transfer with physical support and final release |
| Laboratory sample transfer | TIAGo++ | Same sealed container through a three-second inspection surrogate |
| Clinic supply courier | G1 | Nonmedical rigid supply box across connected corridors |
| Office service route | TIAGo++ | Mailroom-to-meeting-room delivery and ordered navigation stages |
| Retail restocking | TIAGo++ | Correct product identity and supported display placement |
| Hotel rigid-tote delivery | G1 | Rigid tote through a hallway; no claimed cloth simulation |
| Recycling identity sort | TIAGo++ | Red subject versus blue distractor at a distinct output bay |
| Workshop articulated access | TIAGo++ | Measured hinge opening before pickup, then stable delivery |

Every scenario specifies required entity IDs, a minimum environment extent, separate source/destination approaches, minimum transfer distance, a bounded delivery region, and mandatory rubric predicates. Ordered stages, stability duration, tolerance ceilings, measured support contact and final release cannot be traded for an easier alternative clause. Each transfer requires measured net planar displacement during a continuous sampled holding interval; displacement is not accumulated path length. Process scenarios additionally bind fixture, workpiece identity, released loading condition, duration, output state and station separation, followed by a fresh carry after processing. Holding continuity is observed at the runtime trace sampling rate, not proven between samples.

## Independent checks

`tests/scenario_contracts.py` checks a candidate `WorldSpec` against the selected contract. It does not call the generation model or trust the candidate's prose explanation. It rejects omitted or weakened criteria, wrong object/region bindings, collapsed stations, oversized goals, shortened transfers and altered process requirements.

The pure tests in `tests/test_scenario_contracts.py` construct semantic fixtures and privileged synthetic traces to test the grader. For all ten contracts they check a positive witness and wrong-object, wrong-order and unstable-final-placement counterexamples. Process-credit tests reject substituting a distractor's completion state. `tests/test_eval_adversarial.py` separately exercises the actual process monitor with injected measured states, including an interrupted load briefly replaced by a decoy. These tests do not step MuJoCo or demonstrate a policy solving a physical environment.

```bash
python -m pytest tests/test_scenario_contracts.py tests/test_task_integrity.py \
  tests/test_eval_adversarial.py -q
```

For a generated candidate, load the independent oracle from the repository's test directory and select the frozen scenario by ID:

```python
# Run with PYTHONPATH=tests from the repository root.
from scenario_contracts import acceptance_errors, scenarios

contract = next(s for s in scenarios() if s["id"] == "factory_three_station")
errors = acceptance_errors(world.spec, contract)
```

Passing this oracle establishes only the listed descriptor and rubric constraints. Each scenario includes separate physical review checks: native reset/settling, real fixture and access geometry, sensor-only policy execution, replay/contact evidence, and visual inspection of the requested setting. A room label does not prove that a room was built, and an optimistic connectivity result does not prove task solvability.

## Evidence status

The focused pure suite passed 89 tests. A bounded ten-attempt generation trial produced four candidates that passed structural contracts and remote native reset checks; the remaining six were unsuccessful or unvalidated. No policies were evaluated on these ten generated scenes. See [the dated trial report](validation-2026-10-30.md) for failures, accounting, immutable world IDs, grasp/prose limitations and the separate native released-process regression. These results do not establish task solvability or high-fidelity scene quality.

Keep the contracts fixed when comparing versions. A deliberate task change needs a new contract version; changing a target after seeing a failed rollout is not a repair of the same evaluation.
