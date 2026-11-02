"""Original source asset checks on native CI; no model or autonomous policy call."""
import io

import numpy as np
from PIL import Image
from test_asset_import import imported_scene

from robot_worlds.models import SceneObject
from robot_worlds.runtime import Runtime


def test_original_textured_microwave_keeps_empty_cavity_and_passive_joints(native_rendering):
    asset, spec = imported_scene()
    spec.objects.append(SceneObject(id="probe", label="Cavity physics probe", kind="sphere",
                                   position=(1.96, .02, .76), size=(.04, .04, .04), mass=.02, movable=True))
    runtime = Runtime(spec)
    try:
        state = runtime.snapshot()
        assert .69 < state["objects"]["probe"]["position"][2] < .71
        assert asset.geom_ids["tray"] in state["objects"]["probe"]["contacts"]
        for obj in asset.objects:
            gid = runtime.model.geom("geom_"+obj.id).id
            assert bool(runtime.model.geom_contype[gid]) == obj.collision
            if not obj.visible:
                assert runtime.model.geom_rgba[gid, 3] == 0
        jid = runtime.model.joint("joint_"+asset.joint_ids["microjoint"]).id
        assert jid not in runtime.model.actuator_trnid[:, 0]
        before = runtime.data.qpos[runtime.model.jnt_qposadr[jid]]
        runtime.data.qfrc_applied[runtime.model.jnt_dofadr[jid]] = -3
        runtime._step(.4)
        after = runtime.data.qpos[runtime.model.jnt_qposadr[jid]]
        assert after < before - .01  # Applied test torque moves a real passive hinge.
        disc = runtime.model.joint("joint_"+asset.joint_ids["Disc001_joint"]).id
        assert not runtime.model.jnt_limited[disc]
        assert np.isfinite(runtime.data.qpos).all()
        # Aim just in front of the door. A target inside the enclosed cavity
        # correctly fails the spectator's camera-clearance guard.
        with Image.open(io.BytesIO(runtime.render(view="overview", lookat=[1.8, -.5, .8],
                                                 azimuth=90, elevation=-15, distance=1.2))) as image:
            assert image.size == (960, 720)
            assert len(set(image.resize((40, 30)).getdata())) > 30
    finally:
        runtime.close()
