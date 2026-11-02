"""Layout paint is visible, but cannot become a collision or grading fixture."""
import pytest
from pydantic import ValidationError
from test_generator import valid

from robot_worlds.generator import validate_spec
from robot_worlds.models import SceneObject, WorldSpec
from robot_worlds.rubric import grade, measure_predicate


def marking(**changes):
    return SceneObject(**(dict(id='lane', label='Yellow floor lane', kind='box',
                              position=[0, 0, .002], size=[4, .05, .002],
                              color=[1, .8, 0], collision=False) | changes))


def test_floor_paint_can_cross_robot_start_without_a_mesh_or_obstacle():
    spec = WorldSpec.model_validate(valid())
    spec.objects.append(marking())
    assert validate_spec(spec) == []
    spec.rubric[0].object_id = 'lane'
    assert any('physical object' in error for error in validate_spec(spec))


@pytest.mark.parametrize('change', [dict(movable=True), dict(visible=False),
                                  dict(size=[1, 1, .3]), dict(kind='sphere', size=[.01]*3)])
def test_marking_exception_does_not_allow_dynamic_or_thick_ghost_objects(change):
    with pytest.raises(ValidationError):
        marking(**change)


@pytest.mark.parametrize('predicate,subject,target', [('in_region', 'lane', 'destination'),
    ('near', 'part', 'lane'), ('on_top', 'part', 'lane')])
def test_markings_cannot_supply_physical_grading_evidence(predicate, subject, target):
    plan = valid()
    plan['objects'].append(marking().model_dump())
    plan['rubric'] = [dict(id='paint', description='Invalid physical reference', predicate=predicate,
                          object_id=subject, target_id=target, threshold=.5 if predicate == 'near' else None)]
    spec = WorldSpec.model_validate(plan)
    snapshot = {'time': 1, 'objects': {obj.id: {'position': list(obj.position), 'size': list(obj.size),
        'quaternion': [1, 0, 0, 0], 'contacts': ['lane']} for obj in spec.objects}}
    assert measure_predicate(spec, spec.rubric[0], snapshot)['satisfied'] is None
    with pytest.raises(ValueError, match='physical object'):
        grade(spec, [snapshot])


def test_native_marking_adds_no_mass_or_contact_geometry():
    from robot_worlds.runtime import Runtime
    spec = WorldSpec.model_validate(valid())
    spec.objects.append(marking())
    runtime = Runtime(spec, render=False, max_sim_seconds=1)
    try:
        geom = runtime.model.geom('geom_lane').id
        assert runtime.model.geom_contype[geom] == runtime.model.geom_conaffinity[geom] == 0
        assert runtime.model.body_mass[runtime.model.body('object_lane').id] == 0
    finally:
        runtime.close()
