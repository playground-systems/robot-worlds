"""Acquisition tests only: no conversion, simulator, model or real HTTP calls."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from robot_worlds import simready, source_asset
from robot_worlds.asset_download import AssetDownloadError


@pytest.fixture
def source(tmp_path, monkeypatch):
    body = b"original source GLB bytes" * 100
    catalog = simready._catalog()
    catalog["selected"] = [catalog["selected"][0]]
    row = catalog["selected"][0]
    row.update(download_sha256=hashlib.sha256(body).hexdigest(), download_bytes=len(body))
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    monkeypatch.setattr(simready, "_CATALOG", path)
    return row["asset_id"], body, tmp_path / "cache"


def test_catalog_has_pinned_licensed_candidates_and_unknown_is_unresolved():
    candidates = simready.simready_asset_catalog()
    assert len(candidates) == 5
    for item in candidates:
        assert len(item.revision) == 40 and item.revision in item.source_url
        assert item.license == item.metadata["upstream_packaging"]["license"] == "CC-BY-4.0"
        assert item.metadata["compatibility"].startswith("source_only")
        assert item.metadata["download_bytes"] == item.metadata["optimized_manifest_record"]["optimized"]["bytes"]
    with pytest.raises(ValueError, match="Unknown"):
        simready.simready_asset_search("missing-shelf")


def test_source_acquisition_and_offline_cache_keep_provenance(source):
    name, body, cache = source
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        sourced = source_asset(name, search=simready.simready_asset_search,
            import_asset=lambda candidate: simready.download_simready_asset(
                candidate.metadata["asset_id"], cache, client=client),
            create_asset=lambda *args: pytest.fail("Existing asset must not be recreated"))
        first = sourced.asset
        assert not first.cache_hit and first.glb_path.read_bytes() == body
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("Cache used network"))) as offline:
        second = simready.download_simready_asset(name, cache, client=offline)
    assert second.cache_hit and second.glb_path == first.glb_path
    assert len(requests) == 1
    assert sourced.decision == "reuse_or_adapt"
    assert "NVIDIA" in second.notice_path.read_text()
    assert "CodePawl" in second.notice_path.read_text()
    record = json.loads(second.metadata_path.read_text())
    assert record["source"]["download_sha256"] == hashlib.sha256(body).hexdigest()
    assert record["status"] == "source_only_not_simulation_ready"
    assert len(list(cache.iterdir())) == 1


@pytest.mark.parametrize("tamper", ["bytes", "missing", "extra", "symlink", "metadata", "notice"])
def test_corrupt_cache_fails_without_overwrite(source, tamper):
    name, body, cache = source
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as client:
        saved = simready.download_simready_asset(name, cache, client=client)
    if tamper == "bytes":
        saved.glb_path.write_bytes(b"x" * len(body))
    elif tamper == "missing":
        saved.glb_path.unlink()
    elif tamper == "extra":
        (saved.glb_path.parent / "extra").write_text("unexpected")
    elif tamper == "symlink":
        saved.glb_path.unlink()
        saved.glb_path.symlink_to(saved.notice_path)
    else:
        target = saved.metadata_path if tamper == "metadata" else saved.notice_path
        target.write_bytes(b"x" * target.stat().st_size)
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("No redownload"))) as offline:
        with pytest.raises(AssetDownloadError, match="cache"):
            simready.download_simready_asset(name, cache, client=offline)


@pytest.mark.parametrize("failure", ["short", "long", "hash", "partial", "network", "redirect", "loop"])
def test_failed_download_never_publishes(source, failure):
    name, body, cache = source

    def handle(request):
        if failure == "network":
            raise httpx.ReadTimeout("provider secret")
        if failure in {"redirect", "loop"}:
            return httpx.Response(302, headers={"Location": "https://untrusted.example/file" if failure == "redirect" else str(request.url)})
        payload = {"short": body[:-1], "long": body+b"x", "hash": b"x"*len(body)}.get(failure, body)
        return httpx.Response(206 if failure == "partial" else 200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        with pytest.raises(AssetDownloadError) as error:
            simready.download_simready_asset(name, cache, client=client)
    assert "provider secret" not in str(error.value)
    assert list(cache.iterdir()) == []


def test_trusted_cdn_redirect_is_bounded_and_content_verified(source):
    name, body, cache = source
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "https://cas-bridge.xethub.hf.co/source"})
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert simready.download_simready_asset(name, cache, client=client).glb_path.read_bytes() == body
    assert len(requests) == 2


def test_cancelled_acquisition_does_not_create_cache(source):
    name, _, cache = source
    with pytest.raises(AssetDownloadError, match="cancelled"):
        simready.download_simready_asset(name, cache, cancelled=lambda: True)
    assert not cache.exists()


def test_timeout_during_stream_leaves_no_partial_cache(source, monkeypatch):
    name, body, cache = source
    tick = [0.]
    monkeypatch.setattr(simready.time, "monotonic", lambda: tick[0])

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            tick[0] = 5.
            yield body

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))) as client:
        with pytest.raises(AssetDownloadError, match="time budget"):
            simready.download_simready_asset(name, cache, client=client, timeout=1)
    assert list(cache.iterdir()) == []


def test_concurrent_requests_publish_one_verified_entry(source):
    name, body, cache = source
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: simready.download_simready_asset(name, cache, client=client), range(2)))
    assert results[0].glb_path == results[1].glb_path
    assert results[0].glb_path.read_bytes() == body
    assert len(list(cache.iterdir())) == 1


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, 601])
def test_invalid_budget_rejected_before_io(source, timeout):
    name, _, cache = source
    with pytest.raises(ValueError, match="timeout"):
        simready.download_simready_asset(name, cache, timeout=timeout)
    assert not cache.exists()
