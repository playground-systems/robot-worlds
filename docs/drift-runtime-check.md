# Drift runtime check — 2026-10-30

We downloaded the official Drift 2.0.1 Linux amd64 release and tried its CLI in an isolated Ubuntu container on this Apple M1 Pro Mac. The CLI did not reach help, a prompt, or an authentication URL. This is a failed startup experiment under this machine's existing x86 emulation, not a successful product evaluation and not proof that Drift fails on native Ubuntu.

## Artifact and environment

- Official public release: [Drift 2.0.1 amd64 package](https://github.com/godrift-ai/drift-releases/releases/download/2.0.1/drift_2.0.1_amd64.deb).
- Package metadata: `drift-desktop` 2.0.1, amd64, providing/replacing `drift-cli`; desktop dependencies `libwebkit2gtk-4.1-0` and `libgtk-3-0`.
- Package SHA-256: `a5be77bb481d6d3d2bc39467c31675c7a1047c98b74d59c2bfcd90038c014f71`.
- The package contained `drift` (184,399,497 bytes), `drift-sidecar`, `drift-desktop`, and a `drift-cli` symlink. We extracted the package rather than running its installer or installing the desktop dependencies.
- Host: Apple M1 Pro, Docker Desktop 4.20.1 / Docker Engine 24.0.2, Linux arm64 engine. Container: official `ubuntu:24.04`, explicitly `linux/amd64`; `uname -m` returned `x86_64`, and process inspection showed QEMU x86_64 execution.
- Resource limits: 1 GiB memory, two CPUs, 128 processes, all Linux capabilities dropped, no-new-privileges. `docker inspect` confirmed an empty mount list. No host workspace, Docker socket, account data or API credentials were mounted or copied.

The [official quickstart](https://docs.godrift.ai/getting-started/quickstart) recommends native x86_64 Ubuntu, describes an amd64-container route on macOS, and says the first interactive launch should print a Google sign-in URL. Those are documentation claims; this experiment did not reach that flow.

## Observed behavior

1. The Docker daemon became available and the official Ubuntu image pulled successfully. The public release download completed and its package metadata could be read.
2. `drift --help`, without a TTY, produced no output before a 20-second timeout (exit 124).
3. A 45-second retry also produced no output (exit 124). A container snapshot during startup showed approximately 124% CPU utilization and 181 MiB memory usage.
4. After the user authorized preparing a sign-in session, we enabled container networking, keeping host mounts and credentials absent. A real-TTY `drift --help` attempt still produced no output before its 45-second timeout.
5. Dynamic-loader debugging found the CLI's ordinary shared libraries and reached program entry. `ldd`, and a direct loader `--list` invocation, exited 139 under this emulation. This is evidence of an environment/runtime problem, but does not identify its exact cause.
6. A final attempt from an empty disposable project directory remained silent and was terminated during cleanup (exit 137). No prompt or CLI-specific Google sign-in URL was obtained.

We did not authenticate the CLI, accept terms, copy a host model API key, purchase a plan, launch a paid GPU, or run a Drift-generated simulation. Browser sign-in is a separate user-controlled step and does not establish CLI authentication. The disposable container was removed. The public package remains in `/tmp` for provenance/retry; the ordinary Ubuntu image remains in the local Docker cache.

## What to adapt

These are recommendations based on the documentation and startup observations, not claims about an authenticated Drift session:

- Show a useful environment diagnostic before expensive initialization: native architecture, graphics backend, simulator version, and a concrete reason when unsupported emulation prevents startup.
- Keep our native Mac execution path prominent and verify it with real compile/render/step checks. Installing a package is different from proving the runtime works.
- Drift's documented `/init` and project-context workflow is a useful pattern for persistent robot/task conventions. Our immutable world/task/rubric bundle should make that context explicit and reviewable instead of relying solely on a long chat.
- Preserve visible planning, bounded diagnosis/repair, and artifact progress. Silence during a long startup makes it hard to distinguish computation, authentication, missing dependencies and a hang.

A practical next product test is on native Linux amd64 with a normal package installation, followed by the user's explicit sign-in flow and a small disposable MuJoCo task. This test did not establish whether another emulator version, a different container configuration, or a full package installation would also work. No working authenticated simulation is claimed from this container attempt.
