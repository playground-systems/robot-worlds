"""Portable source imports through generation/edit/export, without native physics."""
import copy
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from test_generator import Client, valid

from robot_worlds import (
    AssetLibrary,
    World,
    import_mjcf_asset,
    load,
    load_prepared_asset,
    prepared_asset_catalog,
    source_asset,
)
from robot_worlds.artifacts import digest
from robot_worlds.asset_sourcing import AssetCandidate, AssetSearch
from robot_worlds.editing import apply_patch
from robot_worlds.generator import GenerationError, generate_spec, validate_spec
from robot_worlds.models import WorldSpec


def fixture_import(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "ATTRIBUTION.md").write_text("Synthetic test fixture only; not a searched production asset.")
    from test_mesh_assets import cube
    vertices, faces = cube()
    for name, size, offset in (("base", [.4, .3, .06], [-.2, -.15, 0]),
                               ("panel", [.4, .04, .4], [0, -.02, -.2])):
        points = vertices * size + offset
        (root / f"{name}.obj").write_text("".join("v " + " ".join(map(str, v)) + "\n" for v in points)
                                          + "".join("f " + " ".join(str(i+1) for i in f) + "\n" for f in faces))
    path = root / "model.xml"
    path.write_text('''<mujoco><compiler angle="radian"/>
      <asset><mesh name="base_mesh" file="base.obj"/><mesh name="panel_mesh" file="panel.obj"/></asset><worldbody>
      <body name="frame"><geom name="base" type="box" size=".2 .15 .03" pos="0 0 .03"/>
        <geom type="mesh" mesh="base_mesh" contype="0" conaffinity="0"/>
        <body name="door" pos="0 .15 .25"><joint name="door_hinge" type="hinge" axis="0 0 1" range="0 1.5"/>
          <geom name="panel" type="box" size=".2 .02 .2" pos=".2 0 0"/>
          <geom type="mesh" mesh="panel_mesh" contype="0" conaffinity="0"/>
        </body>
      </body></worldbody></mujoco>''')
    candidate = AssetCandidate("fixture:hinged-station", "https://example.invalid/test-fixture", "test-1", "Test fixture only")
    report = AssetSearch("hinged inspection station", ("synthetic test catalog",), (candidate,), True,
                         {"fixture": True, "not_a_live_search": True})
    return source_asset(report.query, search=lambda _: report,
                        import_asset=lambda _: import_mjcf_asset(path, prefix="original"),
                        create_asset=lambda *_: pytest.fail("An available source was ignored"))


def fixture_library(tmp_path):
    return AssetLibrary().add("inspection-station", fixture_import(tmp_path),
                              description="A compact inspection fixture with a passive hinged panel.")


def test_custom_source_catalog_and_generation_keep_joints_and_source_evidence(tmp_path):
    library = fixture_library(tmp_path)
    catalog = prepared_asset_catalog(library)
    custom = next(item for item in catalog if item["asset_id"] == "inspection-station")
    assert custom["joint_ids"] == {"door_hinge": "{prefix}_part2"}
    assert custom["revision"] == "test-1"
    plan = valid()
    plan["prepared_assets"] = [dict(id="station", asset_id="inspection-station", position=[-2, -2, 0])]
    client = Client([plan])
    spec = generate_spec("Use the inspection station", client=client, asset_library=library)
    assert "inspection-station" in client.prompts[0]
    assert len(spec.objects) == 5
    assert validate_spec(spec) == []
    panel = next(obj for obj in spec.objects if obj.id == "station_part2")
    assert panel.parent_id == "station_part0"
    assert panel.joint.range == pytest.approx((0, 1.5))
    assert any("example.invalid/test-fixture" in note for note in spec.assumptions)
    again = load_prepared_asset("inspection-station", prefix="second", position=[1, -2, 0],
                                joint_initials=[dict(name="door_hinge", initial=.5)], asset_library=library)
    assert again.asset.joint_ids == {"door_hinge": "second_part2"}
    assert again.asset.objects[2].joint.initial == .5
    assert library.load("inspection-station", prefix="third").asset.objects[2].joint.initial == 0
    assert again.search.evidence["fixture"] is True


def test_portable_library_and_frozen_world_survive_removal_of_original_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda _: {"fixture": "only"})
    library = fixture_library(tmp_path)
    saved = library.save(tmp_path / "portable")
    assert AssetLibrary.open(saved).asset_ids == ("inspection-station",)
    manifest = json.loads((saved / "library.json").read_text())
    assert manifest["entries"]["inspection-station"]["source"]["search"]["query"] == "hinged inspection station"
    shutil.rmtree(tmp_path / "source")
    library = AssetLibrary.open(saved)
    parent = WorldSpec.model_validate(valid())
    patch = {"operations": [{"op": "add_prepared_asset", "asset": dict(id="custom", asset_id="inspection-station", position=[-2, -2, 0])}]}
    before = parent.model_dump_json()
    with pytest.raises(ValueError, match="scope"):
        apply_patch(parent, patch, asset_library=library, scope=["custom"])
    revised = apply_patch(parent, patch, asset_library=saved, scope=[f"custom_part{i}" for i in range(4)])
    assert parent.model_dump_json() == before
    world = World.from_spec(revised, tmp_path / "worlds", validate=False)
    shutil.rmtree(saved)
    frozen = load(world.path)
    assert frozen.id == world.id
    assert "Synthetic test fixture" in (frozen.path / next(obj.source_notice for obj in frozen.spec.objects if obj.source_notice)).read_text()
    assert frozen.export(tmp_path / "world.zip").is_file()
    assert frozen.manifest["validation"]["status"] == "preview"  # No native certification in this test.


