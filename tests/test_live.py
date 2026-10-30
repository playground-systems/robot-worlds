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
