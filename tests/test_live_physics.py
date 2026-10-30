"""Native spectator rendering must leave actor sensors and dynamics untouched."""
import io

import numpy as np
from PIL import Image
from test_physics import block_and_support, world

from robot_worlds.models import SceneObject
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


def test_native_spectator_eye_stays_inside_the_wall_at_delivery_position(native_rendering):
    spec = world(block_and_support())
    spec.bounds = (12, 8, 3)
    spec.robot_start = (3.55, .77, 1.57)
    spec.objects.append(SceneObject(id="east_wall", label="East wall", kind="box",
                                    position=(5.95, 0, 1.5), size=(.1, 8, 3)))
    runtime = Runtime(spec, render=True)
    try:
        before = runtime.snapshot()
        runtime.render(view="overview")
        cameras = runtime._overview_renderer.scene.camera
        eye = (np.asarray(cameras[0].pos) + np.asarray(cameras[1].pos))/2
        assert eye[0] < 5.9-.25
        assert np.linalg.norm(eye-np.array([3.55, .77, .8])) < 4
        np.testing.assert_array_equal(runtime.data.qpos, before["qpos"])
        assert runtime.data.time == before["time"]
    finally:
        runtime.close()
