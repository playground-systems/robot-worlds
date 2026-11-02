"""Task portability and reference checks without native physics or model calls."""
import copy
import shutil
import zipfile

import pytest

from robot_worlds.artifacts import read_json, write_json
from robot_worlds.generator import GenerationError
from robot_worlds.models import WorldSpec
from robot_worlds.sdk import World, WorldNotReady, load
from robot_worlds.task_catalog import TaskVariant


def scene():
    return WorldSpec.model_validate({
        "name": "Reusable room", "description": "Pure task-catalog fixture", "task": "Move the part left",
        "bounds": [10, 10, 3],
        "objects": [
            {"id": "part", "label": "Part", "kind": "box", "position": [2, 0, .1],
             "size": [.2, .2, .2], "movable": True},
            {"id": "door", "label": "Door", "kind": "box", "position": [3, 2, 1],
             "size": [.2, .2, .2], "joint": {"type": "hinge", "axis": [0, 0, 1],
             "anchor": [3, 2, 1], "range": [0, 1.2]}},
        ],
        "regions": [{"id": "left", "label": "Left destination", "position": [-2, 0, .2],
                     "size": [1, 1, .4]}],
        "rubric": [{"id": "place", "description": "Move the part left", "predicate": "in_region",
                    "object_id": "part", "target_id": "left"}],
    })


def variants():
    result = []
    for index in range(12):
        criterion = {"id": "action", "description": "Perform the requested action", "object_id": "part"}
        if index % 3 == 0:
            criterion.update(predicate="holding", mode="eventually", duration=index / 10)
        elif index % 3 == 1:
            criterion.update(predicate="in_region", target_id="left", duration=index / 10)
        else:
            criterion.update(predicate="joint_at", object_id="door", threshold=.8, duration=index / 10)
        result.append({"id": f"task{index}", "name": f"Interaction {index + 1}",
                       "task": f"Perform interaction {index + 1} for the specified duration.",
                       "rubric": [criterion]})
    return result


class FixtureRuntime:
    """Only supplies reset measurements for orchestration, never starts MuJoCo."""
    def __init__(self, spec, **kwargs):
        self.spec = spec
        self.manifest = {"model_fingerprint": "1" * 64, "implementation_fingerprint": "2" * 64}

    def snapshot(self):
        return {"time": 0, "objects": {
            obj.id: {"position": list(obj.position), "size": list(obj.size),
                     "quaternion": [1, 0, 0, 0], "held_by": None, "contacts": [],
                     "joint_position": obj.joint.initial if obj.joint else 0, "velocity": [0, 0, 0]}
            for obj in self.spec.objects
        }, "robot": {"position": [0, 0, 0]}}

    def render(self):
        return b"fake-render"

    def close(self):
        pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr("robot_worlds.sdk._robot_assets", lambda robot: {"fixture": "unchanged"})
    def no_native(*args, **kwargs):
        pytest.fail("Task catalog operations must not start native simulation")
    monkeypatch.setattr("robot_worlds.sdk._runtime", no_native)
    return World.from_spec(scene(), tmp_path / "worlds", runtime_factory=FixtureRuntime)


def test_twelve_tasks_share_geometry_and_do_not_promote_new_rubric(world):
    original = world.spec.model_dump(mode="json")
    catalog = world.with_tasks(variants())
    assert world.tasks == []
    assert len(catalog.tasks) == 12
    assert catalog.spec.model_dump(mode="json") == original
    assert catalog.manifest["validation"] == world.manifest["validation"]
    assert catalog.manifest["validation"]["status"] == "ready"
    assert (catalog.path / "preview.png").read_bytes() == (world.path / "preview.png").read_bytes()
    selected = catalog.with_task("task2")
    assert selected.id != catalog.id
    assert selected.selected_task_id == "task2"
    assert selected.manifest["physical_id"] == world.manifest["physical_id"]
    assert selected.spec.objects == world.spec.objects
    assert selected.spec.robot_start == world.spec.robot_start
    assert selected.spec.regions == world.spec.regions
    assert selected.spec.process_cycles == world.spec.process_cycles
    assert selected.spec.task == variants()[2]["task"]
    assert selected.spec.rubric[0].predicate == "joint_at"
    assert selected.manifest["validation"]["status"] == "preview"
    with pytest.raises(WorldNotReady):
        selected.evaluate()
    assert load(world.path).spec.model_dump(mode="json") == original
    assert catalog.selected_task_id is None


