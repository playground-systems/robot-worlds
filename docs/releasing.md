# Prepare a reviewable release

This procedure prepares artifacts for review. It does not publish to a package index, create a GitHub release, or authorize a merge. The repository is an early prerelease; [product evidence](product-goal.md) separates demonstrated behavior from acceptance gaps.

## Build and clean-install gates

1. Choose the reviewed commit and version. Merge the current main branch into the implementation branch; keep local worlds, credentials and unrelated work outside the change.
2. Record which checks ran against the candidate. This alpha uses the explicitly authorized focused household acceptance workflow instead of repeatedly running the full matrix. It includes native SDK/physics, affected rubric and viewer checks, archive inspection and clean wheel/source installation. Do not describe that selection as a full-suite pass. Native physical tests and browser resource tests serve different boundaries; retain both results.
3. Build wheel and source archive with `uv build`. Run `python tools/check_distribution.py dist`; inspect the package metadata, licenses, robot/controller assets and local browser modules. The checker rejects local run/world directories, environment files and bytecode in either archive.
4. Install each artifact into its own clean virtual environment outside the checkout. Run `uv pip check --python PATH_TO_ENV_PYTHON`, `robot-worlds doctor --json` and `robot-worlds --help`. An editable install or `PYTHONPATH=src` does not establish wheel completeness.
5. Exercise native loading, reset, tools and rendering from the installed artifact on the declared target platforms. Record exact dependencies and graphics backend. A driver being installed or `MUJOCO_GL=egl` being set does not establish an actual NVIDIA context.
6. Separately opt into bounded live API verification using an ordinary BYO account. Check the selected model's JSON, image/function and exact-counting capabilities. Record model/reasoning, price profile, usage and errors without credentials. A successful internal Astra request does not establish access for every user.

After a presentation-only change, the opt-in `run-package-acceptance` label builds
and clean-installs both archives on one Linux runner without repeating simulation
or model calls. Preserve the earlier native/runtime evidence and identify both
commits; this package-only check does not certify changed physics. Remove opt-in
labels after recording their results.

The full packaging workflow installs freshly built wheel and source archives on Linux with Python 3.11, Linux with Python 3.13, and macOS with Python 3.13. It runs offline diagnostics and dependency checks without a model key, simulation or server. It uploads the artifacts for review and has no publishing step. Workflow definitions become evidence only after their actual runs pass.

## Review evidence and user flow

Use a newly described world, not a bundled pre-generated factory or demo controller. Confirm generation → preview → validation → scoped edit/repair → evaluation → criterion evidence/replay. Preserve the original world during edits. Export and reopen the world/run bundle. Failed validation, exhausted budgets, cancellation, policy errors and missing grading evidence must stay visible.

Before release, reconcile the README, setup instructions, current product evidence and screenshots with that exact build. Do not present engineering policies, short navigation probes or attractive scene views as a completed autonomous long-horizon mission. Record the remaining mission gap if it is not established.

Review [third-party notices](../THIRD_PARTY_NOTICES.md) against the actual shipped files. Preserve upstream revisions, license copies and modifications; a generic project-level license badge is not evidence for unrelated weights. User-generated or uploaded scenes need their own provenance when shared. No Airsup development reference, provider key or private replay should enter the distribution.

## Release record

Attach or link the reviewed commit, version, wheel/source SHA-256 values, CI run URLs, clean-install logs, native/browser evidence, explicit live model compatibility result, retained license inventory and known limitations. Verify public issue/private security reporting settings before announcing them. Publishing, index-name availability and maintainer credentials remain separate actions; this repository does not assume a published PyPI package.
