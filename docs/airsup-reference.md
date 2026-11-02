# Airsup production-area reference import

This importer executes the actual procedural constructors from [Airsup Lab](https://github.com/AirsupHQ/airsup-lab/tree/e21864bda5a614ffae8d0a835518be8e2e571dce), pinned to `e21864bda5a614ffae8d0a835518be8e2e571dce`. It is a source-derived reference scene, not a model's approximation of a screenshot and not a bundled onboarding world.

The selected scene is the five-station drone line: Frame, Motors, Electronics, Test and Pack. The export also selects the immediate room context, including the original orange-and-blue storage racks, cardboard boxes, wall, floor, ceiling fixtures and nearby decorations. Meshes entirely outside that area are excluded; floor and wall planes are clipped to the selected area with their UV coordinates preserved.

## Reproduce the export

Node.js and the Python project environment are required. The Node dependencies are only for this optional source importer, not for running Robot Worlds.

```sh
git clone --filter=blob:none --no-checkout https://github.com/AirsupHQ/airsup-lab.git /tmp/robot-worlds-airsup-source
git -C /tmp/robot-worlds-airsup-source checkout e21864bda5a614ffae8d0a835518be8e2e571dce
npm --prefix /tmp/robot-worlds-airsup-source install --no-save --ignore-scripts --omit=dev three@0.185.1 esbuild@0.28.2 @napi-rs/canvas@1.1.0
node tools/import_airsup_reference.mjs --source /tmp/robot-worlds-airsup-source
```

The tool checks the Git revision and verifies every executed source module against its pinned Git blob. It uses a real Canvas2D implementation for upstream screen and surface textures. The only source adjustment captures the original studio environment scene before GPU environment-map baking; it does not modify geometry constructors. The UUID seed is fixed, the source simulator uses its own seeded random generator, and the source line is warmed for 40 real seconds as in upstream `main.ts`, then frozen at 800 line seconds.

Use `--output PATH`, `--warm-seconds 0..120` or `--no-room` to change explicit export settings. The default output is `.robot-worlds/references/airsup-production-line/`, which is outside packaged assets.

## Files and coordinate contract

| File | Contents |
|---|---|
| `world.json` | Robot Worlds specification with static source meshes and a separate five-station aisle-navigation task. |
| `assets/*.obj` | One source mesh or visible instance per file, with source transforms baked and geometry centered on its declared object position. |
| `reference-manifest.json` | Source hashes, dependency versions, original hierarchy paths, station coordinates, camera shots, material/texture metadata, exclusions and collision limitations. |
| `visual-scene.json` | Three.js ObjectLoader scene retaining selected source geometry, lights, PBR materials and CanvasTextures, including nonphysical planes. |
| `environment-scene.json` | Actual source studio scene; the viewer can bake it with `PMREMGenerator.fromScene(scene, 0.02)` using its WebGL renderer. |
| `assets/texture-*.png` | Original procedural Canvas2D textures at export time. |
| `AIRSUP-LICENSE.txt` | Complete upstream MIT license and copyright notice. Each OBJ embeds the full notice, so importing an individual mesh preserves attribution; the visual scene carries it in `userData`. |

Source coordinates use Y-up. The transform first subtracts the original line mount `[9.7, 0, -0.3]`, then maps `(x,y,z)` to `(x,-z,y)`. This is a proper rotation, preserving handedness, dimensions, winding and relative placement. Robot Worlds positions are mesh bounding-box centers; OBJ vertices are local to those centers. Sizes are full XYZ extents. Source material colors remain linear RGB, with source values and sRGB display colors in the manifest. Values above one are retained as emissive metadata because the physical schema's diffuse RGB range is zero to one.

The default export contains **352 visual meshes, 340 physical meshes, 60,159 triangles and eight textures**. Twelve zero-thickness meshes remain visual-only. Eight solids crossing the outer crop boundary are excluded rather than remodeled. The 6.7 m bench, source station X positions `[-2.3,-1.2,-0.1,1,2.1]` and all retained component geometry come from source.

The matching source `line` camera, already transformed in the manifest, uses position `[0.2,-5,2.65]`, target `[0.1,0.35,1.32]` and a 34° field of view. The visual scene preserves source glow layer 2, so a Three.js camera must enable that layer to show screen faces and effects. Retain the exported source lights and bake the exported studio scene for matching reflections. Source custom surface shaders, ambient occlusion, bloom and font availability still affect a pixel comparison.

OBJ comments contain a bounded numeric material record:

```text
# robot_worlds_material {"metalness":0.8,"roughness":0.42,"opacity":1,"emissive":[0,0,0]}
```

The browser can use these values for actual OBJ geometry. Physics ignores the comment. Because the comment is in the OBJ bytes, asset hashes also cover these appearance parameters.

## Physical boundary and checks

The 340 physical meshes compile in native MuJoCo with the real TIAGo++ model, producing 440 geometries. Every export also parses its serialized BufferGeometry and matrices back and compares all 352 component centers and extents to the source-derived manifest, rejecting errors above 0.00001 m. These checks verify mesh import, placement preservation and model compilation; they do not prove the source factory operates physically or that the navigation task is complete.

- Each retained solid uses a static mesh with a per-mesh convex collision hull. A whole building is never replaced by one hull. Concave interiors within an individual source component may still be filled by that approximation.
- The source conveyor, cutting spindle, assembly arms and discrete production simulation are frozen reference state. They are not robot controllers or simulated manufacturing physics in this import.
- Source screen faces, floor/wall planes and zero-thickness effects remain in `visual-scene.json`; native physics uses its own floor and does not silently invent collision thickness for them.
- PBR properties, source procedural-detail parameters and textures remain inspectable. MuJoCo's material rendering does not reproduce the original custom shaders, transparency and postprocessing.
- Original static assembly parts overlap by design. The initial generic AABB validator reports 101 static overlap pairs, so this reference must not be labeled a fully validated runnable world solely because mesh compilation succeeded. Preserve geometry and disclose validation status; do not move source parts to obtain a pass.

The upstream [line builder](https://github.com/AirsupHQ/airsup-lab/blob/e21864bda5a614ffae8d0a835518be8e2e571dce/src/line/line.ts), [room](https://github.com/AirsupHQ/airsup-lab/blob/e21864bda5a614ffae8d0a835518be8e2e571dce/src/scene/room.ts), [camera](https://github.com/AirsupHQ/airsup-lab/blob/e21864bda5a614ffae8d0a835518be8e2e571dce/src/camera.ts) and [MIT license](https://github.com/AirsupHQ/airsup-lab/blob/e21864bda5a614ffae8d0a835518be8e2e571dce/LICENSE) are the reference sources.
