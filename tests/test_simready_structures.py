"""Small synthetic fixtures exercise reviewed profiles; no native physics/network."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from test_simready_rack import fixture

from robot_worlds import AssetLibrary, World, import_simready_structure, load, load_prepared_asset
from robot_worlds.generator import validate_spec
from robot_worlds.models import WorldSpec
from robot_worlds.simready_structures import structure_geometry


def box(low, high):
    return np.array(low, dtype=float), np.array(high, dtype=float)


def pallet_boxes():
    result = []
    for y in (-.318, 0, .318):
        result.append(box([-.6021, y-.082175, 0], [.6021, y+.082175, .0303]))
        for x in (-.522, 0, .522):
            result.append(box([x-.08, y-.08, .031], [x+.08, y+.08, .136]))
    for x in (-.522, 0, .522):
        result.append(box([x-.078, -.4, .1364], [x+.078, .4, .1716]))
    for y in (-.322, -.103, .103, .322):
        result.append(box([-.6021, y-.078, .172], [.6021, y+.078, .2086]))
    return result


def barrier_boxes():
    return [box([-.1149, -.115, 0], [.1149, .115, .058]),
            box([.8851, -.115, 0], [1.1149, .115, .058]),
            box([-.04, -.04, .03], [.04, .04, 1.0719]),
            box([.96, -.04, .03], [1.04, .04, 1.0719]),
            box([0, -.04, .318], [1, .04, .398]),
            box([0, -.04, .963], [1, .04, 1.043])]


def carton_boxes():
    return [box([-.1495, -.3108, 0], [.1495, .3108, .302])]


@pytest.mark.parametrize("asset_id,parts,movable,clearances", [
    ("pallet_a01", pallet_boxes, True, 4),
    ("box_corrugated_brown_b20", carton_boxes, True, 0),
    ("barrier_fence_metal_a02", barrier_boxes, False, 2),
])
def test_source_structure_preserves_visuals_mass_openings_and_portable_layouts(tmp_path, monkeypatch, asset_id, parts, movable, clearances):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda _: {"fixture": "no native robot loading"})
    fixture(tmp_path, monkeypatch, boxes=parts())
    sourced = import_simready_structure(asset_id, tmp_path / "cache")
    asset = sourced.asset
    assert sourced.decision == "reuse_or_adapt"
    assert sum(o.movable for o in asset.objects) == int(movable)
    assert all(not o.movable for o in asset.objects if o.parent_id)
    assert sum(o.mass for o in asset.objects if o.collision) == pytest.approx(123)
    assert all(o.friction == .3 for o in asset.objects if o.collision)
    assert any(o.kind == "mesh" and o.visible and not o.collision for o in asset.objects)
    recipe_path = next(o.source_metadata for o in asset.objects if o.collision)
    recipe = json.loads(Path(recipe_path).read_text())
    assert recipe["asset_id"] == asset_id
    assert len(recipe["clearances"]) == clearances
    assert asset.regions == []  # Fork/rail clearances must not become world-fixed task regions.
    for region in recipe["clearances"]:
        a, b = np.array(region["low"]), np.array(region["high"])
        assert not any(np.all(np.minimum(b, c["high"])-np.maximum(a, c["low"]) > 1e-6)
                       for c in recipe["components"])
    library = AssetLibrary().add(asset_id, sourced, description="Synthetic rigid-structure fixture")
    library.save(tmp_path / "library")
    shutil.rmtree(tmp_path / "cache")
    restored = AssetLibrary.open(tmp_path / "library")
    objects = []
    for prefix, location in (("left", (-2, 2, 0)), ("right", (2, 2, 0))):
        objects.extend(load_prepared_asset(asset_id, prefix=prefix, position=location, asset_library=restored).asset.objects)
    spec = WorldSpec(name="Structures fixture", description="Synthetic geometry", task="Approach marker",
        bounds=(10, 10, 4), objects=objects,
        regions=[dict(id="marker", label="Approach", position=(0, -2, .2), size=(1, 1, .4))],
        rubric=[dict(id="approach", description="Approach", predicate="robot_in_region", target_id="marker")])
    assert validate_spec(spec) == []
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    archive = world.export(tmp_path / "world.zip")
    assert archive.exists()
    shutil.rmtree(tmp_path / "library")
    assert load(world.path).id == world.id
    assert world.manifest["validation"]["status"] == "preview"


def test_filled_pallet_channels_are_rejected(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, boxes=[box([-.6021, -.400175, 0], [.6021, .400175, .2086])])
    with pytest.raises(ValueError, match="clearance"):
        import_simready_structure("pallet_a01", tmp_path / "cache")


def test_open_carton_is_not_silently_filled(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, boxes=carton_boxes(), open_top=True)
    with pytest.raises(ValueError, match="closed face"):
        import_simready_structure("box_corrugated_brown_b20", tmp_path / "cache")


def test_zero_index_joint_is_not_mistaken_for_absent_articulation(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, boxes=carton_boxes(), joint="index-zero")
    with pytest.raises(ValueError, match="Articulated source"):
        import_simready_structure("box_corrugated_brown_b20", tmp_path / "cache")


def test_barrier_triangle_spanning_opening_cannot_disappear(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, boxes=barrier_boxes()+[box([.1, -.02, .45], [.9, .02, .9])])
    with pytest.raises(ValueError, match="cover all source triangles"):
        import_simready_structure("barrier_fence_metal_a02", tmp_path / "cache")


def test_unsupported_or_articulated_source_does_not_trigger_download(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.simready_structures.download_simready_asset", lambda *a, **k: pytest.fail("unexpected download"))
    with pytest.raises(ValueError, match="reviewed rigid adapter"):
        import_simready_structure("conveyor_belt_a05", tmp_path / "cache")
    with pytest.raises(ValueError, match="recipe"):
        structure_geometry("unknown", [])
    assert not (tmp_path / "cache").exists()
