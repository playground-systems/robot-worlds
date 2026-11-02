# SimReady manipulation scenes

Three Astra-authored scenes use NVIDIA SimReady furniture and props. Room
architecture and floor paint are the only generated geometry. The older AWS
reference scenes remain separate, static imports.

| Preset ID | Tasks | Setting and interactions |
| --- | --- | --- |
| `simready-kitchen` | 16 | Kitchen counter and dishware: seven sliding mechanisms, a hinged door, hollow bowls, retrieval and counter-to-drawer transfers. |
| `simready-factory` | 15 | Tool preparation, inspection and dispatch: grasping/carrying, drawer pulling/pushing, carton staging and an explicit inspection cycle. |
| `simready-living-room` | 13 | Reading and tidying: book/notebook and ball transfers, two drawers, retrieval and ordered tidying. |

Open a preset from the app's starter cards. In **Task**, choose a named task.
Selection makes a new version with the same objects and initial poses. **Checks**
runs native validation for the selected rubric before evaluation.

```python
from robot_worlds.starter_environments import add_starter

world = add_starter("simready-living-room", output="worlds")
print([(task.id, task.name) for task in world.tasks])
selected = world.with_task("open_upper")
checked = selected.revalidate()  # native reset/render; no model call
assert checked.manifest["validation"]["status"] == "ready"

# Supply your policy through the existing act(observation) contract:
# report = checked.evaluate(policy=my_policy, output="runs")
```

Installing a preset or selecting a task makes no model or network calls. A task
is an instruction plus an executable rubric, not a separate reset configuration.
Exports retain the catalog, source assets and integrity checks. SDK generation
can restrict its prepared catalog with `allowed_asset_ids=[...]` and
`online_assets=False`; missing or excluded sources cannot become substitute props.

## Evidence and limits

Each packaged scene includes its Astra plan, semantic rationale, task catalog,
source references and `preparation-evidence.json`. Strict geometry checks,
native MuJoCo settling/reset, rendering and task-reference/reset checks are
required before packaging. Robot policy completion is **unverified**: these
checks do not prove that every grasp, drawer sweep or task is feasible for TIAGo++.

The [dated acceptance record](evidence/manipulation-presets-2026-11-02.json)
identifies all three frozen worlds. Computer use also verified kitchen task
selection followed by real local native validation; geometry and physical
identity stayed unchanged. [Task selector](images/manipulation-presets/task-selection.png)
and [factory canvas](images/manipulation-presets/factory-app.png) show the app.

The rubrics measure physical holding, sampled carry distance, contact-supported
placement, release, actual joint positions and ordered events. Drawer placements
use their moving support surfaces; closed-cavity regions are checked only as
closed-position containment. The factory's internal inspection is an explicit
three-second process abstraction, not material processing.

The carton is too wide for a one-handed TIAGo++ grasp and is used for staging;
heavy pallets are scenery. Books keep their source rigid shape. There are no
fluids, deformable fabrics, powered conveyors, screw-thread operations or
microwave behavior in these presets. Source mechanisms stay passive.

## Source preparation

Official per-package license files and packaging metadata were checked as
CC BY 4.0. Optimized GLBs are pinned to CodePawl revision
`e52172290393dde80e8d383861db94b8d0e92ec3`; official reference files are pinned
to NVIDIA revision `4763fbfaa4f8e97658e68b57d01b4c86659e08b5`.
The optimized provider does not identify its exact original upstream revision.
That uncertainty is retained in provenance rather than treating the two pins
as an exact derivation chain.

Original visual geometry and materials are retained. The bowl uses source-derived
radial collision wedges to keep its cavity open. Cabinets use source-derived
component proxies and joints. USD's joined-body collision policy is expanded
into precise MuJoCo body-pair exclusions; robot, loose-object and other-drawer
contacts remain enabled. Counter hinge proxies are split along their actual
source profile to avoid filling empty clearance space.

Preparation commands live in `tools/prepare_*_library.py`. Astra's composition
is saved in `tools/acceptance/astra_authored_scene_plans.py`. Revalidate a saved
plan with `tools/acceptance/create_manipulation_presets.py --saved-plan ...
--task-suite ... --semantic-plan ...`; this runs real native checks without
another model request. `freeze_manipulation_preset.py` packages successful
results and produces an aerial native inspection image without policy actions.
