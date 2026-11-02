"""Small mock ZIP checks for the offline hospital Fuel acquisition step."""
import hashlib
import importlib.util
import io
import json
import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from robot_worlds.asset_download import AssetDownloadError

MODULE = Path(__file__).resolve().parents[1] / "tools/starters/fuel.py"
specification = importlib.util.spec_from_file_location("starter_fuel", MODULE)
fuel = importlib.util.module_from_spec(specification)
specification.loader.exec_module(fuel)


def fixture_source(tmp_path, *, extra=()):
    content = {
        "model.config": b"<model><name>Fixture model</name><author><name>Fixture author</name></author></model>",
        "model.sdf": b"<sdf><model name='fixture'><static>true</static></model></sdf>",
        "meshes/source.obj": b"# source fixture geometry, no conversion in this test\n",
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in content.items():
            archive.writestr(name, data)
        for name, data in extra:
            archive.writestr(name, data)
    archive = stream.getvalue()
    (tmp_path / "licenses").mkdir()
    legalcode = b"Fixture license text; the real manifest pins the full official CC BY 4.0 legalcode."
    (tmp_path / "licenses/CC-BY-4.0.txt").write_bytes(legalcode)
    model = {"name": "CGMClassic", "owner": "OpenRobotics", "version": 4, "license": "CC-BY-4.0",
             "license_url": "https://creativecommons.org/licenses/by/4.0/", "author": "Fixture author",
             "source_url": "https://app.gazebosim.org/OpenRobotics/fuel/models/CGMClassic",
             "metadata_url": "https://fuel.gazebosim.org/1.0/OpenRobotics/models/CGMClassic",
             "download_url": "https://fuel.gazebosim.org/1.0/OpenRobotics/models/CGMClassic/4/CGMClassic.zip",
             "archive_bytes": len(archive), "expected_files": list(content),
             "expected_file_sha256": {name: hashlib.sha256(content[name]).hexdigest()
                                      for name in ("model.config", "model.sdf")}}
    manifest = {"models": [model], "license_legalcode_sha256": hashlib.sha256(legalcode).hexdigest()}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=archive, headers={"content-type": "application/zip",
                                                           "X-Ign-Resource-Version": "4"})
    return path, manifest, content, httpx.Client(transport=httpx.MockTransport(handle)), requests


def test_hospital_fuel_retains_exact_source_authors_license_and_cache(tmp_path):
    manifest, _, content, client, requests = fixture_source(tmp_path)
    source, cache = tmp_path / "source", tmp_path / "cache"
    with client:
        records = fuel.acquire_hospital_fuel(source, cache, manifest_path=manifest, client=client)
        assert fuel.acquire_hospital_fuel(source, cache, manifest_path=manifest, client=client) == records
    assert len(requests) == 1
    assert str(requests[0].url).endswith("/CGMClassic/4/CGMClassic.zip")
    record = records[0]
    assert record["name"] == "CGMClassic" and record["version"] == 4
    assert record["license"] == "CC-BY-4.0" and record["author"] == "Fixture author"
    assert record["original_model_config"] == content["model.config"].decode()
    assert len(record["archive_sha256"]) == 64
    for name, data in content.items():
        assert (source / "models/CGMClassic" / name).read_bytes() == data
    notice = (source / "FUEL-NOTICE.txt").read_text()
    assert "Fixture author" in notice and "Publisher: OpenRobotics" in notice
    assert "Robot Worlds converts source visuals/materials" in notice
    assert "Fixture license text" in notice and record["source_url"] in notice
    assert json.loads((source / "fuel-sources.json").read_text()) == records


@pytest.mark.parametrize("change", ["size", "files", "hash", "archive_hash", "author", "license", "url", "legalcode"])
def test_reviewed_hospital_metadata_cannot_change_silently(tmp_path, change):
    path, manifest, _, client, _ = fixture_source(tmp_path)
    model = manifest["models"][0]
    if change == "size":
        model["archive_bytes"] -= 1
    elif change == "files":
        model["expected_files"].append("missing.obj")
    elif change == "hash":
        model["expected_file_sha256"]["model.sdf"] = "0" * 64
    elif change == "archive_hash":
        model["expected_archive_sha256"] = "0" * 64
    elif change == "author":
        model["author"] = "Invented author"
    elif change == "license":
        model["license"] = "MIT-0"
    elif change == "url":
        model["download_url"] = "https://unreviewed.example/source.zip"
    else:
        manifest["license_legalcode_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with client, pytest.raises((ValueError, AssetDownloadError)):
        fuel.acquire_hospital_fuel(tmp_path / "source", tmp_path / "cache", manifest_path=path, client=client)
    assert not (tmp_path / "source/fuel-sources.json").exists()


@pytest.mark.parametrize("kind", ["traversal", "link", "unexpected"])
def test_hospital_fuel_rejects_unsafe_or_unreviewed_zip_members(tmp_path, kind):
    if kind == "traversal":
        extra = [("../escape", b"unsafe")]
    elif kind == "link":
        entry = zipfile.ZipInfo("meshes/link.obj")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        extra = [(entry, b"source.obj")]
    else:
        extra = [("unexpected.txt", b"unreviewed")]
    path, _, _, client, _ = fixture_source(tmp_path, extra=extra)
    with client, pytest.raises((ValueError, AssetDownloadError)):
        fuel.acquire_hospital_fuel(tmp_path / "source", tmp_path / "cache", manifest_path=path, client=client)
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "source/fuel-sources.json").exists()


def test_real_hospital_manifest_pins_all_43_models_and_full_license():
    manifest = json.loads(fuel.MANIFEST.read_text())
    assert len(manifest["models"]) == len({model["name"] for model in manifest["models"]}) == 43
    assert sum(model["archive_bytes"] for model in manifest["models"]) == 48_885_215
    for model in manifest["models"]:
        assert model["owner"] == "OpenRobotics" and model["license"] == "CC-BY-4.0"
        assert {"model.config", "model.sdf"} <= set(model["expected_files"])
    license_text = fuel.MANIFEST.parent / "licenses/CC-BY-4.0.txt"
    assert hashlib.sha256(license_text.read_bytes()).hexdigest() == manifest["license_legalcode_sha256"]
    assert "Section 3 -- License Conditions." in license_text.read_text()
