"""Synthetic rack physics-adapter checks; no native physics or network."""
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_gltf_geometry import write
from test_mesh_assets import cube

from robot_worlds import AssetLibrary, World, load, load_prepared_asset
from robot_worlds import simready_rack as adapter
from robot_worlds.asset_sourcing import AssetCandidate, AssetSearch
from robot_worlds.generator import validate_spec
from robot_worlds.gltf_geometry import GLBGeometry
from robot_worlds.models import WorldSpec


def rack_boxes():
    boxes = []
    for x in (-1.935, 1.935):
        for y in (-.966675, .966675):
            boxes.append((np.array([x-.065, y-.066675, -.0001]), np.array([x+.065, y+.066675, 3.0101])))
    for top in (.5059, 1.63, 2.3813):
        boxes.append((np.array([-1.86, -.89, top-.05]), np.array([1.86, .89, top])))
    return boxes


def fixture(tmp_path, monkeypatch, *, solid=False, joint=False):
    points, triangles = cube()
    boxes = rack_boxes()
    if solid:
        boxes.append((np.array([-1.8, -.8, .5]), np.array([1.8, .8, 3.])))
    vertices, faces = [], []
    for i, (low, high) in enumerate(boxes):
        vertices.append(points*(high-low)+low)
        faces.append(triangles+i*len(points))
    # Convert source fixture from Z-up back into glTF Y-up.
    vertices = np.concatenate(vertices)[:, [0, 2, 1]]
    vertices[:, 2] *= -1
    vdata = vertices.astype('<f4').tobytes()
    faces = np.concatenate(faces).astype('<u4')
    data = vdata + faces.tobytes()
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(data)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(vdata)},
                        {"buffer": 0, "byteOffset": len(vdata), "byteLength": faces.nbytes}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(vertices), "type": "VEC3"},
                      {"bufferView": 1, "componentType": 5125, "count": faces.size, "type": "SCALAR"}],
        "scenes": [{"nodes": [0]}], "scene": 0,
        "nodes": [{"mesh": 0, "extensions": {"KHR_physics_rigid_bodies": {"motion": {"mass": 123.}}}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "extensions": {"KHR_physics_rigid_bodies": {"physicsMaterials": [{"dynamicFriction": .3}]}}}
    if joint:
        doc["nodes"][0]["extras"] = {"joints": [{"type": "revolute"}]}
    source = tmp_path / "fixture.glb"
    write(source, doc, data)
    notice, metadata, decoded_manifest = (tmp_path / name for name in ("NOTICE.md", "source.json", "decoded.json"))
    notice.write_text("Synthetic geometry fixture only; not NVIDIA source evidence.")
    metadata.write_text(json.dumps({"source": "synthetic rack fixture"}))
    decoded_manifest.write_text(json.dumps({"sha256": hashlib.sha256(source.read_bytes()).hexdigest()}))
    candidate = AssetCandidate("fixture:rack", "https://example.invalid/fixture", "fixture-v1", "Synthetic test only",
                               {"optimized_manifest_record": {"physics": {"joints": 0}}})
    monkeypatch.setattr(adapter, "simready_asset_search", lambda q: AssetSearch(q, ("synthetic fixture",), (candidate,), True))
    monkeypatch.setattr(adapter, "download_simready_asset", lambda *a, **k: SimpleNamespace(
        glb_path=source, notice_path=notice, metadata_path=metadata))
    monkeypatch.setattr(adapter, "decode_glb", lambda *a, **k: SimpleNamespace(path=source, manifest_path=decoded_manifest))
    return source


def test_structural_components_preserve_disconnected_shelves(tmp_path, monkeypatch):
    source = fixture(tmp_path, monkeypatch)
    boxes = adapter.component_envelopes(GLBGeometry(source).primitives())
    assert len(boxes) == 7
    shelves = adapter.rack_clearances(boxes)
    assert len(shelves) == 3
    assert [support for _, _, support in shelves] == pytest.approx([.5059, 1.63, 2.3813])
    for low, high, _ in shelves:
        point = (low+high)/2
        assert not any(np.all(point >= a) and np.all(point <= b) for a, b in boxes)


def test_import_preserves_source_mass_friction_placement_and_cavity_bounds(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch)
    sourced = adapter.import_simready_rack(tmp_path / "cache", prefix="rack", position=(3, 1, .01))
    asset = sourced.asset
    assert sourced.decision == "reuse_or_adapt"
    assert sum(obj.mass for obj in asset.objects if obj.collision) == pytest.approx(123)
    assert all(obj.friction == .3 for obj in asset.objects if obj.collision)
    assert not any(obj.movable or obj.joint for obj in asset.objects)
    assert len(asset.regions) == 3
    assert min(obj.position[2]-obj.size[2]/2 for obj in asset.objects) == pytest.approx(.01)
    assert all(region.interior_of == "rack_body" for region in asset.regions)
    assert any("conservative" in text for text in asset.disclosures)
    recipe_path = next(obj.source_metadata for obj in asset.objects if obj.collision)
    recipe = json.loads(Path(recipe_path).read_text())
    assert recipe["source_total_mass_kg"] == 123
    assert recipe["mobility"] == "explicitly fixed warehouse fixture"


@pytest.mark.parametrize("failure", ["solid", "joint"])
def test_filled_openings_or_articulated_sources_do_not_become_static_rack(tmp_path, monkeypatch, failure):
    fixture(tmp_path, monkeypatch, solid=failure == "solid", joint=failure == "joint")
    with pytest.raises(ValueError, match="fills|required|Articulated"):
        adapter.import_simready_rack(tmp_path / "cache")
    assert not (tmp_path / "cache/rack-physics").exists()


def test_registered_rack_places_in_multiple_layouts_and_survives_cache_removal(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda _: {"fixture": "no native robot loading"})
    fixture(tmp_path, monkeypatch)
    source = adapter.import_simready_rack(tmp_path / "cache")
    library = AssetLibrary().add("rack", source, description="Synthetic rack adapter fixture")
    library.save(tmp_path / "library")
    shutil.rmtree(tmp_path / "cache")
    library = AssetLibrary.open(tmp_path / "library")
    objects, regions = [], []
    for prefix, position in (("left", (-3, 2, 0)), ("right", (3, 2, 0))):
        asset = load_prepared_asset("rack", prefix=prefix, position=position, asset_library=library).asset
        objects.extend(asset.objects)
        regions.extend(asset.regions)
    regions.append(dict(id="approach", label="Approach", position=(0, -2, .2), size=(1, 1, .4)))
    spec = WorldSpec(name="Rack fixture", description="Synthetic adapter checks", task="Move to approach",
        bounds=(14, 10, 5), objects=objects, regions=regions,
        rubric=[dict(id="approach_task", description="Approach", predicate="robot_in_region", target_id="approach")])
    assert validate_spec(spec) == []
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    assert world.manifest["validation"]["status"] == "preview"
    world.export(tmp_path / "rack-world.zip")
    shutil.rmtree(tmp_path / "library")
    assert load(world.path).id == world.id
    assert len(world.spec.regions) == 7


def test_missing_support_or_changed_source_dimensions_fail():
    boxes = rack_boxes()
    with pytest.raises(ValueError, match="support"):
        adapter.rack_clearances(boxes[:-1])
    enlarged = [(a.copy(), b.copy()) for a, b in boxes]
    enlarged[0][0][0] -= .1
    with pytest.raises(ValueError, match="dimensions"):
        adapter.rack_clearances(enlarged)


def test_cancel_before_acquisition_and_invalid_position_do_not_write(tmp_path):
    with pytest.raises(ValueError, match="cancelled"):
        adapter.import_simready_rack(tmp_path / "cache", cancelled=lambda: True)
    with pytest.raises(ValueError):
        adapter.import_simready_rack(tmp_path / "cache", position=(float("nan"), 0, 0))
    assert not (tmp_path / "cache").exists()
