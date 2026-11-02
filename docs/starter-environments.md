# Starter environments

Choose **Browse starter environments → Add to workspace** in the workbench.
Four complete source layouts are included: a small warehouse, furnished house,
hospital and bookstore. Adding one verifies and unpacks local data. It needs no
model key, network connection, ROS installation or asset conversion. Selecting
the same starter again reopens its saved version.

```sh
robot-worlds starters
robot-worlds add-starter house --output .robot-worlds/worlds
```

```python
from robot_worlds import add_starter

world = add_starter("house", output=".robot-worlds/worlds")
print(world.spec.task)
print(world.manifest["validation"])
```

The scenes come from the [AWS RoboMaker scene collection](https://github.com/aws-robotics).
The packaged catalog pins each repository and commit. Each exported world keeps
the MIT-0 source notice, file hashes and conversion record. The hospital also
retains 43 original OpenRobotics Fuel models under CC BY 4.0, with their author
fields, source URLs and full license. No upstream launch
scripts, plugins or executable dependencies run during import.

These presets retain the original object placement, visual meshes and texture
coordinates. Conversion changes include centimetres to metres, centered layout
coordinates, 1024-pixel texture limits and estimated rough materials. Verified
convex collision solids remain solids. Other static collision surfaces use thin
convex pieces; they preserve openings instead of filling rooms with a hull.
The provenance records the 0.5 mm contact-surface backing and 0.1 mm backing for
planar visuals. Hospital scanned props and curtain hardware use conservative
component envelopes, which can fill concavities and spaces under furniture;
curtain cloth and architectural openings retain their source contact surfaces.
Source-specific ODE contact parameters use the runtime's default
isotropic friction of 0.7.

All imported equipment is fixed. Source doors, appliances and furniture are not
certified manipulation assets. Each scene adds TIAGo++ and a short task: drive
into a marked arrival zone and remain there for one second. Native reset,
settling, rubric and rendering checks establish a loadable starting scene;
they do not establish autonomous task success. Evaluation still checks runtime
compatibility. When the Python or simulation installation differs from the
recorded version, open **Checks → Validate on this computer**. This runs native
checks without model calls and saves an unchanged scene as a new version. A
failed check stays visible and blocks evaluation. Generation and text edits
require model access.

```python
local_world = world.revalidate()
```

The same action is available as `robot-worlds revalidate PATH_TO_WORLD`.

Release preparation runs `tools/starters/prepare.py`, then
`tools/starters/freeze.py` on a Linux worker. The release gate requires successful
native MuJoCo checks and a verified portable export for all four scenes. Customer
selection does not repeat that conversion or native validation.
