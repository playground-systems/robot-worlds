"""Hosted native check of coherent translation; no model or policy trial."""
import numpy as np
from test_generator import valid

from robot_worlds.editing import apply_patch
from robot_worlds.models import WorldSpec
from robot_worlds.runtime import Runtime


def test_translated_source_appliance_retains_native_joint_mount_and_motion():
    spec = apply_patch(WorldSpec.model_validate(valid()), {"operations": [{
        "op": "add_prepared_asset", "asset": {"id": "oven", "asset_id": "nvidia-microwave079", "position": [-2, -2, 0]},
    }]})
    shifted = apply_patch(spec, {"operations": [{"op": "translate_assembly", "id": "oven_part0", "offset": [.4, .2, 0]}]})
    samples = []
    for candidate in (spec, shifted):
        runtime = Runtime(candidate, render=False)
        try:
            jid = runtime.model.joint("joint_oven_part9").id
            body = runtime.model.body("object_oven_part9").id
            root = runtime.model.body("object_oven_part0").id
            qadr, dadr = runtime.model.jnt_qposadr[jid], runtime.model.jnt_dofadr[jid]
            start = runtime.data.qpos[qadr]
            runtime.data.qfrc_applied[dadr] = -3
            runtime._step(.4)
            end = runtime.data.qpos[qadr]
            assert end < start-.01
            samples.append((runtime.model.jnt_pos[jid].copy(), runtime.model.body_pos[body].copy(),
                            runtime.model.body_pos[root].copy(), end))
        finally:
            runtime.close()
    assert np.allclose(samples[0][0], samples[1][0], rtol=0, atol=1e-9)
    assert np.allclose(samples[0][1], samples[1][1], rtol=0, atol=1e-9)
    assert np.allclose(samples[1][2]-samples[0][2], [.4, .2, 0], rtol=0, atol=1e-9)
    assert abs(samples[1][3]-samples[0][3]) < 1e-5
