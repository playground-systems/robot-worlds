"""Named tasks over one physical scene; variants contain no geometry or poses."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .models import Criterion, Identifier, Model, WorldSpec
from .rubric import validate_rubric


class TaskVariant(Model):
    """An instruction and executable rubric for the world's unchanged reset."""

    id: Identifier
    name: str = Field(min_length=1, max_length=160)
    task: str = Field(min_length=1, max_length=32000)
    rubric: list[Criterion] = Field(min_length=1, max_length=256)


class TaskCatalog(Model):
    schema_version: Literal["1.0"] = "1.0"
    tasks: list[TaskVariant] = Field(min_length=1, max_length=256)
    selected_task_id: Identifier | None = None

    @model_validator(mode="after")
    def unique_tasks(self):
        identifiers = [task.id for task in self.tasks]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Task IDs must be unique")
        if self.selected_task_id is not None and self.selected_task_id not in identifiers:
            raise ValueError("Selected task is absent from the catalog")
        return self

    def get(self, task_id: str) -> TaskVariant:
        for task in self.tasks:
            if task.id == task_id:
                return task.model_copy(deep=True)
        raise ValueError(f"Unknown task ID: {task_id}")

    def validate_for(self, spec: WorldSpec) -> TaskCatalog:
        """Check physical references, predicates and ordering without simulation.

        This proves neither reachability nor successful execution. In particular,
        selecting an instruction cannot change a joint's initial value or place a
        workpiece into the pose that would make the instruction easier.
        """
        for task in self.tasks:
            # Validation only reads the shared scene; avoid copying thousands of
            # geometry components for every instruction in a large catalog.
            variant = spec.model_copy(update={"task": task.task, "rubric": task.rubric})
            errors = validate_rubric(variant)
            if errors:
                raise ValueError(f"Task {task.id}: " + "; ".join(errors))
        if self.selected_task_id is not None:
            selected = self.get(self.selected_task_id)
            if selected.task != spec.task or selected.rubric != spec.rubric:
                raise ValueError("Selected task does not match the frozen instruction and rubric")
        return self
