"""Offline safety/integrity checks for the bounded source-only acquisition tool."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "factory_source_acquisition", Path(__file__).parents[1] / "tools/prepare_factory_v3_sources.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
URL = "https://huggingface.co/datasets/example/source/resolve/pin/file"


class Response(io.BytesIO):
    status = 200
    headers = {}
    url = URL


class Opener:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        return Response(next(self.payloads))


def glb(document=None, binary=b"data"):
    document = document or {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}]}
    raw = json.dumps(document).encode()
    raw += b" " * (-len(raw) % 4)
    return (struct.pack("<4sIIII", b"glTF", 2, 28 + len(raw) + len(binary), len(raw), 0x4E4F534A)
            + raw + struct.pack("<II", len(binary), 0x004E4942) + binary)


def test_parent_containment_and_symlinks_do_not_escape_cache(tmp_path):
    cache, outside = tmp_path / "cache", tmp_path / "outside"
    cache.mkdir()
    outside.mkdir()
    (cache / "linked").symlink_to(outside, target_is_directory=True)
    for relative in ("../outside/file", str(outside / "file"), "linked/file"):
        with pytest.raises(ValueError):
            tool.safe_path(cache, relative)
    assert not (outside / "file").exists()


def test_atomic_publication_preserves_existing_bytes_and_refuses_symlink(tmp_path):
    target = tmp_path / "source"
    tool.atomic_bytes(target, b"original")
    inode = target.stat().st_ino
    tool.atomic_bytes(target, b"original")
    assert target.stat().st_ino == inode
    with pytest.raises(ValueError, match="changed"):
        tool.atomic_bytes(target, b"different")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        tool.atomic_bytes(link, b"different", replace=True)
    assert target.read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["link", "source"]


@pytest.mark.parametrize("algorithm", ["sha256", "git-blob-sha1"])
def test_lfs_and_git_blob_hashes_are_distinct_and_rechecked(tmp_path, algorithm):
    path, data = tmp_path / "model.glb", b"original source"
    path.write_bytes(data)
    expected = (hashlib.sha256(data).hexdigest() if algorithm == "sha256"
                else hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest())
    publisher = {"algorithm": algorithm, "value": expected}
    assert tool.verify_source(path, len(data), publisher) == hashlib.sha256(data).hexdigest()
    path.write_bytes(b"X" + data[1:])
    with pytest.raises(ValueError, match="checksum"):
        tool.verify_source(path, len(data), publisher)
    with pytest.raises(ValueError, match="length"):
        tool.verify_source(path, len(data) + 1, publisher)


def test_cumulative_budget_accounts_for_oversized_responses_and_failed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "NETWORK_CAP", 12)
    acquisition = tool.Acquisition(tmp_path)
    opener = Opener([b"abc", b"123456789"])
    acquisition.opener = opener
    assert acquisition.fetch(URL, 3) == b"abc"
    with pytest.raises(ValueError, match="exceeded"):
        acquisition.fetch(URL, 8)
    ledger = json.loads((tmp_path / "download-ledger.json").read_text())
    assert sum(row["bytes"] for row in ledger["requests"]) == 12
    with pytest.raises(ValueError, match="cap"):
        tool.Acquisition(tmp_path).fetch(URL, 1)
    assert opener.calls == 2


def test_pending_request_reservations_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "NETWORK_CAP", 10)
    (tmp_path / "download-ledger.json").write_text(json.dumps({
        "schema_version": 1, "cap_bytes": 10,
        "requests": [{"url": URL, "reserved_bytes": 7, "status": "pending"}]}))
    acquisition = tool.Acquisition(tmp_path)
    acquisition.opener = Opener([])
    with pytest.raises(ValueError, match="cap"):
        acquisition.fetch(URL, 3)
    assert acquisition.opener.calls == 0


def test_fresh_checkout_fetches_catalog_once_and_rejects_changed_cache(tmp_path, monkeypatch):
    payload = b'{"asset":"example"}\n'
    monkeypatch.setattr(tool, "CATALOG", tmp_path / "absent-research-cache")
    monkeypatch.setattr(tool, "CATALOG_SHA256", hashlib.sha256(payload).hexdigest())
    acquisition = tool.Acquisition(tmp_path)
    acquisition.opener = Opener([payload])
    assert tool.load_catalog(acquisition, tool.CATALOG_REVISION) == {"example": {"asset": "example"}}
    assert tool.load_catalog(acquisition, tool.CATALOG_REVISION)["example"]["asset"] == "example"
    assert acquisition.opener.calls == 1
    (tmp_path / "optimized-manifest.jsonl").write_bytes(payload + b" ")
    with pytest.raises(ValueError, match="checksum"):
        tool.load_catalog(acquisition, tool.CATALOG_REVISION)


def test_glb_parser_retains_physics_and_rejects_external_dependencies(tmp_path):
    document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}],
                "extensions": {"KHR_physics_rigid_bodies": {"physicsJoints": [{"limits": []}]}},
                "nodes": [{"name": "source", "extras": {"mass": 0.5}}]}
    path = tmp_path / "model.glb"
    path.write_bytes(glb(document))
    parsed = tool.read_glb(path)
    assert parsed == document
    assert tool.inventory(parsed)["nodes"][0]["extras"] == {"mass": 0.5}
    for key, value in [("buffers", [{"byteLength": 4, "uri": "../external.bin"}]),
                       ("images", [{"uri": "https://example.com/external.png"}])]:
        path.write_bytes(glb({**document, key: value}))
        with pytest.raises(ValueError, match="self-contained"):
            tool.read_glb(path)


@pytest.mark.parametrize("mutation", ["version", "truncated", "chunk-length", "buffer-length"])
def test_glb_parser_rejects_inconsistent_lengths(tmp_path, mutation):
    raw = glb()
    if mutation == "version":
        raw = raw[:4] + struct.pack("<I", 1) + raw[8:]
    elif mutation == "truncated":
        raw = raw[:-1]
    elif mutation == "chunk-length":
        raw = raw[:-8] + struct.pack("<I", 12) + raw[-4:]
    else:
        raw = glb({"asset": {"version": "2.0"}, "buffers": [{"byteLength": 9}]})
    path = tmp_path / "model.glb"
    path.write_bytes(raw)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        tool.read_glb(path)


def test_inventory_rejects_multiple_parents_and_cycles():
    for nodes in [[{"children": [2]}, {"children": [2]}, {}],
                  [{"children": [1]}, {"children": [0]}]]:
        with pytest.raises(ValueError):
            tool.inventory({"nodes": nodes})
