"""Hosted generation timing with a mocked model and real native validation.

This measures software overhead for a pinned 181-component fixture, not model
latency, arbitrary scene quality or autonomous task success. Do not run on a
resource-constrained laptop: compilation/rendering use native simulation.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_prepared_assets import prepared_plan  # noqa: E402

from robot_worlds import generate  # noqa: E402
from robot_worlds.artifacts import read_json  # noqa: E402


class FixtureModel:
    def complete_json(self, *args):
        return prepared_plan()


def main():
    with tempfile.TemporaryDirectory(prefix="robot-worlds-generation-timing-") as directory:
        world = generate("Put the porcelain bowl inside the microwave", client=FixtureModel(),
                         max_repairs=0, output=directory)
        assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
        record = read_json(next(Path(directory).glob("generation-*.json")))
        phases = [{key: event[key] for key in ("phase", "attempt", "duration_seconds", "status")}
                  for event in record["events"] if event["type"] == "generation_phase_finished"]
        preview = next(event for event in record["events"] if event["type"] == "world_preview")
        print(json.dumps({"fixture": "prepared bowl and microwave", "model": "mocked; no API request",
                          "policy_executed": False, "components": len(world.spec.objects),
                          "preview_seconds": preview["elapsed_seconds"],
                          "ready_seconds": record["elapsed_seconds"], "phases": phases}, indent=2))


if __name__ == "__main__":
    main()
