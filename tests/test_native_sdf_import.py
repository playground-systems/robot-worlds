"""Hosted SDF-to-world checks; no model network request or policy rollout."""
import shutil
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest
from test_generator import valid
from test_runtime_asset_reuse import scene_xml
from test_sdf_import import candidate, download_fixture, import_fixture, package

from robot_worlds import AssetLibrary, generate, import_fuel_mesh_asset, load, source_asset
from robot_worlds.asset_sourcing import AssetSearch
from robot_worlds.models import WorldSpec


def test_native_sdf_mobility_is_preserved_for_dynamic_and_static_sources(tmp_path):
    for static in (False, True):
        root = package(tmp_path / str(static))
        if static:
            sdf = root / "scan.sdf"
            sdf.write_text(sdf.read_text().replace('<model name="Fixture">', '<model name="Fixture"><static>true</static>'))
        asset = import_fixture(root, position=(0, 0, .5))
        spec = WorldSpec.model_validate({**valid(), "objects": [obj.model_dump(mode="json") for obj in asset.objects]})
        model = mujoco.MjModel.from_xml_string(ET.tostring(scene_xml(spec, root), encoding="unicode"))
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        body = model.body("object_fixture_body").id
        initial = data.xpos[body].copy()
        for _ in range(250):
            mujoco.mj_step(model, data)
        if static:
            np.testing.assert_array_equal(data.xpos[body], initial)
            assert model.nq == 0
        else:
            assert data.xpos[body][2] < initial[2] - .3
            assert model.body_mass[body] == pytest.approx(.3)
            assert data.ncon > 0


def test_cached_fuel_scan_registers_generates_validates_and_exports_offline(tmp_path, native_rendering):
    downloaded = download_fixture(package(tmp_path / "source"), tmp_path / "cache")
    selected = candidate()
    search = AssetSearch("synthetic source scan", ("synthetic Fuel response",), (selected,), True)
    sourced = source_asset(search.query, search=lambda _: search,
                           import_asset=lambda item: import_fuel_mesh_asset(downloaded, candidate=item, prefix="source", mass=.3),
                           create_asset=lambda *_: pytest.fail("Existing asset ignored"))
    library = AssetLibrary().add("source-scan", sourced, description="Synthetic textured scanned object").save(tmp_path / "library")
    shutil.rmtree(tmp_path / "cache")
    shutil.rmtree(tmp_path / "source")

    class Model:
        def complete_json(self, instructions, prompt, schema):
            assert "source-scan" in prompt
            plan = valid()
            plan["objects"] = []
            plan["prepared_assets"] = [dict(id="scan", asset_id="source-scan", position=[2, 0, .001])]
            plan["rubric"][0]["object_id"] = "scan_part0"
            return plan

    world = generate("Move the source object into the destination", client=Model(), max_repairs=0,
                     asset_library=library, output=tmp_path / "worlds")
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    shutil.rmtree(library)
    assert load(world.path).id == world.id
    assert world.export(tmp_path / "world.zip").is_file()
