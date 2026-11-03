"""Tiny source fixture only; no kitchen compilation, renderer or physics steps."""

import io
import json
import struct
import xml.etree.ElementTree as ET
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

from robot_worlds.source_scene_bundle import package_source_scene, verify_source_scene_bundle
from robot_worlds.source_scene_preview import (
    _geometry,
    _png_metadata,
    export_source_scene_preview,
    prepare_texture_free_scene,
)


def png(srgb=False):
    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + (chunk(b"sRGB", b"\0") if srgb else b"")
        + chunk(b"IDAT", zlib.compress(b"\0\xff\x80\x40"))
        + chunk(b"IEND", b"")
    )


def tiny_bundle(tmp_path, extra_bodies=""):
    root = tmp_path / "source"
    (root / "meshes").mkdir(parents=True)
    (root / "paint.png").write_bytes(png(srgb=True))
    (root / "LICENSE.txt").write_text("Synthetic source fixture only, CC0.\n")
    (root / "meshes/tetra.obj").write_text(
        "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\n"
        "vt 0 0\nvt 1 0\nvt 0 1\nvt 1 1\n"
        "f 1/1 3/3 2/2\nf 1/1 2/2 4/4\nf 1/1 4/4 3/3\nf 2/2 3/3 4/4\n"
    )
    xml = root / "fixture.xml"
    xml.write_text("""<mujoco model="synthetic tiny fixture"><compiler angle="radian" meshdir="meshes/"/>
<visual><map znear=".003" zfar="40"/><headlight active="1" ambient=".1 .2 .3" diffuse=".4 .5 .6"/></visual>
<asset><texture name="source_sky" type="skybox" builtin="gradient" rgb1=".98 .98 1" rgb2=".1 .2 .3" width="1" height="6"/>
<texture name="paint" type="2d" file="paint.png" hflip="true"/>
<mesh name="original_mesh" file="tetra.obj" scale=".2 .3 .4"/>
<material name="paint_mat" texture="paint" rgba=".8 .7 .6 .5" specular=".3" shininess=".4" emission=".2" reflectance=".1"/>
</asset><worldbody><light name="source_light" pos="1 2 3" dir="0 0 -1" directional="true" castshadow="false"/>
<body name="frame" pos=".3 .4 .5"><geom name="physical" type="box" size=".1 .1 .1" group="0"/>
<body name="door" pos=".1 .2 .3"><inertial pos="0 0 0" mass="1" diaginertia=".1 .1 .1"/>
<joint name="hinge" type="hinge" axis="0 0 1" ref=".2" range="-1 1"/>
<geom name="visible" type="mesh" mesh="original_mesh" material="paint_mat" group="1" pos=".1 .2 .3" quat=".7071067811865476 0 0 .7071067811865476"/>
<geom name="color_override" type="sphere" size=".05" material="paint_mat" rgba=".2 .3 .4 .5" group="1"/>
</body></body></worldbody></mujoco>""")
    if extra_bodies:
        xml.write_text(xml.read_text().replace("</worldbody>", extra_bodies + "</worldbody>"))
    return package_source_scene(
        xml,
        root,
        tmp_path / "bundle",
        {"licenses": [{"path": "LICENSE.txt", "license": "CC0-1.0"}]},
    )


def test_offline_derivative_detaches_only_render_textures_and_resolves_native_files(tmp_path):
    bundle = tiny_bundle(tmp_path)
    before = bundle.xml_path.read_bytes()
    prepared = prepare_texture_free_scene(bundle.directory)
    original, derived = ET.fromstring(before), ET.fromstring(prepared["xml"])
    assert derived.findall("./asset/texture") == []
    assert "texture" not in derived.find("./asset/material").attrib
    for tag in ["worldbody", "visual"]:
        assert ET.tostring(original.find(tag)) == ET.tostring(derived.find(tag))
    assert derived.find("compiler").attrib == {
        **original.find("compiler").attrib, "usethread": "false"
    }
    assert prepared["edits"]["compiler_execution"] == {
        "attribute": "usethread", "original": None, "derived": "false"
    }
    assert prepared["materials"]["paint_mat"]["resolved_attributes"]["texture"] == "paint"
    assert prepared["textures"][1]["original_attributes"]["file"].startswith("assets/")
    assert derived.find("./asset/mesh").get("file").startswith(str(bundle.directory))
    assert bundle.xml_path.read_bytes() == before
    assert prepared["edits"]["absolute_asset_references"] == 2


