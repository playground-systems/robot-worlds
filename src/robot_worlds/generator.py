"""Bounded model generation and deterministic checks; no bundled scene fallback."""
from __future__ import annotations

import json
import math
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .large_scene import MAX_EXPANDED_OBJECTS, candidate_pairs, expand_assemblies
from .models import WorldSpec
from .revolved import MAX_SCENE_COLLIDERS, collision_count, intersects_box
from .rubric import measure_predicate, rigid_members, validate_rubric

PHYSICAL_DEFAULTS = "Unspecified masses, friction coefficients and material appearance are estimates, not measured properties."
INSTRUCTIONS = """Generate a runnable robot world and executable physical rubric matching the user's complete request. Return only the supplied JSON schema. Preserve every requested setting, layout constraint, task stage, object identity and robot requirement during repair. Do not replace a difficult request with an easier task or claim unsupported physical processes are simulated.
The task field is an ordinary operator instruction, separate from the privileged world definition. Never put simulator object IDs, world/map coordinates, robot approach poses, hidden object state, semantic-detection assumptions, or an action script into task. Describe visible identifying features and the desired outcome. The actor receives RGB-D measurements and proprioception only, not simulator segmentation or object labels. Make referenced stations distinguishable from their actual visible geometry/color; a SceneObject.label is metadata, not a rendered sign.
Appliances and containers must have real hollow interiors: separate floor, side, back and top geometry with physical clearances. Use hinged/slide joints for doors and drawers, accessible handles attached to the moving panel, and clear opening paths. For curved bowls/cups/funnels without a compatible sourced asset use kind=revolved with a closed material cross-section profile of normalized (radius,height) points: radius 0..0.5 and height -0.5..0.5, touching all those outer extrema. The profile includes outer wall, rim, inner wall and bottom; do not repeat its first point. Set equal XY sizes for diameter and Z size for total height. Segments (12..64, multiple of4, default32) control both surface and physical wedge fidelity. Concave material profiles are triangulated into convex collision wedges preserving the empty cavity. The object mass is distributed across its wedges. A hollow bowl can alternatively use attached rim/wall components; do not replace a bowl by a solid cylinder or a concave mesh's convex collision hull. A movable compound has ONE movable root and fixed parented components. Declare cavity regions with interior_of referencing the physical appliance/container root. Placement rubrics need full-object containment in the cavity, physical support and release for a stable interval; nearby is not inside. Door closure, heating or other operations are required only when requested. Heating is a disclosed timed process, never thermal physics. Do not claim validation has run; the validation record reports actual checks.
World coordinates: metres, Z up, floor Z=0, centered XY bounds. Object position is geometry center in the zero-joint reference configuration and size is FULL XYZ extents. Joint initial values are applied afterward; keep articulated initial poses inside bounds and collision-free. Robot_start is x,y,yaw. All IDs are stable unique ASCII identifiers. Primitive shapes box/sphere/cylinder, general revolved profiles, or explicit local mesh_path; sphere XYZ and cylinder XY extents must match. Compose detailed furniture, fixtures, rooms, walls, bins and arbitrary layouts using separate physical pieces. Do not model a bin or shelf as a solid box filling its usable interior. Use grounded realistic sizes, enough clear space for a 0.8m-wide mobile robot, connected routes, stable supporting surfaces and reachable workpieces. Navigation clearance contract: leave connected ground-plane routes for a 0.8m-diameter, 1.6m-high envelope from robot_start to required robot_in_region destinations. Do not seal destinations behind fixed walls; articulated-door routes require separate physical validation. This contract does not certify every robot posture or task solvability. No invisible decorative solids or overlapping collision geometry. An object may touch a supporting surface but must not intersect it. Geometry must remain within bounds, floor and ceiling.
Regions are NON-colliding task volumes. They cannot substitute for physical fixtures. Full-object containment volumes around support surfaces need physical contact clearance: extend the volume about 5 mm below the tabletop (or declare an explicit, justified containment tolerance), while separately requiring on_top contact and released holding state. Never put a zero-tolerance region boundary exactly on a contact plane; compliant contact allows slight penetration even at stable rest. Preserve requested precision constraints and report conflicts instead of silently widening them. Objects may be rigid, freely movable, or parented articulated hinge/slide mechanisms with world-space anchors/axes and valid joint ranges. No pose-teleportation scripts. Meshes must be real files explicitly supplied by the user; never invent an asset path. If requested geometry can be constructed parametrically, use a detailed assembly. Describe collision/physical simplifications and estimated masses/friction in assumptions.
Generate a meaningful task and rubric jointly. Process prerequisites support released alongside on_top/in_region/near/joint_at: combine on_top and released on each workpiece to require physical loading without gripper holding throughout the cycle. Measured carrying uses sampled trajectory evidence at 0.05-second intervals, with additional action-boundary samples; disclose that sampling limitation as an assumption, not an unsupported requirement. Process monitors run every physics step. Reset/reachability checks do not certify execution success; keep capability uncertainty as an assumption unless a specific requested feature is unsupported. Predicates supported: in_region (full object containment), robot_in_region (robot origin), on_top (measured contact plus support geometry), near (center distance threshold metres), holding (bilateral physical finger contact), carried_distance (net XY displacement in metres during uninterrupted recorded holding; positive threshold, movable object_id, no target_id, usually mode=eventually), released (no measured holding), joint_at (threshold in joint units), process_complete (explicit timed cycle output state). Ground every reference. Task achievement must not already pass at reset. Use intent=maintain for genuine maintenance/safety or already-true final constraints (e.g. a door that must end closed without an instruction to operate it); never introduce an extra open/close operation merely to create a transition. Achievement requires a measured false-to-true transition, and after requires that new true interval begin strictly after prerequisites complete. Never use maintain to evade a requested achievement. Add duration seconds to require stable sampled success; use mode final for outcomes that must remain true at the end, eventually for completed stages, always for conditions throughout the run. after references earlier criterion IDs and requires strict temporal ordering. Required ungrouped criteria combine with AND; criteria in the same alternative_group combine with OR. Optional criteria only report partial progress. Do not use alternatives to weaken requested prerequisites. Grading cannot measure unimplemented quality, cutting, deformable, fluid or chemical predicates.
Internal manufacturing processes may be EXPLICIT timed abstractions: process_cycles declare a fixture, exact workpiece IDs, duration, output_state and continuously satisfied physical prerequisites. Every workpiece needs an on_top or in_region loading prerequisite at the fixture. Output state is simulated bookkeeping, not internal material physics. Disclose each such abstraction in assumptions. Unsupported task requirements must be listed in unvalidated_requirements, not silently changed or labeled validated. This includes requested measurements or physical capabilities absent from the supported schema; assumptions alone do not excuse dropping a requirement.
For large or repeated geometry, generate your OWN assembly_templates (local SceneObject parts) and assembly_instances (template_id, position, quarter_turns 0/1/2/3, repeat=[nx,ny,nz], spacing in WORLD xyz). This is not a built-in asset catalogue. A part is materialized as instanceID_ix_iy_iz_partID, even for a single instance; use those exact persistent IDs in parent links outside the template, process prerequisites and rubric. Template parent_id names another LOCAL part; joint anchors/axes are LOCAL and transformed with the instance. Keep instance/part names short so expanded IDs stay under 80 characters. Explicit objects hold unique task/workpiece geometry; do not redundantly emit all expanded parts. Repetition positions must fit world bounds and preserve clear walkable aisles; templates must be stable hollow furniture assemblies, not solid bounding boxes. Mesh instances only support quarter_turns=0; otherwise supply a pre-rotated source mesh. All expanded parts count against the stated object budget. Parameters generate real collision geometry, not visual-only scenery.
The schema accepts tiago++ and g1 model identities; runtime capability checks are separate. Use tiago++ unless user requests another supported robot. Return an arbitrary world appropriate to the prompt, never a fixed demo or template. Include source_prompt exactly as the original request.
"""


