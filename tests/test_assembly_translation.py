"""Assembly pose edits must preserve relative geometry and mechanism mounts."""
import copy

import numpy as np
import pytest
from test_generator import valid
from test_prepared_asset_editing import asset_patch

from robot_worlds.editing import apply_patch, enforce_protections, scene_diff
from robot_worlds.models import WorldSpec


@pytest.fixture
def scene():
    return apply_patch(WorldSpec.model_validate(valid()), asset_patch())


def translate(root, offset):
    return {"operations": [{"op": "translate_assembly", "id": root, "offset": offset}]}


def test_mechanism_guard_rejects_moving_a_body_without_its_joint_anchor(scene):
    door = next(o for o in scene.objects if o.id == "oven_part9")
    patch = {"operations": [{"op": "update_object", "id": door.id, "fields": ["position"],
                              "values": {"position": [door.position[0]+.2, *door.position[1:]]}}]}
    with pytest.raises(ValueError, match="joint.*protected"):
        apply_patch(scene, patch)


def test_mechanism_guard_accepts_coherent_world_translation(scene):
    moved = copy.deepcopy(scene)
    for obj in moved.objects:
        if obj.id.startswith("oven_"):
            obj.position = np.array(obj.position)+[.3, .1, 0]
            if obj.joint:
                obj.joint.anchor = np.array(obj.joint.anchor)+[.3, .1, 0]
    for region in moved.regions:
        if region.interior_of == "oven_part0":
            region.position = np.array(region.position)+[.3, .1, 0]
    enforce_protections(scene, moved)


@pytest.mark.parametrize("root,prefix,count", [("oven_part0", "oven_", 21), ("bowl_body", "bowl_", 159)])
def test_one_translation_moves_all_components_and_owned_regions(scene, root, prefix, count):
    old = scene.model_dump_json()
    offset = [.35, .12, .02]
    moved = apply_patch(scene, translate(root, offset))
    assert scene.model_dump_json() == old
    for before, after in zip(scene.objects, moved.objects):
        if before.id.startswith(prefix):
            assert np.array(after.position)-before.position == pytest.approx(offset)
            assert after.model_dump(exclude={"position", "joint"}) == before.model_dump(exclude={"position", "joint"})
            if before.joint:
                assert np.array(after.joint.anchor)-before.joint.anchor == pytest.approx(offset)
                assert after.joint.model_dump(exclude={"anchor"}) == before.joint.model_dump(exclude={"anchor"})
        else:
            assert after == before
    for before, after in zip(scene.regions, moved.regions):
        if before.interior_of and before.interior_of.startswith(prefix):
            assert np.array(after.position)-before.position == pytest.approx(offset)
        else:
            assert after == before  # Unowned destination regions do not follow.
    assert scene_diff(scene, moved)[1]["changed"] == count


def test_translation_scope_covers_children_and_owned_cavity(scene):
    ids = [o.id for o in scene.objects if o.id.startswith("oven_")]
    patch = translate("oven_part0", [.2, 0, 0])
    for scope in (["oven_part0"], ids):
        with pytest.raises(ValueError, match="exceeds explicit scope"):
            apply_patch(scene, patch, scope=scope)
    ids.append("oven_interior0")
    assert apply_patch(scene, patch, scope=ids)


def test_task_bound_appliance_cannot_move_with_its_destination_region(scene):
    before = scene.model_copy(deep=True)
    before.rubric[0].target_id = "oven_interior0"
    patch = translate("oven_part0", [.5, 0, 0])
    with pytest.raises(ValueError, match="task-bound region"):
        apply_patch(before, patch)
    assert apply_patch(before, patch, allow_task_changes=True)


def test_movable_task_subject_can_be_repositioned_without_deforming_its_rim(scene):
    scene.rubric[0].object_id = "bowl_body"
    moved = apply_patch(scene, translate("bowl_body", [.2, 0, 0]))
    assert scene_diff(scene, moved)[1]["changed"] == 159
    assert moved.rubric == scene.rubric


def test_moving_jointed_child_alone_changes_its_mount_and_needs_physics_permission(scene):
    patch = translate("oven_part9", [.2, 0, 0])
    with pytest.raises(ValueError, match="joint mechanism/mount.*protected"):
        apply_patch(scene, patch)
    assert apply_patch(scene, patch, allow_physics_changes=True)


def test_moving_parent_without_children_changes_joint_mount_and_is_rejected(scene):
    root = next(o for o in scene.objects if o.id == "oven_part0")
    patch = {"operations": [{"op": "update_object", "id": root.id, "fields": ["position"],
                              "values": {"position": [root.position[0]+.2, *root.position[1:]]}}]}
    with pytest.raises(ValueError, match="joint mechanism/mount.*protected"):
        apply_patch(scene, patch)


@pytest.mark.parametrize("root", ["missing", "bowl_visual"])
def test_unknown_or_appearance_only_root_rejected(scene, root):
    with pytest.raises(ValueError, match="unknown|physical assembly root"):
        apply_patch(scene, translate(root, [.2, 0, 0]))


def test_zero_move_cannot_claim_a_changed_world(scene):
    with pytest.raises(ValueError, match="no materialized scene changes"):
        apply_patch(scene, translate("oven_part0", [0, 0, 0]))


def test_joint_parameter_edits_stay_protected_after_translation(scene):
    moved = apply_patch(scene, translate("oven_part0", [.2, 0, 0]))
    next(o for o in moved.objects if o.id == "oven_part9").joint.damping += .1
    with pytest.raises(ValueError, match="joint mechanism/mount.*protected"):
        enforce_protections(scene, moved)
