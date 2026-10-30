"""Native spectator rendering must leave actor sensors and dynamics untouched."""
import io

import numpy as np
from PIL import Image
from test_physics import block_and_support, world

from robot_worlds.runtime import Runtime


def test_live_overview_has_separate_resolution_and_does_not_advance_physics(native_rendering):
    runtime = Runtime(world(block_and_support()), render=True)
    try:
        before = runtime.snapshot()
        actor = runtime.render()
        frames = []
        runtime.set_live_observer(frames.append)
        runtime.publish_live(force=True)
        assert Image.open(io.BytesIO(actor)).size == (640, 480)
        assert Image.open(io.BytesIO(frames[0]["image"])).size == (960, 720)
        assert runtime._renderer is not runtime._overview_renderer
        # Separate GL contexts can round a few antialiased channels by one byte.
        # Camera geometry and state must stay identical; PNG byte equality is
        # stronger than the native driver's color precision guarantees.
        difference = np.abs(np.asarray(Image.open(io.BytesIO(runtime.render())), dtype=float)
                            - np.asarray(Image.open(io.BytesIO(actor)), dtype=float))
        assert difference.max() <= 1 and difference.mean() < .01
        np.testing.assert_array_equal(runtime.data.qpos, before["qpos"])
        np.testing.assert_array_equal(runtime.data.qvel, before["qvel"])
        assert runtime.snapshot()["time"] == before["time"]
        runtime.set_live_observer(None)
        runtime.publish_live(force=True)
        assert len(frames) == 1
    finally:
        runtime.close()
    assert runtime._renderer is None and runtime._overview_renderer is None
