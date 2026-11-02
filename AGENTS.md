# Scene authoring

Delegate all modeling, asset selection, material, physical-property inference,
layout and placement decisions to GPT-6 Astra with high reasoning or Claude
Opus 5.5. Record the configured model and reasoning effort with the authored
plan. Other agents handle imports, provenance, SDK wiring and validation;
return geometry or placement repairs to an authorized scene author.

Reuse existing simulation-ready environments and supplement them with NVIDIA
SimReady assets where useful. Simulation-ready is a capability requirement,
not an NVIDIA-only source restriction. Preserve source visual meshes,
materials, rigid bodies, collision geometry and articulation; validate the
combined scene in our runtime and retain each source's license and notices.

# Asset sourcing

Before creating a new object asset, search for an existing asset online. Reuse or
adapt a suitable result before designing geometry from scratch. This is an
internal agent step; it does not require per-object user confirmation.

Record the query, catalogs searched, source URL, pinned revision, license and
reasons for rejecting candidates. Check the physical features required by the
task: an openable microwave needs a door joint and usable cavity; a bowl needs a
concave interior. A network failure or unsupported importer is an unresolved
lookup/import problem, not proof that an asset cannot be found.

For bowl/microwave work, read `docs/household-assets.md` and the adjacent manifest
before doing another search: suitable original source assets are already pinned.
Use `robot_worlds.source_asset` for new asset pipelines. Keep original visual
meshes/textures when adapting physics, and disclose conversion changes. Simple
layout primitives such as floors and walls do not require an asset search.
