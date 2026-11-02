"""Remote workflow cancellation without SSH, simulation or a real model."""
import importlib.util
from pathlib import Path

module_spec = importlib.util.spec_from_file_location(
    "mission_worker", Path(__file__).parents[1] / "tools" / "demo" / "mission_worker.py")
worker = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(worker)


def test_generation_cancellation_cannot_fall_through_into_policy_execution(tmp_path, monkeypatch):
    prompt = tmp_path / "request.txt"
    prompt.write_text("Build a factory")
    calls = []
    class Client:
        def __init__(self, channel):
            assert channel == "generation"
        def call(self, method):
            calls.append(method)
            assert method == "cancelled"
            return True
    def generate(text, **options):
        assert text == "Build a factory"
        assert options["cancelled"]() is True
        return object()  # Even a returned candidate must not start a policy.
    monkeypatch.setattr(worker, "RelayClient", Client)
    monkeypatch.setattr(worker, "generate", generate)
    monkeypatch.setattr("sys.argv", ["mission_worker", "--output", str(tmp_path / "out"),
                                    "--prompt", str(prompt)])
    assert worker.main() == 0
    assert calls == ["cancelled", "cancelled"]
    assert not (tmp_path / "out" / "mission-result.json").exists()
