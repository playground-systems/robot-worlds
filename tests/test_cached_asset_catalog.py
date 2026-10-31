"""Later generations reuse source imports without acquisition or selection calls."""
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_asset_resolution import final_plan, request, selection
from test_asset_resolution import source as source
from test_generator import Client

from robot_worlds import AssetLibrary, FuelAssetResolver, World, load, prepared_asset_catalog
from robot_worlds import asset_resolution as resolution
from robot_worlds.artifacts import digest, write_json
from robot_worlds.generator import GenerationCancelled, generate_spec


def alias(req=None):
    return "cached_" + resolution._request_key(req or request())


def test_later_prompt_uses_cached_catalog_with_one_model_call_and_no_source_files(tmp_path, source, monkeypatch):
    cache = tmp_path / "cache"
    FuelAssetResolver(cache, client=Client([selection()]))(request())
    shutil.rmtree(source[0])
    shutil.rmtree(cache / "downloads")
    shutil.rmtree(cache / "collision")
    monkeypatch.setattr(resolution, "search_online_assets", lambda *_: pytest.fail("Cached source was searched again"))
    monkeypatch.setattr(resolution, "download_fuel_asset", lambda *_: pytest.fail("Cached source was downloaded again"))
    events = []
    model = Client([final_plan(alias())])
    spec = generate_spec("A different layout with the painted object", client=model, max_repairs=0,
                         online_asset_cache=cache, online_assets=False, event_callback=events.append)
    assert len(model.prompts) == 1
    catalog = json.loads(model.prompts[0].split("Prepared source asset catalog:\n")[1].split("\nResource profile:")[0])
    item = next(item for item in catalog if item["asset_id"] == alias())
    assert item["movable"] is True and item["physical_mass_kg"] == pytest.approx(.3)
    assert item["mass_policy"] == "preserve_import"
    assert item["size"] == pytest.approx([.2]*3)
    assert item["body_ids"]["object"] == "{prefix}_part0"
    assert len([e for e in events if e["type"] == "asset_cache_catalog" and e["assets"] == 1]) == 1
    assert not any(e["type"] == "asset_requests" for e in events)
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    shutil.rmtree(cache)
    assert load(world.path).id == world.id
    assert world.export(tmp_path / "world.zip").exists()


def test_same_requested_name_different_mass_has_distinct_stable_catalog_ids(tmp_path, source):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection(), selection()]))
    resolver(request())
    heavier = request(mass=.5)
    resolver(heavier)
    library, catalog = resolver.cached_catalog(None, prepared_asset_catalog())
    entries = {item["asset_id"]: item for item in catalog if item["asset_id"].startswith("cached_")}
    assert set(library.asset_ids) == {alias(), alias(heavier)}
    assert entries[alias()]["physical_mass_kg"] == pytest.approx(.3)
    assert entries[alias(heavier)]["physical_mass_kg"] == pytest.approx(.5)
    assert entries[alias()]["movable"] and entries[alias(heavier)]["movable"]


def test_installed_library_is_preserved_and_not_shadowed(tmp_path, source):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]))
    sourced = resolver(request())
    installed = AssetLibrary().add(alias(), sourced, description="Owner-installed description")
    before = prepared_asset_catalog(installed)
    combined, catalog = resolver.cached_catalog(installed, before)
    assert combined is not installed and catalog == before
    assert installed.asset_ids == (alias(),)
    assert combined.description(alias()) == "Owner-installed description"


def test_optional_cache_catalog_reserves_capacity_for_new_requests(tmp_path, source, monkeypatch):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]))
    sourced = resolver(request())
    installed = AssetLibrary()
    for i in range(24):
        installed.add(f"installed{i}", sourced, description="Owner-installed source")
    monkeypatch.setattr(resolver, "_cached", lambda *_args, **_kwargs: pytest.fail("Optional cache consumed new-request capacity"))
    combined, catalog = resolver.cached_catalog(installed, prepared_asset_catalog(installed))
    assert combined.asset_ids == installed.asset_ids and len(catalog) == 26


