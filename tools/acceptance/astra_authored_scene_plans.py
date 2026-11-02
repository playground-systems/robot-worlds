"""Astra-authored scene composition and tasks, 2026-11-02.

Executed directly by the Astra Codex subagent, not a replay of an SDK model call.
Only data authoring and static validation; no physics or robot policy execution.
"""

import json
from pathlib import Path

from robot_worlds.asset_library import AssetLibrary
from robot_worlds.generator import _source_only_layout, validate_spec
from robot_worlds.large_scene import expand_assemblies
from robot_worlds.prepared_assets import (
    GenerationSpec,
    _catalog_entry,
    load_prepared_asset,
    materialize_prepared_assets,
)
from robot_worlds.task_catalog import TaskCatalog

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".robot-worlds/manipulation-presets-2026-11-02/astra-authored"
LIVING = "living-room-library-v2"
CAB = "fixture_cabinet_b01"
NOTE = "misc_notebook_a01"
BOOK = "book_open_a01"
BALL = "misc_golfBall_a01"
TABLE = "furniture_table_coffee_low_a01"
BENCH = "table_packing_heavyduty_c01"
TOOL = "handTool_screwdriver_phillips_ph2_a01"
COUNTER = "fixture_counter_a01"
BOWL = "kitchenware_bowl_b01"


def library(scene, selections):
    dest = OUT / scene / "asset-library"
    if dest.exists():
        return AssetLibrary.open(dest)
    result = AssetLibrary()
    for folder, names in selections:
        src = AssetLibrary.open(ROOT / ".robot-worlds" / folder)
        for name in names:
            if name not in result.asset_ids:
                result.add(name, src.load(name, prefix="asset"), description=src.description(name))
    result.save(dest)
    return AssetLibrary.open(dest)


def criterion(id, predicate, obj=None, target=None, after=(), mode="final", duration=0.3, **kw):
    return dict(
        id=id,
        description=id.replace("_", " "),
        predicate=predicate,
        object_id=obj,
        target_id=target,
        after=list(after),
        mode=mode,
        duration=duration,
        **kw,
    )


def task(id, name, instruction, rubric):
    return dict(id=id, name=name, task=instruction, rubric=rubric)


def transfer(id, name, obj, support, instruction, carry=0.3, region=None, after=(), prefix=""):
    h = prefix + "grasp"
    c = prefix + "carry"
    s = prefix + "support"
    r = prefix + "release"
    # Carrying already proves uninterrupted sampled holding. A zero-duration
    # grasp witness avoids imposing an artificial pause before motion. Support
    # and release may begin in the same sample after the completed carry.
    rub = [
        criterion(h, "holding", obj, after=after, mode="eventually", duration=0),
        criterion(
            c, "carried_distance", obj, threshold=carry, after=[h], mode="eventually", duration=0
        ),
        criterion(s, "on_top", obj, support, after=[c], tolerance=0.012, duration=0.25),
        criterion(r, "released", obj, after=[c], duration=0.4),
    ]
    if region:
        rub.append(
            criterion(prefix + "contained", "in_region", obj, region, tolerance=0.003, duration=0.4)
        )
    return task(id, name, instruction, rub)


def drawer_task(id, name, joint, instruction, close=False, open_value=0.40):
    rub = [
        criterion(
            "pulled_open",
            "joint_at",
            joint,
            threshold=open_value,
            tolerance=0.025,
            mode="eventually" if close else "final",
            duration=0.25,
        )
    ]
    if close:
        rub.append(
            criterion(
                "pushed_closed",
                "joint_at",
                joint,
                threshold=0,
                tolerance=0.015,
                after=["pulled_open"],
                duration=0.4,
            )
        )
    return task(id, name, instruction, rub)


def store_task(id, name, obj, joint, support, region, instruction, open_value=0.40):
    rub = [
        criterion(
            "pulled_open",
            "joint_at",
            joint,
            threshold=open_value,
            tolerance=0.025,
            mode="eventually",
            duration=0.25,
        )
    ]
    rub += transfer(id, name, obj, support, instruction, carry=0.3, after=["pulled_open"])["rubric"]
    rub.append(
        criterion(
            "pushed_closed",
            "joint_at",
            joint,
            threshold=0,
            tolerance=0.015,
            after=["release"],
            duration=0.4,
        )
    )
    rub.append(
        criterion(
            "closed_cavity_containment", "in_region", obj, region, tolerance=0.003, duration=0.4
        )
    )
    return task(id, name, instruction, rub)


def architecture(w, d, h=3.2, door=1.4):
    def box(id, pos, size, color):
        return dict(
            id=id, label=id.replace("_", " "), kind="box", position=pos, size=size, color=color
        )

    # All bounds are inside the world; floor top is Z=0.
    # Floor omitted: runtime creates the actual ground plane at Z=0.
    side = (w - door) / 2
    return [
        box("west_wall", [-w / 2 + 0.06, 0, h / 2], [0.12, d - 0.24, h], [0.85, 0.83, 0.77]),
        box("east_wall", [w / 2 - 0.06, 0, h / 2], [0.12, d - 0.24, h], [0.85, 0.83, 0.77]),
        box("north_wall", [0, d / 2 - 0.06, h / 2], [w - 0.24, 0.12, h], [0.90, 0.88, 0.82]),
        box(
            "entry_wall_left",
            [-door / 2 - side / 2, -d / 2 + 0.06, h / 2],
            [side, 0.12, h],
            [0.90, 0.88, 0.82],
        ),
        box(
            "entry_wall_right",
            [door / 2 + side / 2, -d / 2 + 0.06, h / 2],
            [side, 0.12, h],
            [0.90, 0.88, 0.82],
        ),
        box(
            "entry_lintel",
            [0, -d / 2 + 0.06, (h + 2.3) / 2],
            [door, 0.12, h - 2.3],
            [0.90, 0.88, 0.82],
        ),
    ]


