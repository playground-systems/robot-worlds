"""Batch source selection retains per-request provenance and physical checks."""
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image
from test_asset_resolution import draft, request, selection
from test_asset_resolution import source as source
from test_generator import Client
from test_sdf_import import candidate, package

from robot_worlds import FuelAssetResolver
from robot_worlds import asset_resolution as resolution
from robot_worlds.asset_download import download_fuel_asset
from robot_worlds.asset_sourcing import AssetSearch
from robot_worlds.collision_preparation import CollisionPreparationCancelled
from robot_worlds.generated_assets import AssetCreationEvidence
from robot_worlds.generator import GenerationError, generate_spec


def batch_selection(requests):
    return {"selections": [{"asset_id": item.asset_id, **selection()} for item in reversed(requests)]}


def distinct_sources(tmp_path, monkeypatch):
    roots = [package(tmp_path / name) for name in ("blue-source", "red-source")]
    Image.new("RGB", (2, 2), (220, 40, 30)).save(roots[1] / "materials/textures/surface.png")
    first = candidate()
    url = "https://fuel.gazebosim.org/1.0/FixtureOwner/models/SecondFixture/1"
    second = replace(first, id="fuel:FixtureOwner/SecondFixture", source_url=url,
                     metadata={**first.metadata, "name": "SecondFixture", "download_url": url+"/SecondFixture.zip"})
    requests = [request(asset_id="blue-source", query="synthetic blue fixture"),
                request(asset_id="red-source", query="synthetic red fixture", mass=.6)]
    candidates = [first, second]
    by_query = {req.query: item for req, item in zip(requests, candidates)}
    archives = {}
    for root, item in zip(roots, candidates):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        archives[item.id] = stream.getvalue()
    monkeypatch.setattr(resolution, "search_online_assets", lambda query:
                        AssetSearch(query, ("Synthetic fixture catalog",), (by_query[query],), True))
    def download(item, cache):
        def serve(request):
            assert str(request.url) == item.metadata["download_url"]
            return httpx.Response(200, content=archives[item.id], headers={"content-type": "application/zip"})
        with httpx.Client(transport=httpx.MockTransport(serve)) as client:
            return download_fuel_asset(item, cache, client=client)
    monkeypatch.setattr(resolution, "download_fuel_asset", download)
    response = {"selections": [{"asset_id": req.asset_id, "candidate_id": item.id, "reason": "Synthetic matching color source"}
                                for req, item in reversed(list(zip(requests, candidates)))]}
    return requests, response, candidates


def test_distinct_sources_keep_their_textures_and_selection_provenance(tmp_path, monkeypatch):
    requests, response, candidates = distinct_sources(tmp_path, monkeypatch)
    client = Client([response])
    results = FuelAssetResolver(tmp_path / "cache", client=client).resolve_many(requests)
    assert len(client.prompts) == 1
    textures = []
    for req, expected, result in zip(requests, candidates, results):
        assert result.selected == expected
        assert result.search.query == req.query
        assert result.search.evidence["selection"]["candidate_id"] == expected.id
        assert sum(obj.mass for obj in result.asset.objects if obj.collision) == pytest.approx(req.mass)
        textures.append(next(Path(obj.texture_path).read_bytes() for obj in result.asset.objects if obj.texture_path))
    assert textures[0] != textures[1]


def test_eight_requests_share_search_and_selection_without_mixing_mass_or_evidence(tmp_path, source, monkeypatch):
    requests = [request(asset_id=f"scan{i}", mass=(i+1)/10) for i in range(8)]
    client, events = Client([batch_selection(requests)]), []
    resolver = FuelAssetResolver(tmp_path / "cache", client=client, on_event=events.append)
    results = resolver.resolve_many(requests)
    assert len(client.prompts) == 1
    assert [entry for entry in source[1] if entry[0] == "search"] == [("search", request().query)]
    payload = json.loads(client.prompts[0])
    assert len(payload["requests"]) == 8
    for expected, result in zip(requests, results):
        assert result.search.evidence["resolution_request"] == expected.model_dump(mode="json")
        assert result.search.query == expected.query and result.selected == candidate()
        assert sum(obj.mass for obj in result.asset.objects if obj.collision) == pytest.approx(expected.mass)
    assert len([e for e in events if e["type"] == "asset_search_reused"]) == 7
    monkeypatch.setattr(resolution, "search_online_assets", lambda *_: pytest.fail("Cached batch searched again"))
    assert len(resolver.resolve_many(requests)) == 8
    assert len(client.prompts) == 1


@pytest.mark.parametrize("failure", ["missing", "duplicate", "extra", "invented", "none"])
def test_invalid_batch_choices_never_download_or_publish_a_preview(tmp_path, source, monkeypatch, failure):
    requests = [request(asset_id="first"), request(asset_id="second")]
    response = batch_selection(requests)
    rows = response["selections"]
    if failure == "missing":
        rows.pop()
    elif failure == "duplicate":
        rows[0]["asset_id"] = rows[1]["asset_id"]
    elif failure == "extra":
        rows[0]["asset_id"] = "not_requested"
    elif failure == "invented":
        rows[0]["candidate_id"] = "not_in_search"
    else:
        rows[0]["candidate_id"] = None
    monkeypatch.setattr(resolution, "download_fuel_asset", lambda *_: pytest.fail("Invalid choices started a download"))
    plan = draft()
    plan["asset_requests"] = [item.model_dump(mode="json") for item in requests]
    client, previews = Client([plan, response]), []
    with pytest.raises(GenerationError):
        generate_spec("Use both sources", client=client, online_asset_cache=tmp_path / "cache", on_candidate=previews.append)
    assert not previews and len(client.prompts) == 2
    assert not (tmp_path / "cache/resolved").exists()


