"""Real source/import/cache orchestration, synthetic HTTP/model inputs, no physics."""
import copy
import json
import shutil

import pytest
from test_generator import Client, valid
from test_sdf_import import candidate, download_fixture, package

from robot_worlds import AssetLibrary, FuelAssetResolver, SourceAssetRequest, World, generate, load
from robot_worlds import asset_resolution as resolution
from robot_worlds.asset_sourcing import AssetSearch
from robot_worlds.collision_preparation import CollisionPreparationCancelled
from robot_worlds.generator import GenerationCancelled, GenerationError, generate_spec
from robot_worlds.prepared_assets import GenerationSpec, materialize_prepared_assets


def request(**changes):
    return SourceAssetRequest(**{**dict(asset_id="source-scan", query="synthetic painted scan",
        description="Small textured rigid test object", mass=.3, movable=True,
        min_size=[.15, .15, .15], max_size=[.25, .25, .25]), **changes})


def draft(req=None):
    return {**valid(), "asset_requests": [(req or request()).model_dump(mode="json")]}


def final_plan(asset_id="source-scan", prefix="scan"):
    plan = valid()
    plan["objects"] = []
    plan["prepared_assets"] = [dict(id=prefix, asset_id=asset_id, position=[2, 0, .001])]
    plan["rubric"][0]["object_id"] = prefix+"_part0"
    return plan


def selection():
    return dict(candidate_id=candidate().id, reason="Synthetic fixture identity matches the test request")


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = package(tmp_path / "source")
    calls = []
    def search(query):
        calls.append(("search", query))
        return AssetSearch(query, ("Synthetic Fuel response",), (candidate(),), True)
    def download(item, cache):
        assert item == candidate()
        calls.append(("download", item.id))
        return download_fixture(root, cache)
    monkeypatch.setattr(resolution, "search_online_assets", search)
    monkeypatch.setattr(resolution, "download_fuel_asset", download)
    return root, calls


def test_resolver_keeps_source_evidence_and_reuses_without_model_network_or_source(tmp_path, source, monkeypatch):
    client, events = Client([selection()]), []
    resolver = FuelAssetResolver(tmp_path / "cache", client=client, on_event=events.append)
    first = resolver(request())
    assert first.search.evidence["resolution_request"] == request().model_dump(mode="json")
    assert first.selected == candidate()
    assert first.asset.objects[0].mass == pytest.approx(.3)
    assert any(obj.texture_path for obj in first.asset.objects)
    assert len(source[1]) == 2 and len(client.prompts) == 1
    shutil.rmtree(source[0])
    shutil.rmtree(tmp_path / "cache/downloads")
    shutil.rmtree(tmp_path / "cache/collision")
    monkeypatch.setattr(resolution, "search_online_assets", lambda *_: pytest.fail("Cache hit searched online"))
    second = resolver(request())
    assert first.asset.objects == second.asset.objects
    assert events[-1]["type"] == "asset_resolution_reused"
    assert len(client.prompts) == 1


def test_generator_resolves_before_preview_replans_grounded_geometry_and_exports(tmp_path, source):
    client = Client([draft(), selection(), final_plan()])
    previews, events = [], []
    spec = generate_spec("Move the painted source object", client=client, max_repairs=0,
                         online_asset_cache=tmp_path / "cache", on_candidate=previews.append, event_callback=events.append)
    assert len(client.prompts) == 3 and len(previews) == 1
    assert len(spec.objects) == 2 and spec.objects[0].id == "scan_part0"
    assert '"asset_id":"source-scan"' in client.prompts[-1]
    catalog = json.loads(client.prompts[-1].split("Prepared source asset catalog:\n")[1].split("\nResource profile:")[0])
    imported = next(item for item in catalog if item["asset_id"] == "source-scan")
    assert imported["body_ids"]["object"] == "{prefix}_part0"
    assert imported["size"] == pytest.approx([.2]*3)
    assert "Move the painted source object" in client.prompts[-1]
    assert "asset_requests" not in spec.model_dump()
    assert len([event for event in events if event["type"] == "asset_requests"]) == 1
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    shutil.rmtree(tmp_path / "cache")
    assert load(world.path).id == world.id
    metadata = json.loads((world.path / world.spec.objects[0].source_metadata).read_text())
    assert metadata["source_resolution"]["search"]["query"] == request().query
    assert metadata["source_resolution"]["selected"]["license"] == candidate().license
    assert world.export(tmp_path / "world.zip").is_file()


@pytest.mark.parametrize("response", [dict(candidate_id=None, reason="No match"), dict(candidate_id="invented", reason="Match")])
def test_unresolved_selection_never_downloads_or_creates_a_preview(tmp_path, source, response):
    client, previews = Client([draft(), response, valid()]), []
    with pytest.raises(GenerationError, match="no procedural fallback"):
        generate_spec("Use the source", client=client, online_asset_cache=tmp_path / "cache", on_candidate=previews.append)
    assert len(client.prompts) == 2 and not previews
    assert source[1] == [("search", request().query)]


@pytest.mark.parametrize("changes", [dict(movable=False), dict(max_size=[.1]*3, min_size=[0]*3),
                                    dict(requires_articulation=True), dict(requires_cavity=True)])
def test_source_requirements_fail_without_rewriting_physics_or_publishing(tmp_path, source, changes):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]))
    with pytest.raises(resolution.AssetResolutionError):
        resolver(request(**changes))
    assert not (tmp_path / "cache/resolved").exists()