class Scene:
    def __init__(self, key, lib, bounds, name, description, start):
        self.key = key
        self.lib = lib
        self.cat = {
            a: _catalog_entry(
                a, load_prepared_asset(a, prefix="asset", asset_library=lib), lib.description(a)
            )
            for a in lib.asset_ids
        }
        self.instances = {}
        self.tasks = []
        self.plan = dict(
            name=name,
            description=description,
            robot="tiago++",
            bounds=bounds,
            robot_start=start,
            objects=architecture(*bounds),
            prepared_assets=[],
            regions=[],
            process_cycles=[],
            source_prompt=(ROOT / f"tools/acceptance/prompts/manipulation-{key}.txt").read_text(),
            assumptions=[
                "Spatial composition and every task were authored by the Astra model running as a Codex subagent on 2026-11-02. This is direct model-authored saved planning data, not a completed paid SDK generation call.",
                "All furniture and loose objects use the selected NVIDIA SimReady prepared sources at original scale; source meshes, materials, masses and articulation are retained. Only room architecture and optional floor paint are new primitives.",
                "Initial joint values are shared by every selectable task. Task selection changes instruction and rubric only.",
                "Holding means bilateral physical finger contact. Carry distance is net XY displacement during uninterrupted sampled holding, sampled at 0.05 seconds and action boundaries; unseen intervening motion is not certified.",
                "Static geometry and task-reference checks do not prove native contact stability, arm reachability, successful grasping or autonomous task execution. Native validation remains a separate step.",
                "Placement regions extend 5 mm below the support contact plane and separately require measured on_top contact and release. Joint closure tolerance is 15 mm; this is not permission for an item to protrude from the closed cavity.",
                "Books are rigid source shapes; pages do not turn. Drawers are passive source mechanisms. No new appliance, deformable, liquid or screw-thread behavior is claimed.",
            ],
        )

    def add(self, prefix, asset, pos, q=0, initials=None):
        inst = dict(id=prefix, asset_id=asset, position=list(pos), quarter_turns=q)
        if initials:
            inst["joint_initials"] = initials
        self.plan["prepared_assets"].append(inst)
        self.instances[prefix] = inst
        return prefix

    def ref(self, prefix, name=None, group="geom_ids"):
        cat = self.cat[self.instances[prefix]["asset_id"]]
        mapping = cat[group]
        value = mapping[name] if name is not None else next(iter(cat["body_ids"].values()))
        return value.replace("{prefix}", prefix)

    def local(self, prefix, xyz):
        a = self.instances[prefix]
        x, y, z = xyz
        for _ in range(a["quarter_turns"]):
            x, y = -y, x
        return [x + a["position"][0], y + a["position"][1], z + a["position"][2]]

    def geom(self, prefix, name):
        c = self.cat[self.instances[prefix]["asset_id"]]
        g = c["reference_geometry"][c["geom_ids"][name]]
        p = self.local(prefix, g["position"])
        sz = list(g["size"])
        if self.instances[prefix]["quarter_turns"] % 2:
            sz[0], sz[1] = sz[1], sz[0]
        return p, sz

    def top(self, prefix, name):
        p, s = self.geom(prefix, name)
        return p[2] + s[2] / 2

    def region(self, id, label, pos, size, interior=None):
        r = dict(id=id, label=label, position=pos, size=size)
        if interior:
            r["interior_of"] = interior
        self.plan["regions"].append(r)
        return id

    def area(self, id, label, prefix, surface, xy, size=(0.36, 0.38), height=0.16):
        z = self.top(prefix, surface)
        return self.region(id, label, [*xy, z + (height - 0.005) / 2], [*size, height + 0.005])

    def cavity(self, id, prefix, localcenter, size):
        if self.instances[prefix]["quarter_turns"] % 2:
            size = [size[1], size[0], size[2]]
        return self.region(
            id,
            "Fully contained in the closed drawer",
            self.local(prefix, localcenter),
            size,
            self.ref(prefix),
        )

    def finish(self, semantic):
        self.plan["task"] = self.tasks[0]["task"]
        self.plan["rubric"] = self.tasks[0]["rubric"]
        p = GenerationSpec.model_validate(self.plan)
        _source_only_layout(p)
        world = expand_assemblies(
            materialize_prepared_assets(p, max_objects=5000, asset_library=self.lib),
            max_objects=5000,
        )
        catalog = TaskCatalog.model_validate(
            dict(tasks=self.tasks, selected_task_id=self.tasks[0]["id"])
        )
        catalog.validate_for(world)
        errors = validate_spec(world, validation_profile="strict")
        dest = OUT / self.key
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "generation-plan.json").write_text(p.model_dump_json(indent=2) + "\n")
        (dest / "task-suite.json").write_text(catalog.model_dump_json(indent=2) + "\n")
        (dest / "semantic-plan.md").write_text(semantic + "\n")
        (dest / "static-validation.json").write_text(
            json.dumps(
                dict(
                    checks=[
                        "GenerationSpec schema",
                        "source-only architecture",
                        "materialization",
                        "assembly expansion",
                        "TaskCatalog.validate_for",
                        "validate_spec strict",
                    ],
                    objects=len(world.objects),
                    tasks=len(self.tasks),
                    errors=errors,
                ),
                indent=2,
            )
            + "\n"
        )
        print(self.key, len(world.objects), "objects", len(self.tasks), "tasks", json.dumps(errors))
        return errors


