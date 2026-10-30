"""Fixed reference assemblies still collide physically with the mobile robot."""

import numpy as np

from robot_worlds.models import Criterion, Region, SceneObject, WorldSpec
from robot_worlds.runtime import Runtime


def test_overlapping_fixed_mesh_assembly_does_not_disable_robot_collision(tmp_path):
    mesh = tmp_path / "cube.obj"
    mesh.write_text("""v -1 -1 -1
v 1 -1 -1
v 1 1 -1
v -1 1 -1
v -1 -1 1
v 1 -1 1
v 1 1 1
v -1 1 1
f 1 3 2
f 1 4 3
f 5 6 7
f 5 7 8
f 1 2 6
f 1 6 5
f 2 3 7
f 2 7 6
f 3 4 8
f 3 8 7
f 4 1 5
f 4 5 8
""")
    spec = WorldSpec(
        name="Fixed assembly contact fixture",
        description="Engineering regression for native imported mesh contacts",
        task="Reach the far side of the wall",
        bounds=(6, 6, 3),
        objects=[
            SceneObject(
                id="wall_a",
                label="fixed mesh",
                kind="mesh",
                position=(0.8, 0, 0.6),
                size=(0.2, 2, 1.2),
                mesh_path="cube.obj",
            ),
            SceneObject(
                id="wall_b",
                label="overlapping fixed mesh",
                kind="mesh",
                position=(0.85, 0, 0.6),
                size=(0.2, 2, 1.2),
                mesh_path="cube.obj",
            ),
        ],
        regions=[Region(id="far", label="far side", position=(2, 0, 0.75), size=(1, 1, 1.5))],
        rubric=[
            Criterion(
                id="reach",
                description="Reach far side",
                predicate="robot_in_region",
                target_id="far",
            )
        ],
    )
    runtime = Runtime(spec, render=False, asset_root=tmp_path)
    try:
        fixed_ids = set(runtime._geom_objects)
        assert all(runtime.model.body_weldid[runtime.model.geom_bodyid[g]] == 0 for g in fixed_ids)
        runtime.execute({"tool": "wait", "arguments": {"seconds": 0.2}})
        assert not any(
            int(c.geom1) in fixed_ids and int(c.geom2) in fixed_ids
            for c in runtime.data.contact[: runtime.data.ncon]
        )
        scene_contacts = []
        sample = runtime._sample

        def record_contacts():
            scene_contacts.extend(
                (int(c.geom1), int(c.geom2))
                for c in runtime.data.contact[: runtime.data.ncon]
                if int(c.geom1) in fixed_ids or int(c.geom2) in fixed_ids
            )
            sample()

        runtime._sample = record_contacts
        result = runtime.execute({"tool": "drive", "arguments": {"distance": 1, "turn": 0}})
        assert result["status"] == "blocked_or_unreached"
        assert runtime.snapshot()["robot"]["position"][0] < 0.55
        assert scene_contacts
        assert np.isfinite(runtime.data.qpos).all()
        assert np.isfinite(runtime.data.qvel).all()
    finally:
        runtime.close()
