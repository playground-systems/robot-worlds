# RoboCasa source-only kitchen assembly

`tools/assemble_robocasa_kitchen.py` composes the selected kitchen using original
RoboCasa fixture constructors and placement sampling. It requires an **already
acquired, ignored source snapshot and isolated environment**. Acquisition,
source/assets, dependencies and generated XML are not distributed in this PR.
This is not a fresh-checkout installer or native-runtime acceptance result.

## Preserved inputs

GPT-6 Astra with high reasoning selected unchanged layout004/style004 L-shaped
Island Farmhouse, `clutter_mode=1`, seed `0`. The source-disabled blender, dish
rack, electric kettle and stand mixer stay disabled. No task objects are added.

| Input | Revision or inventory |
| --- | --- |
| RoboCasa code/configuration | `3443cdf3dd20f9ae8ed41adba442059b506891d0` |
| robosuite XML support | `5ce6643f3092639d08f7b0f90ed1c6a84f50552c` |
| Original asset subset | 352 files, 66,312,975 bytes |
| `acquired-source-files.json` SHA-256 | `83b2f40257cec0304cd1b99b2e446bfa5837302e3d7e108c445bd29721313771` |

The snapshot defaults to
`.robot-worlds/existing-scenes-2026-11-02/robocasa-kitchen`. It must contain the
original `source/robocasa/` tree/assets, `github-{commit,tree}.json`,
`acquired-source-files.json`, `assembly-audit/robosuite-source/`,
`assembly-audit/robosuite-{commit,tree}.json`, `assembly-config/` and
`assembly-env/`. The two scene YAMLs are
`scenes/kitchen_layouts/test/layout004.yaml` and
`scenes/kitchen_styles/test/style004.yaml` beneath the original asset directory.

The measured environment is Python 3.13, NumPy 2.5.4, Pillow 12.3.0 and pure
Python PyYAML 6.0.3. NumPy/Pillow are reused through an explicit read-only `.pth`
binding; cached original PyYAML Python files are in the isolated environment.
No compiled package was installed. These versions differ from RoboCasa's full
published environment requirements; the retained dependency audit is a source
assembly inventory, not a validated full installation recipe.

## Invocation and relocation

Run from the checkout with the acquired snapshot. `--source-root` overrides the
checkout-relative default. `--output` must name a new directory outside the
preserved source/audit/configuration/environment trees.

```sh
ROBOCASA_SNAPSHOT="$PWD/.robot-worlds/existing-scenes-2026-11-02/robocasa-kitchen"
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONNOUSERSITE=1 \
  "$ROBOCASA_SNAPSHOT/assembly-env/bin/python" \
  tools/assemble_robocasa_kitchen.py \
  --source-root "$ROBOCASA_SNAPSHOT" \
  --output "$ROBOCASA_SNAPSHOT/assembly-output-local"
```

After moving a preserved environment, repair its dependency binding to the
**existing** NumPy/Pillow site-packages directory before assembly. This mode
uses only the standard library, installs nothing and saves a new relocation
receipt. It cannot repair an unavailable base interpreter or missing packages.

```sh
python3 tools/assemble_robocasa_kitchen.py \
  --source-root "$ROBOCASA_SNAPSHOT" \
  --repair-environment-only \
  --dependency-site "$PWD/.venv/lib/python3.13/site-packages"
```

Original constructors emit absolute asset references. Regenerate XML into a
new output directory after relocation; preserve historical XML/reports unchanged.

## Source execution and limits

The tool verifies all 81 cached original Python files against pinned Git blob
identities and all 352 asset hashes before/after assembly. Explicit namespace
loading avoids eager task/robot registries. Audited AST extraction executes
unchanged original helper/class/method bodies without unused native imports;
there are no dependency mocks or replacement placement rules. The original
`KitchenArena`, texture substitutions, XML/object classes, `Task` merge methods
and 12 fixture-placement statements from `Kitchen._load_model` lines 663–794
supply the result. The source Generator and legacy NumPy annotation-color RNG
both use seed `0`. Every extraction records source/AST hashes. Official
`macros_private.py` disables Numba. Unsupported full-environment sampler retries
fail explicitly; no geometry/placement repair occurs.

The 30-second source-stage alarm starts after dependency imports. The tool
checks that MuJoCo, Numba, SciPy, robots and controllers were not imported.
Robot-dependent camera initialization, task objects, full environment/reset
initialization and runtime fixture-state updates are excluded. Source joints
being present does not establish their native initialization or behavior.

## Recorded result

Current output beneath the snapshot:
`assembly-output-relocated-v4/kitchen_layout004_style004_clutter1_seed0.xml`.
SHA-256: `53268191d328f1a8bf3676fb30d37bdbede60e6e9f85475a432dd4b2790ef2dc`.

| Source-only check | Result |
| --- | --- |
| Fixtures / sampled placements / sampler calls | 83 / 18 / 80 |
| Bodies / joints / geoms | 248 / 67 / 1,749 |
| Asset references / missing references | 543 / 0 |
| XML size / unchanged original assets | 717,830 bytes / 352 |

`relocation-verification.json` confirms byte-identical XML versus preserved v1
after replacing only its old absolute source-root prefix. Fixture metadata and
placement/sampler records are byte-identical. Other evidence:
`assembly-report.json`, `source-execution-audit.json`, `source-fixtures.json`,
`source-fixture-placements.json`. Prior attempts/reports are retained unchanged.
No native compilation, rendering, collision/physics validation, task trial or
fidelity acceptance was performed. Any scene change remains an Astra decision.

## Sources and notices

RoboCasa [code is MIT](https://github.com/robocasa/robocasa/blob/3443cdf3dd20f9ae8ed41adba442059b506891d0/LICENSE);
its [assets/data are CC BY 4.0](https://github.com/robocasa/robocasa/blob/3443cdf3dd20f9ae8ed41adba442059b506891d0/README.md#license).
Retain RoboCasa and the original archives' NVIDIA/Lightwheel
[collection credits](https://huggingface.co/datasets/nvidia/PhysicalAI-Robotics-Manipulation-Objects-Kitchen-MJCF/blob/420a04af939c34873e6839a586b70844baf28aab/README.md).
robosuite has its own [MIT license](https://github.com/ARISE-Initiative/robosuite/blob/5ce6643f3092639d08f7b0f90ed1c6a84f50552c/LICENSE).
License copies, archive notices and `license-provenance.json` remain with the
snapshot. Original fixture-stage source:
[`Kitchen`](https://github.com/robocasa/robocasa/blob/3443cdf3dd20f9ae8ed41adba442059b506891d0/robocasa/environments/kitchen/kitchen.py),
[`KitchenArena`](https://github.com/robocasa/robocasa/blob/3443cdf3dd20f9ae8ed41adba442059b506891d0/robocasa/models/scenes/kitchen_arena.py),
[placement helpers](https://github.com/robocasa/robocasa/blob/3443cdf3dd20f9ae8ed41adba442059b506891d0/robocasa/utils/env_utils.py).
