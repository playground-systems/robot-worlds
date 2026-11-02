"""Only geometric obstruction proofs may reject navigation requirements."""

import pytest

from robot_worlds.connectivity import diagnose_connectivity
from robot_worlds.generator import validate_spec
from robot_worlds.models import Criterion, JointSpec, Region, SceneObject, WorldSpec


def box(oid, x, y, sx, sy, *, z=.8, sz=1.6):
    return SceneObject(id=oid, label=oid, kind="box", position=(x, y, z), size=(sx, sy, sz))


def room(gap=None):
    objects = [box("east", 3, 0, .1, 2), box("north", 2, 1.05, 2.1, .1),
               box("south", 2, -1.05, 2.1, .1)]
    if gap is None:
        objects.append(box("west", 1, 0, .1, 2))
    else:
        length = 1-gap/2
        objects.extend([box("west_lower", 1, -(gap/2+length/2), .1, length),
                        box("west_upper", 1, gap/2+length/2, .1, length)])
    return WorldSpec(
        name="Connectivity fixture", description="Internal geometry proof fixture",
        task="Visit the room", bounds=(10, 10, 3), robot_start=(-2, 0, 0), objects=objects,
        regions=[Region(id="goal", label="goal", position=(2, 0, .75), size=(.5, .5, 1.5))],
        rubric=[Criterion(id="visit", description="Visit", predicate="robot_in_region", target_id="goal")],
    )


def test_sealed_required_destination_is_rejected_and_evidence_exposed():
    spec = room()
    evidence = {}
    errors = validate_spec(spec, connectivity_diagnostics=evidence)
    assert any("navigation clearance contract" in e for e in errors)
    assert evidence["status"] == "blocked"
    assert evidence["criteria"]["visit"]["status"] == "blocked"
    assert evidence["contract"] == {"radius_m": .4, "height_m": 1.6, "motion": "planar"}


@pytest.mark.parametrize("gap", [.80001, 1.0])
def test_submillimeter_clear_route_is_never_lost_to_grid_resolution(gap):
    spec = room(gap)
    assert validate_spec(spec) == []
    result = diagnose_connectivity(spec)
    assert result["status"] == "unknown"
    assert result["criteria"]["visit"]["status"] == "unknown"
    assert "unverified" in result["criteria"]["visit"]["reason"]


def test_too_narrow_doorway_fails_clearance_contract():
    result = diagnose_connectivity(room(.6))
    assert result["status"] == "blocked"


def test_geometry_relaxation_allows_exact_tangent_clearance():
    assert diagnose_connectivity(room(.8))["status"] == "unknown"


def test_articulated_door_and_fixed_descendants_are_uncertain_not_a_wall():
    spec = room(1.0)
    door = box("door", 1, 0, .1, 1.0)
    door.joint = JointSpec(type="hinge", axis=(0, 0, 1), anchor=(1, -.5, .8), range=(0, 1.57))
    panel = box("panel", 1, 0, .1, 1.0)
    panel.parent_id = "door"
    spec.objects.extend([door, panel])
    result = diagnose_connectivity(spec)
    assert result["status"] == "unknown"
    excluded = {o["id"]: o["reason"] for o in result["excluded_objects"]}
    assert "articulated" in excluded["door"] and "articulated" in excluded["panel"]


@pytest.mark.parametrize("kind", ["mesh", "sphere", "cylinder"])
def test_uncertain_nonbox_never_creates_a_blockage_proof(kind):
    spec = room()
    wall = spec.objects[-1].model_dump()
    wall["kind"] = kind
    if kind == "mesh":
        wall["mesh_path"] = "uninspected.obj"
    else:
        wall["size"] = (2, 2, 2)
    spec.objects[-1] = SceneObject.model_validate(wall)
    result = diagnose_connectivity(spec)
    assert result["status"] == "unknown"
    assert any(o["id"] == "west" for o in result["excluded_objects"])


def test_overhead_box_does_not_block_planar_envelope():
    spec = room()
    spec.objects[-1].position = (1, 0, 2)
    spec.objects[-1].size = (.1, 2, .5)
    assert diagnose_connectivity(spec)["status"] == "unknown"


def test_region_overlap_with_reachable_space_is_not_rejected_by_center():
    spec = room()
    spec.regions[0].position = (1.4, 0, .75)
    spec.regions[0].size = (2.1, .5, 1.5)
    assert diagnose_connectivity(spec)["status"] == "unknown"


def test_world_bounds_do_not_create_invisible_walls():
    spec = room()
    spec.objects = [box("partition", 0, 0, .1, 10)]
    assert validate_spec(spec) == []
    assert diagnose_connectivity(spec)["status"] == "unknown"


def test_alternative_with_reachable_branch_is_not_rejected():
    spec = room()
    spec.regions.append(Region(id="outside", label="outside", position=(-3, 0, .75), size=(.5, .5, 1.5)))
    spec.rubric[0].alternative_group = "either"
    spec.rubric.append(Criterion(id="outside_visit", description="Other route", predicate="robot_in_region",
                                target_id="outside", alternative_group="either"))
    result = diagnose_connectivity(spec)
    assert result["criteria"]["visit"]["status"] == "blocked"
    assert result["errors"] == []
    spec.regions[-1].position = (2, 0, .75)
    assert any("every branch" in e for e in diagnose_connectivity(spec)["errors"])


def test_non_navigation_alternative_remains_unknown():
    spec = room()
    spec.rubric[0].alternative_group = "either"
    spec.objects.append(box("part", -3, 0, .1, .1, z=.1, sz=.2))
    spec.rubric.append(Criterion(id="other", description="Other task", predicate="released", object_id="part",
                                alternative_group="either", intent="maintain"))
    assert diagnose_connectivity(spec)["errors"] == []


def test_optional_navigation_can_be_a_required_temporal_witness():
    spec = room()
    spec.rubric[0].required = False
    spec.objects.append(box("part", -3, 0, .1, .1, z=.1, sz=.2))
    spec.rubric.append(Criterion(id="finish", description="Finish", predicate="released", object_id="part",
                                after=["visit"], intent="maintain"))
    result = diagnose_connectivity(spec)
    assert result["status"] == "blocked"
    assert result["criteria"]["finish"]["status"] == "blocked"


def test_resource_cap_returns_unknown_not_a_coarse_grid_blockage():
    result = diagnose_connectivity(room(), max_cells=2)
    assert result["status"] == "unknown"
    assert result["errors"] == []
    assert "budget" in result["reason"]


def test_five_thousand_objects_are_bounded_before_dense_allocation():
    spec = room()
    spec.bounds = (100, 60, 3)
    spec.objects = [box(f"b{i}", -40+i*.016001, -25+i*.010003, .001, .001)
                    for i in range(5000)]
    result = diagnose_connectivity(spec)
    assert result["status"] == "unknown"
    assert result["statistics"]["fixed_box_count"] == 5000
    assert result["statistics"]["cells"] > result["limits"]["max_cells"]
    assert "budget" in result["reason"]


def test_optional_isolated_goal_alone_cannot_reject_required_other_task():
    spec = room()
    spec.rubric[0].required = False
    spec.objects.append(box("part", -3, 0, .1, .1, z=.1, sz=.2))
    spec.rubric.append(Criterion(id="other", description="Other task", predicate="released", object_id="part", intent="maintain"))
    assert diagnose_connectivity(spec)["errors"] == []