def test_candidate_must_belong_to_its_own_request_results(tmp_path, monkeypatch):
    requests = [request(asset_id="first", query="one"), request(asset_id="second", query="two")]
    items = {query: replace(candidate(), id=f"fixture:{query}") for query in ("one", "two")}
    monkeypatch.setattr(resolution, "search_online_assets", lambda query: AssetSearch(query, ("Test catalog",), (items[query],), True))
    monkeypatch.setattr(resolution, "download_fuel_asset", lambda *_: pytest.fail("Cross-request selection downloaded"))
    rows = [{"asset_id": item.asset_id, "candidate_id": items[other].id, "reason": "Wrong request scope"}
            for item, other in zip(requests, ("two", "one"))]
    with pytest.raises(resolution.AssetResolutionError, match="another request"):
        FuelAssetResolver(tmp_path, client=Client([{"selections": rows}])).resolve_many(requests)


def test_large_metadata_splits_calls_without_truncating_evidence(tmp_path, monkeypatch):
    description = "x"*130_000
    item = replace(candidate(), metadata={**candidate().metadata, "description": description})
    monkeypatch.setattr(resolution, "search_online_assets", lambda query: AssetSearch(query, ("Test catalog",), (item,), True))
    requests = [request(asset_id="first", query="one"), request(asset_id="second", query="two")]
    client = Client([selection(), selection()])
    resolver = FuelAssetResolver(tmp_path, client=client)
    imported = []
    monkeypatch.setattr(resolver, "_import_selected", lambda req, *_: imported.append(req.asset_id) or req.asset_id)
    assert resolver.resolve_many(requests) == ["first", "second"]
    assert imported == ["first", "second"] and len(client.prompts) == 2
    for prompt in client.prompts:
        assert len(prompt) <= 200_000
        assert json.loads(prompt)["candidates"][0]["metadata"]["description"] == description


def test_later_chunk_failure_stops_before_any_new_import(tmp_path, monkeypatch):
    item = replace(candidate(), metadata={**candidate().metadata, "description": "x"*130_000})
    monkeypatch.setattr(resolution, "search_online_assets", lambda query: AssetSearch(query, ("Test catalog",), (item,), True))
    resolver = FuelAssetResolver(tmp_path, client=Client([selection(), {"candidate_id": None, "reason": "No match"}]))
    monkeypatch.setattr(resolver, "_import_selected", lambda *_: pytest.fail("Partial selection imported early"))
    with pytest.raises(resolution.AssetResolutionError):
        resolver.resolve_many([request(asset_id="first"), request(asset_id="second")])


def test_cached_and_empty_requests_do_not_consume_selection_slots(tmp_path, source, monkeypatch):
    root = tmp_path / "cache"
    cached = request(asset_id="cached")
    FuelAssetResolver(root, client=Client([selection()]))(cached)
    searches = []
    def search(query):
        searches.append(query)
        return AssetSearch(query, ("Test catalog",), () if query == "empty" else (candidate(),), True)
    monkeypatch.setattr(resolution, "search_online_assets", search)
    client = Client([selection()])
    requests = [cached, request(asset_id="missing", query="empty"), request(asset_id="fresh", mass=.5)]
    results = FuelAssetResolver(root, client=client).resolve_many(requests)
    assert isinstance(results[1], AssetCreationEvidence)
    assert results[0].selected == results[2].selected == candidate()
    assert searches == ["empty", request().query] and len(client.prompts) == 1
    assert json.loads(client.prompts[0])["request"]["asset_id"] == "fresh"


def test_cancellation_after_selection_prevents_all_imports(tmp_path, source, monkeypatch):
    cancelled = [False]
    requests = [request(asset_id="first"), request(asset_id="second")]
    class CancelAfterChoice:
        def complete_json(self, *_):
            cancelled[0] = True
            return batch_selection(requests)
    resolver = FuelAssetResolver(tmp_path, client=CancelAfterChoice(), cancelled=lambda: cancelled[0])
    monkeypatch.setattr(resolver, "_import_selected", lambda *_: pytest.fail("Cancelled batch imported"))
    with pytest.raises(CollisionPreparationCancelled):
        resolver.resolve_many(requests)


def test_batch_limits_and_strict_response_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(resolution, "search_online_assets", lambda *_: pytest.fail("Invalid batch searched"))
    resolver = FuelAssetResolver(tmp_path, client=Client([]))
    assert resolver.resolve_many([]) == []
    for requests in ([request(), request()], [request(asset_id=f"asset{i}") for i in range(9)]):
        with pytest.raises(ValueError):
            resolver.resolve_many(requests)
    from robot_worlds.llm import strict_schema
    schema = strict_schema(resolution._BatchSelection.model_json_schema())
    fields = schema["$defs"]["_RequestedSelection"]
    assert fields["additionalProperties"] is False
    assert set(fields["required"]) == {"asset_id", "candidate_id", "reason"}


def test_custom_batch_resolver_must_return_one_result_per_request():
    class Incomplete:
        def resolve_many(self, requests):
            return []
    with pytest.raises(resolution.AssetResolutionError, match="wrong number"):
        resolution.extend_asset_library(None, [request()], Incomplete(), [])
