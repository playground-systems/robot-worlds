# Connect your own remote policy

`RemotePolicy` adapts a service implementing the protocol below. It does not make an arbitrary URL, OpenPI server, LeRobot checkpoint, or different robot embodiment compatible. Your service must consume this world's sensor observations and choose its exposed tool actions. Physics, time, trace recording, and rubric grading stay on the local machine.

```python
import os
from robot_worlds import load
from robot_worlds.remote_policy import RemotePolicy

world = load("worlds/YOUR_WORLD_VERSION")
with RemotePolicy(
    "https://YOUR_POLICY_SERVICE/robot-worlds",
    token=os.environ.get("POLICY_TOKEN"),
    timeout=30,
    max_seconds=600,
    max_steps=30,
) as policy:
    report = world.evaluate(policy, episodes=1, max_steps=30)
report.open()
```

Local development accepts `http://localhost:PORT` and loopback IP addresses. Other endpoints require HTTPS. The token goes in an Authorization header and is excluded from saved transcripts. Redirects and automatic retries are disabled: retrying a stateful inference after an ambiguous timeout could duplicate an action. A failed request invalidates the active session; the next episode must reset.

## Protocol version 1

All requests are POST with `Content-Type: application/json`. All successful responses have HTTP 200 and `"protocol_version": "1"`. The base URL may include a path prefix. The adapter appends these paths:

1. `/reset` receives `episode_id` (fresh UUID) and `context` containing task, robot identity and available sensor/action contracts. Return `session_id` (a fresh nonempty string, maximum 256 characters) and echo `episode_id`. Clear recurrent state, action chunks, queues and prior episode memory **before** acknowledging. The adapter rejects reused session IDs; the server is responsible for actually resetting its internal policy.
2. `/act` receives `episode_id`, `session_id`, `sequence` (zero-based decision count), `observation`, and `tools` (JSON tool schemas). Return the same `session_id` and `sequence`, plus `action` of the form `{"tool":"NAME_FROM_TOOLS","arguments":{...}}`. Arguments are validated locally against the current tool schema before execution. A `finish` tool ends the attempt; it cannot set the task score.
3. `/result` receives the same episode/session/sequence fields and `result` from the actual local tool execution. Return the same `session_id` and `sequence`, with `"accepted": true`. Another action is not requested until this acknowledgement. A result is also sent for `finish`. The server should expire finished/abandoned sessions; no separate close endpoint is defined.

For example, a reset response is:

```json
{"protocol_version":"1","episode_id":"ECHO_REQUEST_ID","session_id":"FRESH_SERVER_SESSION"}
```

Only actor context and sensor observations are transmitted. The adapter's allowlist excludes the world specification, evaluator rubric, hidden object state, full simulator state and scoring evidence. Camera bytes become `{"encoding":"base64","data":"..."}` in the request; the current renderer produces PNG bytes. Saved transcripts contain camera hashes and byte counts, not image payloads. The server must understand the actual observation/tool contract for the selected robot. Tool-space compatibility does not imply joint-space or checkpoint compatibility.

`timeout` bounds HTTP operations. `max_seconds` is an episode wall-clock budget checked before requests and while reading responses; late responses are rejected. `max_steps` limits decisions. Request/response JSON sizes are bounded (32 MiB/1 MiB by default). Inference time does not advance simulated time. Network/protocol/action-schema failures are policy errors in the report, separate from ordinary task failure.

For concurrent evaluations, construct a separate adapter per episode instead of sharing mutable sessions:

```python
report = world.evaluate(
    policy_factory=lambda: RemotePolicy("https://YOUR_POLICY_SERVICE/robot-worlds"),
    episodes=3,
)
```

The current SDK executes these episodes serially. A factory creates fresh client state; the SDK resets every instance and closes factory-owned adapters at episode end. An explicitly supplied adapter remains caller-owned, so use the context manager as in the first example.
