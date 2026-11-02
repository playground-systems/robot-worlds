# Autonomous factory missions: assisted-perception baseline

On October 30, 2026, Astra controlled TIAGo++ through a three-station manipulation mission. The robot retrieved a red workpiece, carried it to inspection, released it for processing, regrasped it, carried it to dispatch and left it supported and released. All ten required rubric checks passed. This is one development episode, not a benchmark success rate.

| Evidence | Recorded result |
| --- | --- |
| Run | `310289318f1d44be81f11ef043ea04c1` |
| World | `c3ba1e9b189d70cc8b47b6c3d9e9fed86a3bbbbc423d2c51b5216dde61deb8a2` |
| Environment | 12 × 8 m workshop, 51 physical scene objects |
| Policy | `gpt-6-astra`, medium reasoning, existing Codex sign-in through the declared relay transport |
| Execution | 27 model decisions; 152.02 simulated seconds; 461.73 episode wall seconds |
| Inference | 240.80 wall seconds; 713,528 reported tokens; Codex plan usage, no inferred API dollar charge |
| Recording | 3,046 state samples; 599 spectator replay frames |
| Verification | Original trace hash and provenance checked; offline grading exactly matches the saved result |

The policy received task instructions, approximate station/approach locations, ideal semantic RGB-D and robot feedback. It received no evaluator state or rubric progress. Every selected action matches its recorded execution entry. The final red workpiece contacts the dispatch bench, is unheld and retains its inspected state; the blue distractor remains at the source.

The workcells and rubric were originally model-generated. This episode follows an **authored repair** to two loading volumes: their lower bounds extend 5 mm below the tabletops, with unchanged upper bounds and XY areas. The preceding run `266f6808ab234e70a6dd2be1c81a3fd2` failed because compliant contact put the resting workpiece 0.027 mm below a zero-tolerance containment boundary. That failed run remains preserved. The repair changed no physical objects, task text, process duration, contact/release requirement, order constraint or policy action. Generation now diagnoses this support-plane conflict and requests a repair before execution.

| Required stage | Completion time, simulated seconds |
| --- | ---: |
| Initial physical grasp | 19.23 |
| First uninterrupted carry ≥1.5 m | 41.88 |
| Inspection support / release | 77.51 / 77.91 |
| Three-second inspection completed | 79.91 |
| New post-inspection grasp | 87.11 |
| Second uninterrupted carry ≥1.5 m | 108.28 |
| Dispatch containment / support / release | 144.51 / 145.87 / 145.87 |

The workbench streamed native spectator frames and provisional checks, then opened the graded report. Its replay was played and paused in the browser. A separate 960 × 720 video renders recorded states at **3× simulated time**, omitting inference pauses; it does not rerun the policy or interpolate object trajectories. Original report images retain their recorded viewpoints.

The complete run, failed predecessor, source snapshot and video are retained in the ignored development artifacts, with a verified backup. They are not bundled onboarding scenes or scripted controllers. Trace SHA-256: `77b11f71040d235523ef3593089d6198603382aad2d6afa26c4cec7ccee67aba`.

## Fresh generated hall

Run `2405eb324dd341448cdfe5f97099642f` completed a newly generated 24 × 16 m hall with 120 physical components. Generation repaired its first candidate automatically; no authored world edit or injected policy action was needed. Astra took 32 decisions and 157.58 simulated seconds. All 11 required checks passed, and the original hash-verified trace reproduced the saved grading exactly (3,158 samples; SHA-256 `7244c3ba2e873a7c97757de6c16f335165814dc0b3522d247ec329004bb9274e`). The final red part is inspected, unheld and supported by the dispatch bench. Episode wall time was 683.01 seconds, including 315.87 seconds of inference; reported Codex usage was 1,182,770 tokens. The live workbench streamed execution and opened the recorded report with 599 spectator frames.

Both successful runs used simulator-assisted object labels and surface positions, plus station/approach coordinates in the task. They establish an **assisted-perception baseline**, not real-sensor autonomous operation. The revised acceptance requirement excludes those aids: policies must use onboard sensor measurements, robot-relative controls and ordinary task instructions. A complete mission under that restricted interface, more layouts/seeds and broader controller robustness remain unproven. The ten-scenario generation trial is separate evidence and contains no policy evaluations.
