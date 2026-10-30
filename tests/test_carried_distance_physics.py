"""Carry and released-process requirements on the existing physical mission fixture."""
import test_factory_mission as mission

from robot_worlds.models import Criterion, ProcessPrerequisite


def test_native_mission_has_two_continuous_carries_and_released_processing(monkeypatch):
    original = mission.mission_spec

    def strict_spec():
        spec = original()
        spec.process_cycles[0].prerequisites.append(ProcessPrerequisite(predicate="released", object_id="part"))
        spec.rubric[1].after = ["carry_to_inspection"]
        spec.rubric[2].after = ["carry_to_dispatch"]
        spec.rubric += [
            Criterion(id="carry_to_inspection", description="Carry to inspection", predicate="carried_distance",
                      object_id="part", threshold=1.5, mode="eventually", after=["picked"]),
            Criterion(id="carry_to_dispatch", description="Carry to dispatch", predicate="carried_distance",
                      object_id="part", threshold=1.5, mode="eventually", after=["inspected"]),
        ]
        return spec

    monkeypatch.setattr(mission, "mission_spec", strict_spec)
    mission.test_three_station_grasp_carry_process_delivery()
