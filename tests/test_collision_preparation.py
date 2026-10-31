"""Cache/lifecycle tests use geometry checks and mocked conversion, not CoACD."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from test_mesh_assets import cube

from robot_worlds import (
    CollisionPreparationCancelled,
    CollisionPreparationError,
    prepare_collision_mesh,
)
from robot_worlds import collision_preparation as prep
from robot_worlds.mesh_assets import convex_volume


def write_obj(path, vertices, faces):
    path.write_text("".join("v " + " ".join(map(str, row)) + "\n" for row in vertices)
                    + "".join("f " + " ".join(str(int(i)+1) for i in row) + "\n" for row in faces))
    return path


def concave_source(path):
    polygon = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    vertices = np.array([(x*.1, y*.1, z*.1) for z in (0, 1) for x, y in polygon])
    flat = [(0, 1, 3), (1, 2, 3), (0, 3, 5), (3, 4, 5)]
    faces = [tuple(reversed(face)) for face in flat] + [tuple(i+6 for i in face) for face in flat]
    for i in range(6):
        j = (i+1) % 6
        faces.extend([(i, j, j+6), (i, j+6, i+6)])
    return write_obj(path, vertices, np.asarray(faces))


def fake_conversion(source, output, *_):
    output.mkdir()
    records = []
    vertices, faces = cube()
    for index, (size, offset) in enumerate([([.2, .1, .1], [0, 0, 0]), ([.1, .1, .1], [0, .1, 0])]):
        points = vertices*size + offset
        path = write_obj(output / f"part-{index:03}.obj", points, faces)
        records.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "vertices": len(points), "triangles": len(faces), "volume_m3": convex_volume(points, faces)})
    prep._write_manifest(output, hashlib.sha256(source.read_bytes()).hexdigest(), records, "CoACD")


def mock_converter(monkeypatch):
    monkeypatch.setattr(prep, "_installed_converter", lambda: None)
    monkeypatch.setattr(prep, "_run_worker", fake_conversion)


def test_convex_fast_path_preserves_bytes_and_reuses_cache_without_converter(tmp_path, monkeypatch):
    source = write_obj(tmp_path / "cube.obj", *cube())
    monkeypatch.setattr(prep, "_installed_converter", lambda: pytest.fail("Convex source needs no optional dependency"))
    original, events = source.read_bytes(), []
    first = prepare_collision_mesh(source, tmp_path / "cache", on_event=events.append)
    second = prepare_collision_mesh(source, tmp_path / "cache", on_event=events.append)
    assert first.method == "verified-source" and first.parts == 1 and not first.cache_hit
    assert second.cache_hit and second.manifest_path == first.manifest_path
    assert (first.manifest_path.parent / "part-000.obj").read_bytes() == original == source.read_bytes()
    assert events[-1]["type"] == "asset_preparation_reused"
    assert not list((tmp_path / "cache").glob(".preparation-*"))
    source.write_bytes(original + b"# another source revision\n")
    third = prepare_collision_mesh(source, tmp_path / "cache")
    assert third.source_sha256 != first.source_sha256
    assert third.manifest_path != first.manifest_path


def test_source_snapshot_worker_verification_and_published_cache(tmp_path, monkeypatch):
    source = concave_source(tmp_path / "source.obj")
    original = source.read_bytes()
    mock_converter(monkeypatch)
    first = prepare_collision_mesh(source, tmp_path / "cache")
    assert first.parts == 2 and first.method == "CoACD"
    monkeypatch.setattr(prep, "_run_worker", lambda *_: pytest.fail("Repeated import launched conversion"))
    monkeypatch.setattr(prep, "_installed_converter", lambda: pytest.fail("Cache reuse requires no converter"))
    assert prepare_collision_mesh(source, tmp_path / "cache").cache_hit
    assert source.read_bytes() == original
    manifest = json.loads(first.manifest_path.read_text())
    assert manifest["settings"] == prep.conversion_settings()
    assert manifest["preparation"]["worker_threads"] == 1


@pytest.mark.parametrize("failure", ["bytes", "manifest", "extra", "link", "volume"])
def test_corrupt_cache_fails_without_replacing_it_or_launching_worker(tmp_path, monkeypatch, failure):
    source = concave_source(tmp_path / "source.obj")
    mock_converter(monkeypatch)
    result = prepare_collision_mesh(source, tmp_path / "cache")
    path = result.manifest_path.parent / "part-000.obj"
    if failure == "bytes":
        path.write_bytes(path.read_bytes() + b"changed")
    elif failure == "manifest":
        result.manifest_path.write_text("invalid JSON")
    elif failure == "extra":
        (result.manifest_path.parent / "unrecorded").write_text("unexpected")
    elif failure == "link":
        path.unlink()
        path.symlink_to(source)
    else:
        data = json.loads(result.manifest_path.read_text())
        data["parts"][0]["volume_m3"] = 999
        result.manifest_path.write_text(json.dumps(data))
    monkeypatch.setattr(prep, "_run_worker", lambda *_: pytest.fail("Corrupt cache was silently regenerated"))
    with pytest.raises(CollisionPreparationError):
        prepare_collision_mesh(source, tmp_path / "cache")


@pytest.mark.parametrize("failure", ["crash", "incomplete", "outside", "cancelled"])
def test_failed_worker_never_publishes_partial_preparation(tmp_path, monkeypatch, failure):
    source = concave_source(tmp_path / "source.obj")
    mock_converter(monkeypatch)
    cancelled = [False]

    def worker(snapshot, output, *_):
        assert snapshot != source and snapshot.read_bytes() == source.read_bytes()
        if failure == "crash":
            raise CollisionPreparationError("worker stopped")
        fake_conversion(snapshot, output)
        if failure == "incomplete":
            (output / "part-000.obj").unlink()
        elif failure == "outside":
            data = json.loads((output / "manifest.json").read_text())
            data["parts"][0]["file"] = "../source.obj"
            (output / "manifest.json").write_text(json.dumps(data))
        else:
            cancelled[0] = True
    monkeypatch.setattr(prep, "_run_worker", worker)
    with pytest.raises(CollisionPreparationError):
        prepare_collision_mesh(source, tmp_path / "cache", cancelled=lambda: cancelled[0])
    assert [path.name for path in (tmp_path / "cache").iterdir()] == [".preparation.lock"]


def test_missing_optional_dependency_is_actionable_and_does_not_create_a_cache_entry(tmp_path, monkeypatch):
    source = concave_source(tmp_path / "source.obj")
    def missing(*_):
        raise prep.importlib.metadata.PackageNotFoundError("coacd")
    monkeypatch.setattr(prep.importlib.metadata, "version", missing)
    with pytest.raises(CollisionPreparationError, match="preparation extra"):
        prepare_collision_mesh(source, tmp_path / "cache")
    assert not [path for path in (tmp_path / "cache").iterdir() if path.is_dir()]


def test_concurrent_requests_share_one_worker_and_release_the_lock(tmp_path, monkeypatch):
    source = concave_source(tmp_path / "source.obj")
    mock_converter(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def worker(snapshot, output, *_):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        fake_conversion(snapshot, output)
    monkeypatch.setattr(prep, "_run_worker", worker)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(prepare_collision_mesh, source, tmp_path / "cache")
        assert entered.wait(2)
        second = executor.submit(prepare_collision_mesh, source, tmp_path / "cache")
        release.set()
        results = [first.result(), second.result()]
    assert len(calls) == 1
    assert sum(result.cache_hit for result in results) == 1
    assert results[0].manifest_path == results[1].manifest_path


@pytest.mark.parametrize("cancel", [False, True])
def test_real_lightweight_worker_is_reaped_on_timeout_or_cancellation(tmp_path, monkeypatch, cancel):
    # Only an idle Python process is started here, never the native converter.
    original = subprocess.Popen
    processes = []
    ready = tmp_path / "ready"
    code = ("import signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"Path({str(ready)!r}).write_text('ready'); time.sleep(60)")

    def spawn(_command, **kwargs):
        assert kwargs["env"]["OMP_NUM_THREADS"] == "1"
        assert "OPENAI_API_KEY" not in kwargs["env"]
        process = original([sys.executable, "-c", code], **kwargs)
        processes.append(process)
        limit = time.monotonic()+3
        while not ready.exists() and time.monotonic() < limit:
            time.sleep(.01)
        assert ready.exists()
        if os.name == "posix":
            assert os.getpgid(process.pid) == process.pid
        return process
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-not-for-worker")
    monkeypatch.setattr(prep.subprocess, "Popen", spawn)
    error = CollisionPreparationCancelled if cancel else CollisionPreparationError
    with pytest.raises(error, match="cancelled" if cancel else "time budget"):
        prep._run_worker(tmp_path / "source.obj", tmp_path / "output", time.monotonic()+.3,
                         lambda: bool(processes) if cancel else False)
    assert len(processes) == 1 and processes[0].poll() is not None
