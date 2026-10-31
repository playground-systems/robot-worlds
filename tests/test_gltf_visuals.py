"""Tiny source-conversion fixtures, never native simulation or rendering."""
import io
import json
import shutil

import numpy as np
import pytest
from PIL import Image
from test_generator import valid
from test_gltf_geometry import scene, write

from robot_worlds import AssetLibrary, World, load, source_asset
from robot_worlds.asset_import import ImportedAsset
from robot_worlds.asset_sourcing import AssetCandidate, AssetSearch
from robot_worlds.gltf_visuals import prepare_glb_visuals
from robot_worlds.models import SceneObject, WorldSpec


def source_fixture(tmp_path, change=None):
    doc, data = scene()
    doc["nodes"] = [{"mesh": 0, "extras": {"mass": 3}}]
    pixels = np.array([[[10, 20, 30, 255], [40, 50, 60, 255]],
                       [[70, 80, 90, 255], [100, 110, 120, 255]]], dtype=np.uint8)
    stream = io.BytesIO()
    Image.fromarray(pixels).save(stream, format="PNG")
    encoded = stream.getvalue()
    image_offset = doc["bufferViews"][-1]["byteOffset"]
    data = data[:image_offset] + encoded
    doc["bufferViews"][-1]["byteLength"] = len(encoded)
    doc["buffers"][0]["byteLength"] = len(data)
    doc["images"][0]["mimeType"] = "image/png"
    material = doc["materials"][0]
    material["normalTexture"] = {"index": 0, "scale": .7}
    material["pbrMetallicRoughness"]["metallicRoughnessTexture"] = {"index": 0}
    if change:
        doc, data = change(doc, data)
    source = tmp_path / "source.glb"
    write(source, doc, data)
    notice = tmp_path / "NOTICE.md"
    notice.write_text("Synthetic test asset; no external asset license claim.")
    return source, notice, pixels


def prepare(tmp_path, change=None):
    source, notice, pixels = source_fixture(tmp_path, change)
    result = prepare_glb_visuals(source, tmp_path / "cache", source_notice=notice,
                                 source_metadata={"source": "synthetic fixture"})
    return result, source, notice, pixels


def test_source_geometry_material_channels_and_normals_survive_conversion(tmp_path):
    result, source, notice, pixels = prepare(tmp_path)
    obj, = result.objects(prefix="fixture", parents={0: "body"})
    assert not obj.collision and obj.parent_id == "body" and not obj.movable
    assert obj.surface.normal_scale == (.7, .7)
    assert obj.color == pytest.approx((1, 128/255, 0))
    lines = (result.path / "part-0.obj").read_text().splitlines()
    assert "vt 0 1" in lines and "vt 1 0" in lines
    assert "f 1/1/1 2/2/2 3/3/3" in lines
    with Image.open(obj.texture_path) as image:
        assert np.array_equal(np.array(image), pixels)
    with Image.open(obj.normal_texture_path) as image:
        normals = np.array(image)
        assert np.array_equal(normals[:, :, 1], 255-pixels[:, :, 1])
        assert np.array_equal(normals[:, :, 0], pixels[:, :, 0])
    with Image.open(obj.orm_texture_path) as image:
        orm = np.array(image)
        assert np.all(orm[:, :, 0] == 255)  # MR red is not implicit occlusion.
        assert np.array_equal(orm[:, :, 1:], pixels[:, :, 1:3])
    metadata = json.loads((result.path / "source.json").read_text())
    assert metadata["gltf_document"]["nodes"][0]["extras"]["mass"] == 3
    assert metadata["conversion"]["physics"] == "not imported or validated"
    second = prepare_glb_visuals(source, tmp_path / "cache", source_notice=notice,
                                 source_metadata={"source": "synthetic fixture"})
    assert second.cache_hit and second.path == result.path
    with pytest.raises(ValueError, match="physical parent"):
        result.objects(prefix="fixture", parents={})


