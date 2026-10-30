# Install and check your setup

Robot Worlds runs simulation and grading on your computer. Generating a world and using the built-in LLM policy require your own model API access. The browser opens an empty workbench; no factory or controller demo is bundled into onboarding.

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

## Verify model access explicitly

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
