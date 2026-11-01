# Install and check your setup

Robot Worlds runs simulation and grading on your computer. The browser uses your own model API access. The Python SDK also accepts an optional Codex transport using an existing supported sign-in. The browser opens an empty workbench; no factory or controller demo is bundled into onboarding.

## Install from source

Use Python 3.11 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/). The physical execution evidence covers Apple silicon macOS and Linux; Windows is not validated, and the local server uses POSIX file locking.

```sh
git clone https://github.com/playground-systems/robot-worlds.git
cd robot-worlds
uv sync --frozen
uv run robot-worlds doctor
export OPENAI_API_KEY="your-key"
uv run robot-worlds serve
```

Open http://127.0.0.1:8765. Describe a place, robot and task; inspect validation before evaluating. The default data directory is `.robot-worlds/`. Ctrl-C stops the server. Cancel active jobs in the workbench first: cancellation takes effect between bounded operations and may wait for an in-flight request or action.

`doctor --json` produces a shareable offline report. It does not make API calls, import native simulator libraries, create a graphics context or write probe files. An offline pass establishes prerequisite presence; model access, dependency compatibility, native loading and rendering remain separate checks.

For a bounded SDK check without model charges, run:

```sh
uv run python tools/acceptance/offline_household.py --output /tmp/robot-worlds-sdk-check
```

Choose a new output directory. This uses an explicitly mocked generator with the
original bowl and microwave, then runs native sensor/action execution, reports,
export/reload and regrading. The probe deliberately leaves the task incomplete;
it checks the software connection rather than demonstrating bowl placement.
Python 3.11+ is required; the older Python supplied by macOS is insufficient.

To add reusable warehouse assets, install Node.js 20+ for the first mesh decode,
then run:

```sh
uv run robot-worlds prepare-warehouse --data-dir .robot-worlds
```

This prepares five pinned sources: rack, pallet, sealed carton, fixed safety
barrier and an unpowered conveyor with 29 passive rollers. Their GLBs total
about 4.06 MB. Prepared meshes and PNGs are larger: the four rigid assets occupy
about 24 MB and the conveyor about 33 MB. It retains original visual data, attribution and disclosed collision
approximations. No model key, native simulator or GPU is used during preparation.
The conveyor retains hinges and bearing clearances; it does not provide powered
transport. Native contact and rendering acceptance remain pending.

New workbench generation/refine/repair jobs discover
`.robot-worlds/asset-library` automatically. Use the same `--data-dir` when
starting the server. CLI generation and edits accept the library explicitly:

```sh
uv run robot-worlds generate "A warehouse with parts storage and an inspection area. Use TIAGo++; navigate to inspection." --asset-library .robot-worlds/asset-library
```

Preparation supports repeated `--asset` flags for a subset, such as
`--asset pallet_a01 --asset box_corrugated_brown_b20`, and `--timeout` (up to
600 seconds). It refuses to replace existing directories; use an installed
library as-is or choose a new `--output` directory. A failed or cancelled attempt
retains verified caches and publishes no partial library. The final library copy
is atomic; Ctrl-C interrupts setup. Existing prepared libraries and decoded
caches can be used without Node. Scene validation still runs during generation;
preparation alone does not establish native contact behavior or task solvability.

## Optional Codex SDK transport

With a recent Codex CLI installed and signed in, the SDK can request decisions through `codex exec`. This uses the account's Codex plan limits rather than API credits; it has no API dollar cap. The transport bounds calls and wall time, and uses reported token totals to stop subsequent calls. It does not extract or copy sign-in credentials. See [Codex authentication](https://learn.chatgpt.com/docs/auth).

```python
from robot_worlds import LLMAgent, load
from robot_worlds.codex_client import CodexClient

world = load("path/to/your/validated/world")
agent = LLMAgent(client=CodexClient(max_calls=40, max_seconds=900))
report = world.evaluate(agent, max_steps=40, max_sim_seconds=240)
report.open()
```

Generation accepts the same client through `generate(prompt, client=CodexClient(...))`. The CLI receives task text, allowed actions and robot observations in an ephemeral working directory. Shell, apps, web search and delegation are disabled; only a schema-valid action is accepted. The external simulator executes the action. Account access and CLI configuration are not independently attested, and this option does not enable hosted inference for other users.

## Verify API model access explicitly