class GenerationError(RuntimeError):
    def __init__(self, message, *, candidate=None, attempts=0, history=None, errors=None):
        super().__init__(message)
        self.candidate = candidate
        self.spec = candidate
        self.attempts = attempts
        self.history = history or []
        self.errors = errors or []


class GenerationCancelled(GenerationError):
    """Cancellation retains the most recent candidate and generation history."""


def _intersects(a, b, epsilon=1e-5):
    if not getattr(a, "collision", True) or not getattr(b, "collision", True):
        return False
    # Initial shapes have no arbitrary orientation. Articulated swept volumes and
    # mesh contacts require the runtime's real compile/reset checks.
    if a.kind == "revolved" or b.kind == "revolved":
        if a.kind == "revolved" and not intersects_box(a, b, epsilon):
            return False
        if b.kind == "revolved" and not intersects_box(b, a, epsilon):
            return False
        return True
    if a.kind == "sphere" and b.kind == "sphere":
        return math.dist(a.position, b.position) < (a.size[0]+b.size[0])/2-epsilon
    if a.kind == "sphere" or b.kind == "sphere":
        sphere, box = (a, b) if a.kind == "sphere" else (b, a)
        distance = math.sqrt(sum(max(0, abs(sphere.position[i]-box.position[i])-box.size[i]/2)**2 for i in range(3)))
        return distance < sphere.size[0]/2-epsilon
    return all(abs(a.position[i]-b.position[i]) < (a.size[i]+b.size[i])/2-epsilon for i in range(3))


