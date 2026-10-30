# NVIDIA validation — 2026-10-30

Native MuJoCo simulation, both supported robots, NVIDIA EGL rendering, and spawned evaluation workers were exercised on a temporary Linux RTX 4090 machine. **Physics ran on the CPU.** NVIDIA rendered the camera and replay images; this is not a GPU physics implementation.

All experiments ran remotely under a process-lease idle guard. No model credentials were copied to the machine and no hosted model was used. The policy comparison used an explicitly scripted loopback HTTP service.

## Machine and renderer

The probe recorded these values at `2026-10-30T18:24:16Z`:

| Measurement | Observed value |
| --- | --- |
| GPU | NVIDIA GeForce RTX 4090, 24,564 MiB |
| NVIDIA driver | 595.91.07 |
| `GL_VENDOR` | NVIDIA Corporation |
| `GL_RENDERER` | NVIDIA GeForce RTX 4090/PCIe/SSE2 |
| OpenGL | 4.6.0 NVIDIA 595.91.07 |
| Backend setting | `MUJOCO_GL=egl` |
| Operating system | Linux 6.8.0-147-generic, x86_64, glibc 2.39 |
| Python | 3.13 |
| MuJoCo / ONNX Runtime / NumPy | 3.17.0 / 1.31.1 / 2.5.4 |
| Container memory limit | 30,999,998,464 bytes, approximately 28.87 GiB |
| Container CPU quota | 1,020,000 / 100,000 microseconds: 10.2 CPU equivalents |

The host exposed 96 logical CPUs; that was not the container's CPU allocation. OpenGL vendor and renderer strings were read from the actual context after native rendering, rather than inferred from an installed driver. The renderer produced a 59,496-byte PNG of TIAGo++. A physical wheel-drive probe requested 0.25 m and 0.3 rad and measured 0.20955 m and 0.24187 rad, within the controller's existing completion tolerances.

## Physical regression tests

With the frozen dependencies installed, the guarded remote command ran:

```bash
MUJOCO_GL=egl .venv/bin/python -m pytest \
  tests/test_physics.py tests/test_factory_mission.py \
  tests/test_reference_physics.py -q --durations=8
```

Result: **12 passed in 44.21 seconds**. Coverage included TIAGo wheel contacts, grasp/lift/release, a three-station grasp–carry–process–deliver mission, a door moved by robot contact, G1 balance and Dex1 grasp/carry/release, process-loading interruption, and overlapping fixed mesh assemblies that still block the robot. These are engineering fixtures, not autonomous-policy benchmark results. The three-station mission took 28.11 seconds of test wall time.

A subsequent complete remote check ran `MUJOCO_GL=egl .venv/bin/python -m pytest -q` and `.venv/bin/ruff check .` on the same source snapshot. Result: **151 passed, 1 skipped in 53.48 seconds; Ruff passed**. The skipped test requires explicit live API opt-in, which was absent; the model API key was unset. One Starlette test-client dependency deprecation warning was emitted. No source changes were needed. Separate logs are retained as `full-pytest.log`, `full-ruff.log`, and `full-check-status.txt` alongside the other evidence.

## One versus two workers

The same immutable TIAGo world was evaluated twice through `evaluate_batch`: first with one spawned worker, then with two. Each run used four episodes and seeds `[11, 12, 13, 14]`. The remote-policy fixture issued `drive(distance=0.5, turn=0.3)` followed by `finish`; the rubric required entering a nearby region. It received the normal policy observations and did not alter simulator state.

Both runs used `graphics_backend="egl"`, `render=True`, `max_steps=2`, `max_sim_seconds=20`, `replay_fps=1`, and `max_replay_frames=20`. Each episode ran for 10.04 simulated seconds and produced 14 replay frames. Timing includes process startup, simulator initialization, policy transport, physics, rendering, serialization, and report assembly.

| Workers | Episodes | Valid / passed | Wall time | Episodes/second |
| --- | --- | --- | --- | --- |
| 1 | 4 | 4 / 4 | 13.6004 s | 0.2941 |
| 2 | 4 | 4 / 4 | 7.2631 s | 0.5507 |

Two workers produced an approximately **1.87× speedup** in this single comparison. It is a small workload, measured once per configuration, with no randomized run order or confidence estimate. It does not establish general scaling, large-scene throughput, hosted-policy throughput, or GPU physics acceleration.

