"""Hosted custom-library generation with real physics, no robot policy episode."""
import shutil
from pathlib import Path

from test_prepared_assets import prepared_plan

from robot_worlds import AssetLibrary, generate, load, load_prepared_asset


def test_registered_source_runs_through_sdk_native_validation_and_independent_export(tmp_path, native_rendering):
    library = AssetLibrary().add("lab-appliance", load_prepared_asset("nvidia-microwave079", prefix="template"),
                                  description="Original articulated microwave with a usable cavity and passive door.")
    saved = library.save(tmp_path / "library")
    requests = []

    class Model:
        def complete_json(self, instructions, prompt, schema):
            requests.append(prompt)
            plan = prepared_plan()
            plan["prepared_assets"][1]["asset_id"] = "lab-appliance"
            plan["rubric"][0]["target_id"] = "oven_region0"
            return plan

    world = generate("Put the original bowl in the lab appliance", client=Model(), max_repairs=0,
                     asset_library=saved, output=tmp_path / "worlds")
    assert "lab-appliance" in requests[0]
    assert world.manifest["validation"]["status"] == "ready", world.manifest["validation"]
    assert len(world.spec.objects) == 181
    assert all(not Path(value).is_absolute() for obj in world.spec.objects
               for value in (obj.mesh_path, obj.texture_path, obj.source_notice, obj.source_metadata) if value)
    shutil.rmtree(saved)
    restored = load(world.path)
    assert restored.id == world.id
    assert restored.export(tmp_path / "portable-world.zip").is_file()
