import copy
import json

import numpy as np
import pytest

from robot_worlds import World, load_prepared_asset, prepared_asset_catalog
from robot_worlds.generator import GenerationError, generate_spec, validate_spec
from robot_worlds.prepared_assets import GenerationSpec, materialize_prepared_assets


def prepared_plan():
    return dict(name="Sourced appliance task", description="Original assets in a generated layout",
                task="Put the white porcelain bowl in the microwave and leave it resting there.", bounds=[8, 6, 3],
                objects=[dict(id="counter", label="Bowl counter", kind="box", position=[1, 0, .3], size=[.6, .5, .6]),
                         dict(id="oven_counter", label="Microwave counter", kind="box", position=[2, 0, .32715], size=[.85, .7, .6543])],
                prepared_assets=[dict(id="bowl", asset_id="google-porcelain-bowl", mass=.6, position=[1, 0, .601]),
                                 dict(id="oven", asset_id="nvidia-microwave079", position=[2, 0, .6543])],
                rubric=[dict(id="inside", description="Full bowl inside", predicate="in_region", object_id="bowl_body",
                             target_id="oven_interior0", tolerance=.002, duration=.2),
                        dict(id="supported", description="Bowl on tray", predicate="on_top", object_id="bowl_body",
                             target_id="oven_part0", tolerance=.005, duration=.2),
                        dict(id="released", description="Bowl released", predicate="released", object_id="bowl_body", intent="maintain")])


class Client:
    def __init__(self, *responses):
        self.responses, self.requests = iter(responses), []

    def complete_json(self, instructions, prompt, schema):
        self.requests.append((instructions, prompt, schema))
        return copy.deepcopy(next(self.responses))


def test_generator_materializes_two_references_with_original_geometry_without_network(monkeypatch):
    monkeypatch.setattr("robot_worlds.asset_catalog.search_online_assets", lambda *_: pytest.fail("Prepared source needs no fresh lookup"))
    client = Client(prepared_plan())
    events = []
    spec = generate_spec("Original porcelain bowl and microwave", client=client, event_callback=events.append)
    assert len(spec.objects) == 181
    assert validate_spec(spec) == []
    assert "prepared_assets" not in spec.model_dump()
    assert any(o.texture_path for o in spec.objects)
    assert any(o.joint and o.joint.limited is False for o in spec.objects)
    assert len([e for e in events if e["type"] == "asset_loaded"]) == 2
    assert "google-porcelain-bowl" in client.requests[0][1]
    assert "prepared_assets" in client.requests[0][2]["properties"]
    assert len(json.dumps(prepared_plan())) < len(spec.model_dump_json())/10


def test_reference_failure_repairs_original_compact_plan_without_procedural_substitution():
    bad = prepared_plan()
    bad["prepared_assets"][0]["mass"] = None
    client = Client(bad, prepared_plan())
    result = generate_spec("Preserve both original source assets", client=client)
    assert len(result.objects) == 181
    assert "explicit estimated mass" in client.requests[1][1]
    previous = client.requests[1][1].split("Previous candidate:\n")[1].split("\nRepair these")[0]
    assert len(json.loads(previous)["objects"]) == 2


@pytest.mark.parametrize("failure", ["duplicate_prefix", "collision", "budget", "unknown_joint"])
def test_invalid_asset_references_fail_without_a_partial_world(failure):
    raw = prepared_plan()
    maximum = 5000
    if failure == "duplicate_prefix":
        raw["prepared_assets"][1]["id"] = "bowl"
    elif failure == "collision":
        raw["objects"][0]["id"] = "bowl_body"
    elif failure == "budget":
        maximum = 150
    else:
        raw["prepared_assets"][1]["joint_initials"] = [{"name": "invented_motor", "initial": 1}]
    with pytest.raises(GenerationError) as caught:
        generate_spec("Use original assets", client=Client(raw), max_repairs=0, max_objects=maximum)
    assert caught.value.errors
    assert len(caught.value.candidate.objects) == 2


def test_bottom_center_placement_translates_joints_and_cavities_together():
    first = load_prepared_asset("nvidia-microwave079", prefix="first").asset
    second = load_prepared_asset("nvidia-microwave079", prefix="second", position=[2, 3, .7],
                                  joint_initials=[{"name": "microjoint", "initial": -.5}]).asset
    lower = np.min([np.array(o.position)-np.array(o.size)/2 for o in second.objects], axis=0)
    upper = np.max([np.array(o.position)+np.array(o.size)/2 for o in second.objects], axis=0)
    assert [(lower[0]+upper[0])/2, (lower[1]+upper[1])/2, lower[2]] == pytest.approx([2, 3, .7])
    assert np.array(second.regions[0].position)-first.regions[0].position == pytest.approx([2, 3, .7])
    a = next(o.joint for o in first.objects if o.id == first.joint_ids["microjoint"])
    b = next(o.joint for o in second.objects if o.id == second.joint_ids["microjoint"])
    assert np.array(b.anchor)-a.anchor == pytest.approx([2, 3, .7])
    assert (a.initial, b.initial) == (0, -.5)


def test_frozen_export_does_not_depend_on_catalog_resolution(tmp_path, monkeypatch):
    plan = GenerationSpec.model_validate(prepared_plan())
    spec = materialize_prepared_assets(plan, max_objects=5000)
    world = World.from_spec(spec, tmp_path / "worlds", validate=False)
    monkeypatch.setattr("robot_worlds.prepared_assets.load_prepared_asset", lambda *_args, **_kw: pytest.fail("Frozen world must not resolve catalog"))
    from robot_worlds import load
    assert load(world.path).id == world.id
    assert world.export(tmp_path / "world.zip").is_file()


def test_catalog_grounded_references_and_metadata():
    bowl, oven = prepared_asset_catalog()
    assert bowl["components"] == 159
    assert oven["components"] == 20
    assert bowl["size"] == pytest.approx([.208234, .209341, .068883])
    assert oven["regions"][0]["id"] == "{prefix}_interior0"
    assert all(item["source_url"].startswith("https://") and item["revision"] and item["license"] for item in (bowl, oven))


def test_generation_joint_contract_survives_strict_model_schema():
    from robot_worlds.llm import strict_schema
    schema = strict_schema(GenerationSpec.model_json_schema())
    joint = schema["$defs"]["PreparedJointInitial"]
    assert joint["additionalProperties"] is False
    assert set(joint["required"]) == {"name", "initial"}
    assert joint["properties"]["initial"]["type"] == "number"
