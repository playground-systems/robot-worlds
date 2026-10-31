"""Hosted generation timing with a mocked model and real native validation.

This measures software overhead for a pinned 181-component fixture, not model
latency, arbitrary scene quality or autonomous task success. Do not run on a
resource-constrained laptop: compilation/rendering use native simulation.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_prepared_assets import prepared_plan  # noqa: E402

from robot_worlds import generate  # noqa: E402
from robot_worlds.artifacts import read_json  # noqa: E402
from robot_worlds.runtime import Runtime  # noqa: E402


class FixtureModel:
    def complete_json(self, *args):
        return prepared_plan()


def main():
    timings = {}

    class TimedRuntime(Runtime):
        def __init__(self, *args, **kwargs):
            start = time.perf_counter()
            super().__init__(*args, **kwargs)
            timings["initialization_seconds"] = time.perf_counter() - start

        def _build_xml(self):
            start = time.perf_counter()
            result = super()._build_xml()
            timings["scene_xml_seconds"] = time.perf_counter() - start
            return result

        def _reset(self):
            start = time.perf_counter()
            result = super()._reset()
            timings["settling_seconds"] = time.perf_counter() - start
            return result

        def render(self, *args, **kwargs):
            start = time.perf_counter()
            result = super().render(*args, **kwargs)
            timings["first_render_seconds"] = time.perf_counter() - start
            return result

    with tempfile.TemporaryDirectory(prefix="robot-worlds-generation-timing-") as directory:
        world = generate("Put the porcelain bowl inside the microwave", client=FixtureModel(),
                         max_repairs=0, output=directory, runtime_factory=TimedRuntime)
        assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
        record = read_json(next(Path(directory).glob("generation-*.json")))
        phases = [{key: event[key] for key in ("phase", "attempt", "duration_seconds", "status")}
                  for event in record["events"] if event["type"] == "generation_phase_finished"]
        preview = next(event for event in record["events"] if event["type"] == "world_preview")
        print(json.dumps({"fixture": "prepared bowl and microwave", "model": "mocked; no API request",
                          "policy_executed": False, "components": len(world.spec.objects),
                          "preview_seconds": preview["elapsed_seconds"],
                          "ready_seconds": record["elapsed_seconds"], "phases": phases, "native": timings}, indent=2))


if __name__ == "__main__":
    main()