def _world_welded(obj, objects) -> bool:
    """True only when the entire body ancestry is fixed to the world."""
    seen = set()
    while obj is not None:
        if obj.id in seen or obj.movable or obj.joint is not None:
            return False
        seen.add(obj.id)
        if obj.parent_id is None:
            return True
        obj = objects.get(obj.parent_id)
    return False  # A missing parent is not evidence of a fixed body.


def validate_spec(spec: WorldSpec, *, asset_root: str | Path | None = None,
                  validation_profile: str = "strict", skipped_fixed_pairs: list | None = None,
                  connectivity_diagnostics: dict | None = None) -> list[str]:
    """Static checks only. Real simulation settling/contact probes remain required."""
    if validation_profile not in {"strict", "fixed_reference"}:
        raise ValueError("validation_profile must be strict or fixed_reference")
    errors = validate_rubric(spec)
    # These results live for one pass only: a later validation must observe
    # edited source bytes. Shared assets still report errors per component.
    checked_textures, checked_metadata = {}, {}
    collider_count = sum(collision_count(obj) for obj in spec.objects)
    if collider_count > MAX_SCENE_COLLIDERS:
        errors.append(f"Scene needs {collider_count} collision pieces, exceeding the {MAX_SCENE_COLLIDERS} budget; lower revolved segments or repeated geometry")
    errors.extend(f"Unvalidated requirement: {requirement}" for requirement in spec.unvalidated_requirements)
    all_ids = [x.id for x in [*spec.objects, *spec.regions, *spec.process_cycles]]
    if len(all_ids) != len(set(all_ids)) or "robot" in all_ids:
        errors.append("objects, regions and processes need globally unique IDs; robot is reserved")
    objects = {o.id: o for o in spec.objects}
    regions = {r.id: r for r in spec.regions}
    # Source assets commonly use intersecting convex parts on one welded body.
    # They have no relative motion/contact pair; cavity checks still inspect each
    # collider, and distinct moving bodies must pass overlap/native checks.
    welded_roots = {}
    for obj in spec.objects:
        current, chain = obj, set()
        while current.id not in welded_roots and current.id not in chain:
            chain.add(current.id)
            if current.joint or current.movable or current.parent_id not in objects:
                break
            current = objects[current.parent_id]
        root_id = welded_roots.get(current.id, current.id)
        for oid in chain:
            welded_roots[oid] = root_id
    for region in spec.regions:
        if region.interior_of is None:
            continue
        if region.interior_of not in objects:
            errors.append(f"{region.id}: interior_of must reference a physical container")
            continue
        members = rigid_members(objects, region.interior_of)
        low = [min(objects[oid].position[i] - objects[oid].size[i]/2 for oid in members) for i in range(3)]
        high = [max(objects[oid].position[i] + objects[oid].size[i]/2 for oid in members) for i in range(3)]
        if any(region.position[i] - region.size[i]/2 < low[i] - .0051 or
               region.position[i] + region.size[i]/2 > high[i] + .0051 for i in range(3)):
            errors.append(f"{region.id}: cavity lies outside its container envelope")
        for oid in members:
            part = objects[oid]
            if part.kind != "box" or not part.collision:
                continue  # Curved/mesh interiors need native geometry checks.
            penetration = [min(region.position[i] + region.size[i]/2, part.position[i] + part.size[i]/2) -
                           max(region.position[i] - region.size[i]/2, part.position[i] - part.size[i]/2) for i in range(3)]
            if all(p > .0051 for p in penetration):
                errors.append(f"{region.id}: usable cavity intersects solid component {oid}; build hollow walls, not a filled appliance")
    for item in [*spec.objects, *spec.regions]:
        if any(abs(item.position[i])+item.size[i]/2 > spec.bounds[i]/2 + 1e-5 for i in (0, 1)) or item.position[2]-item.size[2]/2 < -1e-5 or item.position[2]+item.size[2]/2 > spec.bounds[2] + 1e-5:
            errors.append(f"{item.id}: geometry/region extends outside world bounds")
    x, y, _ = spec.robot_start
    if abs(x)+.4 > spec.bounds[0]/2 or abs(y)+.4 > spec.bounds[1]/2 or spec.bounds[2] < 1.6:
        errors.append("robot start does not fit within world bounds (0.4m clearance, 1.6m height)")
    for obj in spec.objects:
        if obj.parent_id and obj.parent_id not in objects:
            errors.append(f"{obj.id}: missing parent {obj.parent_id}")
        seen, current = {obj.id}, obj
        while current.parent_id in objects:
            if current.parent_id in seen:
                errors.append(f"{obj.id}: parent cycle")
                break
            seen.add(current.parent_id)
            current = objects[current.parent_id]
        if obj.kind == "mesh":
            path = Path(obj.mesh_path).expanduser()
            if not path.is_absolute() and asset_root is not None:
                path = Path(asset_root) / path
            if path.suffix.lower() not in {".obj", ".stl", ".msh"} or not path.is_file():
                errors.append(f"{obj.id}: missing/unsupported local mesh asset {obj.mesh_path}")
        if obj.texture_path:
            from .mesh_assets import validate_texture
            path = Path(obj.texture_path).expanduser()
            if not path.is_absolute() and asset_root is not None:
                path = Path(asset_root) / path
            if path not in checked_textures:
                try:
                    validate_texture(path)
                    checked_textures[path] = None
                except (ValueError, OSError) as exc:
                    checked_textures[path] = str(exc)
            if checked_textures[path] is not None:
                errors.append(f"{obj.id}: invalid texture: {checked_textures[path]}")
        if obj.source_notice:
            path = Path(obj.source_notice).expanduser()
            if not path.is_absolute() and asset_root is not None:
                path = Path(asset_root) / path
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt"} or path.stat().st_size > 1024 * 1024:
                errors.append(f"{obj.id}: source notice must be an existing text file of at most 1 MiB")
        if obj.source_metadata:
            path = Path(obj.source_metadata).expanduser()
            if not path.is_absolute() and asset_root is not None:
                path = Path(asset_root) / path
            if path not in checked_metadata:
                if not path.is_file() or path.suffix.lower() != ".json" or path.stat().st_size > 2 * 1024 * 1024:
                    checked_metadata[path] = "source metadata must be an existing JSON file of at most 2 MiB"
                else:
                    try:
                        json.loads(path.read_text())
                        checked_metadata[path] = None
                    except (ValueError, OSError) as exc:
                        checked_metadata[path] = f"invalid source metadata: {exc}"
            if checked_metadata[path] is not None:
                errors.append(f"{obj.id}: {checked_metadata[path]}")
        if not obj.visible:
            if not any(objects[oid].visible for oid in rigid_members(objects, welded_roots[obj.id])):
                errors.append(f"{obj.id}: hidden collision geometry needs a visible member of its rigid assembly")
        # Conservative initial robot envelope, not a reachability certificate.
        if obj.collision and obj.position[2]-obj.size[2]/2 < 1.6 and obj.position[2]+obj.size[2]/2 > 0:
            dx = max(0, abs(x-obj.position[0])-obj.size[0]/2)
            dy = max(0, abs(y-obj.position[1])-obj.size[1]/2)
            if math.hypot(dx, dy) < .4-1e-5:
                errors.append(f"{obj.id}: intersects initial robot clearance envelope")
    overlaps = 0
    fixed = {obj.id: _world_welded(obj, objects) for obj in spec.objects}
    for a, b in candidate_pairs(spec.objects):
        if welded_roots[a.id] == welded_roots[b.id]:
            continue
        if (validation_profile == "fixed_reference" and fixed[a.id] and fixed[b.id]
                and _intersects(a, b)):
            if skipped_fixed_pairs is not None:
                skipped_fixed_pairs.append(sorted([a.id, b.id]))
            continue  # Welded reference assembly; robot/dynamic contacts remain enabled.
        if a.parent_id == b.id or b.parent_id == a.id:
            continue  # hinge hardware can meet its parent; runtime checks motion
        if _intersects(a, b):
            errors.append(f"{a.id}/{b.id}: initial collision volumes overlap")
            overlaps += 1
            if overlaps >= 100:
                errors.append("Collision diagnostic limit reached; repair the overlapping assembly/layout before further validation")
                break
    for cycle in spec.process_cycles:
        fixture = objects.get(cycle.fixture_id)
        if not fixture:
            errors.append(f"{cycle.id}: missing physical process fixture")
        if len(cycle.workpiece_ids) != len(set(cycle.workpiece_ids)):
            errors.append(f"{cycle.id}: repeated workpiece ID")
        for oid in cycle.workpiece_ids:
            if oid not in objects or not objects[oid].movable:
                errors.append(f"{cycle.id}: workpiece {oid} must be a movable physical object")
            loading = [p for p in cycle.prerequisites if p.object_id == oid and p.predicate in {"on_top", "in_region"}]
            grounded = False
            for condition in loading:
                if condition.predicate == "on_top":
                    support = objects.get(condition.target_id)
                    grounded |= support is not None and (support.id == cycle.fixture_id or support.parent_id == cycle.fixture_id)
                elif fixture and condition.target_id in regions:
                    region = regions[condition.target_id]
                    grounded |= all(abs(region.position[i]-fixture.position[i]) <= (region.size[i]+fixture.size[i])/2+.25 for i in range(3))
                    for support_condition in loading:
                        if support_condition.predicate != "on_top":
                            continue
                        support = objects.get(support_condition.target_id)
                        if support is None or support.kind != "box" or not _world_welded(support, objects):
                            continue  # Moving/curved supports need a native loading probe.
                        top = support.position[2] + support.size[2]/2
                        effective_bottom = region.position[2] - region.size[2]/2 - condition.tolerance
                        if top - effective_bottom < .0001:
                            errors.append(
                                f"{cycle.id}: {region.id} needs contact clearance below {support.id}'s "
                                "support plane for full-object containment; use a justified region margin "
                                "or explicit tolerance (typically 0.005m), retaining physical support/release checks"
                            )
            if not grounded:
                errors.append(f"{cycle.id}: workpiece {oid} needs a physical loading condition at its fixture")
    if not errors:
        initial = {"time": 0, "objects": {o.id: {"position": list(o.position), "size": list(o.size), "quaternion": [1, 0, 0, 0] if not o.joint or o.joint.initial == 0 else None, "held_by": None, "joint_position": o.joint.initial if o.joint else 0, "process_states": {}} for o in spec.objects}, "robot": {"position": [x, y, 0]}}
        # Grounded geometry can reveal trivial tasks before a costly compile.
        # Missing contact evidence is unknown and cannot certify a no-op pass.
        for criterion in spec.rubric:
            if criterion.required and criterion.intent == "achieve" and not criterion.after and measure_predicate(spec, criterion, initial)["satisfied"] is True:
                errors.append(f"{criterion.id}: achievement is already satisfied at reset (no-op counterexample); use maintain only for a genuine initial/final invariant")
    if not errors:
        from .connectivity import diagnose_connectivity
        connectivity = diagnose_connectivity(spec)
        errors.extend(connectivity["errors"])
    else:
        connectivity = {"status": "not_checked", "reason": "Earlier geometry/rubric checks failed", "errors": []}
    if connectivity_diagnostics is not None:
        connectivity_diagnostics.update(connectivity)
    return errors


