"""Compact source repetition through the real planner/edit expansion path."""
import copy

import numpy as np
import pytest
from test_asset_library import fixture_library
from test_generator import Client, valid

from robot_worlds import World, load
from robot_worlds import prepared_assets as prepared
from robot_worlds.editing import apply_patch
from robot_worlds.generator import generate_spec, validate_spec
from robot_worlds.models import WorldSpec


def reference(**changes):
    return prepared.PreparedAssetInstance(id='row', asset_id='inspection-station', position=(-2,-2,0),
        **{'repeat': (3,2,1), 'spacing': (1.2,1,0), 'quarter_turns': 1,
           'joint_initials': [dict(name='door_hinge', initial=.4)], **changes})


def test_group_imports_once_with_independent_parent_joint_and_pose_state(tmp_path, monkeypatch):
    library = fixture_library(tmp_path)
    original = library.load('inspection-station', prefix='original').asset
    calls = []
    loader = prepared.load_prepared_asset
    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return loader(*args, **kwargs)
    monkeypatch.setattr(prepared, 'load_prepared_asset', counted)
    payload = valid()
    events = []
    prepared.append_prepared_asset(payload, reference(), max_objects=100, asset_library=library, event_callback=events.append)
    spec = WorldSpec.model_validate(payload)
    assert validate_spec(spec) == []
    assert len(calls) == 1
    assert len(spec.objects) == 1+6*len(original.objects)
    for i in range(3):
        for j in range(2):
            prefix = f'row_{i}_{j}_0_'
            parts = [o for o in spec.objects if o.id.startswith(prefix)]
            assert len(parts) == len(original.objects)
            assert all(not o.parent_id or o.parent_id.startswith(prefix) for o in parts)
            hinge = next(o for o in parts if o.joint)
            assert hinge.joint.initial == .4
            assert sum(o.mass for o in parts if o.collision) == pytest.approx(sum(o.mass for o in original.objects if o.collision))
            low = np.min([np.array(o.position)-np.array(o.size)/2 for o in parts], axis=0)
            high = np.max([np.array(o.position)+np.array(o.size)/2 for o in parts], axis=0)
            assert [(low[0]+high[0])/2,(low[1]+high[1])/2,low[2]] == pytest.approx([-2+i*1.2,-2+j,0])
    joints = [o.joint for o in spec.objects if o.joint]
    joints[0].initial = .2
    assert all(j.initial == .4 for j in joints[1:])
    assert events[-1]['copies'] == 6 and events[-1]['components'] == 24
    assert len([a for a in spec.assumptions if a.startswith('Prepared asset row')]) == 1


def test_mocked_generation_and_typed_edit_use_exact_repeated_ids(tmp_path, monkeypatch):
    monkeypatch.setattr('robot_worlds.sdk._robot_assets', lambda _: {'fixture': 'no native robot loading'})
    library = fixture_library(tmp_path)
    raw = valid()
    raw['prepared_assets'] = [reference().model_dump(mode='json')]
    raw['rubric'] = [dict(id='open_last',description='Open final station',predicate='joint_at',
                         object_id='row_2_1_0_part2',threshold=.5)]
    before = copy.deepcopy(raw)
    spec = generate_spec('Three columns of two inspection stations; open the last door', client=Client([raw]), asset_library=library)
    assert raw == before
    assert spec.rubric[0].object_id == 'row_2_1_0_part2'
    assert validate_spec(spec) == []
    world = World.from_spec(spec, tmp_path/'worlds', validate=False)
    world.export(tmp_path/'world.zip')
    assert load(world.path).spec == world.spec
    parent = WorldSpec.model_validate(valid())
    patch = {'operations': [dict(op='add_prepared_asset',asset=reference().model_dump(mode='json'))]}
    with pytest.raises(ValueError, match='scope'):
        apply_patch(parent, patch, scope=['row'], asset_library=library)
    edited = apply_patch(parent, patch, asset_library=library)
    assert len(edited.objects) == 25
    assert edited.rubric == parent.rubric and len(parent.objects) == 1


def test_repeated_microwave_cavities_belong_to_their_own_copy():
    payload = valid()
    request = prepared.PreparedAssetInstance(id='ovens',asset_id='nvidia-microwave079',position=(-2,-2,0),
        repeat=(3,1,1), spacing=(2,0,0), quarter_turns=3)
    prepared.append_prepared_asset(payload, request, max_objects=100)
    spec = WorldSpec.model_validate(payload)
    assert validate_spec(spec) == []
    assert len(spec.objects) == 61
    for i in range(3):
        region = next(r for r in spec.regions if r.id == f'ovens_{i}_0_0_interior0')
        assert region.interior_of.startswith(f'ovens_{i}_0_0_')
    assert len({o.id for o in spec.objects}) == len(spec.objects)


@pytest.mark.parametrize('failure', ['collision','too_long','component_budget'])
def test_failed_group_leaves_payload_unchanged(tmp_path, failure):
    library = fixture_library(tmp_path)
    payload = valid()
    maximum = 100
    request = reference()
    if failure == 'collision':
        payload['objects'][0]['id'] = 'row_2_1_0_part0'  # Last copy collides after earlier copies were staged.
    elif failure == 'too_long':
        request.id = 'a'*75
    else:
        maximum = 10  # Six copies fit, but their 24 components do not.
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError):
        prepared.append_prepared_asset(payload, request, max_objects=maximum, asset_library=library)
    assert payload == before


@pytest.mark.parametrize('changes', [dict(repeat=(1000,1000,1000)), dict(spacing=(0,1,0))])
def test_resource_and_spacing_errors_fail_before_source_access(monkeypatch, changes):
    monkeypatch.setattr(prepared, 'load_prepared_asset', lambda *a, **k: pytest.fail('should not load source'))
    with pytest.raises(ValueError, match='budget|spacing'):
        prepared.append_prepared_asset(valid(), reference(**changes), max_objects=100)


def test_cavity_repetition_has_its_own_budget_before_copying(monkeypatch):
    monkeypatch.setattr(prepared, 'MAX_EXPANDED_REGIONS', 5)
    monkeypatch.setattr(prepared, '_copy_with_prefix', lambda *a: pytest.fail('must check region budget before copying'))
    payload = valid()
    before = copy.deepcopy(payload)
    request = prepared.PreparedAssetInstance(id='ovens',asset_id='nvidia-microwave079',position=(-2,-2,0),
        repeat=(6,1,1), spacing=(2,0,0))
    with pytest.raises(ValueError, match='region resource budget'):
        prepared.append_prepared_asset(payload, request, max_objects=500)
    assert payload == before


def test_single_copy_keeps_existing_catalog_ids_and_negative_spacing_is_world_space(tmp_path):
    library = fixture_library(tmp_path)
    single = valid()
    prepared.append_prepared_asset(single, reference(repeat=(1,1,1)), max_objects=100, asset_library=library)
    assert any(o['id'] == 'row_part0' for o in single['objects'])
    assert not any(o['id'].startswith('row_0_0_0') for o in single['objects'])
    negative = valid()
    prepared.append_prepared_asset(negative, reference(repeat=(1,2,1),spacing=(0,-1,0)), max_objects=100, asset_library=library)
    a = next(o for o in negative['objects'] if o['id']=='row_0_0_0_part0')
    b = next(o for o in negative['objects'] if o['id']=='row_0_1_0_part0')
    assert np.array(b['position'])-a['position'] == pytest.approx([0,-1,0])
