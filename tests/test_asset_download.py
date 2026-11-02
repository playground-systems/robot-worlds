import io
import json
import stat
import zipfile
from dataclasses import replace

import httpx
import pytest

from robot_worlds import AssetDownloadError, download_fuel_asset, source_asset
from robot_worlds.asset_sourcing import AssetCandidate, AssetSearch


def candidate():
    source = "https://fuel.gazebosim.org/1.0/Research/models/White%20bowl/1"
    return AssetCandidate("fuel:Research/White bowl", source, "1", "CC BY 4.0", {
        "owner": "Research", "name": "White bowl", "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "download_url": source + "/White%20bowl.zip"})


def package(entries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (entries or [("model.config", b"config"), ("meshes/bowl.obj", b"original OBJ")]):
            archive.writestr(name, content)
    return stream.getvalue()


def client_for(content=None, **response_kwargs):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(response_kwargs.pop("status_code", 200),
                              content=content if content is not None else package(),
                              headers=response_kwargs.pop("headers", {"content-type": "application/zip",
                                                                       "X-Ign-Resource-Version": "1"}),
                              **response_kwargs)
    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def test_search_source_download_reuses_verified_cache_without_network(tmp_path):
    selected = candidate()
    client, requests = client_for()
    with client:
        sourced = source_asset("white bowl", search=lambda q: AssetSearch(q, ("Fuel",), (selected,), True),
                               import_asset=lambda c: download_fuel_asset(c, tmp_path, client=client),
                               create_asset=lambda *args: pytest.fail("Existing source ignored"))
        first = sourced.asset
        second = download_fuel_asset(selected, tmp_path, client=client)
    assert len(requests) == 1
    assert str(requests[0].url) == selected.metadata["download_url"]
    assert not requests[0].url.query
    assert not first.cache_hit and second.cache_hit
    assert first.source_dir == second.source_dir
    assert (second.source_dir / "meshes/bowl.obj").read_bytes() == b"original OBJ"
    manifest = json.loads(second.manifest_path.read_text())
    assert manifest["candidate"]["license"] == selected.license
    assert manifest["candidate"]["source_url"] == selected.source_url
    assert manifest["response_revision"] == "1"
    assert manifest["archive_sha256"] == first.archive_sha256 == second.archive_sha256
    assert len(list(tmp_path.iterdir())) == 1  # No temporary archives remain.


@pytest.mark.parametrize("tamper", ["modify", "delete", "extra", "symlink", "manifest", "provenance"])
def test_modified_cache_is_never_silently_reused_or_overwritten(tmp_path, tamper):
    client, requests = client_for()
    with client:
        first = download_fuel_asset(candidate(), tmp_path, client=client)
        target = first.source_dir / "model.config"
        if tamper == "modify":
            target.write_bytes(b"changed")
        elif tamper == "delete":
            target.unlink()
        elif tamper == "extra":
            (first.source_dir / "extra").write_bytes(b"new")
        elif tamper == "symlink":
            target.unlink()
            target.symlink_to(first.source_dir / "meshes/bowl.obj")
        elif tamper == "manifest":
            first.manifest_path.write_text("not JSON")
        else:
            data = json.loads(first.manifest_path.read_text())
            data["candidate"]["revision"] = "9"
            first.manifest_path.write_text(json.dumps(data))
        with pytest.raises(AssetDownloadError):
            download_fuel_asset(candidate(), tmp_path, client=client)
    assert len(requests) == 1


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "a\\escape", "C:/escape", "a/./b", "a//b"])
def test_zip_paths_cannot_escape_or_alias(tmp_path, name):
    client, _ = client_for(package([(name, b"bad")]))
    with client, pytest.raises(AssetDownloadError, match="unsafe path"):
        download_fuel_asset(candidate(), tmp_path, client=client)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entries", [
    [("same", b"1"), ("SAME", b"2")],
    [("parent", b"file"), ("parent/child", b"child")],
    [("parent/child", b"child"), ("parent", b"file")],
    [("Parent/one", b"one"), ("parent/two", b"two")],
    [("caf\u00e9.obj", b"one"), ("cafe\u0301.obj", b"two")],
])
def test_duplicate_or_conflicting_entries_fail_atomically(tmp_path, entries):
    client, _ = client_for(package(entries))
    with client, pytest.raises(AssetDownloadError):
        download_fuel_asset(candidate(), tmp_path, client=client)
    assert list(tmp_path.iterdir()) == []


