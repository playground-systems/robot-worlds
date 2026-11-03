# Factory v3 source acquisition

Acquired **only the 15 IDs selected by Astra high** in [the source-selection record](factory-v3-source-selection-2026-11-02.json). This is original-source acquisition and JSON inspection, not an imported scene, geometry adaptation, visual approval or physical-affordance validation.

Run from a checkout with standard-library Python:

```sh
python3 tools/prepare_factory_v3_sources.py
```

The tool supports a fresh checkout: it fetches the pinned optimized manifest if unavailable, verifies its known SHA-256 and treats prior candidate metadata as an optional cache. It checks the author's selected IDs, byte sizes, bounds and physics counts against publisher metadata. Downloads have a persistent **40,000,000-byte cap per output cache**, including interrupted requests; cache hits recheck hashes. Files use checked parents, unique temporary files and atomic publication. It neither installs packages nor decodes meshopt, renders, changes geometry or runs simulation.

The completed cache is `.robot-worlds/factory-v3-sources-2026-11-02/`. `source-manifest.json` records 15 resolved sources, **13,960,236 GLB bytes** and **14,481,028 downloaded bytes** in the cumulative ledger. A preliminary metadata API probe was under 1 KB outside that ledger. The exact optimized publisher pin is [`e52172290393dde80e8d383861db94b8d0e92ec3`](https://huggingface.co/datasets/codepawl/simready-glb/tree/e52172290393dde80e8d383861db94b8d0e92ec3); original NVIDIA metadata is pinned separately to [`4763fbfaa4f8e97658e68b57d01b4c86659e08b5`](https://huggingface.co/datasets/nvidia/simready-assets/tree/4763fbfaa4f8e97658e68b57d01b4c86659e08b5). The optimized publisher does not declare its exact upstream NVIDIA revision, so this is not a verified derivation chain between those pins.

Each `sources/<ID>/` retains `model.glb`, `physics.usda`, `instances_mujoco.usda`, `packaging.json`, `LICENSE.txt`, `NOTICE.md` and `glb-inventory.json`. Where published, `mujoco-enabled.usda` is retained too. The manifest binds raw source files to URLs, revisions, lengths and SHA-256. Fourteen GLBs also match publisher **LFS SHA-256**; the small bracket is a regular Git object, checked with **Git blob SHA-1** (`sha1("blob <size>\0" + bytes)`) and separately recorded SHA-256. Those hash types are explicitly distinguished.

All 15 original package declarations and retained licenses say **CC-BY-4.0**. Notices credit NVIDIA and the CodePawl conversion, link sources and disclose that the acquired bytes remain unchanged. No original dependency or prepared library was overwritten. The retained original material maps remain embedded in each GLB. Actual JSON inventory totals are **54 motion bodies, 54 collider nodes and 39 joint definitions**; two joints are fixed cart-bin attachments. Unique draft constraint definitions can be shared, so their array length is not the number of physical joints.

| Exact selected ID | GLB bytes | Bodies / joints / colliders |
|---|---:|---|
| `equipment_rolling_tool_chest_cabinet_combo_d01` | 3,370,652 | 21 / 20 / 21 |
| `equipment_utility_cart_e01` | 2,817,008 | 11 / 10 / 11 |
| `storage_toolbox_standard_hardCase_d01` | 2,195,628 | 4 / 3 / 4 |
| `crate_blue_a01` | 526,916 | 1 / 0 / 1 |
| `crate_white_a20` | 539,756 | 1 / 0 / 1 |
| `dishware_plastic_tray_a01` | 175,780 | 1 / 0 / 1 |
| `handTool_wrench_combination_a01` | 497,948 | 1 / 0 / 1 |
| `handTool_pliers_combination_a01` | 617,556 | 2 / 1 / 2 |
| `instrument_caliper_a01` | 192,224 | 2 / 1 / 2 |
| `engineComponent_idler_pulley_a01` | 616,564 | 1 / 0 / 1 |
| `brakeSystem_brake_master_cylinder_b01` | 747,224 | 1 / 0 / 1 |
| `engineComponent_spark_plug_b01` | 258,604 | 1 / 0 / 1 |
| `hardware_bracket_largeCorner_a01` | 45,428 | 1 / 0 / 1 |
| `factory_bolt_m20_tight` | 316,972 | 1 / 0 / 1 |
| `equipment_bench_vise_a01` (author marked conditional) | 1,041,976 | 5 / 4 / 5 |

Actionable source caveats for Astra high:

- **Mass evidence is now more complete than the initial candidate audit.** Values absent from the common `physics.usda` can be explicitly authored in `instances_mujoco.usda`; the GLB often rounds those values. Examples from original instance layers: cart base 8.958625 kg; deep crate 2.8367376 kg; shallow crate 1.7019488 kg; tray 0.41487625 kg; wrench 0.82821196 kg; master cylinder 3.19966 kg; pulley 0.13533968 kg; spark plug 0.07230999 kg; bracket 0.0066213747 kg; M20 bolt 0.010707934 kg. Vise base and handle are explicitly 5 and 0.26828215 kg in that layer. Preserve the literal original values; these are publisher-authored physics, not independently measured masses or new engineering estimates.
- The chest retains eight drawer slides, cover/handle/lock hinges and wheels/casters; the case retains top/handle/lock hinges; the cart retains eight wheel/caster hinges plus two fixed bin attachments. Pliers and caliper retain their real joint declarations. **No screw-to-jaw coupling or clamping retention has been established for the conditional vise.** Full pivots, axes, units and hierarchy remain in the raw source/inventory, without converting them to a new model.
- Materials include source normal and metallic/roughness maps. Their presence is verified in JSON; texture appearance and visual fidelity were not judged. Original exported bounds are retained in the author selection and publisher manifest; no source was resized or placed.
- Convex-hull colliders do not establish empty drawer/case/crate/tray cavities. **Interior, handle and swept clearances remain unmeasured** because vertices were not decoded and no rendering/contact check was run. JSON parsing and declared dimensions must not be presented as measured cavity evidence.
- The pinned bracket and M20-bolt sources return 404 for the optional `payloads/MuJoCo/enabled.usda` path. Their original `physics.usda`, `instances_mujoco.usda`, licenses and GLBs are present. The manifest records the missing override explicitly; this does not establish native compatibility or incompatibility.

Validation: `tests/test_factory_source_acquisition.py` has **13 passing offline tests** for cumulative byte limits (including failed/pending requests), fresh-checkout catalog retrieval and hash reuse, symlink/path isolation, immutable cache bytes, LFS versus Git-object hashes, GLB chunk/dependency validation and node-hierarchy integrity. Ruff passes for the tool and focused tests. No broad tests, CI, native physics, policy or model calls were run.
