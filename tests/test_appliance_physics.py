"""SDK integration with native physics and mocked generation/policy, no API credits."""
import zipfile

from test_appliance_generation import appliance_spec

from robot_worlds import generate, load
from robot_worlds.sdk import _verified_regrade_trace


def test_appliance_sdk_generation_sensor_execution_regrade_and_export(tmp_path, native_rendering):
    class GenerationClient:
        def complete_json(self, *args):
            return appliance_spec().model_dump(mode="json")

    class SensorProbe:
        def reset(self, context):
            assert context["sensor_contract"]["object_labels"] is False
            self.step = 0
            self.results = []

        def act(self, observation):
            assert "visible_objects" not in observation
            assert "position" not in observation["robot"]
            assert observation["image"].startswith(b"\x89PNG")
            assert observation["sensors"]["depth"]["values"]
            commands = [
                {"tool": "query_depth", "arguments": {"pixel": [320, 400]}},
                {"tool": "wait", "arguments": {"seconds": .1}},
                {"tool": "finish", "arguments": {}},
            ]
            action = commands[self.step]
            self.step += 1
            return action

        def record_result(self, result):
            self.results.append(result)

    events = []
    world = generate("Put a bowl in a microwave", client=GenerationClient(), max_repairs=0,
                     output=tmp_path / "worlds", on_event=events.append)
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    assert any(e["type"] == "world_created" for e in events)
    world = load(world.path)
    policy = SensorProbe()
    report = world.evaluate(policy, max_steps=3, max_sim_seconds=2, output=tmp_path / "runs")
    assert report.summary["valid"] == 1
    assert report.summary["successes"] == 0  # Looking at a bowl does not put it in the microwave.
    assert policy.results[0]["status"] == "measured"
    assert policy.results[0]["frame"] == "base"
    result = report.result
    _verified_regrade_trace(report.path / "episode-000/trace.json.gz", result["episodes"][0], result, load(report.path / "world"))
    assert report.regrade(world, output=tmp_path / "regraded").summary["successes"] == 0
    archive = world.export(tmp_path / "world.zip")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(tmp_path / "imported")
    assert load(tmp_path / "imported").id == world.id
    archive = report.export(tmp_path / "report.zip")
    with zipfile.ZipFile(archive) as bundle:
        assert {"result.json", "report.html", "episode-000/trace.json.gz"} <= set(bundle.namelist())