@pytest.mark.parametrize("srgb", [False, True])
def test_png_colorspace_uses_explicit_srgb_chunk_without_decoding_pixels(tmp_path, srgb):
    image = tmp_path / "original.png"
    image.write_bytes(png(srgb))
    assert _png_metadata(image) == {"width": 1, "height": 1, "png_srgb_chunk": srgb}


def test_mesh_stream_preserves_independent_corner_indices_and_little_endian_offsets():
    model = SimpleNamespace(
        nmesh=1,
        mesh_faceadr=[0],
        mesh_facenum=[2],
        mesh_vertadr=[0],
        mesh_normaladr=[0],
        mesh_texcoordadr=[0],
        mesh_face=np.array([[0, 1, 2], [0, 2, 3]]),
        mesh_facenormal=np.array([[0, 0, 0], [1, 1, 1]]),
        mesh_facetexcoord=np.array([[0, 1, 2], [3, 2, 4]]),
        mesh_vert=np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32),
        mesh_normal=np.array([[0, 0, 1], [0, 1, 0]], dtype=np.float32),
        mesh_texcoord=np.array([[0, 0], [1, 0], [1, 1], [0.25, 0.75], [0, 1]], dtype=np.float32),
    )
    stream = io.BytesIO()
    mesh = _geometry(model, stream, np)[0]
    assert mesh["position"] == {"offset": 0, "count": 6, "item_size": 3}
    assert mesh["normal"]["offset"] == 72
    assert mesh["uv"]["offset"] == 144
    positions = np.frombuffer(stream.getvalue(), dtype="<f4", count=18).reshape(6, 3)
    normals = np.frombuffer(stream.getvalue(), dtype="<f4", offset=72, count=18).reshape(6, 3)
    uv = np.frombuffer(stream.getvalue(), dtype="<f4", offset=144).reshape(6, 2)
    assert positions[0].tolist() == positions[3].tolist() == [0, 0, 0]
    assert normals[0].tolist() == [0, 0, 1]
    assert normals[3].tolist() == [0, 1, 0]
    assert uv[0].tolist() == [0, 0]
    assert uv[3].tolist() == [0.25, 0.75]


