# Generate a world and evaluate a policy

The SDK uses the same validation, execution and reports as the workbench. Generation and `LLMAgent` make paid model requests unless you supply another supported client. Physics and grading run on your machine.

```python
from robot_worlds import generate, LLMAgent

world = generate(
    "Build a kitchen work area for TIAGo++. Put a white bowl on the counter. "
    "The task is to put the bowl inside the microwave and leave it resting there. "
    "Build a hollow microwave cavity with a hinged door and accessible handle, "
    "and a hollow bowl with attached walls. Keep the door swing and robot approach clear. "
    "Use detailed physical components, not solid bounding boxes. "
    "Grade the entire bowl being inside, supported and released for one second. "
    "Do not add heating or door closure to the task.",
    output="worlds",
    client_options={"max_cost_usd": 2, "max_seconds": 300},
)
if world.manifest["validation"]["status"] != "ready":
    raise RuntimeError(world.manifest["validation"])

# Evaluation is optional. Inspect the generated scene before spending on a policy.
report = world.evaluate(
    LLMAgent(max_cost_usd=2, max_steps=40),
    max_steps=40,
    max_sim_seconds=240,
    output="runs",
)
print(report.summary)
report.open()
world.export("exports/kitchen.zip")
report.export("exports/evaluation.zip")
```

This is a usage example, not a claim of demonstrated microwave-task success. A ready world passed compilation, reset and validation checks; it has not necessarily passed a manipulation or door-opening test. Failed generation retains its candidate and diagnostics. `load("worlds/VERSION")` restores a saved bundle, and `world.refine(...)` creates a new version without changing recorded runs. See [editing](scene-editing.md), [live events](live-evaluation.md) and [remote policies](remote-policy.md).

## Policy interface

Implement `reset(context)`, `act(observation)` and optionally `record_result(result)`. `reset` receives the operator task, robot identity and sensor/action contracts. `act` returns one `{"tool": name, "arguments": {...}}` matching the supplied tool schema. A `finish` action ends the attempt; the evaluator decides success from recorded physics.

| Input | Contract |
| --- | --- |
| `image` | Current 640 × 480 onboard RGB camera, PNG bytes |
| `sensors.camera` | Intrinsics and robot-relative camera calibration |
| `sensors.depth` | 32 × 24 sampled optical-axis depths in metres, with original image pixel origin/stride; `null` outside 0.05–10 m |
| `robot.hands` | Base-relative forward-kinematic hand positions, arm joint encoders and finger encoders |
| `robot.relative_heading_rad` | IMU heading relative to episode start |
| `robot.gravity_direction` | Gravity direction in the robot base frame |
| `robot.odometry` | TIAGo wheel-encoder dead reckoning; zero at start, subject to slip. `null` for G1 |
| `last_tool_result` | Controller/sensor feedback, without object identities, global positions or evaluator contact bookkeeping |

There are no simulator-generated semantic labels, object detections or world maps. These are idealized sensor measurements, without a calibrated hardware noise/dropout model. The spectator camera and rubric progress shown in the browser are evaluator-side displays, never policy inputs. `render=False` disables RGB and depth; it does not substitute hidden geometry.

`query_depth(pixel=[u,v])` samples the full-resolution depth camera and returns a visible surface point in the robot base frame. Pixels start at the image's top-left. It does not identify an object, return its center, or see behind an occluder. `move_arm` accepts the same base frame: X forward, Y left, Z up. G1's origin is its pelvis; TIAGo's is its base link. A measured surface point may need an approach/grasp offset chosen by your policy.

```python
# Actions returned by a policy, not a complete controller:
{"tool": "query_depth", "arguments": {"pixel": [320, 300]}}
{"tool": "move_arm", "arguments": {
    "hand": "right", "position": [0.5, -0.35, 0.65], "approach": "down"
}}
```

Earlier development runs used world-frame arm commands and ideal semantic detections. Those recordings retain their original contracts. New evaluations use the revised manifest and require revalidation of older world bundles; policies written against the earlier observation shape need updating.

## Hollow objects and appliance validation

Define a hollow object as attached physical components, with one movable root for a bowl. Opposing finger contacts on different welded pieces count toward the same body's grasp. Containment includes every fixed attached piece, so a bowl bottom inside the cavity with its rim sticking out fails. `on_top` names a particular support surface, such as the microwave floor; it does not treat the roof as the internal floor.

A cavity region can declare `interior_of` with the appliance's physical root ID. Static checks reject a missing container, a region outside its envelope or a usable volume filled by its box components. The generator is instructed to declare these relationships. This check does not certify a door's swept clearance or curved/mesh cavities. Native reset checks also do not prove the door can be opened by the selected robot.

Imported meshes currently collide as convex hulls. A single concave bowl mesh therefore cannot establish a usable hollow interior; use separate physical pieces or the revolved-profile geometry described below. Photorealistic assets, general concave mesh decomposition and appliance manipulation remain development work.

## Checks without model charges

`tests/test_appliance_generation.py` uses mocked model responses to check repair, cavity geometry and compound-body grading. `tests/test_appliance_physics.py` runs the SDK through generation, native rendering, sensor queries, a no-op evaluation, trace verification, regrading and ZIP round trips. It verifies that observing the bowl does **not** earn a placement success. These are engineering fixtures, not bundled demos or evidence of autonomous task performance.

## Curved hollow geometry

Use `kind="revolved"` for a rotationally symmetric object such as a bowl, cup or funnel. Its `revolved.profile` is a closed polygon describing **material**, including the inner wall and bottom, rather than the outside silhouette alone. Coordinates are normalized `(radius, height)` pairs: radius spans 0–0.5 and height spans −0.5–0.5. The last point connects to the first automatically. `size=[diameter, diameter, height]` sets dimensions in metres.

```python
from robot_worlds import SceneObject

bowl = SceneObject(
    id="bowl", label="White ceramic bowl", kind="revolved",
    position=(1, 0, 0.665), size=(0.16, 0.16, 0.15),
    movable=True, mass=0.08, color=(0.9, 0.9, 0.88),
    revolved={
        "profile": [(0, -0.5), (0.32, -0.5), (0.5, 0.5),
                    (0.45, 0.5), (0.28, -0.4), (0, -0.4)],
        "segments": 32,
    },
)
```

The profile must be simple, nonzero-area, and contain 3–12 points. It must reach radius 0.5 and both height bounds so declared dimensions agree with geometry. Angular resolution is 12–64 segments, in multiples of four. Increasing it changes both rendering and collision fidelity, so it creates a new physical version rather than silently changing an existing evaluation.

The native engine triangulates the material cross-section and sweeps each triangle into convex wedges. These wedges preserve the cavity and share one rigid body; their mass totals the declared object mass. The browser displays the same segmented profile. This supports curved hollow geometry without a prebuilt asset catalog. It is still a faceted rigid-body approximation, not deformable ceramic, a textured scanned asset or a claim of photorealism.

Scene validation counts generated collision pieces separately from object count and caps them at 20,000. Static overlap checks retain empty cavities while conservatively bounding curved facets; native compilation, contact and settling checks still apply. Task-bound profiles, attached geometry and cavity bindings are protected during repair, just like the existing rubric targets.
