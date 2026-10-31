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
