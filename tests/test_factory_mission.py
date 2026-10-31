"""A native-physics engineering fixture, not a bundled onboarding controller.

The scripted test uses fixture coordinates and measured simulator position when
reacquiring its workpiece. It validates controllers/evaluators, not an LLM policy.
"""

from robot_worlds.models import (
    Criterion,
    ProcessCycle,
    ProcessPrerequisite,
    SceneObject,
    WorldSpec,
)
from robot_worlds.rubric import grade
from robot_worlds.runtime import Runtime
from robot_worlds.sensors import local_point


def mission_spec():
    objects = [
        SceneObject(
            id=name,
            label=name + " bench",
            kind="box",
            position=(x, -0.4, 0.56),
            size=(0.5, 0.3, 0.06),
        )
        for name, x in [("source", 0.55), ("machine", -1.45), ("finish", -3.45)]
    ]
    objects.append(
        SceneObject(
            id="part",
            label="workpiece",
            kind="box",
            position=(0.55, -0.4, 0.625),
            size=(0.045, 0.045, 0.06),
            mass=0.03,
            movable=True,
        )
    )
    return WorldSpec(
        name="Three-station engineering fixture",
        description="Internal physical integration test",
        task="Pick the workpiece, inspect it on the machine, and deliver it to the finish bench",
        bounds=(12, 6, 3),
        objects=objects,
        process_cycles=[
            ProcessCycle(
                id="inspection",
                label="timed inspection",
                fixture_id="machine",
                workpiece_ids=["part"],
                duration=2,
                output_state="inspected",
                prerequisites=[
                    ProcessPrerequisite(predicate="on_top", object_id="part", target_id="machine")
                ],
            )
        ],
        rubric=[
            Criterion(
                id="picked",
                description="Pick the workpiece",
                predicate="holding",
                object_id="part",
                duration=0.5,
                mode="eventually",
            ),
            Criterion(
                id="inspected",
                description="Complete loaded inspection",
                predicate="process_complete",
                object_id="part",
                target_id="inspection",
                after=["picked"],
                mode="eventually",
            ),
            Criterion(
                id="delivered",
                description="Deliver and stably place the same workpiece",
                predicate="on_top",
                object_id="part",
                target_id="finish",
                duration=1,
                after=["inspected"],
            ),
        ],
    )


def test_three_station_grasp_carry_process_delivery():
    spec = mission_spec()
    runtime = Runtime(spec, render=False, max_sim_seconds=150)

    def execute(tool, **arguments):
        if tool == "move_arm":
            arguments["position"] = local_point(arguments["position"], *runtime._base_transform())
        result = runtime.execute({"tool": tool, "arguments": arguments})
        assert result["status"] in ("executed", "reached"), result
        return result

    def pickup(x):
        execute("gripper", hand="right", opening=0.045)
        execute("move_arm", hand="right", position=[x, -0.4, 0.78], approach="down")
        # Explicit oracle access is confined to this engineering test policy.
        position = runtime.snapshot()["objects"]["part"]["position"]
        execute("move_arm", hand="right", position=position, approach="down")
        execute("gripper", hand="right", opening=0)
        assert runtime.snapshot()["objects"]["part"]["held_by"] == "right"
        execute("move_arm", hand="right", position=[x, -0.4, 0.78], approach="down")
        assert runtime.snapshot()["objects"]["part"]["held_by"] == "right"

    def place(x):
        execute("move_arm", hand="right", position=[x, -0.4, 0.625], approach="down")
        execute("gripper", hand="right", opening=0.045)
        execute("move_arm", hand="right", position=[x, -0.4, 0.78], approach="down")
        execute("wait", seconds=3)

    try:
        assert not grade(spec, runtime.trace)["success"]
        pickup(0.55)
        execute("drive", distance=-2, turn=0)
        assert runtime.snapshot()["objects"]["part"]["held_by"] == "right"
        place(-1.45)
        assert runtime.snapshot()["objects"]["part"]["process_states"] == {
            "inspection": "inspected"
        }
        pickup(-1.45)
        execute("drive", distance=-2, turn=0)
        assert runtime.snapshot()["objects"]["part"]["held_by"] == "right"
        place(-3.45)
        report = grade(spec, runtime.trace)
        assert report["success"], report
        assert len(runtime.trace) > 1000
        final = runtime.snapshot()["objects"]["part"]
        assert final["held_by"] is None
        assert "finish" in final["contacts"]
        assert final["process_states"] == {"inspection": "inspected"}
        assert runtime.model.neq == 0
    finally:
        runtime.close()
