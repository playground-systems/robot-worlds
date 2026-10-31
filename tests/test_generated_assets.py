"""Procedural creation uses recorded empty searches, never lookup/import errors."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from test_asset_resolution import draft, request
from test_generator import Client, valid

from robot_worlds import World, asset_resolution, load
from robot_worlds.asset_sourcing import AssetSearch, AssetSearchIncomplete
from robot_worlds.generated_assets import (
    AssetCreationEvidence,
    CreatedAssetGroup,
    materialize_created_assets,
)
from robot_worlds.generator import GenerationError, generate_spec
from robot_worlds.models import WorldSpec


def empty_search(query):
    return AssetSearch(query, ("Synthetic empty Fuel catalog",), (), True,
                       {"queried_at": "2026-10-31T00:00:00Z", "scope": "Synthetic test catalog only"})


def created_plan():
    plan = valid()
    plan["objects"][0]["mass"] = .3
    plan["created_assets"] = [dict(asset_id="source-scan", object_ids=["part"], region_ids=[])]
    return plan


def created_cabinet():
    from test_appliance_generation import appliance_spec
    plan = appliance_spec().model_dump(mode="json")
    ids = ["microwave", "left", "right", "back", "top", "door", "handle"]
    req = request(asset_id="custom-cabinet", query="synthetic noncatalog hinged test fixture",
                  description="Synthetic rigid inspection cabinet with hollow cavity and hinged door",
                  mass=7, movable=False, min_size=[.59, .55, .35], max_size=[.61, .57, .37],
                  requires_cavity=True, requires_articulation=True)
    plan["created_assets"] = [dict(asset_id=req.asset_id, object_ids=ids, region_ids=["cavity"])]
    return req, plan


def test_empty_search_authorizes_scoped_generation_without_selection_model(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_resolution, "search_online_assets", empty_search)
    client = Client([draft(), created_plan()])
    events, previews = [], []
    spec = generate_spec("Move a custom fixture", client=client, online_asset_cache=tmp_path / "cache",
                         max_repairs=0, event_callback=events.append, on_candidate=previews.append)
    assert len(client.prompts) == 2 and len(previews) == 1
    assert "Completed empty-search evidence" in client.prompts[1]
    assert "created_assets" not in spec.model_dump()
    metadata = json.loads(Path(spec.objects[0].source_metadata).read_text())
    assert metadata["decision"] == "created_after_search" and metadata["selected"] is None
    assert metadata["search"]["query"] == request().query and metadata["search"]["candidates"] == []
    assert metadata["search"]["sources"] == ["Synthetic empty Fuel catalog"]
    assert any("Search scope is limited" in text for text in spec.assumptions)
    assert any(e["type"] == "asset_created_after_search" for e in events)
    assert not (tmp_path / "cache/resolved-index.json").exists()
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    shutil.rmtree(tmp_path / "cache")
    assert load(world.path).id == world.id
    assert world.export(tmp_path / "world.zip").is_file()


@pytest.mark.parametrize("failure", ["incomplete", "network", "wrong_query"])
def test_lookup_failures_do_not_authorize_creation(tmp_path, monkeypatch, failure):
    def search(query):
        if failure == "network":
            raise AssetSearchIncomplete("network failed")
        return AssetSearch("other" if failure == "wrong_query" else query, ("Catalog",), (), failure != "incomplete")
    monkeypatch.setattr(asset_resolution, "search_online_assets", search)
    client, previews = Client([draft(), created_plan()]), []
    with pytest.raises(GenerationError):
        generate_spec("Keep the task", client=client, online_asset_cache=tmp_path / "cache", on_candidate=previews.append)
    assert len(client.prompts) == 1 and not previews


def test_model_cannot_forge_creation_evidence_or_launder_an_installed_asset(tmp_path):
    for asset_id in ("source-scan", "google-porcelain-bowl"):
        plan = created_plan()
        plan["created_assets"][0]["asset_id"] = asset_id
        previews = []
        with pytest.raises(GenerationError) as caught:
            generate_spec("Keep task", client=Client([plan]), online_asset_cache=tmp_path / "cache",
                          max_repairs=0, on_candidate=previews.append)
        assert "no completed empty-search evidence" in str(caught.value.errors)
        assert not previews


@pytest.mark.parametrize("bad_property", ["mass", "size"])
def test_repairs_keep_search_evidence_and_cannot_drop_the_authorized_asset(tmp_path, monkeypatch, bad_property):
    calls = []
    def search(query):
        calls.append(query)
        return empty_search(query)
    monkeypatch.setattr(asset_resolution, "search_online_assets", search)
    bad = created_plan()
    bad["objects"][0][bad_property] = .8 if bad_property == "mass" else [.3]*3
    client = Client([draft(), bad, valid(), created_plan()])
    previews = []
    spec = generate_spec("Move the custom fixture", client=client, max_repairs=2,
                         online_asset_cache=tmp_path / "cache", on_candidate=previews.append)
    assert len(calls) == 1 and len(previews) == 1
    assert ("estimated mass" if bad_property == "mass" else "requested bounds") in client.prompts[2]
    assert "Requested source assets were dropped" in client.prompts[3]
    assert spec.objects[0].mass == .3


@pytest.mark.parametrize("failure", ["duplicate", "omitted_child", "outside_parent", "cycle", "mesh", "size", "cavity", "joint"])
def test_generated_groups_require_complete_physical_structure(tmp_path, failure):
    plan, req = created_plan(), request()
    groups = plan.pop("created_assets")
    if failure == "duplicate":
        groups *= 2
    elif failure == "omitted_child":
        plan["objects"].append({**copy.deepcopy(plan["objects"][0]), "id": "child", "parent_id": "part", "movable": False})
    elif failure == "outside_parent":
        plan["objects"][0]["parent_id"] = "external"
    elif failure == "cycle":
        for oid, parent in (("first", "second"), ("second", "first")):
            plan["objects"].append({**copy.deepcopy(plan["objects"][0]), "id": oid, "parent_id": parent, "movable": False})
            groups[0]["object_ids"].append(oid)
    elif failure == "mesh":
        plan["objects"][0].update(kind="mesh", mesh_path="invented.obj")
    elif failure == "size":
        plan["objects"][0]["size"] = [.3]*3
    elif failure == "cavity":
        req = request(requires_cavity=True)
    else:
        req = request(requires_articulation=True)
    with pytest.raises(ValueError):
        materialize_created_assets(WorldSpec.model_validate(plan), [CreatedAssetGroup.model_validate(g) for g in groups],
                                   {req.asset_id: AssetCreationEvidence(req, empty_search(req.query))}, output=tmp_path)
    assert not (tmp_path / "generated-evidence").exists()


def test_forged_search_with_candidates_never_authorizes_generated_geometry(tmp_path):
    from test_sdf_import import candidate
    authorization = AssetCreationEvidence(request(), AssetSearch(request().query, ("Catalog",), (candidate(),), True))
    with pytest.raises(ValueError, match="zero candidates"):
        authorization.validate()


def test_hollow_articulated_group_preserves_mechanism_and_owned_cavity(tmp_path, monkeypatch):
    req, plan = created_cabinet()
    monkeypatch.setattr(asset_resolution, "search_online_assets", empty_search)
    spec = generate_spec("Synthetic cabinet fixture", client=Client([draft(req), plan]),
                         online_asset_cache=tmp_path, max_repairs=0)
    door = next(obj for obj in spec.objects if obj.id == "door")
    assert door.joint.model_dump(mode="json") == next(obj["joint"] for obj in plan["objects"] if obj["id"] == "door")
    assert spec.regions[0].model_dump(mode="json") == plan["regions"][0]
    assert sum(bool(obj.source_metadata) for obj in spec.objects) == 7
    assert next(obj for obj in spec.objects if obj.id == "bowl").source_metadata is None
