"""A real local MuJoCo bundle/reset/evaluate/report smoke path."""
from robot_worlds.artifacts import read_json
from robot_worlds.models import WorldSpec
from robot_worlds.sdk import World


def test_real_mujoco_world_and_noop_report(tmp_path):
    spec = WorldSpec.model_validate({
        "name": "Physical SDK smoke", "description": "An internal test fixture",
        "task": "Move the part to the output region", "bounds": [8, 8, 3],
        "objects": [{"id": "part", "label": "part", "kind": "box", "position": [2, 0, .1], "size": [.2, .2, .2], "movable": True}],
        "regions": [{"id": "output", "label": "output", "position": [-2, 0, .2], "size": [1, 1, .4]}],
        "rubric": [{"id": "place", "description": "Part at output", "predicate": "in_region", "object_id": "part", "target_id": "output"}],
    })
    world = World.from_spec(spec, tmp_path / "worlds")
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    assert (world.path / "preview.png").read_bytes().startswith(b"\x89PNG")
    class FinishImmediately:
        def reset(self, context):
            assert "rubric" not in context
        def act(self, observation):
            assert "objects" not in observation
            return {"tool": "finish", "arguments": {}}
    report = world.evaluate(FinishImmediately(), output=tmp_path / "runs", render=True)
    assert report.summary["valid"] == 1
    assert report.summary["successes"] == 0
    episode = report.result["episodes"][0]
    assert episode["runtime"]["engine"] == "mujoco"
    assert episode["rubric"]["status"] == "failed"
    assert read_json(report.path / "episode-000" / "trace.json")[0]["time"] == 0
    assert (report.path / "episode-000" / "final.png").is_file()
    assert (report.path / "report.html").is_file()
