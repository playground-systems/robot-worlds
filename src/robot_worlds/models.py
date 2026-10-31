"""Portable world contract. All lengths are metres; sizes are full extents.

Positions and joint anchors are world coordinates in the zero-joint reference
configuration. Joint initial values are applied by the simulator and may move
articulated objects away from these reference coordinates.
XY bounds are centred on zero; the floor is Z=0. Physical defaults are estimates,
not measured material properties. Semantic furniture is composed from geometry.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Vec3 = tuple[Number, Number, Number]
Color = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Size3 = tuple[Positive, Positive, Positive]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")]
Predicate = Literal["in_region", "on_top", "near", "holding", "released", "carried_distance", "robot_in_region", "joint_at", "process_complete"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, validate_default=True)


class JointSpec(Model):
    type: Literal["hinge", "slide"]
    axis: Vec3
    range: tuple[Number, Number]
    anchor: Vec3
    initial: Number = 0
    damping: Nonnegative = 2

    @model_validator(mode="after")
    def check_joint(self):
        if abs(sum(x*x for x in self.axis) - 1) > 1e-5:
            raise ValueError("joint axis must be a unit vector")
        if self.range[0] >= self.range[1] or not self.range[0] <= self.initial <= self.range[1]:
            raise ValueError("joint range must increase and contain initial")
        return self


class SceneObject(Model):
    id: Identifier
    label: str
    kind: Literal["box", "sphere", "cylinder", "mesh"]
    position: Vec3
    size: Size3
    color: tuple[Color, Color, Color] = Field(default=(0.5, 0.6, 0.7), description="Exactly three red, green, blue components in [0,1]; no alpha component")
    mass: Positive = 1
    movable: bool = False
    friction: Nonnegative = 0.7
    parent_id: Identifier | None = None
    joint: JointSpec | None = None
    mesh_path: str | None = None

    @model_validator(mode="after")
    def check_shape(self):
        if len(self.color) != 3:
            raise ValueError("color must contain three RGB components")
        if self.kind == "mesh" and not self.mesh_path:
            raise ValueError("mesh requires mesh_path (local asset, never executable code)")
        if self.kind != "mesh" and self.mesh_path is not None:
            raise ValueError("mesh_path is only valid for mesh geometry")
        if self.kind == "sphere" and max(self.size) - min(self.size) > 1e-6:
            raise ValueError("sphere requires equal XYZ extents")
        if self.kind == "cylinder" and abs(self.size[0] - self.size[1]) > 1e-6:
            raise ValueError("cylinder requires equal XY extents")
        if self.parent_id and self.movable and not self.joint:
            raise ValueError("parented movable geometry requires a joint")
        return self


class AssemblyTemplate(Model):
    """Model-authored reusable geometry; part coordinates are template-local."""
    id: Identifier
    label: str
    parts: list[SceneObject] = Field(min_length=1, description="Primitive physical parts in local coordinates; parent_id references local part IDs")


class AssemblyInstance(Model):
    id: Identifier
    template_id: Identifier
    position: Vec3
    quarter_turns: Literal[0, 1, 2, 3] = 0
    repeat: tuple[Annotated[int, Field(ge=1, le=1000)], Annotated[int, Field(ge=1, le=1000)], Annotated[int, Field(ge=1, le=1000)]] = (1, 1, 1)
    spacing: Vec3 = (0, 0, 0)


class Region(Model):
    id: Identifier
    label: str
    position: Vec3
    size: Size3
    interior_of: Identifier | None = Field(default=None, description="Physical appliance/container root whose cavity this region describes; never a substitute for hollow collision geometry")


class Condition(Model):
    predicate: Predicate
    object_id: Identifier | None = None
    target_id: Identifier | None = None
    threshold: Number | None = None
    tolerance: Nonnegative = 0.03
    value: str | None = None


class ProcessPrerequisite(Condition):
    predicate: Literal["in_region", "on_top", "near", "joint_at", "released"]


class ProcessCycle(Model):
    """Explicit timed abstraction, not simulation of internal machining physics."""
    id: Identifier
    label: str
    fixture_id: Identifier
    workpiece_ids: list[Identifier] = Field(min_length=1)
    duration: Positive
    output_state: str = Field(min_length=1)
    prerequisites: list[ProcessPrerequisite] = Field(min_length=1)


class Criterion(Condition):
    id: Identifier
    description: str
    duration: Nonnegative = 0
    after: list[Identifier] = Field(default_factory=list)
    required: bool = True
    mode: Literal["final", "eventually", "always"] = "final"
    alternative_group: Identifier | None = None
    # Achievement criteria must not already pass at reset. Maintenance criteria
    # explicitly opt out, rather than weakening a task to obtain easy success.
    intent: Literal["achieve", "maintain"] = "achieve"


class WorldSpec(Model):
    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1)
    description: str
    task: str = Field(min_length=1)
    robot: Literal["tiago++", "g1"] = "tiago++"
    bounds: Size3
    robot_start: Vec3 = (0, 0, 0)  # x, y, yaw radians
    objects: list[SceneObject]
    assembly_templates: list[AssemblyTemplate] = Field(default_factory=list)
    assembly_instances: list[AssemblyInstance] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    rubric: list[Criterion] = Field(min_length=1)
    process_cycles: list[ProcessCycle] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    source_prompt: str = ""
    unvalidated_requirements: list[str] = Field(default_factory=list)
