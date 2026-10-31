"""Source-format tests use synthetic packages; no network or native simulation."""
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image
from test_mesh_assets import cube

from robot_worlds import AssetLibrary, download_fuel_asset, source_asset
from robot_worlds.asset_sourcing import AssetCandidate, AssetSearch
from robot_worlds.sdf_import import import_fuel_mesh_asset, import_sdf_mesh_asset


def package(root):
    (root / "meshes").mkdir(parents=True)
    (root / "materials/textures").mkdir(parents=True)
    vertices, faces = cube()
    vertices = vertices*.2 - [.1, .1, 0]
    mesh = "mtllib material.mtl\nusemtl painted\n"
    mesh += "".join("v " + " ".join(map(str, vertex)) + "\n" for vertex in vertices)
    mesh += "vt 0 0\n"
    mesh += "".join("f " + " ".join(f"{i+1}/1" for i in face) + "\n" for face in faces)
    (root / "meshes/model.obj").write_text(mesh)
    (root / "meshes/material.mtl").write_text("# Copyright synthetic fixture\nnewmtl painted\nmap_Kd surface.png\nKd .8 .7 .6\nd .9\n")
    Image.new("RGB", (2, 2), (80, 170, 220)).save(root / "materials/textures/surface.png")
    (root / "model.config").write_text('<model><name>Fixture</name><author><name>Test author</name></author><sdf version="1.6">scan.sdf</sdf></model>')
    (root / "scan.sdf").write_text('''<!-- Copyright synthetic SDF fixture -->
<sdf version="1.6"><model name="Fixture"><pose>1 0 0 0 0 0</pose>
<link name="scan"><pose>0 2 0 0 0 0</pose>
<visual name="appearance"><pose>0 0 .1 0 0 0</pose><geometry><mesh><uri>meshes/model.obj</uri></mesh></geometry></visual>
<collision name="surface"><pose>0 0 .1 0 0 0</pose><geometry><mesh><uri>model://Fixture/meshes/model.obj</uri></mesh></geometry></collision>
</link></model></sdf>''')
    (root / "ATTRIBUTION.md").write_text("Synthetic test fixture only, not a production asset.")
    return root


def import_fixture(root, **kwargs):
    return import_sdf_mesh_asset(root, prefix="fixture", mass=.3,
                                  source_notice=root / "ATTRIBUTION.md", **kwargs)


def candidate():
    source = "https://fuel.gazebosim.org/1.0/FixtureOwner/models/Fixture/1"
    return AssetCandidate("fuel:FixtureOwner/Fixture", source, "1", "Synthetic test license", {
        "owner": "FixtureOwner", "name": "Fixture", "license_url": "https://example.invalid/license",
        "download_url": source + "/Fixture.zip"})


def download_fixture(root, cache):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root))
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=stream.getvalue(), headers={"content-type": "application/zip"}))) as client:
        return download_fuel_asset(candidate(), cache, client=client)


def test_scan_preserves_mobility_translations_original_bytes_uv_texture_and_material(tmp_path):
    root = package(tmp_path / "source")
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    asset = import_fixture(root, position=[0, 0, .5])
    assert len(asset.objects) == 2
    body, visual = asset.objects
    assert body.movable and body.mass == .3
    assert body.position == pytest.approx([1, 2, .7])
    assert body.size == pytest.approx([.2, .2, .2])
    assert visual.parent_id == body.id and not visual.collision
    assert visual.color == pytest.approx([.8, .7, .6])
    assert visual.opacity == .9
    assert visual.texture_path == str(root / "materials/textures/surface.png")
    assert asset.body_ids["scan"] == body.id
    assert asset.geom_ids["surface"] == body.id
    assert all(Path(path).read_bytes() == data for path, data in before.items())
    for member, checksum in asset.source_files.items():
        if member.startswith("attribution/"):
            continue
        assert hashlib.sha256((root / member).read_bytes()).hexdigest() == checksum


def test_explicit_static_source_is_not_made_dynamic(tmp_path):
    root = package(tmp_path / "source")
    sdf = root / "scan.sdf"
    sdf.write_text(sdf.read_text().replace('<model name="Fixture">', '<model name="Fixture"><static>true</static>'))
    asset = import_fixture(root)
    assert not any(obj.movable for obj in asset.objects)
    assert any("static=true" in note for note in asset.disclosures)


