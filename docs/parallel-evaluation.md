# Run isolated evaluation episodes in parallel

```python
from robot_worlds import load
from robot_worlds.batch import evaluate_batch

if __name__ == "__main__":
    world = load("worlds/YOUR_VALIDATED_VERSION")
    report = evaluate_batch(
        world,
        policy_config={"kind": "llm", "model": "gpt-6-astra", "max_cost_usd": 1},
        episodes=4,
        workers=2,
        seed=100,
        max_steps=30,
    )
    report.open()
```

The main guard is required in standalone scripts because workers use Python's `spawn` method. Each episode gets a fresh process, policy/reset, native MuJoCo simulator and graphics context. Seeds are assigned by episode index before scheduling, so completion order does not change which episode gets which seed. Worker results, errors and partial artifacts are retained, and the aggregate report rewrites frame paths into their correct episode directories.

The default is one worker. More workers trade memory and CPU/graphics contention for concurrency; the implementation permits 1–32 workers. This is native **CPU physics**. On Linux with a supported NVIDIA driver, `graphics_backend="egl"` selects offscreen EGL rendering; it does not switch physics to a GPU or make GPU feature-parity claims. NVIDIA execution and scaling have not been validated for this implementation. macOS uses `graphics_backend="auto"`.

Parallel policies are declarative configurations for the built-in `LLMAgent` or `RemotePolicy`, not arbitrary pickled Python objects. LLM configuration accepts the agent's model/reasoning/request/token/time/cost limits. Cost caps are **per episode**: four episodes with a $1 cap have a combined reservation ceiling of $4. Divide a desired total policy budget across episodes. Actual API usage/reservations remain in each episode's policy usage record. Environment-generation cost is separate.

Remote configurations use `{"kind":"remote","base_url":"https://YOUR_SERVICE","token_env":"POLICY_TOKEN"}`. Credentials are resolved from the child process environment and are not written into the configuration artifact. The service must implement the [explicit remote-policy protocol](remote-policy.md); a URL alone does not establish robot compatibility. Each episode resets to a fresh server session. Simulation clocks remain stepped while the service computes.

Cancellation cooperates between bounded model/HTTP requests and physical tool actions. Already-running requests finish or time out; pending episodes are cancelled. Interrupted, policy-error, simulator-error and worker-error episodes are excluded from the valid task-success denominator. Repeated seeds do not provide task diversity or establish statistical independence.

## Initial Mac measurement

A local engineering test on an Apple M1 Pro ran two independent episodes with real MuJoCo stepping, camera rendering, recorded-state replay and a loopback HTTP protocol test policy. Each episode waited 0.05 simulated seconds and ended without completing its manipulation task. The test used a small scene with the native TIAGo++ robot; it measures orchestration/render startup, not long-horizon policy competence or large-scene sustained throughput.

| Workers | Two-episode elapsed time | Episodes/second |
| --- | ---: | ---: |
| 1 | 5.000 s | 0.400 |
| 2 | 1.605 s | 1.246 |

These are individual sequential measurements from a development machine, including process startup and with potentially different cache/other-work conditions. They are evidence that both configurations execute, not a general speedup guarantee. The native Mac renderer emitted an `ARB_clip_control` depth-precision warning; this result is not a claim of camera-depth equivalence across graphics backends. Re-run `uv run pytest tests/test_batch.py -s` for the current environment. NVIDIA measurements remain pending.

## Local API route

`POST /api/worlds/{world_id}/evaluate` accepts `workers` (1–32, default 1) and `graphics_backend` (`"auto"` or `"egl"`, default `"auto"`). For example:

```json
{
  "model": "gpt-6-astra",
  "episodes": 4,
  "workers": 2,
  "graphics_backend": "auto",
  "seed": 100,
  "max_steps": 30,
  "max_sim_seconds": 120,
  "max_cost_usd": 1,
  "max_model_seconds": 180
}
```

The route returns a queued job (`202`); inspect `/api/jobs/{job_id}` for events and the resulting run ID. With multiple workers or explicit EGL, the job dispatches a declarative LLM policy configuration to `evaluate_batch`; it does not construct a shared policy before launching episodes. One worker with automatic graphics uses the serial `World.evaluate` path. Explicit EGL still requires a supported Linux runtime; accepting the request does not establish driver availability.

`max_model_seconds` becomes the policy's model-call budget (`max_seconds`); `max_sim_seconds` remains the separate simulation-clock limit. `max_steps` bounds actions and is also passed to the policy. Model, reasoning and any explicit token pricing remain part of the policy configuration. In the parallel path, the cost/time limits apply per episode: the example permits up to $4 combined policy cost reservations. Cancellation through `POST /api/jobs/{job_id}/cancel` reaches the batch cancellation callback and remains cooperative between bounded requests/actions. This API route selects the built-in LLM agent; remote-policy configurations remain available through the Python batch API.

## CLI route

```sh
robot-worlds evaluate worlds/YOUR_VALIDATED_VERSION \
  --episodes 4 --workers 2 --seed 100 \
  --max-steps 30 --max-sim-seconds 120 \
  --max-cost-usd 1 --max-model-seconds 180
```

Use `--graphics-backend egl` only on supported Linux hosts; macOS keeps the default `auto`. Multiple workers or explicit EGL select batch execution. Defaults (`--workers 1 --graphics-backend auto`) preserve serial evaluation, including `--policy module:attribute` for an existing Python policy object or a no-argument policy class. Python objects are rejected for spawned execution instead of being pickled.

For a compatible remote service, use `--remote-url https://YOUR_SERVICE`. If configured, the credential comes from `ROBOT_WORLDS_POLICY_TOKEN`; spawned policy configuration contains only that environment-variable name, never the token value. Serial remote evaluation closes its client even if evaluation fails. `--policy` and `--remote-url` are mutually exclusive. Policy compatibility still requires the documented [remote protocol](remote-policy.md).

The same per-episode parallel policy budget caveat applies to CLI runs. `--open-report` opens the completed aggregate report. Generation separately accepts `--scene-profile large --min-objects 500 --max-objects 5000`; these explicit resource/count requirements do not prescribe a particular floorplan.
