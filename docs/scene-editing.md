# Edit and repair a scene

Select a saved world and describe the change. **Edit world** applies a typed patch to existing entity IDs, validates the result and saves a new version. **Repair** uses the recorded validation failures to propose geometric fixes while preserving the mission and physical properties.

```python
from robot_worlds import load

world = load("worlds/my-version")
revised = world.refine(
    "Move inspection_table half a metre east; keep its height and size.",
    scope=["inspection_table"],
)
print(revised.manifest["edit"]["changes"])

repaired = revised.repair(max_repairs=2)
```

In the browser, select an object in **World → Layers** or the 3D canvas. Its ID is attached to the **Agent** prompt automatically; the right inspector shows its read-only properties. The **Changes** tab shows application-computed before/after values, preserved entity counts, validation results and previous versions. Opening a parent version leaves its descendants and their recorded runs intact.

## What stays fixed

- An explicit `scope` limits actual changes to those exact IDs. Without a scope, the model chooses which entities to edit; the application preserves every entity omitted from the patch. This does not independently prove that the model understood the user's intent.
- Task, rubric and process definitions require `allow_task_changes=True`. Existing mass, friction and mechanism definitions require `allow_physics_changes=True`. Both default to false, and repair rejects either permission. Repair also preserves every original object and region ID; deleting a failed object cannot make the repair pass. Protections are checked against the original parent on every retry. A joint's initial pose can change without changing its mechanism.
- The robot preset stays fixed. Existing model/controller versions and previous evaluation artifacts are preserved.
- Repeated assemblies are materialized before editing. Editing one instance's part does not regenerate its neighbors from the template.

The diff is calculated from the resulting specifications, rather than copied from the model's explanation. Edit metadata is included in the new world's content identity. Meshes retain their content hashes and are copied into the new version when referenced.

## Validation and limits

Each candidate goes through the same reference, geometry, connectivity, rubric and native physics checks as generation. Native reset diagnostics record initial penetration and transient object motion as well as final settling state. The report names measured objects and thresholds so a repair can address the failure without weakening the test.

Model calls, time and spending are bounded. Cancellation preserves saved candidates. An unresolved candidate remains unvalidated; a plausible-looking change does not make it runnable. Scene checks do not establish full task solvability, and a patch's semantic correctness still needs inspection.

Large-scene requests use a compact inventory with selected and relevant entity details. Context omissions and resource limits are explicit. A request that cannot fit the model input budget is not silently applied to a truncated world.

## Other interfaces

```bash
robot-worlds refine worlds/my-version "Move inspection_table east by 0.5 m" --scope inspection_table
robot-worlds repair worlds/my-version --max-repairs 2
```

The local API exposes `POST /api/worlds/{id}/refine` and `POST /api/worlds/{id}/repair`. Both return a job ID and accept model budgets plus an optional entity scope. Repair can omit its prompt. Saved results expose `manifest.edit` and the validation evidence through `GET /api/worlds/{id}`.
