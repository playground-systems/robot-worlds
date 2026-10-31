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

Drive primitives also avoid global-position feedback. TIAGo closes its distance
loop on encoder odometry and its turn loop on relative IMU heading. Tool results
identify `translation_feedback="wheel_odometry"`; `estimated_distance_m` is the
odometric projection along the initial heading, while `encoder_distance_m` is
signed accumulated wheel travel. Slipping wheels can make both differ from actual
displacement, so a completed command does not establish task progress.

G1 has no position estimator in this release. Its `drive(distance, turn)` command
requests nominal travel at 0.25 m/s for `abs(distance)/0.25` seconds, followed by an
IMU-controlled turn. Results identify `translation_feedback="unavailable"` and
`commanded_translation_seconds`; they do not claim a measured linear displacement.
The policy must use RGB-D to assess actual progress. The learned joint controller
still controls balance; contacts and payloads can prevent the requested travel.
These are controller v2 contracts and invalidate earlier runtime validation for
new evaluations. Saved recordings are unchanged.

`query_depth(pixel=[u,v])` samples the full-resolution depth camera and returns a visible surface point in the robot base frame. Pixels start at the image's top-left. It does not identify an object, return its center, or see behind an occluder. `move_arm` accepts the same base frame: X forward, Y left, Z up. G1's origin is its pelvis; TIAGo's is its base link. A measured surface point may need an approach/grasp offset chosen by your policy.

```python
# Actions returned by a policy, not a complete controller:
{"tool": "query_depth", "arguments": {"pixel": [320, 300]}}
{"tool": "move_arm", "arguments": {
    "hand": "right", "position": [0.5, -0.35, 0.65], "approach": "down"
}}
```

Earlier development runs used world-frame arm commands and ideal semantic detections. Those recordings retain their original contracts. New evaluations use the revised manifest and require revalidation of older world bundles; policies written against the earlier observation shape need updating.

## Search before creating assets

Search for an existing object before authoring a new asset. The SDK exposes a bounded,
online Fuel catalog lookup:

```python
from dataclasses import asdict
from robot_worlds import search_online_assets

search = search_online_assets("bowl")
for candidate in search.candidates:
    print(candidate.id, candidate.revision, candidate.license)
    print(candidate.metadata["download_url"])
provenance = asdict(search)  # Retain this with your import/conversion record.
```

This returns versioned candidates and license metadata, not simulation-ready assets.
The search scope is recorded: an empty Fuel result is not proof that no asset exists
elsewhere. Use focused object queries, and query additional catalogs when appropriate.
Search has page, candidate, response-size and elapsed-time limits; hitting one fails
the lookup rather than returning a misleading empty result.

`source_asset(query, import_asset=..., create_asset=..., search=...)` coordinates an
asset pipeline. By default it calls the online lookup above. Supply `search` for a
different provider or offline tests. It tries candidates with your import adapter
before calling your creation adapter. The importer must check license, scale,
appearance and required cavities/joints. Raise `AssetIncompatible` only for an
examined candidate that cannot meet the request, with a specific reason. An
unsupported importer or failed download is an error to resolve, not evidence that
the object needs to be recreated. Creation receives the query, completed search and
rejection records. The returned `SourcedAsset` retains that evidence and the selected
source; save it alongside the resulting asset.

**Integration status:** `generate()` and the workbench now expose a prepared-asset
catalog to the model. Compact references load the [selected bowl and microwave](household-assets.md)
through this coordinator using retained source evidence, without another network
lookup or conversion. General online discovery/download/conversion is not yet
automatic. A restricted MJCF importer supports the microwave's
primitive colliders, textured OBJ appearances and hinged body hierarchy. The bowl
ships with 158 prepared convex collision pieces and its original textured surface;
`import_convex_mesh_asset` imports them without running a converter.

For SDK placement, use the prepared loader:

```python
from robot_worlds import load_prepared_asset, prepared_asset_catalog

catalog = prepared_asset_catalog()  # Dimensions, physical IDs, cavity and joint contracts.
sourced = load_prepared_asset(
    "google-porcelain-bowl", prefix="bowl", position=(1, 0, 0.601), mass=0.6,
)
asset = sourced.asset
# position is bottom-center, not the source file's origin. Retain sourced.search
# and sourced.selected as provenance, and asset.disclosures as world assumptions.
```

