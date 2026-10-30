import pytest

from robot_worlds.replay import render_replay, replay_indices


def test_sparse_frame_budget_preserves_exact_criterion_state_and_endpoints():
    trace = [{"time": i / 20} for i in range(2001)]
    selected = replay_indices(trace, fps=5, max_frames=20, evidence_times=[32.25, 76.5])
    assert len(selected) <= 20
    assert {0, 645, 1530, 2000} <= set(selected)
    assert selected == sorted(set(selected))


def test_renderer_restores_recorded_states_without_stepping(tmp_path):
    class Runtime:
        restored = []
        def restore_snapshot(self, state):
            self.restored.append(state["time"])
        def render(self, *, view):
            assert view == "overview"
            return b"test-frame"
    runtime = Runtime()
    frames = render_replay(runtime, [{"time": 0}, {"time": .05}, {"time": .1}], tmp_path)
    assert runtime.restored == [frame["time"] for frame in frames]
    assert frames[-1]["time"] == .1
    assert all((tmp_path / frame["path"]).read_bytes() == b"test-frame" for frame in frames)


def test_invalid_trace_and_excess_evidence_rejected():
    with pytest.raises(ValueError, match="nondecreasing"):
        replay_indices([{"time": 1}, {"time": 0}])
    with pytest.raises(ValueError, match="cannot cover"):
        replay_indices([{"time": i} for i in range(5)], max_frames=2, evidence_times=[1, 2])
