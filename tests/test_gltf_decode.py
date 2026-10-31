"""Small GLB worker/cache checks; no simulation or large asset conversion."""
import json
import shutil
import struct

import pytest

from robot_worlds import gltf_decode


def fixture(path):
    binary = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}],
                "bufferViews": [{"buffer": 0, "byteLength": len(binary)}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
                "nodes": [{"name": "original", "translation": [1, 2, 3]}]}
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    path.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 28+len(text)+len(binary), len(text), 0x4e4f534a)
                     + text + struct.pack("<II", len(binary), 0x004e4942) + binary)
    return path


@pytest.fixture
def node():
    if not shutil.which("node"):
        pytest.skip("Optional cold GLB decoding requires Node.js")


def test_tiny_worker_preserves_data_and_cache_reuses_without_node(tmp_path, monkeypatch, node):
    source = fixture(tmp_path / "original.glb")
    original = source.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "fake-do-not-inherit")
    monkeypatch.setenv("NODE_OPTIONS", "fake-do-not-inherit")
    real_spawn = gltf_decode.subprocess.Popen

    def spawn(*args, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "NODE_OPTIONS" not in kwargs["env"]
        return real_spawn(*args, **kwargs)

    monkeypatch.setattr(gltf_decode.subprocess, "Popen", spawn)
    first = gltf_decode.decode_glb(source, tmp_path / "cache")
    raw = first.path.read_bytes()
    size = struct.unpack_from("<I", raw, 12)[0]
    assert json.loads(raw[20:20+size])["nodes"][0]["translation"] == [1, 2, 3]
    assert raw[-36:] == original[-36:]
    assert source.read_bytes() == original
    assert not first.cache_hit
    monkeypatch.setattr(gltf_decode.shutil, "which", lambda _: None)
    second = gltf_decode.decode_glb(source, tmp_path / "cache")
    assert second.cache_hit and second.path == first.path
    assert json.loads(second.manifest_path.read_text())["status"] == "decompressed_only_not_physics_validated"


@pytest.mark.parametrize("tamper", ["bytes", "metadata", "symlink", "extra"])
def test_corruption_fails_without_worker(tmp_path, monkeypatch, node, tamper):
    source = fixture(tmp_path / "original.glb")
    saved = gltf_decode.decode_glb(source, tmp_path / "cache")
    if tamper == "bytes":
        saved.path.write_bytes(b"x" * saved.path.stat().st_size)
    elif tamper == "metadata":
        saved.manifest_path.write_text("{}")
    elif tamper == "symlink":
        saved.path.unlink()
        saved.path.symlink_to(source)
    else:
        (saved.path.parent / "extra").write_text("extra")
    monkeypatch.setattr(gltf_decode.subprocess, "Popen", lambda *a, **k: pytest.fail("Corrupt cache must not spawn"))
    with pytest.raises(gltf_decode.GLBDecodeError, match="cache"):
        gltf_decode.decode_glb(source, tmp_path / "cache")


def test_unknown_node_reports_requirement_without_cache_publication(tmp_path, monkeypatch):
    source = fixture(tmp_path / "original.glb")
    monkeypatch.setattr(gltf_decode.shutil, "which", lambda _: None)
    with pytest.raises(gltf_decode.GLBDecodeError, match="requires Node.js"):
        gltf_decode.decode_glb(source, tmp_path / "cache")
    assert list((tmp_path / "cache").iterdir()) == []


def test_worker_rejection_preserves_source_and_no_partial_cache(tmp_path, node):
    source = fixture(tmp_path / "original.glb")
    raw = source.read_bytes().replace(b'"buffer": 0', b'"buffer": 9')
    source.write_bytes(raw)
    with pytest.raises(gltf_decode.GLBDecodeError, match="rejected"):
        gltf_decode.decode_glb(source, tmp_path / "cache")
    assert source.read_bytes() == raw
    assert list((tmp_path / "cache").iterdir()) == []


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
def test_cancel_or_timeout_terminates_worker_and_removes_partial_files(tmp_path, monkeypatch, failure):
    source = fixture(tmp_path / "original.glb")
    started = [False]
    tick = [0.]
    monkeypatch.setattr(gltf_decode.time, "monotonic", lambda: tick[0])
    monkeypatch.setattr(gltf_decode.shutil, "which", lambda _: "/fake/node")

    class Worker:
        returncode = None
        stopped = False
        def __init__(self, *args, **kwargs):
            started[0] = True
            tick[0] = 2. if failure == "timeout" else 0.
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = -15
            Worker.stopped = True
        def wait(self, **kwargs):
            return self.returncode

    monkeypatch.setattr(gltf_decode.subprocess, "Popen", Worker)
    with pytest.raises(gltf_decode.GLBDecodeError, match="cancelled|time budget"):
        gltf_decode.decode_glb(source, tmp_path / "cache", timeout=1,
                              cancelled=lambda: started[0] and failure == "cancel")
    assert Worker.stopped
    assert list((tmp_path / "cache").iterdir()) == []


def test_pre_cancel_does_not_read_source_or_create_cache(tmp_path):
    with pytest.raises(gltf_decode.GLBDecodeError, match="cancelled"):
        gltf_decode.decode_glb(tmp_path / "absent", tmp_path / "cache", cancelled=lambda: True)
    assert not (tmp_path / "cache").exists()