def living():
    s = Scene(
        "living-room",
        library("living-room", [(LIVING, ["sofa_soft_b02", TABLE, CAB, NOTE, BOOK, BALL])]),
        [5, 4.6, 3.2],
        "Reading and putting things away",
        "A comfortable west-side seating group and a north-wall household cabinet, with clear east-side access.",
        [0, -1.4, 1.57],
    )
    s.add("sofa", "sofa_soft_b02", [-1.95, -0.3, 0])
    s.add("coffee", TABLE, [-0.85, -0.3, 0])
    s.add("cabinet", CAB, [0.85, 1.80, 0], 3)
    z = s.top("coffee", "tabletop") + 0.002
    cz = s.top("cabinet", "cabinet_top") + 0.002
    s.add("reading_note", NOTE, [-0.72, -0.7, z])
    s.add("reading_book", BOOK, [-0.73, 0.1, z])
    s.add("table_ball", BALL, [-0.70, -0.3, z])
    s.add("cabinet_book", BOOK, [0.65, 1.68, cz], 1)
    s.add("cabinet_ball", BALL, [1.07, 1.66, cz])
    s.add(
        "stored_note",
        NOTE,
        s.local("cabinet", [0.04, 0, s.top("cabinet", "drawer2_floor") + 0.002]),
        1,
    )
    # Since cabinet Z=0, local Z above is exactly the drawer-floor world height.
    upper = s.cavity("upper_closed_cavity", "cabinet", [-0.02, 0, 0.801], [0.48, 0.60, 0.116])
    s.cavity("lower_closed_cavity", "cabinet", [-0.02, 0, 0.658], [0.48, 0.60, 0.116])
    coffee = s.ref("coffee", "tabletop")
    ct = s.ref("cabinet", "cabinet_top")
    up = s.ref("cabinet", "drawer1_floor")
    lo = s.ref("cabinet", "drawer2_floor")
    n, b, ball, cb, bb, stored = [
        s.ref(p)
        for p in [
            "reading_note",
            "reading_book",
            "table_ball",
            "cabinet_book",
            "cabinet_ball",
            "stored_note",
        ]
    ]
    s.area(
        "reading_delivery",
        "Cleared south reading position",
        "coffee",
        "tabletop",
        [-0.87, -0.75],
        (0.40, 0.28),
    )
    s.area(
        "cabinet_delivery",
        "Clear rear cabinet top",
        "cabinet",
        "cabinet_top",
        [0.89, 1.94],
        (0.42, 0.25),
    )
    s.tasks.append(
        store_task(
            "tidy_note_close",
            "Put the reading notebook away",
            n,
            up,
            up,
            upper,
            "Pull open the upper cabinet drawer, pick up the loose notebook beside the ball on the coffee table, carry it to the cabinet, lay it fully inside the upper drawer, let go, and push the drawer shut.",
        )
    )
    s.tasks.append(
        transfer(
            "note_lift_return",
            "Lift and return the notebook",
            n,
            coffee,
            "Grasp the loose coffee-table notebook by its thin edge, lift and carry it a short distance, then return it flat to the coffee table and let go.",
            carry=0.15,
        )
    )
    s.tasks.append(
        transfer(
            "book_to_cabinet",
            "Put the open reading book on the cabinet",
            b,
            ct,
            "Pick up the open book from the coffee table, carry it to a clear part of the cabinet top and set it down without changing its open shape.",
        )
    )
    s.tasks.append(
        transfer(
            "book_to_reading",
            "Bring the cabinet book to the reading area",
            cb,
            coffee,
            "Carry the open book from the cabinet top to a free position on the coffee table and release it.",
        )
    )
    s.tasks.append(
        transfer(
            "ball_to_cabinet",
            "Collect the coffee-table golf ball",
            ball,
            ct,
            "Pick up the golf ball on the coffee table and put it on a clear part of the cabinet top.",
        )
    )
    s.tasks.append(
        transfer(
            "ball_to_reading",
            "Bring the cabinet golf ball over",
            bb,
            coffee,
            "Pick up the golf ball from the cabinet top and carry it to a clear position on the coffee table, then let it settle.",
        )
    )
    for name, j in [("upper", up), ("lower", lo)]:
        s.tasks.append(
            drawer_task(
                "open_" + name,
                "Pull open the " + name + " drawer",
                j,
                "Pull the " + name + " drawer outward by its handle and leave it open.",
            )
        )
        s.tasks.append(
            drawer_task(
                "cycle_" + name,
                "Open and push shut the " + name + " drawer",
                j,
                "Pull the " + name + " drawer open by its handle, then push it fully shut.",
                close=True,
            )
        )
    fetch = transfer(
        "fetch_stored_note",
        "Fetch the stored notebook",
        stored,
        coffee,
        "Pull open the lower cabinet drawer, take out the notebook, carry it to a free part of the coffee table, and release it.",
        after=["opened"],
    )
    fetch["rubric"].insert(
        0, criterion("opened", "joint_at", lo, threshold=0.40, tolerance=0.025, mode="eventually")
    )
    s.tasks.append(fetch)
    s.tasks.append(
        store_task(
            "put_ball_away",
            "Store the small golf ball",
            ball,
            up,
            up,
            upper,
            "Open the upper drawer, carry the coffee-table golf ball into it, set it on the drawer floor, release it, and push the drawer shut.",
        )
    )
    tidy = store_task(
        "two_item_tidy",
        "Clear the reading area and close the drawer",
        n,
        up,
        up,
        upper,
        "First carry the open book from the coffee table onto the cabinet top. Then open the upper drawer, put the coffee-table notebook inside, release it, and push the drawer shut.",
    )
    bookstage = transfer("x", "x", b, ct, "", prefix="book_")["rubric"]
    tidy["rubric"][0]["after"] = ["book_release"]
    tidy["rubric"] = bookstage + tidy["rubric"]
    s.tasks.append(tidy)
    return s.finish("""# Astra-authored living room

The compact 5 × 4.6 m room replaces the initial oversized 10 × 8 m planning envelope after native visual review; furniture scale and all task identities remain unchanged. This footprint makes the source two-seat sofa and table a believable domestic group.

The west sofa faces east across a 45 cm human leg gap toward the low coffee table and open room. The robot approaches the table from its unobstructed east side, never through the sofa gap. A continuous broad central route connects the 1.4 m south doorway, reading area and north-wall cabinet. The cabinet is rotated 270 degrees so both original drawers pull south; there is over 1.5 m of clear operating space in front. The room is intentionally a reading/play room without invented electronics or industrial furniture.

The source coffee table is 0.60 × 1.20 m and 0.443 m high; all three initial objects are close to its east edge. Two more objects rest on the 0.928 m cabinet top; the sixth, a notebook, rests inside the initially closed lower drawer. Book identities refer to their source open shapes, and the two small balls are golf balls. The upper drawer is empty at reset, enabling the flagship tidy mission. Notebook footprint is 0.229 × 0.170 m and thickness 10 mm, comfortably inside the 0.548 × 0.666 m drawer floor. The score additionally requires full containment inside a conservative fixed closed-cavity volume, measured contact with the actual articulated drawer floor, release, and closure. The moving floor is never replaced by a stationary region.

There are 13 selectable tasks: grasp/return, book and ball carrying, two handle-pulling tasks, two open-then-push-shut tasks, stored-item retrieval and ordered tidying. Each uses the exact same six loose-object reset. Tasks express physical holding, sampled carrying, actual support and release where appropriate. Notebook edge grasping, low-table arm reach, source golf-ball rolling and all native stability remain to be demonstrated. No autonomous execution is claimed.
""")