The code defaults to `gpt-6-astra`, reasoning `medium`. The development team's successful Astra calls establish access for that account, not yours. Astra requires supported paid API access; a ChatGPT subscription does not establish that API access. A configured key, a model name or a model-list response does not establish compatibility with the actual workload.

The selected model must support the Responses API, strict structured JSON for generation/editing, and image input with function selection for the robot policy. Cost-limited calls also require the input-token-counting endpoint for the same payload. The [official Astra model page](https://developers.openai.com/api/docs/models/gpt-6-astra) documents capabilities; [the model guide](https://developers.openai.com/api/docs/guides/latest-model) specifies Responses for tool use. Check current account access and pricing rather than relying on a development account's results.

After choosing a budget, explicitly opt into the live protocol test from the source checkout:

```sh
uv sync --frozen --dev
ROBOT_WORLDS_LIVE_API=1 uv run --frozen pytest tests/test_llm_live.py -q
```

This test uses the default Astra model, sends two bounded generation requests plus their token-count requests, and creates no simulator or robot actions. It incurs model usage. It verifies JSON, image/function input and accounting for that account; it does not prove task performance. Ordinary tests never opt into live requests merely because a key exists.

For another model, use the SDK with a verified model ID, supported reasoning effort and explicit current input/output prices. Supply both rates; retain the monetary cap:

```python
import os
from robot_worlds import generate, LLMAgent

model = os.environ["ROBOT_WORLDS_MODEL"]
prices = {
    "input_price_per_million": float(os.environ["MODEL_INPUT_USD_PER_MILLION"]),
    "output_price_per_million": float(os.environ["MODEL_OUTPUT_USD_PER_MILLION"]),
}
world = generate(
    "A small workshop with an inspection approach. Use TIAGo++; navigate to inspection.",
    model=model,
    reasoning="low",
    client_options={**prices, "max_cost_usd": 1, "max_seconds": 180},
)
if world.manifest["validation"]["status"] == "ready":
    report = world.evaluate(
        LLMAgent(model=model, reasoning="low", **prices, max_cost_usd=1),
        max_steps=10, max_sim_seconds=60,
    )
    report.open()
```

This is a configuration pattern, not evidence that every named model supports this workload. The built-in Astra price profile is pinned in source. Alternate rates must account for the model's actual cache and large-context pricing when applicable. If exact counting is unavailable, monetary mode stops before generation; do not disable accounting to conceal incompatibility. [Token counting](https://developers.openai.com/api/docs/guides/token-counting) describes the API requirement. Unknown models can use explicitly selected SDK token-only mode, but that does not provide a dollar cap.

Authentication, permission, unsupported-feature and rate-limit failures are policy/infrastructure errors; they are not evidence that the robot failed its task. Share sanitized error type/status and the offline doctor report, never keys or provider response bodies containing account data.

## Graphics and native execution

On macOS, keep the automatic graphics backend and start with one worker. On a compatible headless Linux NVIDIA host, set `MUJOCO_GL=egl` before starting Python. Installing a driver or setting the environment variable alone does not verify rendering. MuJoCo physics remains on CPU; NVIDIA provides rendering, with concurrency selected explicitly. [GPU evidence](gpu-validation.md) and [parallel evaluation](parallel-evaluation.md) describe the measured limits.

Source-checkout engineering tests exercise native execution without model API calls:

```sh
uv sync --frozen --dev
uv run --frozen pytest tests/test_physics.py tests/test_factory_mission.py -q
```

These start real simulation. Use a working graphics environment for tests that render; CI uses Xvfb on Linux, while a supported NVIDIA host can use EGL. The scripted fixtures validate physical and grading boundaries, not autonomous-policy competence.

## Install a reviewed wheel

No package-index publication is assumed. Download a wheel from a reviewed build artifact, or build one with `uv build`. Install it into a new environment outside the source tree:

```sh
uv venv /tmp/robot-worlds-install-check
uv pip install --python /tmp/robot-worlds-install-check/bin/python /absolute/path/robot_worlds-VERSION-py3-none-any.whl
/tmp/robot-worlds-install-check/bin/robot-worlds doctor --json
```

The [packaging workflow](../.github/workflows/packaging-smoke.yml) checks the built wheel and source archive in clean environments, including packaged robot assets, licenses and browser modules. An installed wheel carries package assets; a source checkout is still needed to run repository tests. The [release procedure](releasing.md) records the remaining publication gates.
