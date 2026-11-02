"""Pure error-classification checks; these never create a graphics context."""

import sys
from types import ModuleType

import pytest
from conftest import _hosted_macos_graphics_unavailable


class FakeCGLError(RuntimeError):
    pass


@pytest.mark.parametrize("platform,actions,error,expected", [
    ("darwin", "true", FakeCGLError("invalid pixel format"), True),
    ("darwin", "false", FakeCGLError("invalid pixel format"), False),
    ("darwin", None, FakeCGLError("invalid pixel format"), False),
    ("linux", "true", FakeCGLError("invalid pixel format"), False),
    ("darwin", "true", RuntimeError("invalid pixel format"), False),
    ("darwin", "true", FakeCGLError("invalid context"), False),
])
def test_skip_requires_hosted_macos_and_exact_cgl_capability_error(
    monkeypatch, platform, actions, error, expected
):
    module = ModuleType("mujoco.cgl.cgl")
    module.CGLError = FakeCGLError
    monkeypatch.setitem(sys.modules, "mujoco.cgl.cgl", module)
    monkeypatch.setattr(sys, "platform", platform)
    if actions is None:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", actions)
    assert _hosted_macos_graphics_unavailable(error) is expected
