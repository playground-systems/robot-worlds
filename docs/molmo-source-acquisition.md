# MolmoSpaces FloorPlan201 source acquisition

Astra high selected the existing furnished living-room scene unchanged. This
records source acquisition, not a Robot Worlds import, native physics check or
visual-quality approval. No packages were installed and no model, policy or CI
run was used for acquisition.

## Reproduce the acquisition

The script pins the publisher's dataset revision, scene version
`20251217_with_occupancy`, THOR object version `20251117`, and license-resolver
source revision. It uses Python's standard library and an existing system
`zstd` executable. Read its constants before selecting a different source;
selection and modeling decisions belong to Astra high or the designated model.

```sh
.venv/bin/python tools/prepare_molmo_scene.py \
  --output .robot-worlds/existing-scenes-2026-11-02/molmo-living-room \
  --max-download-bytes 650000000
```

The initial 50 MB budget could obtain the scene but not its dependencies:
publisher range-download units are whole category archives. The authorized
650 MB continuation retrieved exactly the seven categories referenced by this
scene, sequentially. Compressed archives are retained and hashed; extraction
writes only referenced THOR files and license notices. The script checks disk
space, range responses, paths, member sizes and aggregate extraction/download
limits. Existing originals must match their recorded hashes to be reused.

## Recorded result

- **564,226,516 bytes** downloaded, including metadata and original archives.
- **527** original scene files plus **267** unique referenced THOR files,
  **30,426,700 bytes** of extracted original source in total.
- **321** XML file references resolve to **320** unique files; one external
  reference is repeated. No original file path, geometry, pose or material changed.
- Literal source XML inventory: **151 bodies, 32 free joints and 7 other joints**
  (2 slides, a laptop lid hinge and 4 box flap hinges). This is not a claim that
  every prop is movable, graspable or reachable by our robot.

The ignored acquisition directory contains `source-selection.json`,
`download-ledger.json`, `scene-files.json`, `raw-scene-inventory.json`,
`dependency-manifest.json` and `ATTRIBUTION.md`. The dependency manifest records
per-file hashes, archive origins, license evidence and `sources_resolved` status.
Its original entry point is `source/scenes/ithor/FloorPlan201_physics.xml`;
source references resolve into the adjacent `source/objects/thor/` tree.

## Licenses and integration limits

The publisher's [license resolver](https://github.com/allenai/molmospaces/blob/22ee21c0fb1d6f2bbccd4a7d5ee6f6dccc488bf1/molmo_spaces/utils/license_utils.py)
declares **CC BY 4.0** for scene composition and separately for THOR objects;
all external dependencies here are THOR, not Objaverse. Attribution, license
links and change notices must survive any later conversion or distribution.
The retained resolver code has its separate **Apache 2.0** notice. Evidence
copies are under `metadata/`; this records the publisher's declarations rather
than claiming an independent legal clearance of every original texture.

The existing `import_mjcf_asset` cannot ingest the complete scene's free joints
and mesh collision geometry without further engineering. This source XML has
no explicit inertial elements; its authored geometry/defaults remain intact.
Original source XML requests a 16,384-pixel shadow map. A subsequent single-frame
source preview used Astra's recorded camera and rendering overrides: 640 × 360,
1,024-pixel shadows and no multisampling. The source compiled with 152 bodies
(including world), 39 joints and 760 geoms; no physics steps or policy ran.
The process took 1.30 seconds and peaked at approximately 435 MB RSS. Original
XML hashes remained unchanged; evidence is in `source-preview-evidence.json`.

Astra inspected the still and found its angular furniture, coarse textures and
weak daylight below the user's Homebody reference. FloorPlan201 remains a
provisional interaction-capable base, not an accepted high-fidelity scene.
See [the visual review](scene-photo-references-2026-11-02.md). Robot Worlds
integration, native contacts and task execution remain unverified.
