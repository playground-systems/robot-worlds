"""Hosted native SDK path; source HTTP and model responses remain synthetic."""
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
    shutil.rmtree(tmp_path / "asset-cache")
    assert load(world.path).id == world.id
    assert world.export(tmp_path / "world.zip").is_file()