@contextmanager
def _generation_phase(emit, phase: str, attempt: int):
    """Wall-clock diagnostics only; completion does not mean validation passed."""
    started = time.perf_counter()
    if emit:
        emit({"type": "generation_phase_started", "phase": phase, "attempt": attempt})
    error_type = None
    try:
        yield
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        if emit:
            emit({"type": "generation_phase_finished", "phase": phase, "attempt": attempt,
                  "duration_seconds": max(0.0, time.perf_counter() - started),
                  "status": "failed" if error_type else "finished", "error_type": error_type})


def generate_spec(prompt: str, *, model="gpt-6-astra", reasoning="medium", client=None, max_repairs=2, on_candidate: Callable | None = None, event_callback: Callable | None = None, validator: Callable | None = None, asset_root=None,
                  cancelled: Callable | None = None, client_options: dict | None = None,
                  scene_profile: str = "standard", max_objects: int = 5000, min_objects: int = 0, asset_library=None,
                  online_asset_cache=None, asset_resolver=None, online_assets=True) -> WorldSpec:
    if not isinstance(online_assets, bool) or (not online_assets and asset_resolver is not None):
        raise ValueError("online_assets must be boolean; a resolver cannot be supplied when disabled")
    if scene_profile not in {"standard", "large"}:
        raise ValueError("scene_profile must be standard or large")
    if not isinstance(max_objects, int) or isinstance(max_objects, bool) or not 1 <= max_objects <= MAX_EXPANDED_OBJECTS:
        raise ValueError(f"max_objects must be from 1 to {MAX_EXPANDED_OBJECTS}")
    if not isinstance(min_objects, int) or isinstance(min_objects, bool) or not 0 <= min_objects <= max_objects:
        raise ValueError("min_objects must be an integer between zero and max_objects")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be nonempty")
    if not isinstance(max_repairs, int) or isinstance(max_repairs, bool) or not 0 <= max_repairs <= 10:
        raise ValueError("max_repairs must be an integer from 0 to 10")
    allowed_options = {"timeout", "max_seconds", "max_calls", "max_total_output_tokens", "max_output_tokens", "max_cost_usd", "input_price_per_million", "output_price_per_million", "cached_input_price_per_million", "cache_write_price_per_million"}
    if client_options and set(client_options) - allowed_options:
        raise ValueError("Unsupported generation client options: " + ", ".join(sorted(set(client_options) - allowed_options)))
    if client is not None and client_options:
        raise ValueError("Configure budgets on the supplied client, or pass client_options without a client")
    if client is None:
        from .llm import ResponsesClient
        defaults = {"timeout": 480.0, "max_output_tokens": 16384} if scene_profile == "large" else {"timeout": 120.0}
        client = ResponsesClient(model=model, reasoning=reasoning, **{**defaults, **(client_options or {})})
    if event_callback:
        from .llm import audit_value
        event_callback({"type": "model_budget", "budget": audit_value(getattr(client, "budget", {}))})
    from .prepared_assets import (
        GenerationSpec,
        materialize_prepared_assets,
        prepared_asset_catalog,
    )
    cache_resolver = None
    if online_asset_cache is not None:
        from .asset_resolution import FuelAssetResolver
        cache_resolver = FuelAssetResolver(online_asset_cache, client=client, on_event=event_callback, cancelled=cancelled)
        if asset_resolver is None and online_assets:
            asset_resolver = cache_resolver
    with _generation_phase(event_callback, "catalog", 0):
        from .asset_library import resolve_asset_library
        asset_library = resolve_asset_library(asset_library)
        catalog_entries = prepared_asset_catalog(asset_library)
        if cache_resolver is not None:
            from .collision_preparation import CollisionPreparationCancelled
            try:
                asset_library, catalog_entries = cache_resolver.cached_catalog(asset_library, catalog_entries)
            except CollisionPreparationCancelled as exc:
                raise GenerationCancelled("World generation cancelled while loading cached sources", attempts=0,
                                          errors=["Generation cancelled before model request"]) from exc
        catalog = json.dumps(catalog_entries, separators=(",", ":"))
    asset_instructions = "\nPrefer prepared_assets when the supplied catalog matches the requested object. These are previously sourced online originals, already downloaded and converted. Emit one reference with asset_id, instance id, bottom-center position and required mass instead of recreating its geometry or inventing local file paths. Use catalog body/geom/joint/region IDs with {prefix} replaced by the instance id in rubric and process references. Referenced objects and cavity regions are added automatically; do not duplicate them in objects/regions. Catalog coordinates are relative to the instance bottom-center at position [0,0,0]. Preserve original size/orientation; record incompatible requirements as unvalidated rather than silently shrinking, rotating or substituting geometry. The catalog is an asset library, not a fixed scene or task. Keep generating arbitrary surrounding layouts and requested tasks."
    asset_instructions += "\nBefore creating an object asset absent from the installed catalog, request it through asset_requests. Layout floor and wall primitives remain freely generated; other new object assets require discovery. A request pauses scene materialization: keep its current scene fields minimal because they are only a draft until the actual source geometry arrives. Do not invent source dimensions, joint IDs or collision parts. After resolution, use the new grounded catalog, place every requested asset through prepared_assets and clear asset_requests. Never replace a failed lookup/import with procedural geometry. Search is limited to Fuel's supported single-link scanned OBJ sources; openable mechanisms and declared cavities need compatible installed assets or further source preparation. Use existing catalog matches first to avoid unnecessary network/model work. At most two resolution rounds and eight new assets are allowed."
    asset_instructions += "\nOnline asset resolution is " + ("available." if asset_resolver is not None else "disabled; missing assets remain unresolved.")
    history, candidate, previous = [], None, None
    source_rounds, resolved_ids, attempt = 0, set(), 0
    errors = []
    while attempt <= max_repairs:
        if cancelled and cancelled():
            raise GenerationCancelled("World generation cancelled", candidate=candidate, attempts=attempt,
                                      history=history, errors=["Generation cancelled before next model request"])
        request = "Original user request (preserve all requirements):\n" + prompt
        request += "\nPrepared source asset catalog:\n" + catalog
        request += f"\nResource profile: {scene_profile}; materialized physical object count must be between {min_objects} and {max_objects}."
        if scene_profile == "large":
            request += "\nKeep the JSON source compact: aim for at most 40 template parts total, 20 bespoke physical objects, and 30 placement/repetition blocks; these are planning guides, not layout restrictions. Do NOT individually emit hundreds of repeated objects. Compute materialized_count = len(objects) + sum(template_part_count * nx * ny * nz for each instance). For example a 7-part rack repeated [8,5,1] creates 280 real physical objects from just 7 part definitions and one placement block; its last top part is instanceID_7_4_0_top. Use several distinct functional-zone assemblies and avoid tiny meaningless pieces solely to inflate the count. Unless the user's dimensions/detail constraints say otherwise, create an explorable facility at least 100m by 60m, with at least 500 meaningful physical geometries across several connected functional zones. Use reusable model-authored assembly_templates and placement/repetition assembly_instances for repeated infrastructure and furniture. Keep a clear connected main circulation network, short robot decision-level missions that cross at least two nearby zones, distinctive zone geometry, safe starting location and physical workpieces. Large scale is not achieved by empty bounds, distant duplicate props, or thousands of tiny decorative colliders. Preserve the user's requested task; do not add unrelated required chores."
        if previous is not None:
            request += "\nPrevious candidate:\n" + json.dumps(previous, ensure_ascii=False)
            request += "\nRepair these validation failures without dropping requirements:\n" + json.dumps(errors)
        try:
            with _generation_phase(event_callback, "model", attempt + 1):
                raw = client.complete_json(INSTRUCTIONS + asset_instructions, request, GenerationSpec.model_json_schema())
            if event_callback:
                from .llm import audit_value
                event_callback({"type": "model_usage", "usage": audit_value(getattr(client, "usage", {}))})
        except Exception as exc:
            # Preserve previews on transport/model budget failure. The cause is
            # retained for callers, but no credentials/request headers are saved.
            raise GenerationError("World generation request failed; latest candidate retained", candidate=candidate, attempts=attempt+1, history=history, errors=[type(exc).__name__]) from exc
        previous = raw
        try:
            plan = GenerationSpec.model_validate(raw)
            if plan.asset_requests:
                from .asset_resolution import AssetResolutionError, extend_asset_library
                from .collision_preparation import CollisionPreparationCancelled
                try:
                    if asset_resolver is None:
                        raise AssetResolutionError("Online asset resolution is disabled; install a source library or enable it")
                    if source_rounds >= 2 or len(resolved_ids) + len(plan.asset_requests) > 8:
                        raise AssetResolutionError("Online asset resolution exceeded two rounds/eight assets; no world was substituted")
                    if event_callback:
                        event_callback({"type": "asset_requests", "requests": [r.model_dump(mode="json") for r in plan.asset_requests]})
                    with _generation_phase(event_callback, "source_assets", attempt + 1):
                        asset_library = extend_asset_library(asset_library, plan.asset_requests, asset_resolver,
                                                             [item["asset_id"] for item in json.loads(catalog)])
                        catalog = json.dumps(prepared_asset_catalog(asset_library), separators=(",", ":"))
                    resolved_ids.update(r.asset_id for r in plan.asset_requests)
                    source_rounds += 1
                    errors = ["Requested source assets are now in the catalog. Plan with their actual dimensions/IDs, use all requested assets, and clear asset_requests."]
                    continue
                except CollisionPreparationCancelled as exc:
                    raise GenerationCancelled("Asset resolution cancelled", candidate=candidate, attempts=attempt+1,
                                              history=history, errors=["Asset resolution cancelled"]) from exc
                except Exception as exc:
                    from .artifacts import public_error
                    raise GenerationError("Source asset resolution failed; no procedural fallback", candidate=candidate,
                                          attempts=attempt+1, history=history, errors=[public_error(exc)["message"]]) from exc
                finally:
                    if event_callback:
                        from .llm import audit_value
                        event_callback({"type": "model_usage", "usage": audit_value(getattr(client, "usage", {}))})
            with _generation_phase(event_callback, "assets", attempt + 1):
                spec = WorldSpec.model_validate(plan.model_dump(mode="json", exclude={"prepared_assets", "asset_requests"}))
                assembly_errors = []
                missing = resolved_ids - {item.asset_id for item in plan.prepared_assets}
                if missing:
                    assembly_errors.append("Requested source assets were dropped: " + ", ".join(sorted(missing)))
                try:
                    spec = materialize_prepared_assets(plan, max_objects=max_objects, event_callback=event_callback, asset_library=asset_library)
                    spec = expand_assemblies(spec, max_objects=max_objects)
                except (ValueError, ValidationError) as exc:
                    assembly_errors.append(str(exc))
            spec.source_prompt = prompt
            if PHYSICAL_DEFAULTS not in spec.assumptions:
                spec.assumptions.append(PHYSICAL_DEFAULTS)
            for cycle in spec.process_cycles:
                disclosure = f"Process {cycle.id} is a timed state-machine abstraction ({cycle.duration:g}s); internal material transformation is not physically simulated."
                if disclosure not in spec.assumptions:
                    spec.assumptions.append(disclosure)
            candidate = spec
            if on_candidate:
                with _generation_phase(event_callback, "preview", attempt + 1):
                    on_candidate(spec)
            if event_callback:
                event_callback({"type": "candidate", "attempt": attempt+1, "spec": spec.model_dump(mode="json")})
            with _generation_phase(event_callback, "static_validation", attempt + 1):
                errors = assembly_errors + validate_spec(spec, asset_root=asset_root)
                if len(spec.objects) < min_objects:
                    errors.append(f"Scene has {len(spec.objects)} physical objects but the explicit minimum is {min_objects}; add meaningful physical detail using generated assemblies without changing the requested task/layout")
            if cancelled and cancelled():
                raise GenerationCancelled("World generation cancelled", candidate=candidate, attempts=attempt+1,
                                          history=history, errors=["Generation cancelled before physical validation"])
            if not errors and validator is not None:
                with _generation_phase(event_callback, "physical_validation", attempt + 1):
                    errors.extend(validator(spec))
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_url=False)]
        history.append({"attempt": attempt+1, "errors": list(errors)})
        if event_callback:
            event_callback({"type": "validation", **history[-1]})
        if not errors:
            return candidate
        attempt += 1
    raise GenerationError("World generation exhausted its repair budget", candidate=candidate, attempts=max_repairs+1, history=history, errors=errors)
