# Robot Worlds

Describe a place and a task. Generate a physical robot world and editable rubric, run a policy, and inspect what happened.

Robot Worlds is an early self-hosted robotics project. Simulation runs locally with MuJoCo; generation and the default robot agent use your OpenAI API key. A factory is the first reference setting. The scene format also supports workshops, homes, labs, warehouses and other arrangements of rigid objects and articulated mechanisms.

## Run locally

Requires Python 3.11 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/). Physical execution is tested on Apple silicon macOS and Linux with NVIDIA EGL rendering. Use `MUJOCO_GL=egl` on a compatible headless NVIDIA host.

```sh
git clone https://github.com/playground-systems/robot-worlds.git
cd robot-worlds
uv sync
export OPENAI_API_KEY="your-key"
uv run robot-worlds serve
```

Open **http://127.0.0.1:8765**. Enter a scene and task, inspect the generated geometry and rubric, then start an evaluation. The workbench stores worlds, job progress and reports under `.robot-worlds/`. A failed validation preserves its candidate and explains the failed checks.

See [setup and model access](docs/setup.md) for offline `doctor` checks, paid API requirements and clean installation.

```python
from robot_worlds import generate, LLMAgent

world = generate(
    "Build a workshop with separate parts storage and inspection areas. "
    "Use TIAGo++. The task is to carry a small part from storage to inspection."
)
report = world.evaluate(LLMAgent(), max_steps=30, max_sim_seconds=120)
report.open()
```

Generation can return an **unvalidated** world. Inspect `world.manifest["validation"]` before evaluation; the SDK refuses to run a version that did not pass its checks. A validated reset is evidence of a loadable, stable scene—not proof that its task is solvable.

## What runs

- **Generation:** Astra produces a scene and rubric, checks their references and geometry, compiles and settles the physical world, and repairs failures within a bounded number of attempts. Every saved version has a content address.
- **Editing and repair:** text requests patch persistent scene entities, preserve prior versions and produce computed before/after diffs. Optional scope limits which objects can change; repair preserves the task and physical properties. [Scene editing](docs/scene-editing.md) explains the contracts.
- **Robot tools:** the agent selects bounded motion, arm, gripper and sensing actions. Controllers execute these actions in MuJoCo. Object motion comes from physical contact.
- **Evaluation:** local checks grade the recorded state. Criteria support object identity, contact, containment, grasping, temporal order, holding duration and alternatives. Inference wall time is separate from simulated time.
- **Inspection:** the browser shows the generated layout, native simulator images, validation findings, an editable rubric and an evidence-linked report. Worlds and runs export as ZIP archives.

TIAGo++ uses the pinned MuJoCo Menagerie model with documented wheel and gripper collision approximations. Unitree G1 uses the 29-DOF body, Dex1 parallel grippers and an official walking policy. Engineering tests cover contact grasping, carrying and flat-ground locomotion; arbitrary terrain and payloads remain unvalidated. See [the implementation evidence and remaining work](docs/product-goal.md).

For larger facilities, select the large-scene generation profile. For repeated evaluations, the SDK, API and CLI support isolated workers and explicit EGL rendering. MuJoCo physics remains CPU-based. [Parallel evaluation](docs/parallel-evaluation.md) explains the controls and per-episode budgets; [GPU validation](docs/gpu-validation.md) records the measured limits.

## Physical boundaries

The first release models rigid bodies and articulated mechanisms. Internal machining may be represented by an explicit timed process with physical loading prerequisites and persistent workpiece identity. That abstraction appears in the saved specification and report. Cutting, welding, fluids, cloth and material deformation are outside this release.

Mass, friction and visual material defaults are estimates. Sensors currently use ideal semantic RGB-D and proprioception. The arm controller uses local inverse kinematics; it does not guarantee a collision-free motion plan. Evaluation keeps rubric and full simulator state outside the policy observations.

## Development

```sh
uv sync --dev
uv run pytest
uv run ruff check src tests
```

Tests use generated engineering fixtures and bounded fake transports where appropriate. Live model calls require explicit opt-in:

```sh
ROBOT_WORLDS_LIVE_API=1 uv run pytest tests/test_llm_live.py
```

Read [Contributing](CONTRIBUTING.md) for focused checks, [Security](SECURITY.md) for reporting, and [release preparation](docs/releasing.md) for packaging gates.

The root code is MIT licensed. Robot models, controller weights and vendored browser libraries retain their own licenses and source attribution beside the assets. The [third-party inventory](THIRD_PARTY_NOTICES.md) records the pinned sources and retained terms.