For every matching seed, the complete recorded `qpos` trajectory was identical between worker counts. The check hashed `json.dumps([sample["qpos"] for sample in trace])` with SHA-256; all eight traces yielded:

```text
c9b8424f967ad08fe5a1563d4b099004a149424f60eed82f88b675cbf99a6160
```

The reset currently has no domain randomization, so all four seeds also describe the same physical initial state. They are protocol identifiers, not four independent task variations. This comparison establishes worker-count consistency on this machine; it does not establish bitwise equivalence across macOS and Linux or different simulator versions.

## Evidence and source snapshot

Raw evidence is retained locally under the ignored `.robot-worlds/gpu-check/` directory: `probe.json`, `nvidia-native-tiago.png`, `physical-tests.log`, `batch-comparison.json`, both probe scripts, and complete run bundles. The bundles include episode traces, replay images, runtime manifests, policy actions, rubric evidence, and reports.

The one-worker run ID is `6828af6b726f494b910451cbbc4d5b05`; the two-worker run ID is `3a4305061d44429986f3714ad2c77682`. These files are engineering evidence and are not distributed as a bundled project demo.

The tested remote copies matched the local worktree byte-for-byte for `runtime.py`, `g1_runtime.py`, `batch.py`, `sdk.py`, `server.py`, and `report.py` when checked after completion. Thus the copied snapshot included the then-current server, summary, and resource-cleanup changes, although these focused tests do not independently certify every server behavior. Source SHA-256 values:

```text
runtime.py     f3902501e96c42a85eaad884b9dd7bd18448224e23ccf777c0e53109706f9192
g1_runtime.py  4daee5fa5d8ef08becc96c267899cf50585bda5356cd21d523799926b95f95be
batch.py       0ff975ffbc1265af287fd5e1f56d12f63ee788f6fc95e64930e3167689d3f490
sdk.py         a248bbf43c0089e7dba614110e9937feb1695d79746a1b9bbbf25b38b6770b8d
server.py      b2b1379df695c85e7caad7da1dbade6aa237af724058258b4de2c754a748f43b
report.py      d381ac69290a0c0c83ab41f6f38b43038f378500eda6eed96ae0c2078d2ad6b9
uv.lock        25aad6e6abe9caa52b1052e951adf6635f061811e4f0969edd209c969640abfd
```

No controller changes were needed for this validation. All validation processes and their leases exited after the checks; the separate authorized Drift trial was still running when evidence was collected.

## Subsequent API, CLI and packaging checks

After the full-suite snapshot, the API and CLI were wired to the existing batch executor and `evaluate_batch` was exported from the package. Focused remote checks passed: 25 server/UI/dispatch tests and 17 CLI tests, with Ruff passing. These checks verify option forwarding, independent-episode budgets, cancellation, serial compatibility and credential references; they do not add a new physical benchmark.

A distribution build and fresh Linux Python 3.13 environment install also passed. From outside the source tree, the installed `robot-worlds --help` succeeded, package imports resolved under the fresh environment’s `site-packages`, and the G1 asset directory was present. Build/install and CLI-help logs are retained under `.robot-worlds/gpu-check/`. The repository still requires hosted CI and a reviewable PR.

## Review-fix verification

After adding runtime/policy identity, trace-integrity checks and connectivity diagnostics, the remote machine passed 109 focused identity/policy/SDK/regrading tests, 18 physical/batch integration tests, 70 generator/API/CLI boundary tests, and 39 connectivity/generator/large-scene tests. The last two selections overlap; these counts are not an aggregate suite total. Ruff passed. A lightweight Node regression also verified shared scene-resource and instanced-buffer disposal. Full hosted CI is tracked on the implementation PR.

## Hosted CI graphics boundary

[Implementation PR #1](https://github.com/playground-systems/robot-worlds/pull/1) runs the suite on Ubuntu 24.04 and macOS 15. The first complete Linux run passed **262 tests**, skipped the credential-dependent live API test, and built both distributions. The hosted macOS runner passed 257 tests but lacked the CGL pixel format needed for five rendering-dependent cases. The CI fixture probes native rendering and skips only that specific CGL failure on GitHub-hosted macOS; other rendering failures remain errors. Linux must run the render cases, and nonrendering robot physics remains active on macOS. This limitation does not replace the separate native Apple-silicon and NVIDIA evidence above.

The workflow moved from macOS 14 after GitHub's scheduled retirement brownout prevented startup. [GitHub retirement notice](https://github.blog/changelog/2026-10-01-github-actions-macos-14-runner-image-retirement/).
