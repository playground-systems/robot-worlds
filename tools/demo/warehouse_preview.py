"""Prepare a sourced visual preview without simulation or paid model calls.

The deterministic plan exercises the product's asset expansion and portable-world
pipeline. It is not evidence of live text generation or physical validation.
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

from robot_worlds import World
from robot_worlds.asset_library import AssetLibrary
from robot_worlds.generator import GenerationError, generate_spec


def warehouse_plan(marking_mesh):
    objects, assets = [], []

    def box(identifier, label, position, size, color, collision=True):
        objects.append(dict(id=identifier, label=label, kind="box", position=position,
                            size=size, color=color, collision=collision,
                            parent_id=None if identifier == "north_wall" else "north_wall"))
        if not collision:
            objects[-1].update(kind="mesh", mesh_path=str(marking_mesh))

    box("north_wall", "Warehouse north wall", [0, 7.8, 2.5], [18, .16, 5], [.73, .75, .75])
    box("west_wall", "Warehouse west wall", [-8.9, 0, 2.5], [.16, 15.6, 5], [.73, .75, .75])
    for i, x in enumerate((-6.5, -2, 2.5, 7)):
        box(f"wall_post_{i}", "Wall structural upright", [x, 7.65, 2.5], [.15, .12, 5], [.18, .22, .24])
    for index, (x, y) in enumerate(((-5.8, 0), (-1.3, 0), (-5.8, 4), (-1.3, 4))):
        assets.append(dict(id=f"rack_{index}", asset_id="rack_large_empty_a02", position=[x, y, .003]))
        # Source shelf collision tops plus a 3 mm initial clearance.
        for shelf, height in enumerate((.512, 1.637, 2.388)):
            assets.append(dict(id=f"stock_{index}_{shelf}", asset_id="box_corrugated_brown_b20",
                               position=[x-1.4, y-.4, height], repeat=[5, 2, 1], spacing=[.68, .8, 0]))
    for index, (x, y) in enumerate(((-6, -5.3), (-3.5, -5.3), (-1, -5.3))):
        assets.append(dict(id=f"pallet_{index}", asset_id="pallet_a01", position=[x, y, .003]))
        assets.append(dict(id=f"dispatch_{index}", asset_id="box_corrugated_brown_b20",
                           position=[x-.35, y, .214], repeat=[3, 1, 2], spacing=[.35, 0, .304]))
    for index, y in enumerate((0, 2.05)):
        assets.append(dict(id=f"conveyor_{index}", asset_id="conveyor_belt_a05", position=[5.4, y, .003]))
    assets.append(dict(id="barrier", asset_id="barrier_fence_metal_a02",
                       position=[4.3, 5.5, .003], repeat=[3, 1, 1], spacing=[1.3, 0, 0]))
    # Flat markings are layout detail, never physical obstacles.
    for index, x in enumerate((-8.1, 1.1, 3.5, 7.5)):
        box(f"lane_{index}", "Painted safety lane", [x, .3, .006], [.055, 13.6, .006], [.95, .70, .12], False)
    for index, y in enumerate((-6.9, -3.9)):
        box(f"staging_line_{index}", "Dispatch floor marking", [-3.5, y, .006], [8.9, .055, .006], [.95, .70, .12], False)
    for index, x in enumerate((-7.4, -4.8, -2.2, .4)):
        box(f"bay_line_{index}", "Pallet staging bay", [x, -5.4, .006], [.045, 3, .006], [.84, .86, .87], False)
    return dict(
        name="NVIDIA warehouse · visual preview", bounds=[18, 16, 6],
        description="Original NVIDIA/CodePawl racks, cartons, pallets, barriers and passive roller conveyors. Deterministic layout; no native validation.",
        task="Navigate from receiving to the dispatch inspection zone through the clear aisle.",
        robot_start=[2.3, -6.4, 1.5708], objects=objects, prepared_assets=assets,
        regions=[dict(id="dispatch_zone", label="Dispatch inspection", position=[2.3, -3, .5], size=[1.5, 1.5, 1])],
        rubric=[dict(id="arrive", description="Reach dispatch inspection", predicate="robot_in_region", target_id="dispatch_zone", duration=.5)],
        assumptions=["Deterministic visual preview, not a live model-generated or physically validated environment.",
                     "Conveyors are unpowered; no warehouse throughput or successful robot behavior is claimed."],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--conveyor-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    library = AssetLibrary.open(args.library)
    conveyors = AssetLibrary.open(args.conveyor_library)
    asset_id = "conveyor_belt_a05"
    if asset_id not in library.asset_ids:
        library.add(asset_id, conveyors.load(asset_id, prefix="conveyor"), description=conveyors.description(asset_id))
    # Unit cube for layout paint only; object assets always come from the library.
    args.output.mkdir(parents=True, exist_ok=True)
    marking_mesh = (args.output / "layout-marking.obj").resolve()
    marking_mesh.write_text("\n".join([
        "v -0.5 -0.5 -0.5", "v 0.5 -0.5 -0.5", "v 0.5 0.5 -0.5", "v -0.5 0.5 -0.5",
        "v -0.5 -0.5 0.5", "v 0.5 -0.5 0.5", "v 0.5 0.5 0.5", "v -0.5 0.5 0.5",
        "f 1 3 2", "f 1 4 3", "f 5 6 7", "f 5 7 8", "f 1 2 6", "f 1 6 5",
        "f 2 3 7", "f 2 7 6", "f 3 4 8", "f 3 8 7", "f 4 1 5", "f 4 5 8", ""]))
    plan = warehouse_plan(marking_mesh)

    class DeterministicPlan:
        def complete_json(self, *args):
            return copy.deepcopy(plan)

    prompt = "Arrange the installed original NVIDIA warehouse assets into four stocked racks, a clear aisle, dispatch staging and two passive conveyors. Visual preview only."
    try:
        spec = generate_spec(prompt, client=DeterministicPlan(), asset_library=library,
                             online_assets=False, max_repairs=0, max_objects=5000, scene_profile="large")
    except GenerationError as error:
        raise SystemExit(json.dumps(error.errors, indent=2)) from error
    world = World.from_spec(spec, args.output / "worlds", validate=False)
    record = dict(world_id=world.id, path=str(world.path), components=len(spec.objects),
                  planning="deterministic", physical_validation=False, paid_model_calls=0,
                  seconds=time.monotonic()-started, asset_ids=list(library.asset_ids))
    (args.output / "preview-plan.json").write_text(json.dumps(plan, indent=2)+"\n")
    (args.output / "preview-evidence.json").write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
