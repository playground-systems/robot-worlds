"""Hosted checks that sharing file assets preserves dynamics and rendered pixels."""
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest
from test_runtime_asset_reuse import per_geom_assets, repeated_mesh_scene, scene_xml


def models(tmp_path):
    shared = scene_xml(repeated_mesh_scene(tmp_path), tmp_path)
    return [mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
            for root in (shared, per_geom_assets(shared))]


def test_native_mesh_reuse_preserves_independent_mass_pose_contact_and_motion(tmp_path):
    shared, separate = models(tmp_path)
    assert shared.nmesh == 2 and separate.nmesh == 3
    assert shared.ntex == separate.ntex - 2
    assert shared.nbody == separate.nbody
    assert shared.ngeom == separate.ngeom
    for field in ("body_mass", "body_inertia", "geom_pos", "geom_quat", "geom_friction", "geom_rgba"):
        np.testing.assert_allclose(getattr(shared, field), getattr(separate, field), atol=1e-12)
    for i in range(3):
        assert shared.body(f"object_part{i}").mass[0] == pytest.approx(.1*(i+1))
    data = [mujoco.MjData(model) for model in (shared, separate)]
    for model, state in zip((shared, separate), data):
        mujoco.mj_forward(model, state)
    initial = data[0].qpos.copy()
    for _ in range(300):
        for model, state in zip((shared, separate), data):
            mujoco.mj_step(model, state)
    assert not np.allclose(data[0].qpos, initial)  # Bodies actually fall and contact the floor.
    assert data[0].ncon > 0
    np.testing.assert_allclose(data[0].qpos, data[1].qpos, atol=1e-9)
    np.testing.assert_allclose(data[0].qvel, data[1].qvel, atol=1e-9)
    assert data[0].ncon == data[1].ncon


def test_native_shared_texture_render_matches_unshared_instances(tmp_path, native_rendering):
    images = []
    for model in models(tmp_path):
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        camera = mujoco.MjvCamera()
        camera.type = mujoco.mjtCamera.mjCAMERA_FREE
        camera.lookat[:] = [1.5, 0, .2]
        camera.distance, camera.azimuth, camera.elevation = 2.5, 90, -35
        with mujoco.Renderer(model, height=160, width=240) as renderer:
            renderer.update_scene(data, camera=camera)
            images.append(renderer.render().copy())
    assert np.std(images[0]) > 10
    np.testing.assert_allclose(images[0], images[1], atol=1)