def kitchen():
    lib = library(
        "kitchen",
        [
            (LIVING, [CAB, NOTE]),
            ("kitchen-counter-library-v3", [COUNTER]),
            ("kitchen-dish-library", [BOWL]),
            ("workbench-library", [BENCH]),
        ],
    )
    s = Scene(
        "kitchen",
        lib,
        [9, 7, 3.2],
        "Dish preparation and kitchen storage",
        "A real rear-wall kitchenette faces an open preparation island; a wooden sideboard provides accessible household storage.",
        [0, -2.5, 1.57],
    )
    s.add("counter", COUNTER, [-0.8, 2.95, 0], 3)
    s.add("sideboard", CAB, [-3.95, 0.45, 0])
    s.add("prep", BENCH, [0.2, -0.45, 0])
    pz = s.top("prep", "work_surface") + 0.002
    cz = s.top("counter", "countertop") + 0.002
    sz = s.top("sideboard", "cabinet_top") + 0.002
    s.add("prep_bowl", BOWL, [-0.38, -0.92, pz])
    s.add("counter_bowl", BOWL, [-1.75, 2.77, cz])
    s.add("serving_bowl", BOWL, [-3.78, 0.55, sz])
    s.add("stored_bowl", BOWL, [-3.87, 0.45, s.top("sideboard", "drawer1_floor") + 0.002])
    s.add("recipe_note", NOTE, [0.73, -0.88, pz], 1)
    prep = s.ref("prep", "work_surface")
    counter = s.ref("counter", "countertop")
    side = s.ref("sideboard", "cabinet_top")
    d7 = s.ref("counter", "drawer7_floor")
    d6 = s.ref("counter", "drawer6_floor")
    su = s.ref("sideboard", "drawer1_floor")
    sl = s.ref("sideboard", "drawer2_floor")
    door = s.ref("counter", "joint_shelf_05_joint_00", "joint_ids")
    pb, cb, sb, stored, note = [
        s.ref(p)
        for p in ["prep_bowl", "counter_bowl", "serving_bowl", "stored_bowl", "recipe_note"]
    ]
    # Source drawer7 internal floor: X [-.188,.271], Y [.821,1.448], top .54979.
    # Conservative closed cavity avoids side/front walls and leaves >.20m headroom.
    cavity = s.cavity(
        "dish_drawer_closed",
        "counter",
        [0.04, 1.135, s.top("counter", "drawer7_floor") + 0.110],
        [0.40, 0.55, 0.23],
    )
    sidecavity = s.cavity(
        "sideboard_lower_closed", "sideboard", [-0.02, 0, 0.658], [0.48, 0.60, 0.116]
    )
    serving = s.area(
        "serving_position",
        "Clear serving position on the island",
        "prep",
        "work_surface",
        [0.25, -0.87],
        (0.34, 0.32),
        0.12,
    )
    s.tasks.append(
        store_task(
            "store_dish_close",
            "Put the preparation bowl away",
            pb,
            d7,
            d7,
            cavity,
            "Pull open the upper deep drawer at the right end of the kitchen counter. Grasp the preparation-island bowl by its rim, carry it over, set it fully inside on the drawer floor, let go, and push the drawer shut.",
            open_value=0.38,
        )
    )
    s.tasks.append(
        transfer(
            "bowl_grasp_return",
            "Lift and return the preparation bowl",
            pb,
            prep,
            "Grasp the preparation-island bowl by its rim, lift and carry it a short distance, then put it back on the island and let go.",
            carry=0.16,
        )
    )
    s.tasks.append(
        transfer(
            "prep_to_counter",
            "Move a bowl to the kitchen counter",
            pb,
            counter,
            "Carry the preparation-island bowl to a clear part of the kitchen counter and release it.",
        )
    )
    s.tasks.append(
        transfer(
            "counter_to_prep",
            "Bring the counter bowl to preparation",
            cb,
            prep,
            "Carry the bowl from the kitchen counter to a free place on the preparation island, then let it settle.",
        )
    )
    s.tasks.append(
        transfer(
            "serve_from_sideboard",
            "Set out a bowl for serving",
            sb,
            prep,
            "Pick up the bowl on top of the wooden sideboard and set it near the front centre of the preparation island.",
            region=serving,
        )
    )
    s.tasks.append(
        transfer(
            "recipe_to_counter",
            "Bring the recipe notebook to the counter",
            note,
            counter,
            "Pick up the recipe notebook by its thin edge and carry it from the island to a clear part of the kitchen counter. Set it down flat and release it.",
        )
    )
    s.tasks.append(
        transfer(
            "recipe_to_sideboard",
            "Put the recipe notebook on the sideboard",
            note,
            side,
            "Carry the recipe notebook from the island to the sideboard top and set it down flat.",
        )
    )
    for name, j in [("upper_deep", d7), ("wide_lower", d6)]:
        label = (
            "upper deep drawer at the right end"
            if name == "upper_deep"
            else "wide lower drawer in the middle"
        )
        s.tasks.append(
            drawer_task(
                "open_" + name,
                "Pull open the " + label,
                j,
                "Pull the " + label + " of the kitchen counter open by its handle.",
                open_value=0.38,
            )
        )
        s.tasks.append(
            drawer_task(
                "cycle_" + name,
                "Open and shut the " + label,
                j,
                "Pull the " + label + " open by its handle, then push it fully shut.",
                close=True,
                open_value=0.38,
            )
        )
    s.tasks.append(
        drawer_task(
            "open_counter_door",
            "Swing open the kitchen cabinet door",
            door,
            "Pull the vertical handle on the kitchen cabinet door and swing the door open.",
            open_value=-1.35,
        )
    )
    s.tasks.append(
        drawer_task(
            "cycle_counter_door",
            "Open and close the kitchen cabinet door",
            door,
            "Swing the kitchen cabinet door open by its vertical handle, then push the door shut.",
            close=True,
            open_value=-1.35,
        )
    )
    fetch = transfer(
        "fetch_stored_bowl",
        "Fetch the bowl from the sideboard drawer",
        stored,
        prep,
        "Pull open the wooden sideboard upper drawer. Grasp the bowl inside by its rim, carry it to the preparation island, and set it down.",
        after=["opened"],
    )
    fetch["rubric"].insert(
        0, criterion("opened", "joint_at", su, threshold=0.40, tolerance=0.025, mode="eventually")
    )
    s.tasks.append(fetch)
    s.tasks.append(
        store_task(
            "store_recipe_close",
            "Put the recipe notebook in the sideboard",
            note,
            sl,
            sl,
            sidecavity,
            "Open the lower sideboard drawer, pick up the recipe notebook from the island, place it fully inside the drawer and let go, then push the drawer shut.",
        )
    )
    multi = store_task(
        "clear_prep_close",
        "Clear the preparation island",
        pb,
        d7,
        d7,
        cavity,
        "First put the recipe notebook on the sideboard top. Then pull open the upper deep kitchen drawer, carry the preparation bowl into it, release the bowl on its floor, and push the drawer shut.",
        open_value=0.38,
    )
    stage = transfer("x", "x", note, side, "", prefix="recipe_")["rubric"]
    multi["rubric"][0]["after"] = ["recipe_release"]
    multi["rubric"] = stage + multi["rubric"]
    s.tasks.append(multi)
    s.plan["assumptions"].extend(
        [
            "Bowls are the original hollow 13.01 cm diameter, 6.00 cm high SimReady bowl source at retained imported mass 1.145 kg. Grasping must use the rim; no full-diameter pinch or liquid behavior is assumed. Native rim-grasp feasibility remains unverified.",
            "The existing packing table is reused only as a secondary preparation island. The full original kitchenette, drawers and hinged door define the kitchen; there is no invented cooker or microwave.",
            "Kitchen cabinet door closing uses 0.015 radians tolerance; slide drawer closing uses 0.015 metres.",
        ]
    )
    return s.finish("""# Astra-authored kitchen

A full 3.02 m original kitchen fixture lines the north wall, rotated 270 degrees so its real drawer fronts and hinged door face south into the room. A wooden sideboard against the west wall opens east. The 0.994 m high source worktable serves as a secondary preparation/serving island; it does not pretend to be an appliance. A 1.4 m south doorway feeds broad routes along both ends of the island. Even with the rear drawers extended, approximately 2 m separates them from the island's north edge; the west sideboard has over 2 m of clear approach space.

Four independent original hollow bowls occupy the island, kitchen counter, sideboard top, and sideboard upper drawer. The source bowls are 0.130 m in diameter and 0.060 m high. The initial stored bowl has over 50 mm headroom in the sideboard drawer. The flagship destination is the empty upper deep drawer at the kitchen's right end: its 0.459 × 0.627 m floor and tall cavity easily accommodate one bowl. All loose items are near accessible front edges; no task requires gripping across a bowl's full diameter. A thin recipe notebook provides a separate household carrying and storage task.

The right kitchen drawer's actual moving floor is the support target. Final containment is measured in a conservative fixed cavity only together with physical closure; it is not an imaginary region following the drawer. The closed cavity has 5 mm floor-contact allowance. Both sideboard and kitchen drawers start closed in every task. Every closing task measures opening first. The hinged kitchen door has its original source hinge and a clear sweep, and its open/close tasks never claim interior shelves behind it are independently accessible while it is shut.

Sixteen tasks cover rim grasp/return, dish transfers, serving placement, recipe delivery, two kitchen drawer mechanisms, a real hinged door, stored-bowl retrieval and multistep clearing. All share exactly the same reset. The 3-second process abstraction belongs only to the factory; no kitchen heating, fluid handling or cooking is invented. Original source dynamics are preserved; collision adapter disclosures remain in materialized assumptions. Static validation does not prove native grasping or execution.
""")