def test_one_wrapped_model_root_and_configured_sdf_filename_are_resolved(tmp_path):
    package(tmp_path / "outer" / "wrapper")
    asset = import_sdf_mesh_asset(tmp_path / "outer", prefix="item", mass=.2,
                                  source_notice=tmp_path / "outer/wrapper/ATTRIBUTION.md")
    assert asset.objects[0].movable
    package(tmp_path / "outer" / "another")
    with pytest.raises(ValueError, match="unambiguous"):
        import_sdf_mesh_asset(tmp_path / "outer", prefix="item", mass=.2,
                              source_notice=tmp_path / "outer/wrapper/ATTRIBUTION.md")


@pytest.mark.parametrize("old,new", [
    ('<link name="scan">', '<joint name="door" type="revolute"/><link name="scan">'),
    ('<link name="scan">', '<plugin name="execute" filename="anything"/><link name="scan">'),
    ('<link name="scan">', '<model name="nested"/><link name="scan">'),
    ('<link name="scan">', '<link name="extra"/><link name="scan">'),
    ('<link name="scan">', '<link name="scan"><inertial><mass>2</mass></inertial>'),
    ('<link name="scan">', '<link name="scan"><gravity>false</gravity>'),
    ('<link name="scan">', '<link name="scan"><kinematic>true</kinematic>'),
    ('<collision name="surface">', '<collision name="surface"><surface><friction/></surface>'),
    ('1 0 0 0 0 0', '1 0 0 0 0 1'),
    ('<pose>1 0 0 0 0 0</pose>', '<pose relative_to="world">1 0 0 0 0 0</pose>'),
    ('<mesh>', '<mesh><scale>2 1 1</scale>'),
    ('<mesh>', '<mesh><submesh><name>part</name></submesh>'),
    ('meshes/model.obj', 'https://example.invalid/object.obj'),
    ('meshes/model.obj', '../../outside.obj'),
    ('model://Fixture/', 'model://OtherModel/'),
    ('<visual name="appearance">', '<visual name="appearance"><material><script/></material>'),
])
def test_unsupported_semantics_and_external_paths_fail_before_import(tmp_path, old, new):
    root = package(tmp_path / "source")
    sdf = root / "scan.sdf"
    sdf.write_text(sdf.read_text().replace(old, new))
    with pytest.raises(ValueError):
        import_fixture(root)


def test_entity_declarations_are_rejected_while_copyright_comments_are_preserved(tmp_path):
    root = package(tmp_path / "source")
    sdf = root / "scan.sdf"
    sdf.write_text('<!DOCTYPE sdf [<!ENTITY secret SYSTEM "file:///etc/passwd">]>' + sdf.read_text())
    with pytest.raises(ValueError, match="declarations"):
        import_fixture(root)


def test_concave_or_open_mesh_cannot_be_silently_replaced_by_its_convex_hull(tmp_path):
    root = package(tmp_path / "source")
    mesh = root / "meshes/model.obj"
    data = mesh.read_text()
    lines = data.splitlines()
    vertex_lines = [i for i, line in enumerate(lines) if line.startswith("v ")]
    lines[vertex_lines[6]] = "v 0 0 .08"
    mesh.write_text("\n".join(lines))
    with pytest.raises(ValueError, match="decomposition"):
        import_fixture(root)
    mesh.write_text("\n".join(data.splitlines()[:-1]))
    with pytest.raises(ValueError, match="decomposition"):
        import_fixture(root)


def test_missing_texture_and_multiple_materials_fail_instead_of_dropping_appearance(tmp_path):
    root = package(tmp_path / "source")
    texture = root / "materials/textures/surface.png"
    texture.unlink()
    with pytest.raises(ValueError, match="Texture"):
        import_fixture(root)
    Image.new("RGB", (2, 2)).save(texture)
    with (root / "meshes/material.mtl").open("a") as stream:
        stream.write("newmtl another\n")
    with pytest.raises(ValueError, match="MTL"):
        import_fixture(root)