The model can use `prepared_assets` in its generation response with `asset_id`,
`id`, `position`, `mass` and optional `joint_initials`. Referenced geometry and
cavity regions are materialized before preview/validation; they must not also
appear in `objects`/`regions`. Catalog IDs use `{prefix}` for the instance ID.
Original dimensions and orientation are preserved. Unsupported rotations/scales,
missing mass, invalid joints and ID collisions must be resolved explicitly.
This compact representation is generation input only: saved worlds contain the
expanded objects and copied files, and load/export never resolves the catalog.
Surrounding layouts remain model-authored. Mocked generation exercises this path;
live model selection and autonomous appliance manipulation are not yet demonstrated.
Refinement and repair use the same catalog through the typed
[`add_prepared_asset` edit operation](scene-editing.md#add-an-existing-source-asset).

For a local MJCF outside the prepared catalog:

```python
from importlib.resources import files
from robot_worlds import import_mjcf_asset

source = files("robot_worlds") / "assets/household/microwave079/model.xml"
asset = import_mjcf_asset(source, prefix="microwave", position=(2, 0, 0.8))
# Add asset.objects and asset.regions to your WorldSpec before World.from_spec().
# Ground rubric references using asset.joint_ids, body_ids and geom_ids.
door_id = asset.joint_ids["microjoint"]
support_id = asset.geom_ids["tray"]
```

This importer accepts local radian MJCF with axis-aligned primitive colliders,
OBJ visual meshes and PNG textures. It retains source body hierarchy and supported
hinge/slide parameters, while rejecting rotated colliders, collision meshes and
unsupported physical features. Source appliance motors and global simulation
settings are excluded. Unspecified masses and material response remain estimates;
torsional/rolling friction use runtime defaults. Conversion is not proof of robot
task success. Retain `asset.disclosures` as world assumptions and `asset.source_files`
as source hash evidence.

Visual meshes have `collision=False` and must be fixed children of physical parts.
Physical pieces may have `visible=False` when their welded assembly has a visible
member. This keeps an original concave surface from becoming a solid collision
hull, while keeping collision pieces out of the rendered image. `texture_path`
uses source OBJ UVs; `source_notice` retains attribution. World freezing hashes and
copies meshes, PNGs, notices and source/conversion metadata, so ZIP exports remain self-contained. Texture files
are bounded to 16 MiB and 16 megapixels.

### Import a prepared concave asset

```python
from robot_worlds import import_convex_mesh_asset

bowl_source = files("robot_worlds") / "assets/household/porcelain_bowl"
bowl = import_convex_mesh_asset(
    bowl_source / "meshes/model.obj",
    bowl_source / "collision/manifest.json",
    prefix="bowl", mass=0.6,  # Caller estimate, not a measured source property.
    source_notice=bowl_source / "ATTRIBUTION.md",
    texture_path=bowl_source / "materials/textures/texture.png",
)
# Add bowl.objects to the scene; place the full assembly above its support.
# Use bowl.body_ids["object"] for compound-body rubric references.
```

The importer verifies up to 256 closed convex pieces, preserves the original visual
mesh and distributes total mass by component volume. Retain its disclosures and
source hashes. A bounded cache reuses geometry verification by content hash;
checksums are still checked on every import. Fresh prefixes and positions produce
independent objects. Prepared assets need no network request or converter at runtime.

## Hollow objects and appliance validation

Define a hollow object as attached physical components, with one movable root for a bowl. Opposing finger contacts on different welded pieces count toward the same body's grasp. Containment includes every fixed attached piece, so a bowl bottom inside the cavity with its rim sticking out fails. `on_top` names a particular support surface, such as the microwave floor; it does not treat the roof as the internal floor.

A cavity region can declare `interior_of` with the appliance's physical root ID. Static checks reject a missing container, a region outside its envelope or a usable volume filled by its box components. The generator is instructed to declare these relationships. This check does not certify a door's swept clearance or curved/mesh cavities. Native reset checks also do not prove the door can be opened by the selected robot.

A mesh with `collision=True` still collides as a convex hull. For a concave source
asset, retain its original surface as an appearance-only mesh and supply verified
collision components separately. The MJCF importer does this for appliances with
existing primitive colliders. The selected Google bowl has a prepared decomposition;
new concave assets still require an offline conversion and native validation.
Autonomous appliance manipulation remains development work.

## Checks without model charges

`tests/test_appliance_generation.py` uses mocked model responses to check repair, cavity geometry and compound-body grading. `tests/test_appliance_physics.py` runs the SDK through generation, native rendering, sensor queries, a no-op evaluation, trace verification, regrading and ZIP round trips. It verifies that observing the bowl does **not** earn a placement success. These are engineering fixtures, not bundled demos or evidence of autonomous task performance.

## Curved hollow geometry

When asset sourcing has not found a suitable existing model, `kind="revolved"` can represent a rotationally symmetric object such as a bowl, cup or funnel. The following is an engineering example of that fallback. Its `revolved.profile` is a closed polygon describing **material**, including the inner wall and bottom, rather than the outside silhouette alone. Coordinates are normalized `(radius, height)` pairs: radius spans 0–0.5 and height spans −0.5–0.5. The last point connects to the first automatically. `size=[diameter, diameter, height]` sets dimensions in metres.

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
