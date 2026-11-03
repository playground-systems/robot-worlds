"""Offline native-source packaging contracts; no simulator or model calls."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from robot_worlds.artifacts import digest, file_digest
from robot_worlds.source_scene_bundle import package_source_scene, verify_source_scene_bundle


def source_scene(tmp_path):
    root = tmp_path / "source"
    (root / "meshes").mkdir(parents=True)
    (root / "textures").mkdir()
    # Deliberately tiny opaque dependencies: this test never compiles geometry.
    (root / "meshes/panel.obj").write_bytes(b"# synthetic source mesh\nv 0 0 0\n")
    (root / "textures/a&b.png").write_bytes(b"synthetic texture payload")
    (root / "LICENSE.txt").write_text(
        "Synthetic test fixture, CC0; not a production source asset.\n"
    )
    xml = root / "kitchen.xml"
    xml.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<mujoco model="source fixture">
<!-- Preserve comments, spacing, attribute ordering and quote choices. -->
<compiler angle='radian' meshdir="meshes/" texturedir='textures/'/>
<default><geom friction=".8 .01 .001"/><default class="door"><joint damping=".3"/></default></default>
<option timestep="0.002"/><asset>
  <mesh name='original_mesh' file = 'panel.obj' scale="1 2 3" />
  <texture name="paint" file="a&amp;b.png" type="2d"/>
  <material name="paint_material" texture="paint"/>
</asset><worldbody><body name="frame" pos="0 0 .5">
<geom type="box" size="1 1 .1"/><body name="door" childclass="door">
<inertial pos=".1 0 0" mass=".7" diaginertia=".01 .02 .02"/>
<joint name="door_hinge" type="hinge" axis="0 0 1" range="0 1.57"/>
<geom type="mesh" mesh="original_mesh" material="paint_material"/>
</body></body></worldbody><contact><exclude body1="frame" body2="door"/></contact>
</mujoco>"""
    )
    provenance = {
        "source_url": "https://example.invalid/synthetic-fixture",
        "revision": "synthetic-test-1",
        "licenses": [{"path": "LICENSE.txt", "license": "CC0-1.0"}],
    }
    return root, xml, provenance


def test_only_file_attribute_values_change_and_bundle_survives_source_removal(tmp_path):
    root, xml, provenance = source_scene(tmp_path)
    original = xml.read_bytes()
    expected = original.replace(b"'panel.obj'", b"'assets/meshes/panel.obj'").replace(
        b'"a&amp;b.png"', b'"assets/textures/a&amp;b.png"'
    )
    result = package_source_scene(xml, root, tmp_path / "portable", provenance)
    assert result.xml_path.name == xml.name
    assert result.xml_path.read_bytes() == expected
    assert xml.read_bytes() == original
    assert (result.directory / "original/kitchen.xml").read_bytes() == original
    assert (result.directory / "meshes/assets/meshes/panel.obj").read_bytes() == (
        root / "meshes/panel.obj"
    ).read_bytes()
    assert (result.directory / "textures/assets/textures/a&b.png").read_bytes() == (
        root / "textures/a&b.png"
    ).read_bytes()
    assert (result.directory / "notices/LICENSE.txt").read_bytes() == (
        root / "LICENSE.txt"
    ).read_bytes()
    assert result.manifest["provenance"] == provenance
    assert all(
        row["original_sha256"] == row["bundled_sha256"] for row in result.manifest["files"].values()
    )
    assert result.manifest["validation"] == {
        "xml_preserved_except_file_attributes": True,
        "native_compile_performed": False,
        "physics_verified": False,
    }
    shutil.rmtree(root)
    moved = tmp_path / "moved"
    result.directory.rename(moved)
    verified = verify_source_scene_bundle(moved)
    assert verified.xml_path.read_bytes() == expected


