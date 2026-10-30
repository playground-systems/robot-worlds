"""Remote preview paths and lifecycle without SSH, native physics or model calls."""
import base64
import importlib.util
from pathlib import Path

import pytest

from robot_worlds.artifacts import read_json, write_json

module_spec = importlib.util.spec_from_file_location(
    "live_mirror", Path(__file__).parents[1] / "tools" / "demo" / "live_mirror.py")
module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(module)
LiveMirror = module.LiveMirror


def frame(**changes):
    return {"type": "live_frame", "run_id": "a"*32, "path": "episode-000/live.png",
            "sequence": 0, "time": 1.5, "image_base64": base64.b64encode(b"real-frame").decode(), **changes}


def test_mirror_separates_pixels_from_bounded_events_and_preserves_final_evidence(tmp_path):
    mirror = LiveMirror(tmp_path)
    mirror.event(frame())
    run = tmp_path / "runs" / ("a"*32)
    assert (run / "episode-000/live.png").read_bytes() == b"real-frame"
    assert read_json(run / "result.json")["status"] == "running"
    assert "image_base64" not in read_json(mirror.path)["live_frame"]
    final = {"status": "completed", "summary": {"successes": 0}}
    write_json(run / "result.json", final)
    for step in range(205):
        mirror.event({"type": "step", "step": step, "run_id": "a"*32})
    assert len(read_json(mirror.path)["events"]) == 200
    assert read_json(run / "result.json") == final
    mirror.finish("completed")
    assert read_json(mirror.path)["status"] == "completed"
    assert "success" not in read_json(mirror.path)


@pytest.mark.parametrize("change", [
    {"run_id": "../escape"}, {"path": "../../escape.png"},
    {"path": "episode-000/final.png"}, {"image_base64": "%%%"},
])
def test_mirror_rejects_untrusted_frame_destinations_and_payloads(tmp_path, change):
    mirror = LiveMirror(tmp_path)
    with pytest.raises(ValueError):
        mirror.event(frame(**change))
    assert "live_frame" not in read_json(mirror.path)


def test_cancelled_mirror_remains_cancelled_when_worker_finishes(tmp_path):
    mirror = LiveMirror(tmp_path)
    (mirror.path.parent / "cancel").touch()
    assert mirror.cancelled
    mirror.finish("completed")
    assert read_json(mirror.path)["status"] == "cancelled"
