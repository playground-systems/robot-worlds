"""Starter import orchestration with tiny, explicitly fake native evidence.

These fixtures test packaging and API lifecycle only. They are not physical
validation of the upstream environments; release preparation performs that check.
"""

import json
import shutil
import stat
import threading
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from robot_worlds import starter_environments as starters
from robot_worlds.artifacts import file_digest, read_json, write_json
from robot_worlds.generator import GenerationCancelled
from robot_worlds.sdk import World, load
from robot_worlds.server import create_app


class FakeStarterRuntime:
    """No physics, rendering, model client, or external asset discovery."""

    def __init__(self, spec, **kwargs):
        self.spec = spec
        self.manifest = {"engine": "FAKE_STARTER_ORCHESTRATION_TEST_ONLY", "seed": 0,
                         "model_fingerprint": "1" * 64, "implementation_fingerprint": "2" * 64,
                         "action_contract": [], "sensor_contract": {"fixture": "fake"}}

    def snapshot(self):
        return {"time": 0, "robot": {"position": [0, 0, 0]},
                "objects": {obj.id: {"position": obj.position, "size": obj.size,
                                     "quaternion": [1, 0, 0, 0], "velocity": [0, 0, 0],
                                     "held_by": None, "contacts": []} for obj in self.spec.objects}}

    def render(self):
        return b"FAKE_STARTER_PREVIEW_NOT_A_RENDER"

    def close(self):
        pass


class Queue:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def finish(self):
        future, fn, args = self.jobs[-1]
        assert future.set_running_or_notify_cancel()
        result = fn(*args)
        future.set_result(result)

    def shutdown(self, **kwargs):
        for future, _, _ in self.jobs:
            future.cancel()


def bundle_world(root, identifier, world):
    destination = root / identifier
    destination.mkdir(exist_ok=True)
    archive = destination / "world.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(world.path.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(world.path).as_posix())
    (destination / "thumbnail.png").write_bytes(b"FAKE_STARTER_THUMBNAIL_NOT_A_RENDER")
    return {"bundle_sha256": file_digest(archive), "world_id": world.id}


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    original = starters.starter_catalog()
    root = tmp_path / "packaged"
    root.mkdir()
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda robot: {"test_asset": "fake"})
    # These text assets exercise immutable source notices and provenance without
    # copying upstream geometry or claiming a native scene conversion passed.
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "NOTICE.txt").write_text("FAKE fixture notice; upstream assets are not used here.\n")
    write_json(sources / "provenance.json", {"fixture": "fake", "source": "test-only"})
    entries, worlds = [], {}
    for entry in original:
        identifier = entry["id"]
        world = World.from_spec({
            "name": f"FAKE fixture {identifier}", "description": "Packaging unit fixture, no native physics",
            "task": "Move test part", "bounds": [8, 8, 3],
            "objects": [{"id": "part", "label": "test part", "kind": "box", "movable": True,
                         "position": [2, 0, .1], "size": [.2, .2, .2],
                         "source_notice": "NOTICE.txt", "source_metadata": "provenance.json"}],
            "regions": [{"id": "target", "label": "target", "position": [-2, 0, .2], "size": [1, 1, .4]}],
            "rubric": [{"id": "move", "description": "Move test part", "predicate": "in_region",
                        "object_id": "part", "target_id": "target"}],
        }, tmp_path / "frozen", asset_root=sources, runtime_factory=FakeStarterRuntime)
        assert world.manifest["validation"]["status"] == "ready"
        entries.append(dict(entry, **bundle_world(root, identifier, world)))
        worlds[identifier] = world
    write_json(root / "catalog.json", {"schema_version": 1, "environments": entries})
    monkeypatch.setattr(starters, "_ROOT", root)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Starter import must not start a model or native runtime")

    monkeypatch.setattr("openai.OpenAI", forbidden)
    monkeypatch.setattr("robot_worlds.sdk._runtime", forbidden)
    return root, worlds


