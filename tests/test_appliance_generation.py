"""Appliance/compound-object contract checks with mocked generation, not an LLM demo."""
from types import SimpleNamespace

from robot_worlds.generator import generate_spec, validate_spec
from robot_worlds.models import Condition, WorldSpec
from robot_worlds.rubric import measure_predicate
from robot_worlds.runtime import Runtime


def appliance_spec():
    def box(id, position, size, **kwargs):
        return dict(id=id, label=id, kind="box", position=position, size=size, **kwargs)

    return WorldSpec.model_validate(dict(
        name="Microwave placement engineering fixture", description="Hollow appliance and compound bowl",
        task="Put the white bowl inside the microwave and leave it resting there.", bounds=[8, 6, 3],
        objects=[
            box("counter", [2, 0, .29], [.8, .7, .58]),
            box("source", [1, 0, .295], [.6, .5, .59]),
            box("microwave", [2, 0, .59], [.6, .5, .02]),
            box("left", [1.71, 0, .76], [.02, .5, .32], parent_id="microwave"),
            box("right", [2.29, 0, .76], [.02, .5, .32], parent_id="microwave"),
            box("back", [2, .24, .76], [.56, .02, .32], parent_id="microwave"),
            box("top", [2, 0, .93], [.6, .5, .02], parent_id="microwave"),
            box("door", [2, -.26, .76], [.6, .02, .32], parent_id="microwave",
                joint=dict(type="hinge", axis=[0, 0, 1], anchor=[1.7, -.26, .76], range=[-1.7, 0])),
            box("handle", [2.24, -.29, .76], [.02, .04, .18], parent_id="door"),
            box("bowl", [1, 0, .595], [.16, .16, .01], movable=True, mass=.03),
            box("bowl_left", [.925, 0, .625], [.01, .16, .05], parent_id="bowl", mass=.01),
            box("bowl_right", [1.075, 0, .625], [.01, .16, .05], parent_id="bowl", mass=.01),
            box("bowl_back", [1, .075, .625], [.14, .01, .05], parent_id="bowl", mass=.01),
            box("bowl_front", [1, -.075, .625], [.14, .01, .05], parent_id="bowl", mass=.01),
        ],
        regions=[dict(id="cavity", label="Usable microwave cavity", position=[2, 0, .75], size=[.55, .44, .31], interior_of="microwave")],
        rubric=[dict(id="inside", description="Entire bowl inside", predicate="in_region", object_id="bowl", target_id="cavity", tolerance=0, duration=1),
                dict(id="supported", description="Bowl rests on appliance floor", predicate="on_top", object_id="bowl", target_id="microwave", duration=1),
                dict(id="released", description="Bowl released", predicate="released", object_id="bowl", duration=1, after=["inside"])],
    ))


def states(spec):
    return {"objects": {o.id: dict(position=list(o.position), quaternion=[1, 0, 0, 0], size=list(o.size), contacts=[], held_by=None) for o in spec.objects}}


def test_generation_repairs_filled_appliance_without_changing_request():
    good = appliance_spec()
    assert validate_spec(good) == []
    bad = good.model_copy(deep=True)
    bad.objects[2].size = (.6, .5, .36)
    bad.objects[2].position = (2, 0, .76)

    class Client:
        prompts = []
        def complete_json(self, instructions, prompt, schema):
            self.prompts.append(prompt)
            return (bad if len(self.prompts) == 1 else good).model_dump(mode="json")

    client = Client()
    result = generate_spec("Put a bowl in a microwave", client=client)
    assert result.source_prompt == "Put a bowl in a microwave"
    assert "usable cavity intersects solid component" in client.prompts[1]
    assert result.objects[2].size == good.objects[2].size
    assert result.objects[7].joint.type == "hinge"


def test_containment_covers_attached_rim_not_only_bowl_bottom():
    spec = appliance_spec()
    snapshot = states(spec)
    for obj in spec.objects:
        if obj.id == "bowl" or obj.parent_id == "bowl":
            snapshot["objects"][obj.id]["position"][0] += 1
            snapshot["objects"][obj.id]["position"][2] += .30
    condition = spec.rubric[0]
    # The bottom fits below the ceiling, while its attached rim protrudes.
    assert measure_predicate(spec, condition, snapshot)["satisfied"] is False
    for obj in spec.objects:
        if obj.id == "bowl" or obj.parent_id == "bowl":
            snapshot["objects"][obj.id]["position"][2] -= .20
    assert measure_predicate(spec, condition, snapshot)["satisfied"] is True
    del snapshot["objects"]["bowl_left"]
    assert measure_predicate(spec, condition, snapshot)["satisfied"] is None


def test_opposing_fingers_on_different_rim_pieces_hold_one_compound():
    runtime = object.__new__(Runtime)
    runtime._objects = {o.id: o for o in appliance_spec().objects}
    runtime._grasp_roots = {"bowl_left": "bowl", "bowl_right": "bowl"}
    runtime._geom_objects = {0: "bowl_left", 1: "bowl_right"}
    runtime._finger_bodies = {"left": [], "right": [12, 13]}
    runtime.model = SimpleNamespace(geom_bodyid={2: 12, 3: 13})
    runtime.data = SimpleNamespace(ncon=2, contact=[SimpleNamespace(geom1=0, geom2=2), SimpleNamespace(geom1=1, geom2=3)])
    assert runtime._contact_state()[1] == {("right", "bowl")}
    runtime.data.ncon = 1
    assert runtime._contact_state()[1] == set()


def test_cavity_metadata_cannot_point_to_missing_or_distant_appliance():
    spec = appliance_spec()
    spec.regions[0].interior_of = "absent"
    assert any("physical container" in e for e in validate_spec(spec))
    spec.regions[0].interior_of = "microwave"
    spec.regions[0].position = (-2, 0, .75)
    assert any("outside its container" in e for e in validate_spec(spec))


def test_on_top_uses_the_requested_surface_not_the_appliance_roof():
    spec = appliance_spec()
    snapshot = states(spec)
    for obj in spec.objects:
        if obj.id == "bowl" or obj.parent_id == "bowl":
            snapshot["objects"][obj.id]["position"][0] += 1
            snapshot["objects"][obj.id]["position"][2] += .01
    snapshot["objects"]["bowl"]["contacts"] = ["microwave"]
    condition = Condition(predicate="on_top", object_id="bowl", target_id="microwave")
    assert measure_predicate(spec, condition, snapshot)["satisfied"] is True
