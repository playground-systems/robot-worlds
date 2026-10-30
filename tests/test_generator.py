import copy

import pytest
from pydantic import ValidationError

from robot_worlds.generator import GenerationError, generate_spec, validate_spec
from robot_worlds.models import WorldSpec


def valid():
    return {"name": "Workshop", "description": "Two stations", "task": "Transfer part", "bounds": [8, 8, 3],
            "objects": [{"id": "part", "label": "part", "kind": "box", "position": [2, 0, .1], "size": [.2, .2, .2], "movable": True}],
            "regions": [{"id": "destination", "label": "destination", "position": [-2, 0, .2], "size": [1, 1, .4]}],
            "rubric": [{"id": "transfer", "description": "Transferred", "predicate": "in_region", "object_id": "part", "target_id": "destination"}]}


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []

    def complete_json(self, instructions, prompt, schema):
        self.prompts.append(prompt)
        return copy.deepcopy(next(self.responses))


def test_repairs_preserve_original_request_and_emit_preview_before_validation():
    bad = valid()
    bad["objects"][0]["position"] = [20, 0, .1]
    client = Client([bad, valid()])
    events = []
    result = generate_spec("Detailed workshop with exact part identity", client=client, event_callback=events.append)
    assert len(client.prompts) == 2
    assert "Detailed workshop with exact part identity" in client.prompts[1]
    assert "outside world bounds" in client.prompts[1]
    assert result.source_prompt == "Detailed workshop with exact part identity"
    assert result.assumptions
    assert [e["type"] for e in events if e["type"] in {"candidate", "validation"}] == ["candidate", "validation", "candidate", "validation"]


def test_exhaustion_retains_latest_preview_and_never_returns_fallback():
    bad = valid()
    bad["objects"][0]["position"] = [20, 0, .1]
    with pytest.raises(GenerationError) as caught:
        generate_spec("impossible original", client=Client([bad, bad]), max_repairs=1)
    error = caught.value
    assert error.attempts == 2
    assert error.candidate.objects[0].position[0] == 20
    assert len(error.history) == 2
    assert error.errors


def test_runtime_validation_participates_in_repair():
    calls = []
    def validator(spec):
        calls.append(spec)
        return ["unstable reset contact"] if len(calls) == 1 else []
    client = Client([valid(), valid()])
    generate_spec("a scene", client=client, validator=validator)
    assert "unstable reset contact" in client.prompts[1]


def test_schema_invalid_then_valid_and_no_silent_unsupported_predicate():
    bad = valid()
    bad["rubric"][0]["predicate"] = "visually_clean"
    client = Client([bad, valid()])
    generate_spec("a scene", client=client)
    assert "predicate" in client.prompts[1]


def test_bounds_overlap_identity_and_robot_clearance():
    data = valid()
    assert validate_spec(WorldSpec.model_validate(data)) == []
    data["objects"].append({**data["objects"][0], "id": "other"})
    assert any("overlap" in e for e in validate_spec(WorldSpec.model_validate(data)))
    data["objects"][1]["id"] = "part"
    assert any("unique" in e for e in validate_spec(WorldSpec.model_validate(data)))
    data = valid()
    data["objects"][0]["position"] = [0, 0, .1]
    assert any("robot clearance" in e for e in validate_spec(WorldSpec.model_validate(data)))


def test_noop_achievement_rejected_but_maintenance_allowed():
    data = valid()
    data["objects"][0]["position"] = [-2, 0, .1]
    assert any("no-op" in e for e in validate_spec(WorldSpec.model_validate(data)))
    data["rubric"][0]["intent"] = "maintain"
    assert validate_spec(WorldSpec.model_validate(data)) == []


def test_parent_cycles_and_unbound_process_loading_rejected():
    data = valid()
    data["objects"][0]["movable"] = False
    data["objects"][0]["parent_id"] = "part"
    assert any("parent cycle" in e for e in validate_spec(WorldSpec.model_validate(data)))
    data = valid()
    data["process_cycles"] = [{"id": "cycle", "label": "machine", "fixture_id": "part", "workpiece_ids": ["part"], "duration": 2, "output_state": "done", "prerequisites": [{"predicate": "near", "object_id": "part", "target_id": "absent", "threshold": 1}]}]
    assert any("loading" in e for e in validate_spec(WorldSpec.model_validate(data)))