def test_selecting_current_instruction_preserves_validation_and_repeated_selection_is_noop(world):
    task = {"id": "current", "name": "Current task", "task": world.spec.task,
            "rubric": [criterion.model_dump(mode="json") for criterion in world.spec.rubric]}
    selected = world.with_tasks([task]).with_task("current")
    assert selected.selected_task_id == "current"
    assert selected.spec == world.spec
    assert selected.manifest["validation"] == world.manifest["validation"]
    assert selected.with_task("current").id == selected.id
    assert selected.with_tasks([task]).id == selected.id
    task["task"] = "A revised instruction needs separate validation"
    revised = selected.with_tasks([task])
    assert revised.selected_task_id is None
    assert revised.spec == world.spec


def test_catalog_and_input_values_are_not_mutable_handles_into_the_world(world):
    inputs = variants()
    catalog = world.with_tasks(inputs)
    inputs[0]["rubric"][0]["object_id"] = "missing"
    tasks = catalog.tasks
    tasks[0].task = "Changed only in the returned copy"
    tasks[0].rubric[0].object_id = "missing"
    tasks.clear()
    assert len(catalog.tasks) == 12
    assert catalog.tasks[0].rubric[0].object_id == "part"
    assert catalog.tasks[0].task == variants()[0]["task"]


@pytest.mark.parametrize("change, message", [
    ({"object_id": "missing"}, "physical object"),
    ({"predicate": "in_region", "target_id": "missing"}, "reference a region"),
    ({"predicate": "holding", "object_id": "door"}, "movable workpiece"),
    ({"predicate": "joint_at", "object_id": "part", "threshold": .5}, "articulated object"),
    ({"predicate": "joint_at", "object_id": "door", "threshold": 8}, "joint range"),
    ({"after": ["missing"]}, "distinct other criteria"),
])
def test_invalid_task_references_are_rejected_before_publication(world, change, message):
    data = variants()
    data[0]["rubric"][0].update(change)
    existing = set(world.path.parent.iterdir())
    with pytest.raises(ValueError, match=message):
        world.with_tasks(data)
    assert set(world.path.parent.iterdir()) == existing


def test_catalog_rejects_duplicates_cycles_and_geometry_changes(world):
    data = variants()
    data[1]["id"] = data[0]["id"]
    with pytest.raises(ValueError, match="Task IDs must be unique"):
        world.with_tasks(data)
    data = variants()
    first = data[0]["rubric"][0]
    first["after"] = ["later"]
    data[0]["rubric"].append({**first, "id": "later", "after": ["action"]})
    with pytest.raises(ValueError, match="ordering cycle"):
        world.with_tasks(data)
    data = variants()
    data[0]["objects"] = []
    with pytest.raises(ValueError, match="Extra inputs"):
        world.with_tasks(data)
    with pytest.raises(ValueError, match="Unknown task ID"):
        world.with_tasks(variants()).with_task("invented")
    with pytest.raises(ValueError, match="no task catalog"):
        world.with_task("task0")


def test_selected_catalog_survives_portable_export_and_revalidation(world, tmp_path):
    # Include a source file in the frozen assets to exercise portable handoff.
    notice = tmp_path / "NOTICE.md"
    notice.write_text("Fixture attribution")
    spec = world.spec
    spec.objects[0].source_notice = str(notice)
    sourced = World.from_spec(spec, world.path.parent, runtime_factory=FixtureRuntime)
    selected = sourced.with_tasks(variants()).with_task("task1")
    archive = selected.export(tmp_path / "portable.zip")
    destination = tmp_path / "portable"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)
    shutil.rmtree(world.path.parent)
    notice.unlink()
    reopened = load(destination)
    assert len(reopened.tasks) == 12
    assert reopened.selected_task_id == "task1"
    assert (reopened.path / reopened.spec.objects[0].source_notice).read_text() == "Fixture attribution"
    checked = reopened.revalidate(output=tmp_path / "checked", runtime_factory=FixtureRuntime)
    assert checked.manifest["validation"]["status"] == "ready"
    assert checked.tasks == reopened.tasks
    assert checked.selected_task_id == "task1"
    assert checked.manifest["physical_id"] == reopened.manifest["physical_id"]
    assert checked.with_task("task1").id == checked.id
    assert checked.with_task("task0").selected_task_id == "task0"


