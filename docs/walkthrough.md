# Generate, evaluate and inspect a world

Use a reviewed source checkout or wheel from the [acceptance record](alpha-acceptance.md).
No package-index release is assumed. Python 3.11+ is required; Apple silicon works
without an NVIDIA GPU. Start with one evaluation worker.

## Start the workbench

```sh
uv sync --frozen
uv run robot-worlds doctor
export OPENAI_API_KEY="your-key"
uv run robot-worlds serve
```

Open http://127.0.0.1:8765. The workbench starts empty. Describe the scene and task,
then inspect the preview while generation runs. **Preview** means it is still
being built; **ready** means the saved version passed creation-time checks.
Neither state proves that a robot can complete the task.

For the household scenario, request the original porcelain bowl and articulated
microwave, accessible counters, a clear door sweep, and full containment, support
and release. [The acceptance prompt](../tools/acceptance/live_household.py) gives
an exact example. Do not require heating or door closure unless those are part
of your task.

For a warehouse, prepare the reusable sources first:

```sh
uv run robot-worlds prepare-warehouse --data-dir .robot-worlds
```

The workbench discovers this library automatically. [This prompt](../tools/acceptance/prompts/warehouse-dense.txt)
requests a stocked facility with receiving, storage, manual packing and dispatch.
The roller conveyors are passive. Generating a layout does not create warehouse
automation. Preparation caches downloads and conversions; repeated scenes reuse them. For
wall panels, roof beams, loading bays and named lanes, use the
[architectural prompt](../tools/acceptance/prompts/warehouse-presentation.txt) with
graphics produced by `tools/acceptance/prepare_floor_stencils.py`. The graphics
manifest supplies local paths to include in the prompt; equipment remains sourced.

## Connect a policy

In **Agent**, configure model access and bounded evaluation limits, then choose
**Evaluate**. The default agent sees onboard RGB-D, joint encoders, relative IMU
and wheel odometry. It receives the operator task and available actions; it does
not receive global object poses or evaluator thresholds.

Custom Python policies implement `reset(context)` and `act(observation)`; optional
`record_result(result)` receives controller feedback. The [SDK guide](sdk.md)
includes in-process and HTTP examples. Model inference can run remotely while
physics and grading remain self-hosted.

An existing Codex CLI sign-in is also supported through the Python SDK:

```python
from robot_worlds import generate, LLMAgent
from robot_worlds.codex_client import CodexClient

world = generate(
    "A small workshop with an unobstructed yellow inspection square. "
    "Use TIAGo++; drive to the square and remain for two seconds.",
    client=CodexClient(max_calls=3, max_seconds=300),
)
if world.manifest["validation"]["status"] == "ready":
    report = world.evaluate(
        LLMAgent(client=CodexClient(max_calls=12, max_seconds=420)),
        max_steps=12, max_sim_seconds=90,
    )
    report.open()
    world.export("world.zip")
    report.export("report.zip")
```

This uses Codex plan limits rather than API credits. Separate clients retain
separate generation and policy ledgers. No sign-in credential is copied into the
project. The browser's BYO API configuration remains a separate transport.

## Inspect and recover

- Select an object in the canvas or Layers, then frame it. Use the cutaway view
  to inspect interiors; **Physical** shows the native simulation representation.
- A text edit creates a new version. Inspect **Changes** and open the parent to
  compare. Existing reports stay attached to their original version.
- During evaluation, the action and provisional criteria update. Cancel preserves
  partial evidence and marks the episode interrupted; it does not count as a
  valid task failure.
- Open a criterion in the report to see the measurements behind its verdict.
  Replay frames are recorded samples, not a reconstruction of every physics step.
- Export world and report archives. Extract trusted exports into separate new
  directories; use `load(world_directory)` and `Report(report_directory)`. Calling
  `report.regrade(world, output=new_directory)` creates a separate grading result.
- Invalid credentials, timeouts and unsupported assets have actionable errors.
  Correct the cause and start a new attempt; do not interpret an infrastructure
  error as the robot failing the task. Never share keys in error reports.

## Reproduce software and manipulation evidence

From the source checkout, with no model key required:

```sh
uv run python tools/acceptance/offline_household.py --output /tmp/rw-sdk-check
uv run python tools/acceptance/engineering_household.py --output /tmp/rw-placement --render
```

Use new output directories. The first command mocks generation but runs real
sensors, actions, grading and portable reload; its task intentionally fails.
The second uses a privileged engineering controller and an initially open door.
It demonstrates physical grasp, short carry, guided insertion and stable release
with original assets. It is not an autonomous sensor-only policy and does not
prove robot-operated door opening. Both commands run native simulation; the
rendered placement check is substantially heavier than the short SDK probe.

For complete live acceptance, `tools/acceptance/live_household.py` records prompt,
model usage, validation, policy outcome and portable reload. It supports API or
Codex transport and optional prompt/library paths. Live runs consume model usage;
use finite limits. [Current results and limitations](alpha-acceptance.md) distinguish
live, mocked and engineering evidence.
