"""Capability checks for tests that require a native graphics context."""

import os
import sys

import pytest


def _hosted_macos_graphics_unavailable(error):
    if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
        return False
    from mujoco.cgl.cgl import CGLError

    return isinstance(error, CGLError) and str(error).strip().lower() == "invalid pixel format"


@pytest.fixture(scope="session")
def native_rendering():
    """Probe graphics, without excusing renderer regressions on developer Macs."""
    import mujoco

    model = mujoco.MjModel.from_xml_string(
        '<mujoco><worldbody><geom type="sphere" size="0.05"/></worldbody></mujoco>'
    )
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    try:
        with mujoco.Renderer(model, height=32, width=32) as renderer:
            renderer.update_scene(data)
            image = renderer.render()
            assert image.shape == (32, 32, 3)
    except Exception as error:
        if _hosted_macos_graphics_unavailable(error):
            pytest.skip(
                "GitHub-hosted macOS runner graphics unavailable: MuJoCo CGL returned invalid pixel format"
            )
        raise