def test_task_catalog_is_integrity_bound_and_cannot_be_injected_into_legacy_world(world, tmp_path):
    catalog = world.with_tasks(variants())
    manifest_path = catalog.path / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["task_catalog"]["tasks"][0]["name"] = "Tampered catalog"
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="modified"):
        load(catalog.path)
    manifest = read_json(world.path / "manifest.json")
    manifest["task_catalog"] = {"tasks": variants(), "selected_task_id": None}
    write_json(world.path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="integrity-protected"):
        load(world.path)


def test_rubric_edit_preserves_catalog_without_claiming_named_task_still_matches(world):
    selected = world.with_tasks(variants()).with_task("task1")
    rubric = copy.deepcopy(selected.spec.rubric)
    rubric[0].duration = 4
    edited = selected.with_rubric(rubric, runtime_factory=FixtureRuntime)
    assert edited.tasks == selected.tasks
    assert edited.selected_task_id is None
    assert edited.spec.rubric[0].duration == 4
    assert edited.with_task("task1").spec.rubric[0].duration == variants()[1]["rubric"][0]["duration"]


def test_task_variant_schema_has_only_instruction_rubric_and_naming_fields():
    schema = TaskVariant.model_json_schema()
    assert set(schema["properties"]) == {"id", "name", "task", "rubric"}
    assert schema["additionalProperties"] is False


class PatchClient:
    def __init__(self, operations):
        self.operations = operations

    def complete_json(self, *args):
        return {"operations": self.operations}


@pytest.mark.parametrize("kind", ["refine", "repair"])
def test_scene_edits_preserve_valid_catalog_and_selection(world, kind):
    selected = world.with_tasks(variants()).with_task("task2")
    patch = [{"op": "update_object", "id": "part", "fields": ["position"],
              "values": {"position": [2.5, 0, .1]}}]
    edited = getattr(selected, kind)("Move the part slightly", scope=["part"],
                                    client=PatchClient(patch), max_repairs=0,
                                    runtime_factory=FixtureRuntime)
    assert edited.tasks == selected.tasks
    assert edited.selected_task_id == "task2"
    assert edited.spec.objects[0].position == (2.5, 0, .1)
    assert edited.manifest["validation"]["status"] == "ready"
    assert load(selected.path).spec.objects[0].position == (2, 0, .1)


@pytest.mark.parametrize("operation, message", [
    ({"op": "remove_object", "id": "door"}, "physical object"),
    ({"op": "update_object", "id": "door", "fields": ["joint"],
      "values": {"joint": None}}, "articulated object"),
])
def test_scene_edits_reject_invalid_other_task_before_native_validation(world, operation, message):
    # The active rubric does not use the door; another catalog entry still does.
    selected = world.with_tasks(variants()).with_task("task1")
    def forbidden_runtime(*args, **kwargs):
        pytest.fail("Invalid catalog must be rejected before native validation")
    with pytest.raises(GenerationError) as error:
        selected.refine("Remove the door mechanism", client=PatchClient([operation]), max_repairs=0,
                        allow_physics_changes=True, runtime_factory=forbidden_runtime)
    errors = str(error.value.errors)
    assert "Scene edit invalidates task catalog: Task task2:" in errors
    assert message in errors
    assert len(load(selected.path).tasks) == 12


def test_explicit_instruction_edit_preserves_catalog_and_clears_named_selection(world):
    selected = world.with_tasks(variants()).with_task("task1")
    patch = [{"op": "set_task", "task": "An explicitly revised instruction"}]
    edited = selected.refine("Revise the instruction", client=PatchClient(patch), max_repairs=0,
                             allow_task_changes=True, runtime_factory=FixtureRuntime)
    assert edited.tasks == selected.tasks
    assert edited.selected_task_id is None
    assert edited.spec.task == "An explicitly revised instruction"


def test_task_models_are_public_sdk_exports():
    from robot_worlds import TaskCatalog
    from robot_worlds import TaskVariant as PublicTaskVariant
    assert PublicTaskVariant is TaskVariant
    assert TaskCatalog(tasks=variants()).tasks[0].id == "task0"
