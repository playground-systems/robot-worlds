# Portable native source scenes

`robot_worlds.source_scene_bundle` packages an existing MJCF scene without
converting it to Robot Worlds primitives. It preserves all source XML bytes
except explicit asset file-attribute values, including bodies, joints,
defaults, inertials, contacts, options and material definitions. Dependency and
license files retain their original filenames and bytes.

```python
from robot_worlds.source_scene_bundle import (
    package_source_scene,
    verify_source_scene_bundle,
)

bundle = package_source_scene(
    xml_path=source_root / "assembled/kitchen.xml",
    source_root=source_root,
    output_dir=output_dir,  # Must not exist.
    provenance={
        "source_url": "https://example.invalid/original-source",
        "revision": "the-acquired-source-revision",
        "licenses": [
            {"path": "LICENSE.txt", "license": "the-source-license"},
        ],
    },
)
print(bundle.xml_path, bundle.manifest_path, bundle.manifest["id"])
verified = verify_source_scene_bundle(output_dir)
```

`source_root` explicitly bounds the input XML, dependencies and retained notices.
Provenance must contain a nonempty `licenses` list with caller-supplied license
declarations and source-relative notice paths. Other JSON provenance, such as
source pins, attribution, assembly hashes and author decisions, is retained.
The packager does not determine licensing or choose/modify scene content.

The bundle contains the runnable XML under its original basename, an exact
original XML copy under `original/`, source notices under `notices/`, copied
assets and `source-scene.json`. The manifest records source-relative paths,
original/bundled hashes, every rewritten reference, provenance and an integrity
identity. Verification works after removing the original source directory;
manifest identities are integrity checks rather than signatures.

Asset resolution preserves the original relative `compiler` directory settings.
For example, with `meshdir="meshes/"`, a rewritten `file="assets/panel.obj"`
loads `meshes/assets/panel.obj` within the bundle. `assetdir` fallback and
`texturedir`/`meshdir` precedence follow the
[MuJoCo compiler reference](https://mujoco.readthedocs.io/en/stable/XMLreference.html#compiler).
Rewriting uses lexical attribute spans, so it does not serialize or normalize
the rest of the source XML.

The supported dependency closure is explicit native MJCF mesh, heightfield,
skin and texture file references, including six-sided texture attributes.
It does not reinterpret OBJ material libraries, synthesize missing files or
execute resource providers. Includes, attached models, plugins, XML entities,
processing instructions, non-UTF-8 input, path stripping, escaping paths and
absolute/escaping compiler directories fail explicitly. Symlinks cannot resolve
outside the declared source or bundle root. Inputs are bounded to 8 MiB XML,
64 MiB per dependency, 8,192 copied files and a 256 MiB complete bundle.

The acquired RoboCasa layout004/style004 seed-0 fixture export packages to
`native-source-bundle-v1` beneath the ignored RoboCasa snapshot: 543 references,
308 distinct asset files and eight source notices. Its bundled XML SHA-256 is
`4ae59383d40505f9d11d179f388c5b7a327169238e323b58b57539e38a1d6df4`.
Two original outlet/light-switch OBJ files mention uncached `material.mtl`
sidecars; this is not a generic OBJ/MTL visual-export completeness claim.
Original explicit MJCF materials/textures remain unchanged.

Packaging and offline tests do not compile or simulate the scene. Native
compatibility, initial state, physical interaction and rendered fidelity require
separate validation; the original source's two option elements and all fixture
joints are retained unchanged.

## Browser inspection

`export_source_scene_preview` compiles a derived copy with native texture
allocation detached and mesh compilation serialized. Geometry, bodies, joints,
inertials and physics options remain intact. Original images are copied without
conversion; original material bindings accompany the compiled geometry. The
source bundle is verified again after export.

```python
from robot_worlds.source_scene_preview import export_source_scene_preview

manifest = export_source_scene_preview(
    bundle.directory,
    data_dir / "source-scenes" / "kitchen",  # New directory only.
    name="Source kitchen",
    views=authored_camera_views,
)
```

The camera records require `id`, `eye_m`, `target_m`, `up` and
`vertical_fov_degrees`. The repository CLI accepts the recorded Astra-high
camera decision file:

```sh
python tools/export_source_scene_preview.py \
  --bundle /path/to/verified-bundle \
  --output /path/to/data/source-scenes/kitchen \
  --decisions docs/robocasa-preview-author-decisions-2026-11-02.json \
  --name "RoboCasa Farmhouse Kitchen"
```

Full scenes require an external RSS/wall-time watchdog; the CLI's CPU limit
alone does not bound memory. The verified kitchen export used a 600 MiB RSS,
60-second wall limit and completed in 2.90 seconds with 626,688,000 bytes peak
RSS on this Mac. This is one measured export, not a universal performance bound.

Run the existing server with that data directory and open `/?source=kitchen`.
The app loads original visuals once, displays source camera views, and offers
limited hinge/slider inspection. Selecting a part finds its owning mechanism;
**Hide panels** expands the canvas, and Escape restores the workspace. Free,
ball and multiple-joint bodies remain at their native initial pose where the
browser cannot inspect them faithfully. Their native physics is preserved.

The view uses source Phong coefficients with a scoped diffuse/ambient
normalization correction. Original cube/2D mapping is retained where supported.
The inspector lists missing mappings, source reflections and other differences.
Exact native-render equivalence is unclaimed; source material values above the
documented range and appliance edge artifacts still need investigation.

Source inspections have separate read-only routes and cannot be evaluated as
Robot Worlds tasks. The export calls `mj_forward`, never `mj_step`, and does not
add a robot, settle objects, prove grasping or grade a task. See the
[recorded visual verdict](robocasa-browser-visual-review-v2-2026-11-02.json) and
[verification evidence](source-scene-inspection-evidence-2026-11-02.json).
