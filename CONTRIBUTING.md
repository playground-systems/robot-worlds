# Contributing

Start with a reproducible failure, a small proposed improvement, or a discussion of a new robot/controller boundary. [Setup](docs/setup.md), [architecture](docs/implementation-contract.md), [scene editing](docs/scene-editing.md) and [product evidence](docs/product-goal.md) describe the current contracts.

```sh
uv sync --frozen --dev
uv run --frozen ruff check src tests
uv run --frozen pytest tests/TEST_RELEVANT_TO_YOUR_CHANGE.py -q
```

Use a feature or fix branch and keep pull requests focused. Describe the user-visible change, relevant tests and remaining limits. CI runs the broader suite on macOS and Linux. Python 3.11 is the minimum supported version; distribution checks also cover the minimum-version install path. Browser resource tests use `node --test tests/scene-resources.test.mjs`.

Changes to control, perception, physics or process monitoring need native tests, including a failure case. Unit stubs must identify themselves as stubs. Never substitute teleports, hidden attachments, textual success flags or animation for physical execution. Keep evaluator state outside actor observations; unsupported measurements remain unvalidated. A changed controller/source fingerprint requires a newly validated world, even if its XML looks unchanged.

For generation/editing changes, test persistent IDs, scope enforcement, parent immutability, protected task/physics fields and repair exhaustion. For scoring changes, include counterexamples such as no-op, wrong workpiece, missing measurements, out-of-order processing or regression. Preserve the distinction between task failure and invalid execution.

Live API tests require explicit opt-in and incur usage. Use bounded model calls, token/time limits and cost reservations. Do not add model credentials to CI, logs, fixtures or pull requests. Tests should normally use injected transports; a real model patch can be retained as an attributed internal test fixture without claiming it is a benchmark.

New assets need a pinned upstream revision, source URL, retained license/notice and a description of modifications. Update [third-party notices](THIRD_PARTY_NOTICES.md) and provenance beside the assets. Do not assume a model repository's code license covers unrelated weights or meshes. Explain controller, sensor and collision approximations separately from visual detail.

Bug reports should include the commit/version, platform, sanitized `robot-worlds doctor --json`, prompt/scope, validation findings and relevant criterion evidence. Review exports before uploading: they can contain scene text, observations and replay images. Report credential exposure or boundary violations through [the security process](SECURITY.md).
