"""Offline package acceptance using original assets and an explicitly mocked generator.

Run from any directory with the installed package. This checks the customer SDK
loop and portable evidence, not successful manipulation or model access.
"""
import argparse
import json
import platform
import time
import zipfile
from pathlib import Path

import robot_worlds
from robot_worlds import generate, load
from robot_worlds.sdk import Report


def prepared_plan():
    return dict(name="Sourced appliance task", description="Original assets in a generated layout",
                task="Put the white porcelain bowl in the microwave and leave it resting there.", bounds=[8, 6, 3],
                objects=[dict(id="counter", label="Bowl counter", kind="box", position=[1, 0, .3], size=[.6, .5, .6]),
                         dict(id="oven_counter", label="Microwave counter", kind="box", position=[2, 0, .32715], size=[.85, .7, .6543])],
                prepared_assets=[dict(id="bowl", asset_id="google-porcelain-bowl", mass=.6, position=[1, 0, .601]),
                                 dict(id="oven", asset_id="nvidia-microwave079", position=[2, 0, .6543])],
                rubric=[dict(id="inside", description="Full bowl inside", predicate="in_region", object_id="bowl_body",
                             target_id="oven_interior0", tolerance=.002, duration=.2),
                        dict(id="supported", description="Bowl on tray", predicate="on_top", object_id="bowl_body",
                             target_id="oven_part0", tolerance=.005, duration=.2),
                        dict(id="released", description="Bowl released", predicate="released", object_id="bowl_body", intent="maintain")])

class MockGenerator:
    def complete_json(self, *args):
        return prepared_plan()


class SensorProbe:
    def reset(self, context):
        assert set(context) == {"task", "robot", "action_contract", "sensor_contract"}
        self.step = 0
        self.results = []

    def act(self, observation):
        assert observation["image"].startswith(b"\x89PNG\r\n\x1a\n")
        assert "objects" not in observation and "position" not in observation["robot"]
        self.step += 1
        if self.step == 1:
            depth = observation["sensors"]["depth"]
            samples = [(x, y) for y, row in enumerate(depth["values"])
                       for x, value in enumerate(row) if value is not None and .1 < value < 8]
            assert samples
            x, y = samples[len(samples)//2]
            return {"tool": "query_depth", "arguments": {"pixel": [
                depth["pixel_origin"][0]+x*depth["pixel_stride"],
                depth["pixel_origin"][1]+y*depth["pixel_stride"]]}}
        return [
            {"tool": "drive", "arguments": {"distance": .08, "turn": 0}},
            {"tool": "gripper", "arguments": {"hand": "right", "opening": .035}},
            {"tool": "wait", "arguments": {"seconds": .2}},
            {"tool": "finish", "arguments": {}},
        ][self.step-2]

    def record_result(self, result):
        self.results.append(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    world = generate("Put the original porcelain bowl in the microwave", client=MockGenerator(),
                     max_repairs=0, online_assets=False, output=args.output / "worlds")
    assert world.manifest["validation"]["status"] == "ready"
    probe = SensorProbe()
    report = world.evaluate(probe, max_steps=5, max_sim_seconds=15,
                            output=args.output / "runs", max_replay_frames=8)
    assert report.summary["valid"] == 1 and report.summary["successes"] == 0, report.summary
    assert len(probe.results) == 5
    assert probe.results[0]["status"] == "measured"
    assert probe.results[1]["estimated_distance_m"] > .02
    for path, folder in [(world.export(args.output / "world.zip"), "restored-world"),
                         (report.export(args.output / "report.zip"), "restored-report")]:
        # Only unpack archives generated above, never arbitrary supplied inputs.
        with zipfile.ZipFile(path) as archive:
            archive.extractall(args.output / folder)
    restored = load(args.output / "restored-world")
    saved = Report(args.output / "restored-report")
    assert restored.id == world.id and saved.summary == report.summary
    assert saved.regrade(restored, output=args.output / "regraded").summary == report.summary
    result = {"status": "passed", "scope": "mocked generation, native sensors/actions, report, export/reload and regrade",
              "manipulation_success": False, "model_calls": 0, "platform": platform.platform(),
              "python": platform.python_version(), "package_path": str(Path(robot_worlds.__file__).resolve()),
              "world_id": world.id, "summary": report.summary, "tool_feedback": probe.results,
              "elapsed_seconds": time.monotonic()-started}
    (args.output / "acceptance.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