def factory():
    rack = "rack_large_empty_a02"
    pallet = "pallet_a01"
    carton = "box_corrugated_brown_b20"
    guard = "barrier_fence_metal_a02"
    lib = library(
        "factory",
        [
            (LIVING, [CAB, NOTE]),
            ("workbench-library", [BENCH]),
            ("factory-part-library", [TOOL]),
            ("asset-library", [rack, pallet, carton, guard]),
        ],
    )
    s = Scene(
        "factory",
        lib,
        [18, 9, 4.5],
        "Tool preparation, inspection and dispatch",
        "Four connected physical work areas around a compact manual tool-processing cell, with reserve rack storage and a clear loading apron.",
        [0, -3.4, 1.57],
    )
    s.plan["objects"] = architecture(18, 9, 4.5, door=3.2)
    for prefix, pos in [
        ("receiving", [-6, 1.8, 0]),
        ("preparation", [-2.7, 2.8, 0]),
        ("inspection", [0.7, 2.8, 0]),
        ("dispatch", [4.1, 1.8, 0]),
    ]:
        s.add(prefix, BENCH, pos)
    s.add("tool_cabinet", CAB, [-2.7, 5.8, 0], 3)
    s.add("stock_rack", rack, [-8.4, 5.0, 0])
    s.add("receiving_pallet", pallet, [-8.4, 1.8, 0.002])
    s.add("pallet_carton", carton, [-8.4, 1.8, 0.2126])
    s.add("storage_guard", guard, [-6.15, 5.0, 0], 1)
    s.add("pallet_guard", guard, [-8.4, 0.65, 0])
    # Direct source inspection identifies two real lower shelf deck slabs, part61/64,
    # top=.505994067. These inventory cartons are scenery, never rack-root rubric targets.
    s.add("stock_left_carton", carton, [-9.35, 4.55, 0.507995])
    s.add("stock_right_carton", carton, [-7.45, 4.55, 0.507995])
    z = s.top("preparation", "work_surface") + 0.002
    s.add("primary_tool", TOOL, [-3.18, 2.32, z])
    s.add("spare_tool", TOOL, [-2.22, 2.32, z])
    s.add("work_order", NOTE, [-2.70, 2.40, z], 1)
    s.add(
        "stored_tool",
        TOOL,
        s.local("tool_cabinet", [0.06, 0, s.top("tool_cabinet", "drawer1_floor") + 0.002]),
        1,
    )
    s.add("outbound_tool", TOOL, [4.65, 1.32, z])
    s.add("receiving_carton", carton, [-6.48, 1.55, z])
    prep = s.ref("preparation", "work_surface")
    inspect = s.ref("inspection", "work_surface")
    dispatch = s.ref("dispatch", "work_surface")
    receive = s.ref("receiving", "work_surface")
    upper = s.ref("tool_cabinet", "drawer1_floor")
    lower = s.ref("tool_cabinet", "drawer2_floor")
    tool, spare, stored, outbound, note, box = [
        s.ref(p)
        for p in [
            "primary_tool",
            "spare_tool",
            "stored_tool",
            "outbound_tool",
            "work_order",
            "receiving_carton",
        ]
    ]
    inspect_area = s.area(
        "inspection_loading",
        "Tool inspection position on the south edge",
        "inspection",
        "work_surface",
        [0.70, 2.34],
        (0.50, 0.30),
        0.12,
    )
    dispatch_area = s.area(
        "outbound_kit",
        "Clear outbound kit position",
        "dispatch",
        "work_surface",
        [3.73, 1.34],
        (0.50, 0.32),
        0.12,
    )
    push_area = s.area(
        "carton_staging",
        "Right-hand carton staging position",
        "receiving",
        "work_surface",
        [-5.60, 1.55],
        (0.44, 0.72),
        0.38,
    )
    lower_cavity = s.cavity(
        "tool_lower_closed", "tool_cabinet", [-0.02, 0, 0.658], [0.48, 0.60, 0.116]
    )
    s.plan["process_cycles"].append(
        dict(
            id="inspection_cycle",
            label="Three-second inspection dwell",
            fixture_id=inspect,
            workpiece_ids=[tool],
            duration=3.0,
            output_state="inspected",
            prerequisites=[
                dict(predicate="on_top", object_id=tool, target_id=inspect, tolerance=0.012),
                dict(
                    predicate="in_region", object_id=tool, target_id=inspect_area, tolerance=0.003
                ),
                dict(predicate="released", object_id=tool),
            ],
        )
    )
    first = transfer(
        "inspect_dispatch",
        "Inspect and dispatch the preparation tool",
        tool,
        inspect,
        "Pick up the screwdriver at the left end of the preparation table by its handle and carry it to the inspection table. Lay it near the front centre and release it for the three-second inspection dwell. After inspection, pick it up again, carry it to the clear left-hand kit position on the outbound table, and release it.",
        carry=1.0,
        prefix="inspect_",
    )
    for c in first["rubric"]:
        c["mode"] = "eventually"
    first["rubric"].append(
        criterion(
            "inspection_position",
            "in_region",
            tool,
            inspect_area,
            mode="eventually",
            duration=0.3,
            tolerance=0.003,
        )
    )
    first["rubric"].append(
        criterion(
            "inspection_finished",
            "process_complete",
            tool,
            "inspection_cycle",
            value="inspected",
            after=["inspect_release"],
            mode="eventually",
            duration=0,
        )
    )
    first["rubric"] += transfer(
        "x",
        "x",
        tool,
        dispatch,
        "",
        carry=1.0,
        region=dispatch_area,
        after=["inspection_finished"],
        prefix="dispatch_",
    )["rubric"]
    s.tasks.append(first)
    s.tasks.append(
        transfer(
            "grasp_return_tool",
            "Grasp and return the preparation tool",
            tool,
            prep,
            "Grasp the left preparation screwdriver by its narrow handle, lift and carry it a short distance, then return it to the preparation table and release it.",
            carry=0.18,
        )
    )
    s.tasks.append(
        transfer(
            "spare_to_inspection",
            "Deliver the spare tool to inspection",
            spare,
            inspect,
            "Pick up the spare screwdriver at the right end of the preparation table and carry it to a free position on the inspection work surface. Set it down and release it.",
            carry=1.0,
        )
    )
    s.tasks.append(
        transfer(
            "spare_to_dispatch",
            "Deliver a spare tool to the outbound kit",
            spare,
            dispatch,
            "Carry the spare screwdriver from the right end of the preparation table to the clear left-hand kit position on the outbound table and release it.",
            carry=1.0,
            region=dispatch_area,
        )
    )
    s.tasks.append(
        transfer(
            "outbound_return",
            "Return the outbound tool for preparation",
            outbound,
            prep,
            "Pick up the screwdriver at the right end of the outbound table and carry it back to a clear part of the preparation work surface.",
            carry=1.0,
        )
    )
    s.tasks.append(
        transfer(
            "work_order_inspection",
            "Deliver the work order to inspection",
            note,
            inspect,
            "Pick up the work-order notebook from the preparation table by its thin edge, carry it to the inspection table, and set it down flat.",
            carry=1.0,
        )
    )
    s.tasks.append(
        transfer(
            "work_order_dispatch",
            "Deliver the work order to outbound",
            note,
            dispatch,
            "Carry the work-order notebook from the preparation table to a clear part of the outbound work surface and release it.",
            carry=1.0,
        )
    )
    for label, j in [("upper", upper), ("lower", lower)]:
        s.tasks.append(
            drawer_task(
                "open_" + label,
                "Pull open the " + label + " tool drawer",
                j,
                "Pull the " + label + " drawer of the tool cabinet open by its handle.",
            )
        )
        s.tasks.append(
            drawer_task(
                "cycle_" + label,
                "Open and push shut the " + label + " tool drawer",
                j,
                "Pull open the "
                + label
                + " tool-cabinet drawer by its handle, then push it fully shut.",
                close=True,
            )
        )
    fetch = transfer(
        "fetch_stored_tool",
        "Fetch the stored screwdriver",
        stored,
        prep,
        "Open the upper tool-cabinet drawer, pick up the screwdriver inside by its handle, carry it to a free part of the preparation table, and release it.",
        carry=0.7,
        after=["opened"],
    )
    fetch["rubric"].insert(
        0,
        criterion("opened", "joint_at", upper, threshold=0.40, tolerance=0.025, mode="eventually"),
    )
    s.tasks.append(fetch)
    s.tasks.append(
        store_task(
            "store_spare_close",
            "Store the spare screwdriver and close up",
            spare,
            lower,
            lower,
            lower_cavity,
            "Open the lower tool-cabinet drawer, pick up the spare screwdriver from the right side of the preparation table, carry it into the drawer, lay it fully inside, release it, and push the drawer shut.",
        )
    )
    s.tasks.append(
        task(
            "push_carton",
            "Push the receiving carton into staging",
            "Without gripping or lifting the carton, push it sideways across the receiving worktable into the clear right-hand staging area. Leave it resting on the worktable.",
            [
                criterion(
                    "never_grasp_carton",
                    "released",
                    box,
                    mode="always",
                    duration=0,
                    intent="maintain",
                ),
                criterion(
                    "carton_supported",
                    "on_top",
                    box,
                    receive,
                    tolerance=0.012,
                    duration=0.5,
                    intent="maintain",
                ),
                criterion(
                    "carton_staged", "in_region", box, push_area, tolerance=0.003, duration=0.5
                ),
            ],
        )
    )
    inspection = transfer(
        "inspection_only",
        "Load and inspect the preparation tool",
        tool,
        inspect,
        "Carry the left preparation screwdriver to the front centre of the inspection table, lay it down and release it, then leave it there for the three-second inspection dwell.",
        carry=1.0,
        region=inspect_area,
    )
    inspection["rubric"].append(
        criterion(
            "inspection_finished",
            "process_complete",
            tool,
            "inspection_cycle",
            value="inspected",
            after=["release"],
            duration=0,
        )
    )
    s.tasks.append(inspection)
    s.plan["assumptions"].extend(
        [
            "Manual material flow runs receiving/storage to tool preparation to inspection to outbound dispatch. Four real worktables have connected front approaches and clear inter-station aisles; no conveyor or powered transfer is implied.",
            "Inspection is explicitly a three-second bookkeeping process, not optical metrology, damage detection or material transformation. It runs only while the exact primary screwdriver is physically supported in the inspection position and released.",
            "The receiving carton is 0.299 by 0.622 by 0.302 m. It is pushed across its worktable; no one-handed grasp is requested. Its push rubric measures supported destination and absence of measured holding, not a dedicated push-force classification.",
            "The 50.73 kg source pallet is inventory scenery and is never a lift or carrying target. Lower rack shelf inventory sits on verified source deck top Z=.505994m; no rubric targets the rack root as a shelf.",
            "Source screwdriver handle diameter is approximately 34 mm. Tools are separate physical instances and tasks require holding the movable tool root, not its fixed handle appearance component.",
            "The 18 by 9 m envelope includes a loading apron south of the compact workcell. Guarding follows the bulk storage and pallet edge; it is not an invented precision fixture or safety interlock.",
        ]
    )
    # Preserve the manually composed relative workcell and transfer distances,
    # then recenter it in a room sized to the actual stations rather than an
    # oversized empty apron. Regions move with their existing source fixtures.
    for entry in [*s.plan["prepared_assets"], *s.plan["regions"]]:
        entry["position"][0] += 2.0
        entry["position"][1] -= 2.0

    def paint(identifier, position, size, color):
        s.plan["objects"].append(
            dict(
                id=identifier,
                label=identifier.replace("_", " "),
                kind="box",
                position=[*position, 0.001],
                size=[*size, 0.002],
                color=color,
                collision=False,
            )
        )

    # Fine floor outlines identify real work areas; they are not invisible
    # collision geometry, task supports, rendered text or invented fixtures.
    zones = [
        ("receiving", -4.0, -0.2, 2.4, 1.9, [0.70, 0.45, 0.20]),
        ("preparation", -0.7, 0.8, 2.4, 1.9, [0.27, 0.48, 0.66]),
        ("inspection", 2.7, 0.8, 2.4, 1.9, [0.20, 0.58, 0.49]),
        ("dispatch", 6.1, -0.2, 2.4, 1.9, [0.76, 0.64, 0.22]),
        ("storage", -6.4, 3.0, 4.6, 2.6, [0.70, 0.45, 0.20]),
    ]
    for name, x, y, width, depth, color in zones:
        paint(name + "_north_paint", [x, y + depth / 2], [width, 0.035], color)
        paint(name + "_south_paint", [x, y - depth / 2], [width, 0.035], color)
        paint(name + "_west_paint", [x - width / 2, y], [0.035, depth], color)
        paint(name + "_east_paint", [x + width / 2, y], [0.035, depth], color)
    paint("workcell_route_paint", [1.05, -1.65], [10.1, 0.05], [0.72, 0.72, 0.66])
    paint("entry_route_paint", [0, -2.94], [0.05, 2.58], [0.72, 0.72, 0.66])
    s.plan["assumptions"].append(
        "Native visual review prompted an 18 by 9 m footprint instead of the initial "
        "24 by 16 m planning brief. The complete workcell was recentered without "
        "changing relative source poses or transfer distances. Thin noncolliding "
        "floor outlines distinguish actual stations and connect their front approach route."
    )
    return s.finish("""# Astra-authored factory

The 18 × 9 m floor contains a compact four-stage manual workcell, with a 3.2 m loading opening in the south wall and a clear apron leading to its front. Receiving is at the west, beside a pallet and a stock rack. Tool preparation occupies the next table, with the articulated tool cabinet directly behind it. Inspection is east of preparation; dispatch turns slightly south at the outbound end, making a shallow U around the unobstructed operator route. Table centres are 3.4 m apart along the principal transfer direction, so the 1.87 m source tables leave approximately 1.53 m between adjacent ends. Front-edge working positions are connected by broad clear aisles. The cabinet drawer sweep faces south into a 1.4 m clear gap behind preparation.

The room was resized after native visual review to remove unused floor while preserving station separations and longer tool transport. Muted, noncolliding floor outlines distinguish receiving/storage, preparation, inspection and dispatch; a thin route marking links their front approaches to the loading opening.

The source rack stores two cartons on its actual low deck slabs at 0.506 m; its full-height root is never treated as a shelf. A separate carton rests on the heavy pallet, and another sits on the receiving worktable for a physical sliding/pushing task. Source guards mark the bulk-rack and pallet edges without fencing off the robot's useful approaches. No pallet lifting, one-handed carton carrying, powered conveyor, machining or screw-thread physics is requested.

Four independent source screwdrivers are staged at preparation (two), in the upper cabinet drawer (one), and at outbound (one). Their ~34 mm handles are physically narrow enough for a plausible pinch grasp. All tabletop tools are within roughly 0.15 m of an accessible front edge. The work-order notebook sits between the preparation tools without overlapping them. The inspection station is initially empty. All manipulation rubrics refer to movable tool roots for holding and named real tabletop or drawer-floor geometry for support.

The flagship retrieves the left preparation tool, measures at least 1 m of continuous held transport, loads the inspection position, releases it, waits for a disclosed three-second process, regrips it, measures another 1 m carry and leaves it supported and released at the outbound kit position. The process is bookkeeping only and continuously requires actual inspection support, full containment in the loading area and release. The intermediate inspection support/release criteria are historical events; only the outbound state is final, so the task does not require the tool to end on two tables at once.

Fifteen selectable tasks share this exact reset: tool grasp/return, transfers among real stations, work-order delivery, opening and open-then-closing both drawers, stored-tool retrieval, spare-tool storage, a carton push, inspection-only and inspection-plus-dispatch. Static checks do not establish arm reach, successful policies or dynamic stability; those require native probes and operator execution.
""")


if __name__ == "__main__":
    import sys

    for scene in sys.argv[1:] or ["living-room", "kitchen", "factory"]:
        {"living-room": living, "kitchen": kitchen, "factory": factory}[scene]()