def test_corrupt_cached_asset_is_reported_without_mutating_it_or_installed_library(tmp_path, source, monkeypatch):
    events = []
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]), on_event=events.append)
    sourced = resolver(request())
    mesh = Path(sourced.asset.objects[0].mesh_path)
    mesh.write_text("broken source")
    monkeypatch.setattr(resolution, "search_online_assets", lambda *_: pytest.fail("Corrupt cache triggered a search"))
    library, catalog = resolver.cached_catalog(None, prepared_asset_catalog())
    assert library.asset_ids == () and len(catalog) == 2
    assert mesh.read_text() == "broken source"
    assert any(e["type"] == "asset_cache_unavailable" for e in events)
    with pytest.raises(ValueError):
        resolver(request())


@pytest.mark.parametrize("damage", ["json", "key", "path", "oversize"])
def test_bad_index_cannot_import_untrusted_paths_or_invalidate_an_exact_cache_hit(tmp_path, source, damage):
    events = []
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]), on_event=events.append)
    resolver(request())
    index = tmp_path / "cache/resolved-index.json"
    payload = json.loads(index.read_text())
    if damage == "json":
        index.write_text("not JSON")
    elif damage == "oversize":
        index.write_bytes(b" " * (128*1024+1))
    else:
        payload.pop("id")
        payload["entries"][0]["key"] = "../outside" if damage == "path" else "0"*64
        write_json(index, {**payload, "id": digest(payload)})
    before = index.read_bytes()
    installed = prepared_asset_catalog()
    library, catalog = resolver.cached_catalog(None, installed)
    assert library is None and catalog == installed
    assert resolver(request()).asset.objects
    assert index.read_bytes() == before
    assert any(e["type"] == "asset_cache_index_unavailable" for e in events)


def test_recent_index_is_bounded_atomic_and_does_not_lose_concurrent_updates(tmp_path):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([]))
    requests = [request(asset_id=f"source{i}") for i in range(32)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(resolver._remember, requests))
    assert {item["key"] for item in resolver._read_index()} == {resolution._request_key(r) for r in requests}
    last = request(asset_id="latest")
    resolver._remember(last)
    entries = resolver._read_index()
    assert len(entries) == 32 and entries[0]["key"] == resolution._request_key(last)
    resolver._remember(last)
    assert resolver._read_index() == entries
    for i in range(32):
        resolver._remember(request(asset_id=f"unicode{i}", description="🌏"*2000))
    assert (tmp_path / "cache/resolved-index.json").stat().st_size <= 128*1024
    assert resolver._read_index()[0]["request"]["asset_id"] == "unicode31"


def test_catalog_only_inspects_eight_recent_entries_and_obeys_planning_budget(tmp_path, source, monkeypatch):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]))
    sourced = resolver(request())
    for i in range(12):
        resolver._remember(request(asset_id=f"scan{i}"))
    calls = []
    def cached(path, req, **kwargs):
        calls.append(req.asset_id)
        from robot_worlds.asset_library import _asset, _payload, _rename
        from robot_worlds.asset_sourcing import SourcedAsset
        return SourcedAsset(_rename(_asset(_payload(sourced.asset)), "asset"), sourced.decision,
                            sourced.search, sourced.selected, sourced.rejected)
    monkeypatch.setattr(resolver, "_cached", cached)
    library, catalog = resolver.cached_catalog(None, prepared_asset_catalog())
    assert len(calls) == 8 and len(library.asset_ids) == 8 and len(catalog) == 10
    events = []
    resolver.emit = events.append
    base = [{"asset_id": "large-existing", "requirements": "x"*199_900}]
    library, catalog = resolver.cached_catalog(None, base)
    assert library.asset_ids == () and catalog == base
    assert any(e["type"] == "asset_cache_unavailable" for e in events)


def test_cancelled_cached_catalog_stops_before_any_model_request(tmp_path):
    with pytest.raises(GenerationCancelled):
        generate_spec("Use cached sources", client=Client([]), online_asset_cache=tmp_path / "cache",
                      cancelled=lambda: True)
