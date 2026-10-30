"""Archive checks catch omissions and accidental local data before release."""
import hashlib
import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

CHECKER_PATH = Path(__file__).parents[1] / "tools/check_distribution.py"
module_spec = importlib.util.spec_from_file_location("check_distribution", CHECKER_PATH)
checker = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(checker)


def archives(tmp_path, monkeypatch, *, wheel_change=None, source_extra=None):
    metadata = b"Metadata-Version: 2.4\nName: robot-worlds\nVersion: 0.1.0a1\n"
    files = {name: b"source document" for name in checker.REQUIRED_SOURCE_FILES}
    files["PKG-INFO"] = metadata
    package = {name: b"package resource" for name in checker.REQUIRED_PACKAGE_FILES}
    package["assets/robots/g1/CONTROLLER_LICENSE"] = b"pinned upstream license"
    monkeypatch.setattr(checker, "CONTROLLER_LICENSE_SHA256", hashlib.sha256(b"pinned upstream license").hexdigest())
    files.update({"src/robot_worlds/" + name: data for name, data in package.items()})
    wheel = {"robot_worlds/" + name: data for name, data in package.items()}
    wheel.update({"robot_worlds/THIRD_PARTY_NOTICES.md": files["THIRD_PARTY_NOTICES.md"],
                  "robot_worlds-0.1.0a1.dist-info/METADATA": metadata,
                  "robot_worlds-0.1.0a1.dist-info/licenses/LICENSE": b"project MIT license"})
    if wheel_change:
        wheel_change(wheel)
    if source_extra:
        files.update(source_extra)
    with zipfile.ZipFile(tmp_path / "robot_worlds-0.1.0a1-py3-none-any.whl", "w") as archive:
        for name, data in wheel.items():
            archive.writestr(name, data)
    with tarfile.open(tmp_path / "robot_worlds-0.1.0a1.tar.gz", "w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo("robot_worlds-0.1.0a1/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def test_archive_pair_is_checked_without_checkout_import_or_extraction(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch)
    report = checker.inspect_distributions(tmp_path)
    assert report["status"] == "passed"
    assert report["version"] == "0.1.0a1"
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("path", [".env", ".env.production", ".robot-worlds/private.json", "runs/run/trace.json", "../escape", "src/robot_worlds/__pycache__/sdk.pyc"])
def test_private_local_state_and_unsafe_paths_are_rejected(tmp_path, monkeypatch, path):
    archives(tmp_path, monkeypatch, source_extra={path: b"private content must never be logged"})
    with pytest.raises(checker.DistributionError) as caught:
        checker.inspect_distributions(tmp_path)
    assert "private content" not in str(caught.value)


def test_missing_vendor_file_fails_before_clean_install(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch, wheel_change=lambda files: files.pop("robot_worlds/static/vendor/OBJLoader.js"))
    with pytest.raises(checker.DistributionError, match="missing required"):
        checker.inspect_distributions(tmp_path)


def test_wheel_cannot_silently_ship_different_source_bytes(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch, wheel_change=lambda files: files.update({"robot_worlds/sdk.py": b"different build source"}))
    with pytest.raises(checker.DistributionError, match="contents differ"):
        checker.inspect_distributions(tmp_path)


def test_changed_controller_license_requires_explicit_provenance_review(tmp_path, monkeypatch):
    archives(tmp_path, monkeypatch)
    monkeypatch.setattr(checker, "CONTROLLER_LICENSE_SHA256", "0" * 64)
    with pytest.raises(checker.DistributionError, match="pinned upstream LICENCE"):
        checker.inspect_distributions(tmp_path)
