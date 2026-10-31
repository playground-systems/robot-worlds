"""Expand model-authored reusable geometry without a fixed scene catalogue."""
from __future__ import annotations

import math
from collections import defaultdict
from itertools import product

from .models import SceneObject, WorldSpec

MAX_EXPANDED_OBJECTS = 10_000


def _rotate(vector, quarter_turns):
    x, y, z = vector
    return ((x, y, z), (-y, x, z), (-x, -y, z), (y, -x, z))[quarter_turns]


def _translated(vector, origin, turns):
    rotated = _rotate(vector, turns)
    return tuple(round(rotated[i] + origin[i], 12) for i in range(3))


def expand_assemblies(spec: WorldSpec, *, max_objects: int = MAX_EXPANDED_OBJECTS) -> WorldSpec:
    """Materialize stable IDs ``instance_ix_iy_iz_part``. Safe to call twice.

    Spacing is world-space; template parts/anchors/axes rotate by quarter turns.
    Existing objects with generated IDs must exactly match their source template.
    Runtime collision and task references always use the materialized objects.
    """
    if not isinstance(max_objects, int) or isinstance(max_objects, bool) or not 1 <= max_objects <= MAX_EXPANDED_OBJECTS:
        raise ValueError(f"max_objects must be an integer from 1 to {MAX_EXPANDED_OBJECTS}")
    result = spec.model_copy(deep=True)
    templates = {t.id: t for t in result.assembly_templates}
    if len(templates) != len(result.assembly_templates):
        raise ValueError("Assembly template IDs must be unique")
    if len({i.id for i in result.assembly_instances}) != len(result.assembly_instances):
        raise ValueError("Assembly instance IDs must be unique")
    existing = {o.id: o for o in result.objects}
    if len(existing) != len(result.objects):
        raise ValueError("Physical object IDs must be unique")
    if len(existing) > max_objects:
        raise ValueError(f"Scene exceeds the {max_objects}-object resource budget")
    estimated = 0
    for instance in result.assembly_instances:
        if instance.template_id not in templates:
            raise ValueError(f"{instance.id}: unknown assembly template {instance.template_id}")
        template = templates[instance.template_id]
        if len({p.id for p in template.parts}) != len(template.parts):
            raise ValueError(f"{template.id}: part IDs must be unique")
        estimated += math.prod(instance.repeat) * len(template.parts)
        if estimated > max_objects:
            raise ValueError(f"Assembly expansion exceeds the {max_objects}-object resource budget")
        if any(count > 1 and abs(spacing) < 1e-9 for count, spacing in zip(instance.repeat, instance.spacing)):
            raise ValueError(f"{instance.id}: repeated axes require nonzero world-space spacing")
        for index in product(*(range(count) for count in instance.repeat)):
            prefix = instance.id + "_" + "_".join(map(str, index)) + "_"
            origin = tuple(instance.position[i] + index[i]*instance.spacing[i] for i in range(3))
            for part in template.parts:
                data = part.model_dump(mode="json")
                data["id"] = prefix + part.id
                data["label"] = f"{template.label} · {part.label}"
                data["position"] = _translated(part.position, origin, instance.quarter_turns)
                if instance.quarter_turns % 2:
                    data["size"] = (part.size[1], part.size[0], part.size[2])
                if part.kind == "mesh":
                    data["mesh_quarter_turns"] = (part.mesh_quarter_turns+instance.quarter_turns) % 4
                if part.parent_id:
                    data["parent_id"] = prefix + part.parent_id
                if part.joint:
                    data["joint"]["anchor"] = _translated(part.joint.anchor, origin, instance.quarter_turns)
                    data["joint"]["axis"] = _rotate(part.joint.axis, instance.quarter_turns)
                obj = SceneObject.model_validate(data)
                if obj.id in existing:
                    if obj != existing[obj.id]:
                        raise ValueError(f"{obj.id}: explicit geometry conflicts with generated assembly part; edit the source template/instance and remove stale materialized objects")
                else:
                    if len(existing) >= max_objects:
                        raise ValueError(f"Scene exceeds the {max_objects}-object resource budget")
                    existing[obj.id] = obj
                    result.objects.append(obj)
    return result


def candidate_pairs(objects):
    """Yield possible AABB intersections using a 3D spatial hash broadphase.

    Large spanning geometry is tested against all objects instead of allocating
    unbounded cell grids. Exact primitive checks remain the caller's job.
    """
    if not objects:
        return
    widths = sorted(max(o.size) for o in objects)
    cell_size = min(2.0, max(.25, widths[len(widths)//2]))
    cells = defaultdict(list)
    oversized = []
    seen = set()
    for i, obj in enumerate(objects):
        ranges = [range(math.floor((obj.position[d]-obj.size[d]/2)/cell_size),
                        math.floor((obj.position[d]+obj.size[d]/2)/cell_size)+1) for d in range(3)]
        if math.prod(len(axis) for axis in ranges) > 4096:
            candidates = range(i)
            oversized.append(i)
        else:
            occupied = list(product(*ranges))
            candidates = [j for cell in occupied for j in cells[cell]] + oversized
            for cell in occupied:
                cells[cell].append(i)
        for j in candidates:
            pair = (j, i)
            if pair not in seen:
                seen.add(pair)
                yield objects[j], obj
