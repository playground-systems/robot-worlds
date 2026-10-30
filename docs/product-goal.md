# MVP requirements and evidence

Robot Worlds turns a text description into a physical environment, executable rubric and reproducible policy evaluation. The public objective is broad AI-developer adoption and GitHub stars through an approachable, self-hosted open-source project.

Evidence below was reviewed on October 30, 2026. Saved development worlds and runs live under the ignored `.robot-worlds/` directory; they are not bundled onboarding demos.

## Acceptance scope

| Requirement | Current evidence / remaining work |
|---|---|
| Arbitrary layouts and environment types | Text generation, custom layouts and model-authored repeated assemblies are implemented. Saved capacity world `03a174e50763a50c8dd9` spans **100 × 60 × 6 m**, contains **884 objects**, and passed reset/render validation. This establishes capacity and a stable reset, not facility-wide reachability or arbitrary-task success. Factory is the first reference setting. |
| Autonomous validation and repair | Schema/reference checks, geometry checks, MuJoCo compilation and settling, initial rubric probes, bounded repair and immutable previews/ready versions are implemented. Natural-language completeness and task solvability still need broader independent validation. |
| Custom assets and generated geometry | Model-authored primitive assemblies and OBJ/STL/MSH import work through the SDK and browser upload. Imported meshes are normalized to declared dimensions and use convex collision hulls; open interiors require separate components. Source-derived Airsup reference geometry is documented in [the reference import](airsup-reference.md). General asset quality and richer refinement remain open. |
| TIAGo++ | Physical wheel motion, arm control, grasp/lift/release and carrying are exercised. [The three-station engineering test](../tests/test_factory_mission.py) picks a workpiece, carries it to a loaded timed inspection, then carries and places the same workpiece at delivery. Its scripted policy uses fixture coordinates and privileged position access; it is controller/evaluator evidence, not an autonomous-policy result. |
| Unitree G1 | The G1 EDU 29-DOF body with Dex1-1 grippers has physical walking, reaching, grasp/lift, short walking carry and release tests. No attachment constraints hold the workpiece. Arbitrary terrain, payload robustness and a full autonomous G1 mission remain unproven. See [physical tests](../tests/test_physics.py). |
| LLM robot-use policy | Astra run `310289318f1d44be81f11ef043ea04c1` completed the three-station TIAGo mission with all ten checks passed, 27 decisions and 152.02 simulated seconds. [Saved evidence](autonomous-factory-demo.md) records the autonomous policy, authored loading-volume repair and remaining limits. This is one episode, not a benchmark success rate. Earlier native Mac evidence includes a five-decision navigation episode. |
| Rubrics | Grounded state/contact predicates, identity, timing, order, alternatives, missing-evidence handling and offline regrading are implemented. [Adversarial review](review-rubric.md) covers no-op, wrong/stale geometry, regression and interrupted process loading. Broader physical mission counterexamples and independent checks of generated task interpretation remain. |
| SDK, API, browser and reports | Generation, inspection, rubric editing, evaluation, cancellation, evidence-linked replay and exports are wired. Saved live reports and browser checks exercise the path. Parallel evaluation is exposed as `robot_worlds.evaluate_batch`, API worker/backend settings, and CLI flags; focused dispatch checks pass. |
| Budgets and cancellation | Model calls, tokens, model wall time, action count and simulated time are bounded. Monetary limits reserve against counted input and maximum output at a recorded price profile, then reconcile reported usage. Ambiguous usage retains its reservation. Generation and evaluation cancellation are cooperative between bounded operations; they do not interrupt an in-flight model request or physics action immediately. |
| Local Mac and NVIDIA scaling | Native Mac simulation/rendering and the live policy run are verified. [NVIDIA validation](gpu-validation.md) records actual RTX 4090 EGL rendering, **12 physical tests passed**, and a four-episode scripted comparison: two spawned workers took 7.26 s versus 13.60 s for one. Physics remained on CPU; this one small workload does not establish general scaling, large-scene throughput or cross-platform bitwise equivalence. |
| Installable distribution and CI | A built wheel contains the browser modules, robot/controller assets and licenses, and served its packaged workbench in an isolated extraction check. A clean Linux wheel/dependency install passed, including the installed CLI and packaged robot assets. [Implementation PR #1](https://github.com/playground-systems/robot-worlds/pull/1) tracks hosted CI and remaining acceptance work. Linux CI passed the complete suite and distribution build. Hosted macOS physics coverage is separate from graphics availability; see [validation details](gpu-validation.md). |

This table records development evidence. It is not a release-completion claim.

## Required end-to-end demonstration

Generate connected workcells, distributed parts storage, tools, inspection and staging. An LLM robot-use policy must retrieve a part, load a fixture, move the same workpiece to another station and deliver the finished kit. Required process order, stable placement and workpiece identity must reject shortcuts. Both robot presets must demonstrate their supported physical interactions. The complete TIAGo policy mission is now demonstrated on a corrected generated world; fresh autonomous generation, more layouts/seeds and a full autonomous G1 mission remain acceptance gaps.

Generation must preserve the original request across repair attempts. Unsupported requirements remain unvalidated. Refinement creates a new version; it cannot alter an earlier run's physics, sensors or grading thresholds. A single attempt is a debug run, not a statistically meaningful benchmark.

## Interface and exclusions

Use a text prompt, Python SDK or local HTTP API. Policies may run in process or through a documented remote adapter. The local browser supports inspection, rubric editing and replay; a graphical layout editor is outside the MVP. No bundled pre-generated factory or scripted demo controller is part of onboarding.

Rigid-body contact and articulated mechanisms are required. Internal manufacturing processes may use disclosed timed state changes with physical prerequisites. Deformables, fluids and detailed material transformation are excluded.

## References

[Airsup Lab](https://github.com/AirsupHQ/airsup-lab) informs the goal of detailed, explorable facilities. Visual detail should correspond to physical geometry and inspectable process state. [MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie) provides licensed research robot models; model availability alone does not establish controller capability.
