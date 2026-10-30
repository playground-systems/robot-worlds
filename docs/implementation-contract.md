# Runtime architecture

The public flow is `generate(prompt) → World`, `load(path) → World`, and `world.evaluate(policy) → Report`. The Python SDK, local API and browser use the same world bundles and evaluation code. [Product requirements and evidence](product-goal.md) track acceptance separately from this architecture.

## World representation and generation

`models.py` defines a typed `WorldSpec`: task, supported robot, bounds, physical objects, assembly templates/instances, regions, process cycles, rubric, assumptions and unvalidated requirements. Coordinates use metres, Z is up, the floor is Z=0, XY bounds are centred on zero, and sizes are full extents. Joint anchors and object positions describe the zero-joint reference configuration; the simulator applies initial joint values.

`generator.py` asks Astra for the scene and executable rubric together, then validates references, geometry and measurements. Model-authored assemblies expand into physical components rather than selecting a bundled floor plan. Custom local OBJ/STL/MSH files are copied into the bundle and content-hashed. The runtime normalizes each mesh to its declared extents and uses a convex collision hull; concave equipment needs separate collision components.

`sdk.py` saves immutable previews and validated versions. Validation compiles MuJoCo, settles the world, measures initial bounds/stability, rejects already-satisfied required achievements without prerequisites or unavailable rubric measurements, and renders a preview. Repair attempts preserve the original request and stop at their budget. A `ready` version establishes a checked reset, not task solvability or complete natural-language interpretation. Unsupported requirements stay explicit.

World identity includes the specification, asset hashes, robot assets, validation and runtime evidence. Refinement and rubric edits create new versions. The separate physical identity permits regrading a new rubric against the same recorded physical world; a changed physical identity requires another evaluation.

## Physical execution and policy boundary

`runtime.py` and `g1_runtime.py` provide MuJoCo simulation, robot controllers, observations, bounded tools, privileged snapshots and native rendering. TIAGo++ uses wheel control and local arm IK. G1 uses a learned locomotion controller, arm servos and Dex1-1 contact grippers. Exact assets, revisions, controller hashes and disclosed approximations appear in manifests. Object motion and grasping come from contact; no tool call awards success or attaches a workpiece invisibly.

Policies implement `reset(context)`, `act(observation)` and optionally `record_result(result)`. `LLMAgent` uses the Responses API with text/image observations and runtime-provided function schemas. `RemotePolicy` exposes the same boundary over its documented HTTP adapter. The actor receives the task, declared sensors and actual tool results; rubric thresholds and privileged simulator snapshots remain outside its input. Without rendering, visual observations are explicitly unavailable.

The SDK resets each episode's runtime, controllers, policy memory and monitors. Simulation is stepped; model inference wall time is recorded separately. Actions execute through controllers, and their measured results return to the policy. Runtime, policy, grading and cancellation outcomes remain distinct from task success.

## Rubrics and evidence

`rubric.py` grades recorded state with supported local predicates and temporal rules. Criteria bind persistent IDs to physical measurements, required ordering, durations, final or historical outcomes, maintenance constraints and alternatives. Initially true achievements cannot earn no-op credit. Missing measurements remain unknown; definite counterevidence still establishes failure. Timed process abstractions require physical loading and preserve workpiece identity, with prerequisites monitored at every physics step.

Runs retain the frozen world, protocol, package/controller provenance, traces, policy transcript and usage, tool results, criterion measurements and replay frames. Reports link criteria to completion, missing-data or decisive failure evidence. Offline regrading reads saved measurements rather than previous verdicts. Recorded traces are trusted simulator data, not tamper-proof attestations. See [rubric semantics and limits](review-rubric.md).

## Budgets, workers and hardware

`llm.py` bounds requests, output tokens, total output, elapsed model time and configured model cost. Cost-limited calls reserve counted input plus maximum output against a pinned price profile; missing or ambiguous accounting retains the reservation. Unknown models require explicit rates or an explicit token-only configuration. Credentials stay outside artifacts. Cancellation is cooperative between bounded operations.

The localhost server uses spawned workers so simulation and graphics contexts stay out of web request threads. Its browser vendors Three.js locally; native simulator frames show the physical scene, while the interactive viewer supports geometry inspection. Source-reference visuals may contain disclosed nonphysical detail.

Native MuJoCo physics runs on CPU. macOS uses its local graphics context; compatible Linux NVIDIA hosts can select EGL rendering. `batch.evaluate_batch` runs declarative built-in LLM or remote-policy configurations in fresh spawned workers with explicit concurrency and graphics settings; live Python policy objects are not pickled. The API and CLI route explicit worker/backend selections through that same batch executor. [GPU validation](gpu-validation.md) records the measured hardware and scaling limits.
