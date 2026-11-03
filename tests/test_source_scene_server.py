"""Source inspections are read-only artifacts, never evaluation-ready worlds."""
from fastapi.testclient import TestClient

from robot_worlds.artifacts import write_json
from robot_worlds.server import create_app


def test_source_inspection_lists_and_serves_only_preview_members(tmp_path):
    folder = tmp_path / "source-scenes" / "kitchen"
    write_json(folder / "source-preview.json", {"name": "Source kitchen", "bodies": [{"joints": [{"id": 2}]}]})
    (folder / "geometry.bin").write_bytes(b"geometry")
    write_json(folder / "private.json", {"not_a_preview": True})
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/static/source-scene-viewer.js").headers["cache-control"] == "no-cache"
        assert client.get("/api/source-scenes").json() == [{"id": "kitchen", "name": "Source kitchen", "bodies": 1, "joints": 1, "status": "source_inspection"}]
        assert client.get("/api/source-scenes/kitchen/files/source-preview.json").json()["name"] == "Source kitchen"
        assert client.get("/api/source-scenes/kitchen/files/geometry.bin").content == b"geometry"
        assert client.get("/api/source-scenes/kitchen/files/private.json").status_code == 404
        assert client.get("/api/worlds/kitchen").status_code == 404
        assert client.post("/api/source-scenes/kitchen/evaluate", json={}).status_code == 404


def test_source_preview_rejects_symlink_escape_and_unpublished_files(tmp_path):
    folder = tmp_path / "source-scenes" / "kitchen"
    folder.mkdir(parents=True)
    outside = tmp_path / "private.bin"
    outside.write_bytes(b"do not publish")
    (folder / "geometry.bin").symlink_to(outside)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/source-scenes/kitchen/files/geometry.bin").status_code == 404
        assert client.get("/api/source-scenes/kitchen/files/textures/not-a-hash.png").status_code == 404
        assert client.get("/api/source-scenes/kitchen/files/%2e%2e/private.bin").status_code == 404
