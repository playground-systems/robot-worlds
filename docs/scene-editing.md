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
- Task, rubric and process definitions require `allow_task_changes=True`. Existing mass, friction and mechanism definitions require `allow_physics_changes=True`. Both default to false, and repair rejects either permission. Repair also preserves every original object and region ID; deleting a failed object cannot make the repair pass. Protections are checked against the original parent on every retry. A joint's initial pose can change without changing its mechanism. Coherent assembly translation preserves body/parent-relative joint anchors; moving a body without its hinge, or remounting a child relative to its parent, requires physics-change permission.
- Task bindings are protected as well as rubric text. Referenced regions cannot be removed, moved or resized without `allow_task_changes=True`. Bound objects retain their IDs, label, color, shape, dimensions, mesh path and parent relationship. A destination support or process fixture cannot move beside the source to make the task easier. Ancestor poses and joint transforms are protected so moving a parent cannot bypass that rule. These checks also apply to process-loading prerequisites and optional rubric criteria.
- The robot start is a protected task initial condition. Deliberate changes to the start or destination require an explicit task-changing refinement; repair cannot grant that permission. Physical-property changes still require their separate permission.
- The robot preset stays fixed. Existing model/controller versions and previous evaluation artifacts are preserved.
- Repeated assemblies are materialized before editing. Editing one instance's part does not regenerate its neighbors from the template.

The diff is calculated from the resulting specifications, rather than copied from the model's explanation. Edit metadata is included in the new world's content identity. Meshes retain their content hashes and are copied into the new version when referenced.

Direct initial placement of a task subject remains editable so a repair can lift a workpiece out of a surface penetration. Its own initial joint pose can also change when it is not a destination or an ancestor of another task-bound entity. This exception does **not** prove preservation of task difficulty: moving the initial workpiece closer to its destination can shorten the task. Review the measured diff and use an independent task contract when distance or stage separation matters. The internal [ten-scenario suite](../tests/scenarios/README.md) demonstrates such checks. Unreferenced scenery can still be edited; preserving an ID does not establish semantic fidelity to arbitrary prose.

## Add an existing source asset

Refinement and repair receive the same prepared-asset catalog as generation. The
model can return one `add_prepared_asset` operation instead of spelling out every
mesh and collider:

```json
{
  "operations": [{
    "op": "add_prepared_asset",
    "asset": {
      "id": "bowl",
      "asset_id": "google-porcelain-bowl",
      "position": [1, -2, 0.601],
      "mass": 0.6
    }
  }]
}
```

Here `position` is the asset's bottom-center, suitable for a support whose top is
at 0.6 m. The source geometry, texture, collision pieces and provenance are retained;
mass is an explicit estimate. Original size and orientation are preserved. This
uses the previously sourced local asset, without another download or conversion.
General online asset discovery remains separate from the edit path.

Scope and resource limits apply to every expanded object and cavity region. A
prefix is not a wildcard: a scope containing only `bowl` does not authorize all
`bowl_*` components. New assets do not authorize changes to the task, rubric or
existing physics. Conflicts or later failures reject the entire patch and preserve
the parent. Progress reports asset reuse; the computed diff lists actual additions.
Mocked model tests exercise import through native validation and export, without
claiming autonomous policy performance or live model selection.

## Validation and limits

### Move a compound object

Use `translate_assembly` to reposition a whole source asset without spelling out
every component coordinate:

```json
{"operations": [{"op": "translate_assembly", "id": "bowl_body", "offset": [0.2, 0, 0]}]}
```

The operation translates the physical root, all descendants (including jointed
children), their joint anchors and cavity regions whose `interior_of` belongs to
the tree. Shapes, mass, friction, source files and joint settings remain unchanged.
It does not move unrelated supports or unowned destinations. Choose the physical
root; an appearance-only mesh is rejected. This is an edit to a new world's initial
configuration, never a robot action or a teleport during evaluation.

In the inspector, **Include whole object** explicitly adds the selected object's
parent tree and owned regions to the edit scope. A complete compound selection
shows its name and count, with exact IDs under **Inspect included entities**.
Other large scopes show eight IDs and collapse the remainder without excluding them. Scope still limits every actual
change, and task-bound destinations cannot move without task-change permission.
The model can use this operation for a subject placement repair, but the usual
task-difficulty limitations above still apply.

Each candidate goes through the same reference, geometry, connectivity, rubric and native physics checks as generation. Native reset diagnostics record initial penetration and transient object motion as well as final settling state. The report names measured objects and thresholds so a repair can address the failure without weakening the test.

Model calls, time and spending are bounded. Cancellation preserves saved candidates. An unresolved candidate remains unvalidated; a plausible-looking change does not make it runnable. Scene checks do not establish full task solvability, and a patch's semantic correctness still needs inspection.

Large-scene requests use a compact inventory with selected and relevant entity details. Context omissions and resource limits are explicit. A request that cannot fit the model input budget is not silently applied to a truncated world.

## Other interfaces

```bash
robot-worlds refine worlds/my-version "Move inspection_table east by 0.5 m" --scope inspection_table
robot-worlds repair worlds/my-version --max-repairs 2
```

The local API exposes `POST /api/worlds/{id}/refine` and `POST /api/worlds/{id}/repair`. Both return a job ID and accept model budgets plus an optional entity scope. Repair can omit its prompt. Saved results expose `manifest.edit` and the validation evidence through `GET /api/worlds/{id}`.