def test_process_loading_volume_at_support_plane_requires_contact_clearance():
    data = valid()
    data["objects"].append({"id": "platen", "label": "platen", "kind": "box",
                            "position": [-2, 0, .55], "size": [1, 1, .08]})
    data["regions"][0].update(position=[-2, 0, .79], size=[1, 1, .4])
    data["process_cycles"] = [{"id": "inspect", "label": "inspection", "fixture_id": "platen",
                              "workpiece_ids": ["part"], "duration": 3, "output_state": "inspected",
                              "prerequisites": [
                                  {"predicate": "on_top", "object_id": "part", "target_id": "platen"},
                                  {"predicate": "in_region", "object_id": "part", "target_id": "destination", "tolerance": 0},
                                  {"predicate": "released", "object_id": "part"}]}]
    original = copy.deepcopy(data)
    errors = validate_spec(WorldSpec.model_validate(data))
    assert any("contact clearance" in error for error in errors)
    assert data == original  # Validation diagnoses; it never widens a user's rubric.
    # Extend the region below the support, preserving its upper bound and XY area.
    data["regions"][0]["position"][2] -= .0025
    data["regions"][0]["size"][2] += .005
    assert validate_spec(WorldSpec.model_validate(data)) == []
    client = Client([original, data])
    generate_spec("Inspect the same part after released physical loading", client=client)
    assert "contact clearance" in client.prompts[1]


def test_explicit_containment_tolerance_allows_process_contact_clearance():
    data = valid()
    data["objects"].append({"id": "platen", "label": "platen", "kind": "box",
                            "position": [-2, 0, .55], "size": [1, 1, .08]})
    data["regions"][0].update(position=[-2, 0, .79], size=[1, 1, .4])
    data["process_cycles"] = [{"id": "inspect", "label": "inspection", "fixture_id": "platen",
                              "workpiece_ids": ["part"], "duration": 3, "output_state": "inspected",
                              "prerequisites": [
                                  {"predicate": "on_top", "object_id": "part", "target_id": "platen"},
                                  {"predicate": "in_region", "object_id": "part", "target_id": "destination", "tolerance": .001}]}]
    assert validate_spec(WorldSpec.model_validate(data)) == []


def test_mesh_must_exist_and_nonfinite_properties_rejected(tmp_path):
    data = valid()
    data["objects"][0].update(kind="mesh", mesh_path="missing.obj")
    assert any("mesh asset" in e for e in validate_spec(WorldSpec.model_validate(data), asset_root=tmp_path))
    data = valid()
    data["objects"][0]["mass"] = float("inf")
    with pytest.raises(ValidationError):
        WorldSpec.model_validate(data)


def test_cancellation_before_request_does_not_spend_or_invent_candidate():
    from robot_worlds.generator import GenerationCancelled
    client = Client([])
    with pytest.raises(GenerationCancelled) as caught:
        generate_spec("mission", client=client, cancelled=lambda: True)
    assert caught.value.candidate is None
    assert caught.value.attempts == 0
    assert client.prompts == []


def test_cancellation_after_preview_preserves_candidate_before_physics():
    from robot_worlds.generator import GenerationCancelled
    previews, physics_calls = [], []
    with pytest.raises(GenerationCancelled) as caught:
        generate_spec("mission", client=Client([valid()]), on_candidate=previews.append,
                      cancelled=lambda: bool(previews), validator=lambda spec: physics_calls.append(spec) or [])
    assert caught.value.candidate is previews[0]
    assert not physics_calls


@pytest.mark.parametrize("profile,options,expected", [
    ("standard", None, {"timeout": 120.0}),
    ("large", None, {"timeout": 480.0, "max_output_tokens": 16384}),
    ("large", {"timeout": 35, "max_output_tokens": 2048}, {"timeout": 35, "max_output_tokens": 2048}),
])
def test_generation_profile_defaults_preserve_explicit_client_budgets(monkeypatch, profile, options, expected):
    captured = {}
    def factory(**kwargs):
        captured.update(kwargs)
        return Client([valid()])
    monkeypatch.setattr("robot_worlds.llm.ResponsesClient", factory)
    generate_spec("mission", scene_profile=profile, client_options=options)
    assert {key: captured[key] for key in expected} == expected
