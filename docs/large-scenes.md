# Generate a large scene

The large profile guides the model toward a facility around 100 × 60 metres, several connected functional zones, and at least 500 physical geometries. The user's requested environment and task still determine the layout. This is a generation target, not a guarantee that every request will validate or that a policy can complete every mission.

```python
from robot_worlds import generate

world = generate(
    "An extensive factory with receiving, storage, assembly and inspection. "
    "The task is to collect the specified part at receiving and carry it "
    "to an inspection fixture in the neighboring zone.",
    scene_profile="large",
    min_objects=500,
    max_objects=5000,
    client_options={
        "max_output_tokens": 16384,
        "max_cost_usd": 5,
        "max_seconds": 600,
    },
)
```

The model invents reusable `assembly_templates`, then places them with `assembly_instances`. Templates are ordinary physical parts in local coordinates: boxes, spheres, cylinders, imported meshes, and articulated mechanisms. There is no fixed warehouse/factory asset catalogue. Unique task objects and fixtures can still be specified directly. Source templates and all materialized physical objects are retained in the bundle.

An instance has a translation, a quarter-turn rotation, and optional XYZ repetition counts and world-space spacing. Expansion creates stable IDs of the form `instance_ix_iy_iz_part`; for example, `storage_7_4_0_top`. Rubrics and processes bind those IDs just like directly generated objects. Local parent links, joint anchors and joint axes are transformed together. Existing materialized objects must agree with their source assembly; conflicting stale edits are rejected.

An explicit `min_objects` (default zero) turns a requested detail count into a repair check; it does not prescribe the layout. The example requires at least 500. The default resource budget is 5,000 physical objects, with an explicit implementation ceiling of 10,000. This bounds expansion memory and does not prescribe a floorplan. Expansion checks counts before allocating repeated geometry. The collision validator uses a spatial hash broadphase and bounded overlap diagnostics. Runtime meshes use native convex-hull collision; rotated mesh instances currently require a pre-rotated mesh asset and `quarter_turns=0`. Primitive assemblies support all four quarter turns.

Validation covers schema, IDs, bounds, initial overlaps, robot starting clearance, physical compilation/reset, and available rubric measurements. It does not certify global route connectivity, arm reachability, task solvability, interpretation of every natural-language requirement, or hardware equivalence. Missing assets or failed checks retain a preview and generation diagnostics; they do not silently produce a ready world. Repairs keep the original request in every model call. Cancellation is checked between model requests and before physical validation; an in-flight HTTP request still uses its bounded timeout.

An internal engineering test constructs 561 physical objects across a 100 × 60 metre scene, compiles them with the native TIAGo++ model, renders an overview, and steps MuJoCo on a Mac. This demonstrates the current runtime path at that scale. It is separate from evidence that a live model generated a useful scene, or that the task has a successful policy. The fixture is test-only and is never returned as a fallback or onboarding world.

## Live model result

On 2026-10-30, a live `gpt-6-astra` request with low reasoning effort generated a 100 × 60 × 6 metre facility containing **884 materialized physical objects**. The explicit `min_objects=500` target was enforced. Schema, geometry/rubric checks, native physics reset, initial-rubric checks and rendering all passed; the immutable world ID is `03a174e50763a50c8dd969732f9ee382fe2a2faa1d55ee237bb256df9283aa56`.

The successful attempt used one model call, 3,221 reported input tokens and 7,437 reported output tokens. The recorded estimated cost for that attempt was $0.412105 at the pinned pricing profile, not a provider invoice. It used a 480-second per-request timeout and a $5 cap. A preceding separate 180-second attempt timed out before returning a candidate and retained its conservative reservation; the successful-attempt cost does not include that earlier attempt.

This confirms live compact-template generation and physical compilation at the requested scale. Global task reachability, successful manipulation across its zones, and the completeness of the natural-language interpretation were not established by those checks.
