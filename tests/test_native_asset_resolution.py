"""Hosted native SDK path; source HTTP and model responses remain synthetic."""
import json
import shutil

from test_asset_resolution import draft, final_plan, selection
from test_generator import Client
from test_sdf_import import candidate, download_fixture, package

from robot_worlds import asset_resolution, generate, load
from robot_worlds.asset_sourcing import AssetSearch


def test_online_request_to_native_world_and_portable_export(tmp_path, monkeypatch, native_rendering):
    source = package(tmp_path / "source")
    monkeypatch.setattr(asset_resolution, "search_online_assets", lambda query:
                        AssetSearch(query, ("Synthetic Fuel response",), (candidate(),), True))
    monkeypatch.setattr(asset_resolution, "download_fuel_asset", lambda item, cache: download_fixture(source, cache))
    client = Client([draft(), selection(), final_plan()])
    world = generate("Move the textured source object to the destination", client=client,
                     output=tmp_path / "worlds", max_repairs=0)
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    assert len(client.prompts) == 3
    shutil.rmtree(tmp_path / "source")
    for folder in ("downloads", "collision", "evidence"):
        shutil.rmtree(tmp_path / "asset-cache" / folder)

    class CachedModel:
        calls = 0
        def complete_json(self, instructions, prompt, schema):
            self.calls += 1
            assert self.calls == 1
            catalog = json.loads(prompt.split("Prepared source asset catalog:\n")[1].split("\nResource profile:")[0])
            cached = [item for item in catalog if item["asset_id"].startswith("cached_")]
            assert len(cached) == 1 and cached[0]["movable"]
            plan = final_plan(cached[0]["asset_id"])
            plan["prepared_assets"][0]["position"] = [2, 1, .001]
            return plan

    cached_client = CachedModel()
    second = generate("Use the painted object in a different starting location", client=cached_client,
                      output=tmp_path / "worlds", online_assets=False, max_repairs=0)
    assert second.manifest["validation"]["status"] == "ready", second.manifest["validation"]
    assert cached_client.calls == 1 and second.id != world.id
    shutil.rmtree(tmp_path / "asset-cache")
    assert load(world.path).id == world.id
    assert load(second.path).id == second.id
    assert world.export(tmp_path / "world.zip").is_file()
    assert second.export(tmp_path / "second.zip").is_file()