def test_absolute_source_references_assetdir_and_cube_files_keep_original_names(tmp_path):
    root, xml, provenance = source_scene(tmp_path)
    mesh = root / "meshes/panel.obj"
    xml.write_text(
        f'''<mujoco><compiler assetdir="textures" meshdir="meshes"/>
<asset><mesh file="{mesh}"/><hfield file="panel.obj" size="1 1 1 .1"/>
<skin file="panel.obj"/><texture fileright="a&amp;b.png" fileleft="a&amp;b.png"/></asset>
<worldbody/></mujoco>'''
    )
    result = package_source_scene(xml, root, tmp_path / "bundle", provenance)
    assert len(result.manifest["references"]) == 5
    assert len([row for row in result.manifest["files"].values() if row["kind"] == "asset"]) == 2
    assert all(
        not Path(row["bundled_value"]).is_absolute() for row in result.manifest["references"]
    )
    verify_source_scene_bundle(result.directory)


def test_utf8_and_file_like_text_inside_other_attributes_are_not_rewritten(tmp_path):
    root, xml, provenance = source_scene(tmp_path)
    (root / "meshes/é panel.obj").write_bytes(b"synthetic unicode mesh")
    original = """<mujoco model="é"><compiler meshdir='meshes'/><asset>
<mesh name='literal file="not-an-attribute"' file="é panel.obj"/>
</asset><custom><text name="notes"><![CDATA[keep file="text"]]></text></custom><worldbody/></mujoco>""".encode()
    xml.write_bytes(original)
    result = package_source_scene(xml, root, tmp_path / "bundle", provenance)
    assert result.xml_path.read_bytes() == original.replace(
        'file="é panel.obj"'.encode(), 'file="assets/meshes/é panel.obj"'.encode()
    )


@pytest.mark.parametrize("kind", ["relative", "absolute", "symlink", "missing"])
def test_file_escape_and_missing_dependencies_leave_no_bundle(tmp_path, kind):
    root, xml, provenance = source_scene(tmp_path)
    outside = tmp_path / "outside.obj"
    outside.write_bytes(b"outside")
    value = {
        "relative": "../../outside.obj",
        "absolute": str(outside),
        "symlink": "escape.obj",
        "missing": "absent.obj",
    }[kind]
    if kind == "symlink":
        (root / "meshes/escape.obj").symlink_to(outside)
    xml.write_text(
        f'<mujoco><compiler meshdir="meshes"/><asset><mesh file="{value}"/></asset></mujoco>'
    )
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="Missing source file|outside source root"):
        package_source_scene(xml, root, destination, provenance)
    assert not destination.exists()


@pytest.mark.parametrize(
    "content",
    [
        '<include file="other.xml"/>',
        '<asset><model file="other.xml"/></asset>',
        '<worldbody><attach model="another"/></worldbody>',
        '<extension><plugin plugin="example.plugin"/></extension>',
        '<worldbody><body plugin="example.plugin"/></worldbody>',
        '<worldbody><geom file="meshes/panel.obj"/></worldbody>',
        '<asset><mesh file="https://example.invalid/panel.obj"/></asset>',
    ],
)
def test_unsupported_external_execution_or_file_semantics_are_rejected(tmp_path, content):
    root, xml, provenance = source_scene(tmp_path)
    xml.write_text(f"<mujoco>{content}</mujoco>")
    with pytest.raises(ValueError):
        package_source_scene(xml, root, tmp_path / "rejected", provenance)


@pytest.mark.parametrize(
    "prefix", ['<!DOCTYPE mujoco [<!ENTITY x "test">]>', '<?xml-stylesheet href="outside"?>']
)
def test_xml_external_mechanisms_are_not_executed(tmp_path, prefix):
    root, xml, provenance = source_scene(tmp_path)
    xml.write_text(prefix + "<mujoco/>")
    with pytest.raises(ValueError, match="declarations/entities"):
        package_source_scene(xml, root, tmp_path / "rejected", provenance)


@pytest.mark.parametrize(
    "setting",
    ['meshdir="/absolute"', 'assetdir="../escape"', 'texturedir="C:\\data"', 'strippath="true"'],
)
def test_unsafe_or_nonportable_compiler_lookup_is_not_silently_changed(tmp_path, setting):
    root, xml, provenance = source_scene(tmp_path)
    xml.write_text(f"<mujoco><compiler {setting}/></mujoco>")
    with pytest.raises(ValueError):
        package_source_scene(xml, root, tmp_path / "rejected", provenance)


