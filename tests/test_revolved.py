"""Pure profile geometry and repair contracts; no native simulator or model usage."""
import math

import pytest
from pydantic import ValidationError
from test_appliance_generation import appliance_spec

from robot_worlds.editing import apply_patch, enforce_protections
from robot_worlds.generator import validate_spec
from robot_worlds.models import RevolvedShape, SceneObject
from robot_worlds.revolved import collision_count, cross, intersects_box, triangulate, wedges

BOWL_PROFILE = [(0, -.5), (.32, -.5), (.5, .5), (.45, .5), (.28, -.4), (0, -.4)]


def bowl(**kwargs):
    return SceneObject(id="bowl", label="Ceramic bowl", kind="revolved", position=(1, 0, .665),
                       size=(.16, .16, .15), movable=True, mass=.08,
                       revolved=RevolvedShape(profile=BOWL_PROFILE, **kwargs))


def test_concave_profile_triangulation_preserves_material_area_and_cavity():
    triangles = triangulate(BOWL_PROFILE)
    expected = sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(BOWL_PROFILE, BOWL_PROFILE[1:]+BOWL_PROFILE[:1]))/2
    assert sum(cross(*triangle)/2 for triangle in triangles) == pytest.approx(expected)
    assert triangulate(list(reversed(BOWL_PROFILE)))
    cavity_point = (.1, 0)
    assert not any(all(cross(a, b, cavity_point) >= 0 for a, b in zip(t, t[1:]+t[:1])) for t in triangles)


@pytest.mark.parametrize("profile", [
    [(0, -.5), (.5, .5), (0, .5), (.5, -.5)],  # crossing material boundary
    [(0, -.5), (.5, -.5), (.5, .5), (0, -.5)],  # duplicated closure
    [(0, -.5), (.4, -.5), (.4, .5)],  # inconsistent declared outer diameter
    [(0, -.5), (.5, 0), (.2, .2)],  # inconsistent declared height
    [(0, -.5), (.5, math.nan), (.2, .5)],
])
def test_invalid_profile_cannot_enter_world_contract(profile):
    with pytest.raises((ValidationError, ValueError)):
        RevolvedShape(profile=profile)


def test_wedges_are_bounded_and_have_consistent_positive_volume():
    obj = bowl()
    pieces = list(wedges(BOWL_PROFILE, 32, obj.size))
    assert len(pieces) == collision_count(obj) == 128
    assert all(len(points) >= 4 and volume > 0 for points, volume in pieces)
    assert all(abs(p[i]) <= obj.size[i]/2+1e-10 for points, _ in pieces for p in points for i in range(3))
    # Polygonal revolution converges to the Pappus integral, with sin(theta)/theta correction.
    integral = sum(cross(*t)/2 * sum(p[0] for p in t)/3 for t in triangulate(BOWL_PROFILE))
    expected = 32*math.sin(2*math.pi/32)*integral*obj.size[0]**2*obj.size[2]
    assert sum(v for _, v in pieces) == pytest.approx(expected)


def test_static_overlap_respects_hollow_interior_but_rejects_wall_penetration():
    obj = bowl()
    probe = SceneObject(id="probe", label="probe", kind="box", position=(1, 0, .68), size=(.02, .02, .02))
    assert not intersects_box(obj, probe)
    probe.position = (1.071, 0, .71)
    assert intersects_box(obj, probe)
    probe.position = (1, 0, .592)
    assert intersects_box(obj, probe)


def test_revolved_shape_is_scoped_editable_but_cannot_weaken_bound_task():
    spec = appliance_spec()
    spec.objects = [o for o in spec.objects if not o.id.startswith("bowl")]+[bowl()]
    assert validate_spec(spec) == []
    patch = {"operations": [{"op": "update_object", "id": "bowl", "fields": ["revolved"],
                             "values": {"revolved": {"profile": BOWL_PROFILE, "segments": 64}}}]}
    changed = apply_patch(spec, patch, scope=["bowl"], allow_task_changes=True)
    with pytest.raises(ValueError, match="revolved is protected"):
        enforce_protections(spec, changed)


def test_cavity_binding_and_attached_rim_cannot_be_removed_to_make_task_easier():
    spec = appliance_spec()
    changed = spec.model_copy(deep=True)
    changed.regions[0].interior_of = None
    with pytest.raises(ValueError, match="region"):
        enforce_protections(spec, changed)
    changed = spec.model_copy(deep=True)
    changed.objects = [o for o in changed.objects if o.id != "bowl_left"]
    with pytest.raises(ValueError, match="presence|membership"):
        enforce_protections(spec, changed)
    changed = spec.model_copy(deep=True)
    next(o for o in changed.objects if o.id == "bowl_left").position = (1.5, 0, .625)
    with pytest.raises(ValueError, match="attached geometry pose"):
        enforce_protections(spec, changed)


def test_explicit_collision_budget_counts_profile_wedges():
    spec = appliance_spec()
    spec.objects = [bowl(segments=64).model_copy(update={"id": f"bowl_{i}", "position": (2+i*.2, 0, 1)}) for i in range(80)]
    assert any("collision pieces" in error for error in validate_spec(spec))
