"""Bound repeated asset work without brittle wall-clock thresholds."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from test_convex_asset_import import converted  # noqa: F401
from test_sourced_household import sourced_scene

from robot_worlds import World, import_convex_mesh_asset, mesh_assets, sdk
from robot_worlds.runtime import Runtime


def test_repeated_import_reuses_verification_but_checks_current_bytes(converted, monkeypatch):  # noqa: F811
    calls = []
    original = mesh_assets.convex_volume

    def counted(vertices, faces):
        calls.append(1)
        return original(vertices, faces)

    monkeypatch.setattr(mesh_assets, "convex_volume", counted)
    first = import_convex_mesh_asset(**converted)
    count = len(calls)
    second = import_convex_mesh_asset(**converted)
    assert len(calls) == count
    second.objects[0].mass = 99
    assert first.objects[0].mass != 99  # No shared mutable scene objects.
    manifest = json.loads(converted["collision_manifest"].read_text())
    path = converted["collision_manifest"].parent / manifest["parts"][0]["file"]
    path.write_text(path.read_text().replace("v 0.0", "v 0.9", 1))
    with pytest.raises(ValueError, match="modified"):
        import_convex_mesh_asset(**converted)
    # Even a new matching manifest cannot reuse a previous geometry verdict.
    manifest["parts"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    converted["collision_manifest"].write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        import_convex_mesh_asset(**converted)


def test_world_freezes_shared_source_once_per_operation(tmp_path, monkeypatch):
    spec, _, _ = sourced_scene()
    metadata = Path(next(o.source_metadata for o in spec.objects if o.source_metadata))
    calls = []
    digest = sdk.file_digest

    def counted(path):
        if Path(path).resolve() == metadata:
            calls.append(1)
        return digest(path)

    monkeypatch.setattr(sdk, "file_digest", counted)
    World.from_spec(spec, tmp_path, validate=False)
    assert len(calls) == 1


def test_preparing_original_obj_scene_does_not_compile_per_piece(monkeypatch):
    spec, _, _ = sourced_scene()
    runtime = object.__new__(Runtime)
    runtime.spec, runtime.asset_root = spec, Path(".")
    runtime._objects = {o.id: o for o in spec.objects}

    class NoProbe:
        @staticmethod
        def from_xml_string(*args):
            pytest.fail("OBJ preparation must not compile a native model per piece")

    monkeypatch.setattr("robot_worlds.runtime.mujoco.MjModel", NoProbe)
    root = ET.fromstring("<mujoco><asset/><worldbody/></mujoco>")
    xml = ET.fromstring(runtime._append_objects(root))
    assert len(xml.findall("asset/mesh")) == sum(o.kind == "mesh" for o in spec.objects)