def rewrite_archive(root, identifier, write_members):
    archive = root / identifier / "world.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        write_members(bundle)
    catalog = read_json(root / "catalog.json")
    entry = next(entry for entry in catalog["environments"] if entry["id"] == identifier)
    entry["bundle_sha256"] = file_digest(archive)
    write_json(root / "catalog.json", catalog)


def test_four_offline_starters_preserve_versions_and_source_evidence(packaged, tmp_path):
    _, expected = packaged
    catalog = starters.starter_catalog()
    assert [entry["id"] for entry in catalog] == ["warehouse", "house", "hospital", "bookstore"]
    assert all(entry["available"] and entry["preview_available"] for entry in catalog)
    for entry in catalog:
        events = []
        world = starters.add_starter(entry["id"], tmp_path / "workspace", on_event=events.append)
        assert world.id == expected[entry["id"]].id
        assert load(world.path).id == world.id
        assert world.manifest == expected[entry["id"]].manifest
        assert world.manifest["runtime"]["engine"] == "FAKE_STARTER_ORCHESTRATION_TEST_ONLY"
        for member in world.manifest["assets"]:
            assert (world.path / member).read_bytes() == (expected[entry["id"]].path / member).read_bytes()
        assert [event["type"] for event in events] == ["starter_import", "world_created"]
        assert events[-1]["world_id"] == world.id
    assert not list((tmp_path / "workspace").glob(".starter-*"))


def test_repeat_selection_reopens_without_unpacking(packaged, tmp_path, monkeypatch):
    world = starters.add_starter("warehouse", tmp_path / "workspace")

    def forbidden(*args):
        raise AssertionError("Already installed immutable worlds should not be extracted again")

    monkeypatch.setattr(starters, "_extract", forbidden)
    assert starters.add_starter("warehouse", tmp_path / "workspace").path == world.path
    member = next(iter(world.manifest["assets"]))
    (world.path / member).write_text("tampered source evidence")
    with pytest.raises(ValueError, match="asset was modified"):
        starters.add_starter("warehouse", tmp_path / "workspace")


def test_concurrent_selection_publishes_one_immutable_version(packaged, tmp_path, monkeypatch):
    original, barrier = starters._checked_world, threading.Barrier(4)

    def synchronize(path, expected):
        result = original(path, expected)
        if path.name.startswith(".starter-"):
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(starters, "_checked_world", synchronize)
    with ThreadPoolExecutor(max_workers=4) as workers:
        worlds = list(workers.map(lambda _: starters.add_starter("warehouse", tmp_path / "workspace"), range(4)))
    assert len({world.id for world in worlds}) == 1
    assert list((tmp_path / "workspace").iterdir()) == [worlds[0].path]


def test_bundle_checksum_rejected_before_extraction(packaged, tmp_path):
    root, _ = packaged
    archive = root / "warehouse" / "world.zip"
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        starters.add_starter("warehouse", tmp_path / "workspace")
    assert not list((tmp_path / "workspace").iterdir())


@pytest.mark.parametrize("filename", ["../escape", "/escape", "C:/escape", "C:escape", "a\\escape", "./manifest.json", "a/../escape", "a//escape"])
def test_unsafe_archive_paths_rejected(packaged, tmp_path, filename):
    root, _ = packaged
    rewrite_archive(root, "warehouse", lambda bundle: bundle.writestr(filename, b"unsafe"))
    with pytest.raises(ValueError, match="Unsafe starter bundle member"):
        starters.add_starter("warehouse", tmp_path / "workspace")
    assert not list((tmp_path / "workspace").iterdir())


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK])
def test_archive_special_files_rejected(packaged, tmp_path, mode):
    root, _ = packaged
    member = zipfile.ZipInfo("special")
    member.create_system = 3
    member.external_attr = (mode | 0o644) << 16
    rewrite_archive(root, "warehouse", lambda bundle: bundle.writestr(member, b"outside"))
    with pytest.raises(ValueError, match="Unsafe starter bundle member"):
        starters.add_starter("warehouse", tmp_path / "workspace")
    assert not list((tmp_path / "workspace").iterdir())


