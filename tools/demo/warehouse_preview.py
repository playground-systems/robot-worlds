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


def warehouse_plan(marking_mesh, signs):
    objects, assets = [], []

    def box(identifier, label, position, size, color, collision=True):
        objects.append(dict(id=identifier, label=label, kind="box", position=position,
                            size=size, color=color, collision=collision,
                            parent_id=None if identifier == "north_wall" else "north_wall"))
        if not collision:
            objects[-1].update(kind="mesh", mesh_path=str(marking_mesh))

    # Real openings connect receiving and dispatch to the outside. The high
    # lintels retain an enclosure without putting a wall across either route.
    box("north_wall", "North wall beside dispatch opening", [-2.5, 7.8, 2.5], [13, .16, 5], [.73, .75, .75])
    box("north_right", "Dispatch opening right jamb", [8.05, 7.8, 2.5], [1.9, .16, 5], [.73, .75, .75])
    box("dispatch_lintel", "Dispatch opening lintel", [5.55, 7.8, 4.2], [3.1, .16, 1.6], [.73, .75, .75])
    box("west_wall", "West wall above receiving bay", [-8.9, 1.95, 2.5], [.16, 11.7, 5], [.73, .75, .75])
    box("west_lower", "Receiving opening south jamb", [-8.9, -7.2, 2.5], [.16, 1.2, 5], [.73, .75, .75])
    box("receiving_lintel", "Receiving opening lintel", [-8.9, -5.25, 4.2], [.16, 2.7, 1.6], [.73, .75, .75])
    for i, x in enumerate((-6.5, -2, 2.5, 7.2)):
        box(f"wall_post_{i}", "Wall structural upright", [x, 7.65, 2.5], [.15, .12, 5], [.18, .22, .24])
    for index, (x, y) in enumerate(((-5.8, 0), (-1.3, 0), (-5.8, 4), (-1.3, 4))):
        assets.append(dict(id=f"rack_{index}", asset_id="rack_large_empty_a02", position=[x, y, .003]))
        # Source shelf collision tops plus a 3 mm initial clearance.
        for shelf, height in enumerate((.512, 1.637, 2.388)):
            assets.append(dict(id=f"stock_{index}_{shelf}", asset_id="box_corrugated_brown_b20",
                               position=[x-1.4, y-.4, height], repeat=[5, 2, 1], spacing=[.68, .8, 0]))
    # Stock enters at the west opening; packed goods leave at the north opening.
    pallet_positions = ((-6.4, -5.4), (-3.9, -5.4), (7, -2.5), (4.5, 5.6), (6.3, 5.6))
    for index, (x, y) in enumerate(pallet_positions):
        assets.append(dict(id=f"pallet_{index}", asset_id="pallet_a01", position=[x, y, .003]))
        assets.append(dict(id=f"dispatch_{index}", asset_id="box_corrugated_brown_b20",
                           position=[x-.35, y, .214], repeat=[3, 1, 2], spacing=[.35, 0, .304]))
    # Two aligned passive modules connect a manual packing infeed at the south
    # to a clear unloading area and dispatch staging at the north.
    for index, y in enumerate((0, 2.01)):
        assets.append(dict(id=f"conveyor_{index}", asset_id="conveyor_belt_a05", position=[5.4, y, .003]))
    assets.append(dict(id="barrier", asset_id="barrier_fence_metal_a02", quarter_turns=1,
                       position=[6.5, -.3, .003], repeat=[1, 3, 1], spacing=[0, 1.3, 0]))
    # Flat markings are layout detail, never physical obstacles.
    for index, x in enumerate((1.3, 3.3)):
        box(f"lane_{index}", "Clear main walking aisle", [x, .4, .006], [.055, 13.8, .006], [.95, .70, .12], False)
    for index, y in enumerate((-3.9, -6.9)):
        box(f"receiving_line_{index}", "Receiving staging boundary", [-4.8, y, .006], [7.9, .055, .006], [.95, .70, .12], False)
    for index, x in enumerate((3.6, 7.2)):
        box(f"dispatch_line_{index}", "Dispatch staging boundary", [x, 5.8, .006], [.055, 3.8, .006], [.95, .70, .12], False)
    for label, position, size in (
        ("RECEIVING", [-5.2, -6.45, .014], [3, .55, .005]),
        ("STORAGE", [-3.6, -1.65, .014], [2.8, .55, .005]),
        ("PACKING", [5.4, -2.35, .014], [2.2, .55, .005]),
        ("DISPATCH", [5.4, 4.2, .014], [2.8, .55, .005]),
        ("TO SHIPPING", [5.4, 7.05, .014], [2.8, .5, .005]),
    ):
        box(label.lower().replace(" ", "_"), label+" floor stencil", position, size, [1, 1, 1], False)
        objects[-1]["texture_path"] = str(signs[label])
    return dict(
        name="NVIDIA warehouse · receiving to dispatch", bounds=[18, 16, 6],
        description="Receiving west door → stocked racks → manual packing infeed → passive roller transfer → dispatch pallets → north shipping opening. Deterministic visual preview; no native validation.",
        task="From receiving, follow the clear aisle past storage and packing to the dispatch unloading area beside the end of the roller line.",
        robot_start=[-7.9, -5.2, 0], objects=objects, prepared_assets=assets,
        regions=[dict(id="packing_zone", label="Manual packing infeed", position=[5.4, -1.8, .5], size=[1.5, 1.4, 1]),
                 dict(id="dispatch_zone", label="Manual unloading before pallet staging", position=[5.4, 3.8, .5], size=[1.5, 1.4, 1])],
        rubric=[dict(id="arrive", description="Reach dispatch inspection", predicate="robot_in_region", target_id="dispatch_zone", duration=.5)],
        assumptions=["Deterministic visual preview, not a live model-generated or physically validated environment.",
                     "Material flow is west receiving → storage → south packing infeed → north discharge → dispatch pallets → north opening.",
                     "The roller line is unpowered. Loading, pushing and unloading are manual; pallet transfers change height and are not an automatic conveyor connection.",
                     "East-side guarding separates the roller line from the service aisle and leaves both operator endpoints open. No warehouse throughput or successful robot behavior is claimed."],
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
        "vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1",
        "f 1 3 2", "f 1 4 3", "f 5/1 6/2 7/3", "f 5/1 7/3 8/4", "f 1 2 6", "f 1 6 5",
        "f 2 3 7", "f 2 7 6", "f 3 4 8", "f 3 8 7", "f 4 1 5", "f 4 5 8", ""]))
    from PIL import Image, ImageDraw, ImageFont
    signs = {}
    for text in ("RECEIVING", "STORAGE", "PACKING", "DISPATCH", "TO SHIPPING"):
        path = (args.output / (text.lower().replace(" ", "_")+".png")).resolve()
        image = Image.new("RGB", (768, 160), (36, 54, 63))
        draw = ImageDraw.Draw(image)
        draw.text((384, 80), text, font=ImageFont.load_default(size=76), anchor="mm", fill=(245, 247, 250))
        image.save(path)
        signs[text] = path
    plan = warehouse_plan(marking_mesh, signs)

    class DeterministicPlan:
        def complete_json(self, *args):
            return copy.deepcopy(plan)

    prompt = "Arrange the installed original NVIDIA assets into a coherent receiving → storage → packing → dispatch warehouse. Provide real receiving/shipping openings, a manual infeed and discharge at the passive conveyor ends, reachable pallet staging and side guarding that leaves the ends open. Visual preview only."
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
