"""Native contact counterexample for released loading; no state injection."""

from test_physics import action, block_and_support, world

from robot_worlds.models import ProcessCycle, ProcessPrerequisite
from robot_worlds.runtime import Runtime


def test_supported_but_grasped_workpiece_cannot_complete_released_process():
    spec = world(block_and_support())
    spec.process_cycles = [ProcessCycle(
        id="inspection", label="Released loading test", fixture_id="table",
        workpiece_ids=["block"], duration=30, output_state="inspected",
        prerequisites=[
            ProcessPrerequisite(predicate="on_top", object_id="block", target_id="table"),
            ProcessPrerequisite(predicate="released", object_id="block"),
        ],
    )]
    runtime = Runtime(spec, render=False, max_sim_seconds=90)
    try:
        for point in ([.55, -.4, .78], [.55, -.4, .625]):
            result = action(runtime, "move_arm", hand="right", position=point, approach="down")
            assert result["status"] == "reached", result
        assert action(runtime, "gripper", hand="right", opening=0)["contact_detected"]
        # Closing can lift it slightly; press down with joint servos until the
        # workpiece contacts its support again. No object pose is assigned.
        result = action(runtime, "move_arm", hand="right", position=[.55, -.4, .615], approach="down")
        assert result["status"] == "reached", result
        # Support alone must not let this cycle advance while the fingers hold it.
        for _ in range(7):
            action(runtime, "wait", seconds=5)
            state = runtime.snapshot()["objects"]["block"]
            assert state["held_by"] == "right"
            assert "table" in state["contacts"]
            assert "inspection" not in state["process_states"]
        action(runtime, "gripper", hand="right", opening=.045)
        action(runtime, "move_arm", hand="right", position=[.55, -.4, .78], approach="down")
        for _ in range(7):
            action(runtime, "wait", seconds=5)
        state = runtime.snapshot()["objects"]["block"]
        assert state["held_by"] is None
        assert "table" in state["contacts"]
        assert state["process_states"]["inspection"] == "inspected"
        assert runtime.model.neq == 0
    finally:
        runtime.close()