@pytest.mark.parametrize("target", ["asset", "scene", "original", "notice", "manifest"])
def test_tampering_fails_offline_verification(tmp_path, target):
    root, xml, provenance = source_scene(tmp_path)
    bundle = package_source_scene(xml, root, tmp_path / "bundle", provenance)
    if target == "scene":
        bundle.xml_path.write_bytes(bundle.xml_path.read_bytes().replace(b'mass=".7"', b'mass="9"'))
    elif target == "manifest":
        manifest = copy.deepcopy(bundle.manifest)
        manifest["provenance"]["revision"] = "changed"
        bundle.manifest_path.write_text(json.dumps(manifest))
    else:
        kind = {"asset": "asset", "original": "original_xml", "notice": "license"}[target]
        member = next(key for key, row in bundle.manifest["files"].items() if row["kind"] == kind)
        (bundle.directory / member).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        verify_source_scene_bundle(bundle.directory)


def test_verifier_checks_geometry_losslessness_even_with_updated_bundle_hash(tmp_path):
    root, xml, provenance = source_scene(tmp_path)
    bundle = package_source_scene(xml, root, tmp_path / "bundle", provenance)
    bundle.xml_path.write_bytes(bundle.xml_path.read_bytes().replace(b'mass=".7"', b'mass="9"'))
    manifest = copy.deepcopy(bundle.manifest)
    manifest["scene"]["bundled_sha256"] = file_digest(bundle.xml_path)
    manifest["id"] = digest({key: value for key, value in manifest.items() if key != "id"})
    bundle.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="outside file references"):
        verify_source_scene_bundle(bundle.directory)


def test_retained_license_source_boundary_and_immutable_destination(tmp_path):
    root, xml, provenance = source_scene(tmp_path)
    with pytest.raises(ValueError, match="retained licenses"):
        package_source_scene(xml, root, tmp_path / "missing-license", {})
    provenance["licenses"][0]["path"] = "../outside.txt"
    with pytest.raises(ValueError, match="escapes"):
        package_source_scene(xml, root, tmp_path / "escape-notice", provenance)
    provenance["licenses"][0]["path"] = "LICENSE.txt"
    destination = tmp_path / "bundle"
    package_source_scene(xml, root, destination, provenance)
    with pytest.raises(ValueError, match="already exists"):
        package_source_scene(xml, root, destination, provenance)


@pytest.mark.parametrize("kind", ["member_escape", "asset_symlink"])
def test_rehashed_manifest_cannot_escape_bundle_boundary(tmp_path, kind):
    root, xml, provenance = source_scene(tmp_path)
    bundle = package_source_scene(xml, root, tmp_path / "bundle", provenance)
    outside = tmp_path / "outside.obj"
    member = next(key for key, row in bundle.manifest["files"].items() if row["kind"] == "asset")
    outside.write_bytes((bundle.directory / member).read_bytes())
    if kind == "member_escape":
        manifest = copy.deepcopy(bundle.manifest)
        manifest["files"]["../outside.obj"] = manifest["files"].pop(member)
        manifest["id"] = digest({key: value for key, value in manifest.items() if key != "id"})
        bundle.manifest_path.write_text(json.dumps(manifest))
    else:
        (bundle.directory / member).unlink()
        (bundle.directory / member).symlink_to(outside)
    with pytest.raises(ValueError, match="outside|escapes"):
        verify_source_scene_bundle(bundle.directory)


def test_non_utf8_xml_declaration_is_rejected_without_transcoding(tmp_path):
    root, xml, provenance = source_scene(tmp_path)
    xml.write_bytes(b'<?xml version="1.0" encoding="ISO-8859-1"?><mujoco/>')
    with pytest.raises(ValueError, match="UTF-8"):
        package_source_scene(xml, root, tmp_path / "bundle", provenance)


def test_manifest_symlink_outside_bundle_is_rejected(tmp_path):
    root, xml, provenance = source_scene(tmp_path)
    bundle = package_source_scene(xml, root, tmp_path / "bundle", provenance)
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(bundle.manifest_path.read_bytes())
    bundle.manifest_path.unlink()
    bundle.manifest_path.symlink_to(outside)
    with pytest.raises(ValueError, match="outside its bundle"):
        verify_source_scene_bundle(bundle.directory)
