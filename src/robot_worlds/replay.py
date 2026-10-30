"""Render recorded simulator states without rerunning a policy or changing grades."""

from __future__ import annotations

import bisect
import math
from pathlib import Path


def replay_indices(trace: list[dict], *, fps: float = 5, max_frames: int = 600,
                   evidence_times=()) -> list[int]:
    if not math.isfinite(fps) or fps <= 0 or max_frames < 2:
        raise ValueError("Replay needs positive finite FPS and at least two frames")
    if not trace:
        return []
    times = [float(state["time"]) for state in trace]
    if any(not math.isfinite(t) for t in times) or any(b < a for a, b in zip(times, times[1:])):
        raise ValueError("Replay timestamps must be finite and nondecreasing")
    def nearest(target):
        right = min(len(times)-1, bisect.bisect_left(times, target))
        left = max(0, right-1)
        return min((left, right), key=lambda index: abs(times[index]-target))
    # Preserve criterion evidence before distributing remaining frames uniformly.
    required = {0, len(trace)-1}
    required.update(nearest(float(t)) for t in evidence_times
                    if t is not None and math.isfinite(float(t)))
    if len(required) > max_frames:
        raise ValueError("Replay frame budget cannot cover all criterion evidence")
    count = min(max_frames-len(required), math.ceil((times[-1]-times[0])*fps))
    selected = set(required)
    if count:
        selected.update(nearest(times[0]+(times[-1]-times[0])*(i+1)/(count+1))
                        for i in range(count))
    return sorted(selected)


def render_replay(runtime, trace: list[dict], directory: str | Path, *, fps: float = 5,
                  max_frames: int = 600, evidence_times=()) -> list[dict]:
    """Return frame paths relative to directory; caller restores live state if needed.

    Call only after execution and grading. restore_snapshot is an inspection API,
    never an action exposed to a robot policy.
    """
    directory = Path(directory)
    destination = directory / "replay"
    destination.mkdir(parents=True, exist_ok=True)
    indices = replay_indices(trace, fps=fps, max_frames=max_frames, evidence_times=evidence_times)
    frames = []
    for index, trace_index in enumerate(indices):
        snapshot = trace[trace_index]
        runtime.restore_snapshot(snapshot)
        image = runtime.render(view="overview")
        if not image:
            raise RuntimeError("Replay renderer returned no frame")
        name = f"replay/{index:05d}.png"
        (directory / name).write_bytes(image)
        frames.append({"time": float(snapshot["time"]), "path": name, "trace_index": trace_index})
    return frames
