from robot_worlds.live import LiveView


def test_live_frames_are_rate_limited_in_wall_time_and_do_not_advance_simulation():
    now = [0.0]
    frames, renders = [], []
    def render():
        renders.append(True)
        return b"pixels"
    view = LiveView(render, frames.append, clock=lambda: now[0])
    for tick in range(100):
        now[0] = tick / 100
        view.publish(tick * .05)
    assert len(frames) == len(renders) == 4
    assert [f["time"] for f in frames] == [0, 1.25, 2.5, 3.75]
    view.publish(5, force=True)
    assert frames[-1]["time"] == 5


def test_spectator_failure_disables_preview_without_raising_into_physics():
    def fail(_):
        raise OSError("private storage path")
    view = LiveView(lambda: b"pixels", fail)
    view.publish(0)
    view.publish(1, force=True)
    assert view.sequence == 0 and view.error["type"] == "OSError"
    assert "private" not in str(view.error)


def test_slow_preview_reserves_wall_time_for_physics_between_frames():
    now = [0.0]
    frames = []
    def slow_render():
        now[0] += .8  # Software rendering exceeds the nominal 250 ms interval.
        return b"pixels"
    view = LiveView(slow_render, frames.append, clock=lambda: now[0])
    view.publish(0)
    # Rendering itself must not immediately make the next frame due.
    view.publish(.05)
    assert len(frames) == 1
    now[0] = 3.19
    view.publish(.1)
    assert len(frames) == 1
    now[0] = 3.21
    view.publish(.15)
    assert len(frames) == 2
    # Action boundaries may still explicitly request a fresh image.
    view.publish(.2, force=True)
    assert len(frames) == 3