def test_occlusion_strength_and_alpha_mode_are_explicit(tmp_path):
    def change(doc, data):
        mat = doc["materials"][0]
        mat.update(occlusionTexture={"index": 0, "strength": .5}, alphaMode="BLEND")
        mat["pbrMetallicRoughness"]["baseColorFactor"] = [1, 1, 1, .4]
        return doc, data
    result, _, _, pixels = prepare(tmp_path, change)
    obj, = result.objects(prefix="fixture", parents={0: "body"})
    assert obj.opacity == .4 and obj.surface.alpha_mode == "BLEND"
    with Image.open(obj.orm_texture_path) as image:
        assert np.array_equal(np.array(image)[:, :, 0], np.rint(127.5 + pixels[:, :, 0]*.5).astype(np.uint8))


@pytest.mark.parametrize("failure", ["vertex_color", "uv_transform", "normal_rotation", "wrap", "filter", "extension", "bad_image"])
def test_unsupported_appearance_never_publishes_partial_visuals(tmp_path, failure):
    def change(doc, data):
        if failure == "vertex_color":
            raw = bytearray(data)
            raw[doc["bufferViews"][3]["byteOffset"]] = 0
            data = bytes(raw)
        elif failure == "uv_transform":
            doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["extensions"] = {
                "KHR_texture_transform": {"offset": [.1, 0]}}
        elif failure == "normal_rotation":
            doc["materials"][0]["normalTexture"]["extensions"] = {"KHR_texture_transform": {"rotation": .1}}
        elif failure == "wrap":
            doc["samplers"][0]["wrapS"] = 999
        elif failure == "filter":
            doc["samplers"][0]["magFilter"] = 9728
        elif failure == "extension":
            doc["materials"][0]["extensions"] = {"KHR_materials_transmission": {"transmissionFactor": 1}}
        else:
            raw = bytearray(data)
            raw[doc["bufferViews"][-1]["byteOffset"]] = 0
            data = bytes(raw)
        return doc, data
    with pytest.raises((ValueError, OSError)):
        prepare(tmp_path, change)
    assert list((tmp_path / "cache").iterdir()) == []


@pytest.mark.parametrize("tamper", ["texture", "position", "extra", "link"])
def test_corrupt_visual_cache_is_not_rebuilt_or_silently_accepted(tmp_path, tamper):
    result, source, notice, _ = prepare(tmp_path)
    obj, = result.objects(prefix="fixture", parents={0: "body"})
    if tamper == "texture":
        from pathlib import Path
        Path(obj.texture_path).write_bytes(b"changed")
    elif tamper == "position":
        path = result.path / "manifest.json"
        record = json.loads(path.read_text())
        record["parts"][0]["object"]["position"][0] += 1
        path.write_text(json.dumps(record))
    elif tamper == "extra":
        (result.path / "extra").write_text("new")
    else:
        path = result.path / "part-0.obj"
        path.unlink()
        path.symlink_to(source)
    with pytest.raises(ValueError, match="cache"):
        prepare_glb_visuals(source, tmp_path / "cache", source_notice=notice,
                            source_metadata={"source": "synthetic fixture"})