def test_tiny_native_export_uses_zero_textures_zero_steps_and_exact_frames(tmp_path, monkeypatch):
    import mujoco

    bundle = tiny_bundle(tmp_path)
    original = bundle.xml_path.read_bytes()
    captured = []
    compile_original = mujoco.MjModel.from_xml_string

    def compile_geometry(xml, *args, **kwargs):
        assert ET.fromstring(xml).findall("./asset/texture") == []
        model = compile_original(xml, *args, **kwargs)
        captured.append(model)
        return model

    monkeypatch.setattr(mujoco.MjModel, "from_xml_string", compile_geometry)
    monkeypatch.setattr(
        mujoco, "mj_step", lambda *_: pytest.fail("A frozen preview stepped physics")
    )
    monkeypatch.setattr(
        mujoco, "Renderer", lambda *_a, **_k: pytest.fail("A native renderer was created")
    )
    views = [{"id": "authored", "eye_m": [2, 3, 4], "target_m": [0, 0, 0]}]
    path = export_source_scene_preview(bundle.directory, tmp_path / "preview", views=views)
    preview = json.loads(path.read_text())
    model = captured[0]
    assert model.ntex == 0
    assert preview["native"]["textures"] == 0
    assert preview["native"]["physics_steps"] == 0
    assert preview["native"]["geoms"] == 3
    assert [o["name"] for o in preview["objects"]] == ["visible", "color_override"]
    for obj in preview["objects"]:
        assert obj["position"] == model.geom_pos[obj["id"]].tolist()
        assert obj["quaternion"] == model.geom_quat[obj["id"]].tolist()
    assert preview["bodies"][0]["parent_id"] is None
    joint = next(body for body in preview["bodies"] if body["name"] == "door")["joints"][0]
    assert joint["reference"] == pytest.approx(0.2)
    assert joint["axis"] == [0, 0, 1]
    assert preview["objects"][0]["effective_rgba"] == preview["materials"][0]["rgba"]
    assert preview["objects"][1]["effective_rgba"] == preview["objects"][1]["rgba"]
    assert preview["materials"][0]["texture_id"] == 1
    assert preview["materials"][0]["metallic"] < 0
    assert preview["textures"][1]["colorspace"] == "srgb"
    assert (path.parent / preview["textures"][1]["path"]).read_bytes() == png(srgb=True)
    assert preview["textures"][1]["original_attributes"]["hflip"] == "true"
    assert preview["views"] == views
    assert preview["background"]["color"] == [0.98, 0.98, 1]
    assert preview["clipping"]["near"] == pytest.approx(model.vis.map.znear * model.stat.extent)
    assert preview["lights"][0]["frame"] == "world"
    assert preview["headlight"]["active"] is True
    assert any(
        issue["feature"] == "procedural_texture" for issue in preview["unsupported_features"]
    )
    assert (path.parent / "geometry.bin").stat().st_size == preview["geometry_byte_length"]
    assert bundle.xml_path.read_bytes() == original
    verify_source_scene_bundle(bundle.directory)


def test_free_and_ball_joints_preserve_native_initial_pose_and_are_inspection_only(tmp_path):
    bundle = tiny_bundle(
        tmp_path,
        """
<body name="loose_prop" pos=".8 .6 .4" quat=".9238795325 0 0 .3826834324">
<freejoint name="source_free"/><geom name="loose_visual" type="box" size=".04 .05 .06" group="1"/>
</body><body name="ball_parent" pos="1 .3 .4" quat=".7071067812 0 .7071067812 0">
<body name="ball_prop" pos=".2 .3 .4" quat=".9238795325 .3826834324 0 0">
<joint name="source_ball" type="ball"/><geom name="ball_visual" type="sphere" size=".05" group="1"/>
</body></body>""",
    )
    before = bundle.xml_path.read_bytes()
    preview = json.loads(
        export_source_scene_preview(bundle.directory, tmp_path / "preview", views=[]).read_text()
    )
    assert preview["native"]["joint_types"] == {"hinge": 1, "free": 1, "ball": 1}
    assert preview["native"]["geoms"] == 5
    loose = next(body for body in preview["bodies"] if body["name"] == "loose_prop")
    ball = next(body for body in preview["bodies"] if body["name"] == "ball_prop")
    assert loose["position"] == pytest.approx([0.8, 0.6, 0.4])
    assert loose["quaternion"] == pytest.approx(loose["world_quaternion"])
    assert ball["position"] == pytest.approx([0.2, 0.3, 0.4])
    assert ball["quaternion"] == pytest.approx(ball["model_quaternion"])
    assert len(loose["joints"][0]["reference"]) == 7
    assert len(ball["joints"][0]["reference"]) == 4
    assert loose["joints"][0]["inspection_supported"] is False
    assert ball["joints"][0]["inspection_supported"] is False
    assert {
        issue["type"]
        for issue in preview["unsupported_features"]
        if issue["feature"] == "joint_inspection_unsupported"
    } == {"free", "ball"}
    assert bundle.xml_path.read_bytes() == before
