"""Bounded local archive checks; no network, models or simulator."""
import importlib.util
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def acquisition(tmp_path):
    path = Path(__file__).parents[1] / "tools/prepare_molmo_scene.py"
    spec = importlib.util.spec_from_file_location("prepare_molmo_scene", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not shutil.which("zstd"):
        pytest.skip("Local source preparation requires an existing zstd executable")
    return module.Acquisition(tmp_path / "sources", 1_000_000)


def archive(acquisition, members):
    raw = acquisition.root / "fixture.tar"
    with tarfile.open(raw, "w") as stream:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            stream.addfile(info, io.BytesIO(data))
    compressed = raw.with_suffix(".tar.zst")
    subprocess.run([acquisition.zstd, "-q", str(raw), "-o", str(compressed)], check=True)
    return compressed, {"inflated_size": raw.stat().st_size}


@pytest.mark.parametrize("name", ["../outside", "/outside"])
def test_download_path_cannot_escape_before_any_network_request(acquisition, name, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid destination attempted a network request")
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    with pytest.raises(ValueError, match="Unsafe source archive path"):
        acquisition.fetch(name, "https://example.invalid/source")


def test_download_rejects_existing_parent_symlink(acquisition, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (acquisition.root / "redirect").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        acquisition.fetch("redirect/model.obj", "https://example.invalid/source")
    assert not list(outside.iterdir())


def test_download_does_not_follow_predictable_partial_symlink(acquisition, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    (acquisition.root / "model.obj.partial").symlink_to(outside)
    response = io.BytesIO(b"original mesh")
    response.headers = {"Content-Length": "13"}
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: response)
    result = acquisition.fetch("model.obj", "https://example.invalid/source", size_limit=100)
    assert result.read_bytes() == b"original mesh"
    assert outside.read_bytes() == b"preserve"
    assert not list(acquisition.root.glob(".download-*"))


def test_extract_only_referenced_assets_and_notices(acquisition):
    source, row = archive(acquisition, {"parts/mesh.obj": b"original mesh",
                                       "parts/unused.obj": b"unused", "LICENSE.txt": b"source terms"})
    destination = acquisition.root / "source"
    acquisition.extract(source, row, destination, {"parts/mesh.obj"})
    assert (destination / "parts/mesh.obj").read_bytes() == b"original mesh"
    assert not (destination / "parts/unused.obj").exists()
    assert (acquisition.root / "source-notices" / source.name / "LICENSE.txt").read_bytes() == b"source terms"
    assert not list(acquisition.root.rglob(".source-*"))


def test_extract_rejects_existing_parent_symlink(acquisition, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = acquisition.root / "source"
    destination.mkdir()
    (destination / "parts").symlink_to(outside, target_is_directory=True)
    source, row = archive(acquisition, {"parts/mesh.obj": b"original mesh"})
    with pytest.raises(ValueError, match="escapes"):
        acquisition.extract(source, row, destination, {"parts/mesh.obj"})
    assert not list(outside.iterdir())
