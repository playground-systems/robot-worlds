# Rubric review: evidence and counterexamples

Reviewed the implementation against the MVP goal on October 30, 2026. The grader uses simulator measurements and fixed local rules. Model-selected actions, textual completion claims and process events alone cannot award success. The actor receives the task and declared sensors, not rubric thresholds or privileged snapshots.

## Findings repaired during review

| Counterexample | Required behavior | Change |
|---|---|---|
| A final predicate is visibly false, but an earlier measurement is missing | Fail the final outcome and retain the missing-data record | Definite final counterevidence takes precedence over unrelated unknown samples. |
| An `always` constraint is violated, then recovers; another sample is missing | Report the observed violation as a failure | Known violations take precedence; a decisive failure records its time and measured value. |
| Trace metadata reports a smaller workpiece than the frozen world | Never earn containment credit by changing rigid geometry | Extents come from the immutable specification; inconsistent supplied extents are unscorable. |
| A moving box or cylinder has no measured orientation | Do not assume it stayed axis-aligned | Predicates needing rigid extents return unknown when orientation is missing. |
| A workpiece touches a fixture's declared top component | Apply the same assembly interpretation used in validation | Process loading accepts contacts with fixture descendants. |
| Loading breaks and recovers between 20 Hz trace samples | Restart the process duration | Process prerequisites now run at every physics step with fresh contacts, separately from trace recording. |
| A failed safety check ends in a safe state | Link the failure to the violating state | Replay sampling retains decisive failure timestamps; report selection prefers failure evidence, then status-appropriate missing or completion evidence. |

`tests/test_eval_adversarial.py` also checks initial no-op achievements, explicit maintenance, required release, valid alternative branches, nonfinite measurements, timer reset and completed historical stages followed by missing data. Process-monitor unit tests inject measured states to isolate identity and timing. They are not evidence of successful manipulation or real contact dynamics; physical integration tests remain separate.

## Semantics that must stay visible

- `achieve` requires an observed false-to-true transition. `maintain` deliberately permits an initially true constraint. The generator must not use maintenance to weaken a requested action.
- `after` requires a new true interval strictly after prerequisite completion. Independent stages remain unordered. Alternative continuations can use separate criteria, each bound to its valid prerequisite branch.
- `final` requires the qualifying interval to reach the last recorded sample. `eventually` preserves a completed stage even if it later regresses. A separate required final criterion can forbid that regression. `always` applies throughout the recorded run.
- Durations establish sampled persistence. Maximum trace gap is reported. They do not establish every intervening contact or a velocity-based settling bound. Process prerequisites have the finer physics-step resolution.
- `holding` means bilateral finger contact. `on_top` means contact plus conservative geometric support. Neither alone proves a completed pick-and-place: lift, transport, release and stable final placement need the corresponding explicit criteria.
- Machine processing is a disclosed timed state abstraction with physical loading and workpiece identity. The grader does not infer cutting, weld quality or material strength from appearance.
- Missing measurements remain unknown unless other evidence already proves failure or a valid existential witness already proves a completed stage. Unsupported requirements prevent a complete task pass.

## Remaining limits

The generator creates the task interpretation, world and rubric together. Schema checks and no-op probes do not independently prove that every natural-language requirement was preserved. A maintenance label, overly broad region or omitted process stage can still be a semantic generation error. World validation records this limitation; a ready version is a compiled, measured reset state, not a certificate of task completeness or solvability.

Trace data are trusted recorded simulator evidence, not a tamper-proof attestation. Rigid size consistency rejects one class of stale or contradictory evidence; it does not authenticate arbitrary edited traces. Regrading preserves world and physical-version provenance and must use saved measurements rather than previous verdicts.

The required distributed mission still needs actual physical rollouts, including wrong-workpiece handling, process loading loss, out-of-order processing, unstable placement and final-state regression. Unit counterexamples and attractive replay frames do not replace that evidence.

## Focused verification

```sh
python -m pytest tests/test_eval_adversarial.py tests/test_rubric.py -q
python -m pytest tests/test_generator.py tests/test_sdk.py tests/test_replay.py -q
```

The report's embedded JavaScript was syntax-checked, and its evidence-time selection was exercised for failed, unscorable, passed and zero-time witnesses. Browser and physical rollout checks are recorded separately by the integration owner.
