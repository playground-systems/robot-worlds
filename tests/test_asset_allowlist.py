"""Closed source catalogs and combined authoring contracts, without native/model calls."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_asset_library import fixture_import
from test_generator import Client, valid

from robot_worlds.artifacts import read_json
from robot_worlds.asset_library import AssetLibrary
from robot_worlds.generator import GenerationError, generate_spec
from robot_worlds.llm import _validate, strict_schema
from robot_worlds.models import WorldSpec
from robot_worlds.prepared_assets import GenerationSpec
from robot_worlds.sdk import generate

ALLOWED = "simready-fixture"
EXCLUDED = "nonsimready-fixture"


@pytest.fixture
def library(tmp_path):
    # Synthetic existing import; the test asserts catalog identity enforcement,
    # never claims this tiny fixture is a real downloaded NVIDIA asset.
    sourced = fixture_import(tmp_path)
    return (AssetLibrary().add(ALLOWED, sourced, description="Explicit test-only allowed source")
            .add(EXCLUDED, sourced, description="Explicit test-only excluded source"))


def sourced_plan():
    plan = valid()
    plan["objects"] = []
    plan["prepared_assets"] = [{"id": "station", "asset_id": ALLOWED, "position": [-2, -2, 0]}]
    plan["rubric"] = [{"id": "open", "description": "Open the panel", "predicate": "joint_at",
                       "object_id": "station_part2", "threshold": .8}]
    return plan


def shown_catalog(prompt):
    return json.loads(prompt.split("Prepared source asset catalog:\n", 1)[1].split("\nResource profile:", 1)[0])


def test_allowlist_filters_model_catalog_and_keeps_source_articulation(library):
    client = Client([sourced_plan()])
    spec = generate_spec("Use only the installed source", client=client, asset_library=library,
                         allowed_asset_ids=[ALLOWED], online_assets=False)
    assert [entry["asset_id"] for entry in shown_catalog(client.prompts[0])] == [ALLOWED]
    assert len(spec.objects) == 4
    panel = next(obj for obj in spec.objects if obj.id == "station_part2")
    assert panel.joint.range == (0, 1.5)
    assert panel.joint.initial == 0


@pytest.mark.parametrize("excluded", ["google-porcelain-bowl", "nvidia-microwave079", EXCLUDED])
def test_excluded_builtin_or_registered_asset_cannot_be_injected(library, monkeypatch, excluded):
    plan = sourced_plan()
    plan["prepared_assets"].append({"id": "injected", "asset_id": excluded, "position": [2, 2, 0]})
    materialize = Mock(side_effect=AssertionError("Excluded sources must not be materialized"))
    monkeypatch.setattr("robot_worlds.prepared_assets.materialize_prepared_assets", materialize)
    validator = Mock(return_value=[])
    with pytest.raises(GenerationError) as error:
        generate_spec("Use only the installed source", client=Client([plan]), asset_library=library,
                      allowed_asset_ids=[ALLOWED], online_assets=False, max_repairs=0, validator=validator)
    assert any("outside the required source allowlist: " + excluded in str(item) for item in error.value.errors)
    materialize.assert_not_called()
    validator.assert_not_called()


def test_excluded_source_repair_stays_inside_same_catalog(library):
    forbidden = sourced_plan()
    forbidden["prepared_assets"][0]["asset_id"] = "google-porcelain-bowl"
    client = Client([forbidden, sourced_plan()])
    spec = generate_spec("Keep source-only policy during repair", client=client, asset_library=library,
                         allowed_asset_ids=[ALLOWED], online_assets=False, max_repairs=1)
    assert len(client.prompts) == 2
    assert "outside the required source allowlist" in client.prompts[1]
    assert all([entry["asset_id"] for entry in shown_catalog(prompt)] == [ALLOWED] for prompt in client.prompts)
    assert all(obj.id.startswith("station_") for obj in spec.objects)


@pytest.mark.parametrize("entrypoint", [generate_spec, generate])
def test_missing_allowed_asset_stops_before_model_or_native_call(library, tmp_path, entrypoint):
    client = Mock(budget={})
    options = {"client": client, "asset_library": library,
               "allowed_asset_ids": [ALLOWED, "missing-source"], "online_assets": False}
    if entrypoint is generate:
        options.update(output=tmp_path / "worlds", runtime_factory=Mock(side_effect=AssertionError("No native call")))
    with pytest.raises(ValueError, match="Allowed asset IDs are not installed: missing-source"):
        entrypoint("Require the exact catalog", **options)
    client.complete_json.assert_not_called()
    if entrypoint is generate:
        options["runtime_factory"].assert_not_called()
        records = list((tmp_path / "worlds").glob("generation-*.json"))
        assert len(records) == 1 and read_json(records[0])["status"] == "failed"


@pytest.mark.parametrize("allowed", [[], [ALLOWED, ALLOWED], "simready-fixture", [None]])
def test_invalid_allowlist_never_calls_model(allowed):
    client = Mock()
    with pytest.raises(ValueError, match="nonempty list of distinct asset IDs"):
        generate_spec("Require sources", client=client, allowed_asset_ids=allowed, online_assets=False)
    client.complete_json.assert_not_called()


def test_allowlist_cannot_enable_online_substitution():
    client = Mock()
    with pytest.raises(ValueError, match="requires online_assets=False"):
        generate_spec("Require sources", client=client, allowed_asset_ids=[ALLOWED])
    client.complete_json.assert_not_called()


@pytest.mark.parametrize("injected", [
    {"id": "invented_part", "label": "Invented prop", "kind": "box", "position": [2, 0, .1],
     "size": [.2, .2, .2], "movable": True},
    {"id": "invented_furniture", "label": "Invented table", "kind": "box", "position": [2, 0, .4],
     "size": [1, 1, .8]},
    {"id": "wall_mesh", "label": "Wall", "kind": "mesh", "position": [2, 0, 1],
     "size": [.1, 1, 2], "mesh_path": "/untrusted/nonsimready.obj"},
    {"id": "wall", "label": "Wall", "kind": "box", "position": [2, 0, 1],
     "size": [.1, 1, 2], "normal_texture_path": "/untrusted/nonsimready.png"},
])
@pytest.mark.parametrize("placement", ["object", "assembly"])
def test_direct_geometry_cannot_bypass_source_policy(library, injected, placement):
    plan = sourced_plan()
    if placement == "object":
        plan["objects"] = [injected]
    else:
        plan["assembly_templates"] = [{"id": "injected", "label": "Untrusted assembly", "parts": [injected]}]
        plan["assembly_instances"] = [{"id": "copy", "template_id": "injected", "position": [0, 0, 0]}]
    validator = Mock(return_value=[])
    with pytest.raises(GenerationError) as error:
        generate_spec("Do not replace source assets", client=Client([plan]), asset_library=library,
                      allowed_asset_ids=[ALLOWED], online_assets=False, max_repairs=0, validator=validator)
    diagnostic = ("texture_path and material maps require mesh geometry" if "normal_texture_path" in injected
                  else "Source-only generation permits only fixed architectural/paint boxes")
    assert any(diagnostic in str(item) for item in error.value.errors)
    validator.assert_not_called()


def test_source_only_policy_retains_architecture_and_floor_paint(library):
    plan = sourced_plan()
    plan["objects"] = [
        {"id": "east_wall", "label": "East wall", "kind": "box", "position": [3.9, 0, 1.25],
         "size": [.1, 4, 2.5]},
        {"id": "floor_marking", "label": "Floor paint", "kind": "box", "position": [1, 0, .005],
         "size": [2, .05, .01], "collision": False},
    ]
    spec = generate_spec("Source props and ordinary architecture", client=Client([plan]), asset_library=library,
                         allowed_asset_ids=[ALLOWED], online_assets=False)
    assert {obj.id for obj in spec.objects} >= {"east_wall", "floor_marking", "station_part2"}


def test_source_only_policy_disallows_creation_authorizations(library):
    plan = sourced_plan()
    plan["created_assets"] = [{"asset_id": "invented", "object_ids": ["floor_marking"]}]
    with pytest.raises(GenerationError) as error:
        generate_spec("Only existing sources", client=Client([plan]), asset_library=library,
                      allowed_asset_ids=[ALLOWED], online_assets=False, max_repairs=0)
    assert any("does not permit generated object assets" in str(item) for item in error.value.errors)


def authoring_module():
    path = Path(__file__).parents[1] / "tools/acceptance/create_manipulation_presets.py"
    spec = importlib.util.spec_from_file_location("fixture_manipulation_author", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task_variants():
    return [{"id": f"task{i}", "name": f"Task {i}", "task": f"Perform task {i}",
             "rubric": copy.deepcopy(valid()["rubric"])} for i in range(12)]


def test_combined_author_schema_resolves_refs_enforces_twelve_tasks_and_persists_raw_response(tmp_path, monkeypatch):
    module = authoring_module()
    response = {"world": sourced_plan(), "tasks": task_variants(), "semantic_plan": "Preserve supports and accessible joints"}

    class Transport:
        def __init__(self):
            self.usage = {"calls": 0}
            self.budget = {"max_calls": 4}
            self.schemas, self.prompts = [], []

        def complete_json(self, instructions, prompt, schema):
            self.schemas.append(schema)
            self.prompts.append(prompt)
            _validate(response, schema, schema)
            self.usage["calls"] += 1
            return copy.deepcopy(response)

    transport = Transport()
    factory = Mock(return_value=transport)
    monkeypatch.setattr(module, "CodexClient", factory)
    author = module.SceneAuthor(tmp_path)
    assert author.complete_json("Author the scene", "Original request", GenerationSpec.model_json_schema()) == response["world"]
    combined = transport.schemas[0]
    assert combined["properties"]["tasks"]["minItems"] == 12
    assert combined["properties"]["tasks"]["maxItems"] == 32
    assert combined["properties"]["world"]["properties"]["prepared_assets"]
    assert strict_schema(combined)["additionalProperties"] is False
    too_short = {**response, "tasks": response["tasks"][:11]}
    with pytest.raises(ValueError, match="tasks has an invalid length"):
        _validate(too_short, combined, combined)
    assert read_json(tmp_path / "astra-response-1.json") == response
    assert author.tasks == response["tasks"] and author.semantic_plan == response["semantic_plan"]
    author.complete_json("Repair", "Physical failures", GenerationSpec.model_json_schema())
    assert "Previous task variants (repair their errors too):" in transport.prompts[1]
    assert read_json(tmp_path / "astra-response-2.json") == response
    assert factory.call_args.kwargs["max_calls"] == 4
    assert factory.call_args.kwargs["timeout"] <= factory.call_args.kwargs["max_seconds"]


def test_authoring_flow_does_not_accept_a_false_check_without_error_details(tmp_path, monkeypatch):
    module = authoring_module()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("A sourced physical room")
    output = tmp_path / "creation"
    author = SimpleNamespace(tasks=task_variants(), semantic_plan="Fixture only", usage={}, budget={})
    monkeypatch.setattr(module, "SceneAuthor", lambda _: author)
    library = SimpleNamespace(asset_ids=(ALLOWED,), load=lambda *a, **k: SimpleNamespace(selected=SimpleNamespace(id="simready:fixture")))
    monkeypatch.setattr(module.AssetLibrary, "open", lambda _: library)
    failed = SimpleNamespace(id="f" * 64, path=tmp_path / "failed-world",
                             manifest={"validation": {"status": "unvalidated", "checks": [{"check": "render", "passed": False}]}})
    monkeypatch.setattr(module, "World", SimpleNamespace(from_spec=lambda *a, **k: failed))

    def generate_without_native(*args, **kwargs):
        errors = kwargs["validator"](WorldSpec.model_validate(valid()))
        if errors:
            raise GenerationError("Rejected failed native check", errors=errors)
        pytest.fail("A false render check was accepted as valid")

    monkeypatch.setattr(module, "generate_spec", generate_without_native)
    monkeypatch.setattr("sys.argv", ["create_manipulation_presets.py", "--prompt", str(prompt),
                                    "--library", str(tmp_path / "library"), "--output", str(output)])
    with pytest.raises(GenerationError):
        module.main()
    assert read_json(output / "creation.json")["status"] == "incomplete"