def test_zip_symlink_is_rejected(tmp_path):
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    client, _ = client_for(package([(link, b"/outside")]))
    with client, pytest.raises(AssetDownloadError, match="link or special"):
        download_fuel_asset(candidate(), tmp_path, client=client)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["redirect", "referral", "revision", "partial", "bad_zip", "crc", "download_limit", "file_limit", "total_limit", "entry_limit"])
def test_failed_acquisition_does_not_authorize_creation_or_publish_cache(tmp_path, monkeypatch, failure):
    from robot_worlds import asset_download
    body, kwargs = package(), {}
    if failure == "redirect":
        kwargs = {"status_code": 302, "headers": {"Location": "https://example.com/asset.zip"}}
    elif failure == "referral":
        body, kwargs = b"https://example.com/asset.zip", {"headers": {"content-type": "text/plain"}}
    elif failure == "revision":
        kwargs = {"headers": {"content-type": "application/zip", "X-Ign-Resource-Version": "2"}}
    elif failure == "partial":
        kwargs = {"status_code": 206}
    elif failure == "bad_zip":
        body = b"not a ZIP"
    elif failure == "crc":
        broken = bytearray(body)
        # Flip the expected CRC in the central directory, retaining a valid ZIP structure.
        offset = body.index(b"PK\x01\x02") + 16
        broken[offset] ^= 1
        body = bytes(broken)
    else:
        setting = {"download_limit": "MAX_ARCHIVE_BYTES", "file_limit": "MAX_FILE_BYTES",
                   "total_limit": "MAX_EXTRACTED_BYTES", "entry_limit": "MAX_ENTRIES"}[failure]
        monkeypatch.setattr(asset_download, setting, 1)
    client, requests = client_for(body, **kwargs)
    selected = candidate()
    with client, pytest.raises(AssetDownloadError):
        source_asset("bowl", search=lambda q: AssetSearch(q, ("Fuel",), (selected,), True),
                     import_asset=lambda c: download_fuel_asset(c, tmp_path, client=client),
                     create_asset=lambda *args: pytest.fail("Download failure authorized creation"))
    assert len(requests) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", [
    {"source_url": "https://example.com/model/1"}, {"revision": "tip"}, {"revision": "01"},
    {"license": ""}, {"id": "fuel:other/model"},
    {"metadata": {**candidate().metadata, "download_url": "https://example.com/asset.zip"}},
    {"metadata": {**candidate().metadata, "name": "../model"}},
    {"metadata": {**candidate().metadata, "license_url": ""}},
])
def test_invalid_candidate_fails_before_any_request(tmp_path, change):
    client, requests = client_for()
    with client, pytest.raises(ValueError):
        download_fuel_asset(replace(candidate(), **change), tmp_path, client=client)
    assert not requests
    assert list(tmp_path.iterdir()) == []


def test_deadline_failure_cleans_up(tmp_path, monkeypatch):
    from robot_worlds import asset_download
    clock = [0.0]
    monkeypatch.setattr(asset_download.time, "monotonic", lambda: clock[0])

    def handle(request):
        clock[0] = 2.0
        return httpx.Response(200, content=package(), headers={"content-type": "application/zip"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client, pytest.raises(AssetDownloadError, match="time budget"):
        download_fuel_asset(candidate(), tmp_path, client=client, timeout=1)
    assert list(tmp_path.iterdir()) == []


def test_interrupted_stream_leaves_nothing_and_retry_succeeds(tmp_path):
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial source"
            raise httpx.ReadError("connection closed")

    attempts = []

    def handle(request):
        attempts.append(request)
        headers = {"content-type": "application/zip"}
        if len(attempts) == 1:
            return httpx.Response(200, stream=Interrupted(), headers=headers)
        return httpx.Response(200, content=package(), headers=headers)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(AssetDownloadError):
            download_fuel_asset(candidate(), tmp_path, client=client)
        assert list(tmp_path.iterdir()) == []
        result = download_fuel_asset(candidate(), tmp_path, client=client)
        assert not result.cache_hit
        assert (result.source_dir / "model.config").read_bytes() == b"config"
    assert len(attempts) == 2