def test_corrupt_cached_asset_is_not_redownloaded_or_regenerated(tmp_path, source, monkeypatch):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]))
    result = resolver(request())
    from pathlib import Path
    Path(result.asset.objects[0].mesh_path).write_text("corrupt")
    monkeypatch.setattr(resolution, "search_online_assets", lambda *_: pytest.fail("Corruption caused a fresh search"))
    with pytest.raises(ValueError):
        resolver(request())


def test_incomplete_search_is_not_passed_to_selection(tmp_path, source, monkeypatch):
    monkeypatch.setattr(resolution, "search_online_assets", lambda query: AssetSearch(query, ("Fuel",), (), False))
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([]))
    with pytest.raises(resolution.AssetResolutionError, match="incomplete"):
        resolver(request())


def test_selection_transport_details_never_enter_saved_generation_errors(tmp_path, source):
    class Broken(Client):
        usage = {"calls": 0}
        def complete_json(self, *args):
            self.usage["calls"] += 1
            if self.usage["calls"] == 2:
                raise RuntimeError("private transport headers must not be saved")
            return super().complete_json(*args)
    with pytest.raises(GenerationError):
        generate("Use source", client=Broken([draft()]), output=tmp_path / "worlds")
    text = next((tmp_path / "worlds").glob("generation-*.json")).read_text()
    assert "private transport headers" not in text
    record = json.loads(text)
    usage = [event for event in record["events"] if event["type"] == "model_usage"]
    assert usage[-1]["usage"]["calls"] == 2
    assert source[1] == [("search", request().query)]


def test_unsupported_source_does_not_attempt_other_assets_or_create_geometry(tmp_path, source):
    path = source[0] / "scan.sdf"
    path.write_text(path.read_text().replace('</model>', '<plugin name="unsupported" filename="unknown.so"/></model>'))
    previews = []
    with pytest.raises(GenerationError):
        generate_spec("Use source", client=Client([draft(), selection(), valid()]),
                      online_asset_cache=tmp_path / "cache", on_candidate=previews.append)
    assert not previews and len(source[1]) == 2
    assert not (tmp_path / "cache/resolved").exists()


def test_cancellation_during_resolution_is_generation_cancellation(tmp_path, source, monkeypatch):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([]), cancelled=lambda: True)
    with pytest.raises(CollisionPreparationCancelled):
        resolver(request())
    assert source[1] == []
    with pytest.raises(GenerationCancelled):
        generate_spec("Use the source", client=Client([draft()]), asset_resolver=resolver)


def test_disabled_resolution_records_terminal_sdk_error_without_model_retry(tmp_path):
    client = Client([draft(), valid()])
    with pytest.raises(GenerationError):
        generate("Use source", client=client, output=tmp_path / "worlds", online_assets=False)
    record = json.loads(next((tmp_path / "worlds").glob("generation-*.json")).read_text())
    assert record["status"] == "incomplete" and not record["versions"]
    assert "disabled" in str(record["errors"])
    assert len(client.prompts) == 1


def test_duplicate_or_existing_ids_fail_before_resolver(tmp_path):
    def forbidden(_):
        pytest.fail("Invalid request launched acquisition")
    for requests in ([request(), request()], [request(asset_id="google-porcelain-bowl")]):
        plan = draft()
        plan["asset_requests"] = [item.model_dump(mode="json") for item in requests]
        with pytest.raises(GenerationError, match="no procedural fallback"):
            generate_spec("Use sources", client=Client([plan]), asset_resolver=forbidden)


def test_resolved_source_cannot_be_dropped_during_replanning(tmp_path, source):
    client = Client([draft(), selection(), valid(), final_plan()])
    result = generate_spec("Use source", client=client, max_repairs=1, online_asset_cache=tmp_path / "cache")
    assert "Requested source assets were dropped" in client.prompts[-1]
    assert result.objects[0].id == "scan_part0"


def test_resolution_rounds_are_bounded_separately_from_repairs(tmp_path, source):
    client = Client([draft(), selection(), draft(request(asset_id="second")), selection(), draft(request(asset_id="third"))])
    with pytest.raises(GenerationError) as caught:
        generate_spec("Use sources", client=client, max_repairs=10, online_asset_cache=tmp_path / "cache")
    assert "two rounds" in str(caught.value.errors)
    assert len(client.prompts) == 5


def test_resolution_does_not_mutate_supplied_library_on_partial_failure(tmp_path, source):
    resolver = FuelAssetResolver(tmp_path / "cache", client=Client([selection()]))
    imported = resolver(request())
    library = AssetLibrary().add("installed", imported, description="Existing source")
    def fail_second(item):
        if item.asset_id == "second":
            raise OSError("failed import")
        return copy.deepcopy(imported)
    with pytest.raises(OSError):
        resolution.extend_asset_library(library, [request(), request(asset_id="second")], fail_second, library.asset_ids)
    assert library.asset_ids == ("installed",)


def test_pending_requests_cannot_materialize_and_model_schema_is_strict():
    from robot_worlds.llm import strict_schema
    with pytest.raises(ValueError, match="Resolve online"):
        materialize_prepared_assets(GenerationSpec.model_validate(draft()), max_objects=5000)
    schema = strict_schema(GenerationSpec.model_json_schema())["$defs"]["SourceAssetRequest"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