def test_visuals_attach_to_explicit_fixture_physics_and_survive_portable_library(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda _: {"fixture": "no native engine"})
    bundle, _, _, _ = prepare(tmp_path)
    body = SceneObject(id="body", label="Synthetic physical test body", kind="box",
                       position=(.5, 0, .5), size=(1, .1, 1))
    imported = ImportedAsset(objects=[body, *bundle.objects(prefix="fixture", parents={0: "body"})],
                             source_files=bundle.source_files, body_ids={"fixture": "body"})
    candidate = AssetCandidate("fixture", "https://example.invalid/fixture", "fixture-v1", "Synthetic test only")
    sourced = source_asset("fixture", search=lambda q: AssetSearch(q, ("synthetic fixture",), (candidate,), True),
                           import_asset=lambda _: imported, create_asset=lambda *_: pytest.fail("No creation"))
    library = AssetLibrary().add("fixture", sourced, description="Synthetic geometry/material persistence test")
    library.save(tmp_path / "library")
    shutil.rmtree(tmp_path / "cache")
    restored = AssetLibrary.open(tmp_path / "library").load("fixture", prefix="restored").asset
    for obj in restored.objects:
        obj.position = (obj.position[0]+2, obj.position[1]+1, obj.position[2])
    spec = WorldSpec.model_validate(valid())
    spec.objects.extend(restored.objects)
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    assert world.manifest["validation"]["status"] == "preview", world.manifest["validation"]
    world.export(tmp_path / "world.zip")
    shutil.rmtree(tmp_path / "library")
    assert load(world.path).id == world.id
    assert any(obj.normal_texture_path for obj in world.spec.objects)


def test_cancellation_during_conversion_cleans_staging(tmp_path):
    source, notice, _ = source_fixture(tmp_path)
    calls = [0]
    def cancelled():
        calls[0] += 1
        return calls[0] > 5
    with pytest.raises(ValueError, match="cancelled"):
        prepare_glb_visuals(source, tmp_path / "cache", source_notice=notice,
                            source_metadata={"source": "fixture"}, cancelled=cancelled)
    assert list((tmp_path / "cache").iterdir()) == []


@pytest.mark.parametrize("turns", [-1, 1, 2, 3])
def test_quarter_turn_normal_maps_bake_pixels_without_rotating_tangent_uvs(tmp_path, turns):
    def change(doc, data):
        material = doc["materials"][0]
        for info in (material["normalTexture"], *[v for k, v in material["pbrMetallicRoughness"].items() if k.endswith("Texture")]):
            info["extensions"] = {"KHR_texture_transform": {"rotation": turns*np.pi/2, "offset": [-1, 1]}}
        return doc, data
    result, _, _, pixels = prepare(tmp_path, change)
    obj, = result.objects(prefix="fixture", parents={0: "body"})
    text = (result.path / "part-0.obj").read_text()
    assert "vt 0 1\n" in text and "vt 1 0\n" in text
    # Independent nearest-sample evaluation of the specified glTF UV matrix.
    # Source UV origin is upper-left; the integer offset disappears under REPEAT.
    c, s = np.cos(turns*np.pi/2), np.sin(turns*np.pi/2)
    with Image.open(obj.texture_path) as image, Image.open(obj.normal_texture_path) as normal:
        actual, normals = np.array(image), np.array(normal)
        for row in range(2):
            for col in range(2):
                u, v = (col+.5)/2, (row+.5)/2
                source_col = int(((c*u-s*v-1) % 1)*2)
                source_row = int(((s*u+c*v+1) % 1)*2)
                expected = pixels[source_row, source_col]
                assert np.array_equal(actual[row, col], expected)
                assert np.array_equal(normals[row, col], [expected[0], 255-expected[1], expected[2]])


@pytest.mark.parametrize("variant", ["clamp", "fractional_offset", "nonunit_scale"])
def test_normal_quarter_turn_baking_rejects_non_equivalent_sampling(tmp_path, variant):
    def change(doc, data):
        material = doc["materials"][0]
        for info in (material["normalTexture"], *[v for k, v in material["pbrMetallicRoughness"].items() if k.endswith("Texture")]):
            transform = {"rotation": np.pi/2}
            if variant == "fractional_offset":
                transform["offset"] = [.25, 0]
            if variant == "nonunit_scale":
                transform["scale"] = [2, 2]
            info["extensions"] = {"KHR_texture_transform": transform}
        if variant == "clamp":
            doc["samplers"] = [{"wrapS": 33071, "wrapT": 33071}]
            doc["textures"][0]["sampler"] = 0
        return doc, data
    with pytest.raises(ValueError, match="Quarter-turn normal-map baking"):
        prepare(tmp_path, change)