def test_real_source_geometry_textures_and_cavities_survive_library_round_trip(tmp_path):
    source = load_prepared_asset("nvidia-microwave079", prefix="original")
    library = AssetLibrary().add("lab-microwave", source, description="Original articulated microwave for laboratory layouts.")
    saved = library.save(tmp_path / "library")
    restored = load_prepared_asset("lab-microwave", prefix="oven", position=[2, 0, .7], asset_library=saved)
    assert len(restored.asset.objects) == 20
    assert restored.asset.regions[0].interior_of == restored.asset.body_ids["object"]
    assert set(restored.asset.joint_ids) == set(source.asset.joint_ids)
    expected = {hashlib.sha256(Path(obj.mesh_path).read_bytes()).hexdigest() for obj in source.asset.objects if obj.mesh_path}
    actual = {hashlib.sha256(Path(obj.mesh_path).read_bytes()).hexdigest() for obj in restored.asset.objects if obj.mesh_path}
    assert actual == expected
    assert sum(bool(obj.texture_path) for obj in restored.asset.objects) == 3
    # Returned evidence and geometry cannot mutate the registered template.
    restored.selected.metadata["changed"] = True
    restored.asset.objects[0].mass = 99
    again = AssetLibrary.open(saved).load("lab-microwave", prefix="fresh")
    assert "changed" not in again.selected.metadata
    assert again.asset.objects[0].mass != 99


@pytest.mark.parametrize("failure", ["manifest", "file", "outside", "missing_reference"])
def test_corrupt_or_escaping_library_never_materializes(tmp_path, failure):
    saved = fixture_library(tmp_path).save(tmp_path / "library")
    manifest_path = saved / "library.json"
    manifest = json.loads(manifest_path.read_text())
    member = next(iter(manifest["files"]))
    if failure == "file":
        (saved / member).write_text("changed")
    elif failure == "manifest":
        manifest["entries"]["inspection-station"]["asset"]["objects"][0]["mass"] = 99
        manifest_path.write_text(json.dumps(manifest))
    else:
        if failure == "outside":
            outside = tmp_path / "outside.md"
            outside.write_text("private")
            manifest["files"][str(outside)] = hashlib.sha256(outside.read_bytes()).hexdigest()
        else:
            manifest["entries"]["inspection-station"]["asset"]["objects"][0]["source_notice"] = "files/missing.md"
        manifest.pop("id")
        manifest["id"] = digest(manifest)
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        AssetLibrary.open(saved)


def test_registration_rejects_missing_provenance_duplicates_cycles_and_modified_imports(tmp_path):
    sourced = fixture_import(tmp_path)
    with pytest.raises(ValueError, match="source-search"):
        AssetLibrary().add("station", replace(sourced, search=replace(sourced.search, completed=False)), description="Station")
    library = AssetLibrary().add("station", sourced, description="Station")
    with pytest.raises(ValueError, match="unique"):
        library.add("station", sourced, description="Again")
    broken = copy.deepcopy(sourced)
    broken.asset.objects[0].parent_id = broken.asset.objects[1].id
    with pytest.raises(ValueError, match="cycle"):
        AssetLibrary().add("station", broken, description="Cycle")
    # The imported bytes are pinned even before the library is written to disk.
    Path(sourced.asset.objects[0].source_notice).write_text("Changed attribution")
    with pytest.raises(ValueError, match="changed"):
        library.load("station", prefix="later")


def test_unknown_asset_is_a_repairable_error_not_a_builtin_substitution(tmp_path):
    library = fixture_library(tmp_path)
    bad = valid()
    bad["prepared_assets"] = [dict(id="station", asset_id="invented", position=[-2, -2, 0])]
    with pytest.raises(GenerationError) as caught:
        generate_spec("Use exact imported asset", client=Client([bad]), asset_library=library, max_repairs=0)
    assert "Unknown prepared asset" in str(caught.value.errors)
    with pytest.raises(ValueError, match="mass"):
        load_prepared_asset("inspection-station", prefix="station", mass=2, asset_library=library)
    with pytest.raises(ValueError, match="joint"):
        load_prepared_asset("inspection-station", prefix="station", asset_library=library,
                            joint_initials=[dict(name="missing", initial=0)])
    saved = library.save(tmp_path / "saved")
    with pytest.raises(ValueError, match="already exists"):
        library.save(saved)


def test_changed_mesh_since_import_cannot_be_registered_as_original(tmp_path):
    sourced = fixture_import(tmp_path)
    mesh = next(Path(obj.mesh_path) for obj in sourced.asset.objects if obj.mesh_path)
    mesh.write_text(mesh.read_text() + "# changed after import\n")
    with pytest.raises(ValueError, match="changed since import"):
        AssetLibrary().add("station", sourced, description="Fixture")


def test_library_cannot_shadow_builtin_asset_identifiers(tmp_path):
    library = AssetLibrary().add("google-porcelain-bowl", fixture_import(tmp_path), description="Not the bowl")
    with pytest.raises(ValueError, match="cannot replace"):
        prepared_asset_catalog(library)
