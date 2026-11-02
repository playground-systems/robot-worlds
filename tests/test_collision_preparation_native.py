"""Opt-in hosted conversion tests; never run by the default local test command."""
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
from test_collision_preparation import concave_source
from test_sdf_import import package

from robot_worlds import (
    asset_import,
    collision_preparation,
    import_sdf_mesh_asset,
    prepare_collision_mesh,
)
from robot_worlds.mesh_assets import read_triangle_obj
from robot_worlds.models import SceneObject, WorldSpec
from robot_worlds.runtime import Runtime

pytestmark = pytest.mark.skipif(os.environ.get("ROBOT_WORLDS_TEST_PREPARATION") != "1",
                                 reason="Native conversion runs only in the explicit hosted preparation job")


def test_native_concave_scan_prepares_on_demand_and_reuses_without_converter(tmp_path, monkeypatch):
    root = package(tmp_path / "source")
    concave_source(root / "meshes/model.obj")
    before = (root / "meshes/model.obj").read_bytes()
    asset = import_sdf_mesh_asset(root, prefix="scan", mass=.3, source_notice=root / "ATTRIBUTION.md",
                                  preparation_cache=tmp_path / "cache", preparation_timeout=120,
                                  position=[0, 0, -.099])
    assert len(asset.objects) > 2
    assert sum(obj.mass for obj in asset.objects if obj.collision) == pytest.approx(.3)
    assert (root / "meshes/model.obj").read_bytes() == before
    monkeypatch.setattr(collision_preparation, "_installed_converter", lambda: pytest.fail("Cache reuse loaded converter"))
    again = prepare_collision_mesh(root / "meshes/model.obj", tmp_path / "cache")
    assert again.cache_hit
    assert again.method == "CoACD"
    # The empty notch is inside the full convex hull. It must remain empty.
    probe = np.array([.13, .13, .05])
    manifest = json.loads(again.manifest_path.read_text())
    for record in manifest["parts"]:
        vertices, faces = read_triangle_obj(again.manifest_path.parent / record["file"])
        points = vertices[faces]
        normals = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
        normals[np.einsum("ij,ij->i", normals, vertices.mean(0)-points[:, 0]) > 0] *= -1
        assert not np.all(np.einsum("ij,ij->i", probe-points[:, 0], normals) <= 1e-12)


def test_new_preparation_of_original_bowl_keeps_native_cavity_open(tmp_path):
    root = Path(asset_import.__file__).parent / "assets/household/porcelain_bowl"
    started = time.perf_counter()
    asset = import_sdf_mesh_asset(root, prefix="bowl", mass=.6, source_notice=root / "ATTRIBUTION.md",
                                  preparation_cache=tmp_path / "cache", preparation_timeout=120,
                                  position=[1, 0, .601])
    first_seconds = time.perf_counter() - started
    started = time.perf_counter()
    repeated = import_sdf_mesh_asset(root, prefix="bowl", mass=.6, source_notice=root / "ATTRIBUTION.md",
                                     preparation_cache=tmp_path / "cache", position=[1, 0, .601])
    metrics = {"first_import_seconds": first_seconds, "repeat_import_seconds": time.perf_counter()-started,
               "components": len(asset.objects), "source": "google-porcelain-bowl"}
    assert repeated.objects == asset.objects
    (tmp_path / "timing.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics), flush=True)
    visual = next(obj for obj in asset.objects if not obj.collision)
    spec = WorldSpec(name="Fresh collision preparation probe", description="Original Google source; native cavity check",
                     task="Keep the test sphere supported inside the bowl", bounds=(8, 6, 3),
                     objects=[SceneObject(id="counter", label="Support", kind="box", position=(1, 0, .3), size=(.6, .5, .6)),
                              *asset.objects,
                              SceneObject(id="marble", label="Cavity test sphere", kind="sphere", position=(1, 0, .651),
                                          size=(.012,)*3, mass=.002, movable=True)],
                     rubric=[dict(id="inside_probe", description="Probe near bowl", predicate="near", object_id="marble",
                                  target_id=asset.body_ids["object"], threshold=.3, intent="maintain")])
    runtime = Runtime(spec, render=False)
    try:
        runtime._step(.3)
        state = runtime.snapshot()["objects"]
        bottom = state[visual.id]["position"][2] - visual.size[2]/2
        assert bottom > .595
        assert bottom+.009 < state["marble"]["position"][2] < bottom+.035
        assert set(state["marble"]["contacts"]) & {obj.id for obj in asset.objects if obj.collision}
    finally:
        runtime.close()
