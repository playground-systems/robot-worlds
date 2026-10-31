"""Installed implementation identity checks; no simulator or graphics context."""

import shutil

import pytest

from robot_worlds import runtime
from robot_worlds.artifacts import digest


@pytest.fixture
def package_tree(tmp_path, monkeypatch):
    root = tmp_path / "installed-package"
    root.mkdir()
    for name in (
        "runtime.py", "reset_diagnostics.py", "rubric.py", "models.py", "robot_assets.py", "artifacts.py", "g1_runtime.py", "appearance.py", "live.py", "spectator.py", "sensors.py"
    ):
        (root / name).write_text(f"# installed source for {name}\n")
    monkeypatch.setattr(runtime.resources, "files", lambda package: root)
    monkeypatch.setattr(runtime.metadata, "version", lambda name: "1.0.0")
    return root


def test_runtime_identity_is_independent_of_installation_path(package_tree, tmp_path, monkeypatch):
    first = runtime.runtime_implementation_identity("tiago++", runtime.TOOLS)
    copied = tmp_path / "wheel-extraction" / "robot_worlds"
    shutil.copytree(package_tree, copied)
    monkeypatch.setattr(runtime.resources, "files", lambda package: copied)
    second = runtime.runtime_implementation_identity("tiago++", runtime.TOOLS)
    assert first == second
    assert digest(first) == digest(second)
    assert str(package_tree) not in str(first) and str(copied) not in str(second)


@pytest.mark.parametrize("module", ["runtime.py", "reset_diagnostics.py", "rubric.py", "models.py", "robot_assets.py", "appearance.py"])
def test_controller_sensor_snapshot_or_process_source_edit_changes_identity(package_tree, module):
    before = runtime.runtime_implementation_identity("tiago++", runtime.TOOLS)
    (package_tree / module).write_text("# changed installed implementation\n")
    after = runtime.runtime_implementation_identity("tiago++", runtime.TOOLS)
    assert before["sources"][module] != after["sources"][module]
    assert digest(before) != digest(after)


def test_dependency_or_action_contract_change_invalidates_identity(package_tree, monkeypatch):
    before = runtime.runtime_implementation_identity("tiago++", runtime.TOOLS)
    monkeypatch.setattr(runtime.metadata, "version", lambda name: "2.0.0" if name == "numpy" else "1.0.0")
    changed_dependency = runtime.runtime_implementation_identity("tiago++", runtime.TOOLS)
    assert digest(before) != digest(changed_dependency)
    changed_actions = runtime.runtime_implementation_identity("tiago++", [])
    assert digest(changed_dependency) != digest(changed_actions)


def test_g1_identity_includes_inherited_code_and_onnx_dependency(package_tree):
    before = runtime.runtime_implementation_identity("g1", [])
    assert {"runtime.py", "g1_runtime.py", "rubric.py"} <= before["sources"].keys()
    assert "onnxruntime" in before["dependencies"]
    (package_tree / "g1_runtime.py").write_text("# changed G1 policy observation assembly\n")
    assert digest(before) != digest(runtime.runtime_implementation_identity("g1", []))


def test_missing_runtime_source_fails_closed(package_tree):
    (package_tree / "rubric.py").unlink()
    with pytest.raises(FileNotFoundError):
        runtime.runtime_implementation_identity("tiago++", [])