def test_duplicate_archive_paths_rejected(packaged, tmp_path):
    root, _ = packaged

    def members(bundle):
        bundle.writestr("duplicate", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            bundle.writestr("duplicate", "second")

    rewrite_archive(root, "warehouse", members)
    with pytest.raises(ValueError, match="Unsafe starter bundle member"):
        starters.add_starter("warehouse", tmp_path / "workspace")
    assert not list((tmp_path / "workspace").iterdir())


@pytest.mark.parametrize("budget", ["members", "bytes", "archive"])
def test_archive_budgets_are_enforced(packaged, tmp_path, monkeypatch, budget):
    root, _ = packaged
    rewrite_archive(root, "warehouse", lambda bundle: [bundle.writestr(str(i), "abc") for i in range(3)])
    setting = {"members": "_MAX_MEMBERS", "bytes": "_MAX_BYTES", "archive": "_MAX_ARCHIVE_BYTES"}[budget]
    monkeypatch.setattr(starters, setting, 2)
    with pytest.raises(ValueError, match="exceeds its extraction budget"):
        starters.add_starter("warehouse", tmp_path / "workspace")
    assert not list((tmp_path / "workspace").iterdir())


@pytest.mark.parametrize("during_extraction", [False, True])
def test_cancellation_cleans_unpublished_scene(packaged, tmp_path, during_extraction):
    output = tmp_path / "workspace"

    def cancelled():
        return bool(list(output.glob(".starter-*"))) if during_extraction else True

    with pytest.raises(GenerationCancelled, match="Starter import cancelled"):
        starters.add_starter("warehouse", output, cancelled=cancelled)
    assert not output.exists() or not list(output.iterdir())


def test_tampered_world_cannot_inherit_native_evidence(packaged, tmp_path):
    root, worlds = packaged
    source = worlds["warehouse"]
    altered = read_json(source.path / "world.json")
    altered["task"] = "Changed without a new world version"

    def members(bundle):
        for path in source.path.rglob("*"):
            if path.is_file():
                name = path.relative_to(source.path).as_posix()
                bundle.writestr(name, json.dumps(altered).encode() if name == "world.json" else path.read_bytes())

    rewrite_archive(root, "warehouse", members)
    with pytest.raises(ValueError, match="validation evidence was modified"):
        starters.add_starter("warehouse", tmp_path / "workspace")
    assert not list((tmp_path / "workspace").iterdir())


def test_existing_prefix_conflict_is_preserved(packaged, tmp_path):
    _, worlds = packaged
    destination = tmp_path / "workspace" / worlds["warehouse"].id[:20]
    shutil.copytree(worlds["house"].path, destination)
    with pytest.raises(ValueError, match="matching validated-world evidence"):
        starters.add_starter("warehouse", destination.parent)
    assert load(destination).id == worlds["house"].id


def test_unvalidated_preview_is_not_a_ready_starter(packaged, tmp_path):
    root, worlds = packaged
    preview = World.from_spec(worlds["warehouse"].spec, tmp_path / "previews",
                              asset_root=worlds["warehouse"].path, validate=False)
    catalog = read_json(root / "catalog.json")
    catalog["environments"][0].update(bundle_world(root, "warehouse", preview))
    write_json(root / "catalog.json", catalog)
    with pytest.raises(ValueError, match="matching validated-world evidence"):
        starters.add_starter("warehouse", tmp_path / "workspace")
    assert not list((tmp_path / "workspace").iterdir())


def test_unknown_missing_and_unprepared_starters(packaged, tmp_path):
    root, _ = packaged
    with pytest.raises(ValueError, match="Unknown starter"):
        starters.add_starter("../warehouse", tmp_path / "workspace")
    (root / "house" / "world.zip").unlink()
    assert not next(entry for entry in starters.starter_catalog() if entry["id"] == "house")["available"]
    with pytest.raises(FileNotFoundError, match="complete Robot Worlds package"):
        starters.add_starter("house", tmp_path / "workspace")
    catalog = read_json(root / "catalog.json")
    catalog["environments"][0].pop("world_id")
    write_json(root / "catalog.json", catalog)
    assert not starters.starter_catalog()[0]["available"]
    assert starters.starter_preview("warehouse").is_file()
    (root / "warehouse" / "thumbnail.png").unlink()
    with pytest.raises(FileNotFoundError, match="preview is not installed"):
        starters.starter_preview("warehouse")


def test_four_starter_api_jobs_complete_without_key_and_reopen(packaged, tmp_path):
    _, worlds = packaged
    queue, workspace = Queue(), tmp_path / "api"
    with TestClient(create_app(workspace, executor_factory=lambda: queue)) as client:
        assert client.get("/api/health").json()["key_configured"] is False
        catalog = client.get("/api/starters").json()
        assert len(catalog) == 4 and all(entry["available"] for entry in catalog)
        for entry in catalog:
            assert client.get(f"/api/starters/{entry['id']}/preview").status_code == 200
            response = client.post(f"/api/starters/{entry['id']}/add")
            assert response.status_code == 202
            job = response.json()
            assert job["kind"] == "starter" and job["status"] == "queued"
            queue.finish()
            completed = client.get(f"/api/jobs/{job['id']}").json()
            assert completed["status"] == "completed", completed
            assert completed["world_id"] == worlds[entry["id"]].id
            reopened = client.get(f"/api/worlds/{completed['world_id']}").json()
            assert reopened["manifest"]["id"] == completed["world_id"]
            assert reopened["manifest"]["validation"]["status"] == "ready"
        again = client.post("/api/starters/warehouse/add").json()
        queue.finish()
        assert client.get(f"/api/jobs/{again['id']}").json()["status"] == "completed"
        assert len(client.get("/api/worlds").json()) == 4
        assert client.post("/api/starters/unknown/add").status_code == 404
        assert client.get("/api/starters/unknown/preview").status_code == 404
        assert client.post("/api/starters/warehouse/add", headers={"Origin": "https://external.example"}).status_code == 403

    # Persistence survives a fresh app process/lifecycle without re-adding scenes.
    with TestClient(create_app(workspace, executor_factory=Queue)) as client:
        assert len(client.get("/api/worlds").json()) == 4
        assert client.get(f"/api/worlds/{worlds['warehouse'].id}").status_code == 200


def test_starter_api_cancel_and_missing_bundle(packaged, tmp_path):
    root, _ = packaged
    queue = Queue()
    with TestClient(create_app(tmp_path / "api", executor_factory=lambda: queue)) as client:
        job = client.post("/api/starters/warehouse/add").json()
        assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
        assert queue.jobs[0][0].cancelled()
        assert not client.get("/api/worlds").json()
        (root / "house" / "world.zip").unlink()
        assert client.post("/api/starters/house/add").status_code == 503


def test_starter_api_corrupt_bundle_is_inspectable_failure(packaged, tmp_path):
    root, _ = packaged
    archive = root / "warehouse" / "world.zip"
    archive.write_bytes(archive.read_bytes() + b"tampered")
    queue = Queue()
    with TestClient(create_app(tmp_path / "api", executor_factory=lambda: queue)) as client:
        job = client.post("/api/starters/warehouse/add").json()
        queue.finish()
        record = client.get(f"/api/jobs/{job['id']}").json()
        assert record["status"] == "failed"
        assert "checksum mismatch" in record["error"]["message"]
        assert not client.get("/api/worlds").json()