def test_download_source_import_library_round_trip_retains_notices_and_provenance(tmp_path):
    root = package(tmp_path / "source")
    downloaded = download_fixture(root, tmp_path / "cache")
    selected = candidate()
    report = AssetSearch("test scanned object", ("synthetic Fuel response",), (selected,), True)
    sourced = source_asset(report.query, search=lambda _: report,
                           import_asset=lambda item: import_fuel_mesh_asset(downloaded, candidate=item, prefix="scan", mass=.3),
                           create_asset=lambda *_: pytest.fail("Available import was ignored"))
    notice = Path(sourced.asset.objects[0].source_notice).read_text()
    assert "Copyright synthetic SDF fixture" in notice
    assert "Copyright synthetic fixture" in notice
    assert "Test author" in notice and "Synthetic test license" in notice
    metadata = json.loads(Path(sourced.asset.objects[0].source_metadata).read_text())
    assert metadata["archive_sha256"] == downloaded.archive_sha256
    assert metadata["source_static"] is False
    assert metadata["mass_estimate_kg"] == .3
    saved = AssetLibrary().add("scanned-object", sourced, description="Synthetic scan").save(tmp_path / "library")
    shutil.rmtree(tmp_path / "cache")
    shutil.rmtree(root)
    asset = AssetLibrary.open(saved).load("scanned-object", prefix="fresh").asset
    assert asset.objects[0].movable
    assert Path(asset.objects[-1].texture_path).is_file()
    assert "Copyright synthetic SDF fixture" in Path(asset.objects[0].source_notice).read_text()


@pytest.mark.parametrize("failure", ["tamper", "unsupported"])
def test_download_tampering_or_unsupported_import_does_not_authorize_creation(tmp_path, failure):
    root = package(tmp_path / "source")
    if failure == "unsupported":
        sdf = root / "scan.sdf"
        sdf.write_text(sdf.read_text().replace('<link name="scan">', '<joint name="hinge"/><link name="scan">'))
    downloaded = download_fixture(root, tmp_path / "cache")
    if failure == "tamper":
        (downloaded.source_dir / "scan.sdf").write_text("changed")
    selected = candidate()
    report = AssetSearch("scan", ("fixture",), (selected,), True)
    with pytest.raises((RuntimeError, ValueError)):
        source_asset("scan", search=lambda _: report,
                     import_asset=lambda item: import_fuel_mesh_asset(downloaded, candidate=item, prefix="scan", mass=.3),
                     create_asset=lambda *_: pytest.fail("Failed source authorized creation"))


def test_original_google_bowl_uses_source_bound_collision_pieces_not_a_filled_hull(tmp_path):
    from robot_worlds import asset_import

    source = Path(asset_import.__file__).parent / "assets/household/porcelain_bowl"
    # The selected package has actual SDF/MTL/comment metadata. No new search or conversion.
    asset = import_sdf_mesh_asset(source, prefix="bowl", mass=.6, source_notice=source / "ATTRIBUTION.md",
                                  collision_manifest=source / "collision/manifest.json")
    assert len(asset.objects) == 159
    assert sum(obj.mass for obj in asset.objects if obj.collision) == pytest.approx(.6)
    assert sum(obj.movable for obj in asset.objects) == 1
    assert np.array_equal(asset.objects[-1].color, [1, 1, 1])
    assert Path(asset.objects[-1].texture_path).read_bytes() == (source / "materials/textures/texture.png").read_bytes()
    assert asset.source_files["model.sdf"] == hashlib.sha256((source / "model.sdf").read_bytes()).hexdigest()


def test_relative_download_cache_still_returns_absolute_import_file_paths(tmp_path, monkeypatch):
    root = package(tmp_path / "source")
    monkeypatch.chdir(tmp_path)
    downloaded = download_fixture(root, Path("cache"))
    asset = import_fuel_mesh_asset(downloaded, candidate=candidate(), prefix="scan", mass=.3, friction=0)
    assert asset.objects[0].friction == 0
    assert all(Path(value).is_absolute() for obj in asset.objects for value in
               (obj.mesh_path, obj.texture_path, obj.source_notice, obj.source_metadata) if value)


def test_package_configuration_and_notice_symlinks_cannot_escape_root(tmp_path):
    root = package(tmp_path / "source")
    (tmp_path / "external.config").write_bytes((root / "model.config").read_bytes())
    (root / "model.config").unlink()
    (root / "model.config").symlink_to(tmp_path / "external.config")
    with pytest.raises(ValueError, match="escapes"):
        import_fixture(root)


def test_configured_version_must_match_source_and_extra_notices_are_retained(tmp_path):
    root = package(tmp_path / "source")
    (root / "LICENSE.txt").write_text("Retain this additional source copyright notice.")
    downloaded = download_fixture(root, tmp_path / "cache")
    asset = import_fuel_mesh_asset(downloaded, candidate=candidate(), prefix="scan", mass=.3)
    assert "additional source copyright" in Path(asset.objects[0].source_notice).read_text()
    config = root / "model.config"
    config.write_text(config.read_text().replace('version="1.6"', 'version="1.7"'))
    with pytest.raises(ValueError, match="versions disagree"):
        import_fixture(root)
