---
name: robot-worlds-acceptance
description: >
  Use this skill when verifying Robot Worlds release readiness, resuming its
  end-to-end acceptance work, collecting demo evidence, or checking a built
  package. Reuse recorded evidence, select the right native or live workflow,
  and preserve the distinction between physical success and visual inspection.
---

# Robot Worlds acceptance

**Failure pattern:** repeated expensive trials and attractive screenshots can
hide missing physical, package, or portability evidence.
**Verified by:** [focused run 41265078569](https://github.com/playground-systems/robot-worlds/actions/runs/41265078569)
passed its checks and installed-wheel engineering placement, export and regrading.
This is historical evidence for the procedure, not certification of later code.

## Resume from evidence

1. Locate the existing linked worktree and draft PR before creating new work.
   The acceptance work originated in `feature/initial-runtime`, PR #1. Read
   repository instructions and inspect the current branch, working tree and PR
   head. Preserve in-progress edits and the user's latest resource preferences.
2. Read `docs/alpha-acceptance.md`, `.github/workflows/household-acceptance.yml`
   and the referenced evidence records. These own the gates, selected checks,
   limits and required native cases; do not maintain a second list here.
   Record which evidence matches the candidate commit and which gates remain.
3. Inspect existing run artifacts before starting another trial. Keep historical
   worlds, scores and replays unchanged. A new implementation gets a new run and
   evidence record. A running job, partial log or earlier green build cannot
   certify the current candidate.

Starters: for bundled source scenes, offline import acceptance or starter package checks, read [references/starters.md](references/starters.md).

## Choose one bounded workflow

Read the selected script and its CLI options before execution. Use a fresh output
directory and the current installed asset library. The entry points live in
`tools/acceptance/`:

| Script | Evidence it provides |
| --- | --- |
| `offline_household.py` | Mocked generation with native sensors, physics and SDK portability. |
| `engineering_household.py` | Known-fixture physical placement through public actuator actions, with privileged robot-frame knowledge. |
| `live_household.py` | Live generation and a sensor-only policy, including honest task failure and budget termination. |
| `live_edit.py` | Live scoped editing, exact preservation checks and a subsequent full evaluation/export flow. |

The engineering fixture starts with an open microwave door and demonstrates a
short carry and guided insertion. Report these limits alongside its passing
criteria; it does not establish autonomous household manipulation or door opening.
The same source geometry, physical parameters and rubric must survive export,
reload and regrading.

For authorized live runs, use the project's supported provider adapter. The
existing `CodexClient` was verified through supported Codex CLI sign-in when the
separate API account returned `credit_balance_exhausted`. That error needs funded
API access or an authorized supported alternative, not retries or extraction of
Codex login secrets. Credential discovery belongs to the existing
`codex-workspace-key-inheritance` skill. Record Codex plan usage separately from
API charges; a missing dollar invoice is not evidence of a zero-dollar charge.

Use mocks for repeatable integration work. Restrict paid/model-backed checks to
authorized, bounded full workflows; fix an identified defect before repeating
one. A successful reset establishes loadability and initial stability, not task
solvability.

## Run focused hosted acceptance when authorized

Honor the latest CI and compute instructions. This skill supplies a trigger
procedure, not additional authorization. On the unmerged branch where the
workflow uses an explicit PR label event:

1. Keep ordinary changes in commits containing `[skip ci]`. Verify the current
   full-suite and packaging workflows suppress jobs while the
   `run-household-acceptance` label is present.
2. Add that label while the head is skipped. Push one deliberate non-skipped
   activation commit with the label already present.
3. Remove and re-add the label to request the dedicated household job. Inspect
   actual Actions runs to ensure only the intended focused execution started.
4. Record the run URL, checked-out commit, outcome and artifact identity. Remove
   the label after collecting the result. Resolve failures against that exact
   candidate before another authorized run.

The same workflow now has narrower label modes: `run-package-acceptance`
refreshes wheel/source archives and clean installs without repeating simulation;
`run-warehouse-acceptance` runs the saved-plan dense storage workflow. These opt-in
labels also suppress the full CI matrices. Use the smallest mode covering the
change, and never describe a package-only run as native physics verification.
The package-only mode passed in run 41268962945; the warehouse-only mode passed in run 41269459293. Those historical results
do not certify later runtime changes.

Once the workflow lands on the default branch, inspect its current triggers
instead of assuming this activation sequence is still necessary.

## Verify artifacts and presentation

1. Download the evidence archive and verify its whole-file SHA-256 against the
   published artifact digest before treating it as the complete artifact. Record
   the verified identity. Install the wheel and source archive outside the
   checkout; verify imported modules resolve into the fresh environment.
2. Run the selected installed-package workflow. Retain original predicate
   evidence and compare the exported/reloaded/regraded result. Resolve trace
   paths through `episode.trace_evidence.file`; standard JSON traces may be
   compressed. Avoid assuming every episode contains `trace.json`.
3. Inspect actual application views and rendered frames for the same run. A
   screenshot supports appearance and interaction claims; predicate evidence
   supports task success. Report a failed optional closeup separately from a
   successful placement, and leave the presentation gate open until verified.
4. For viewport performance, use `tools/acceptance/viewer_benchmark.py` against
   the production viewer and record scene, cache state and sampling method. Its
   verified cached 90-frame orbit measured about 60 fps in one environment;
   this is not a universal performance promise.
5. Update the acceptance record with commit, environment, elapsed time, peak
   memory, model usage, physical verdicts, portable verdicts and visual evidence.
   Stop services and close agent-created browser tabs after evidence collection.

If only logs are urgently needed from a large artifact ZIP, ranged retrieval can
recover individual members. Authenticate the GitHub API request only; follow its
signed blob redirect without forwarding Authorization. A member CRC validates
that extracted member, not the whole archive's SHA-256. Label partial recovery
accordingly and complete whole-file verification before package acceptance.

## Ruled-out approaches

- Serializing the entire growing trace repeatedly and retaining duplicate dense
  scene states exhausted resources. Use the project's incremental checkpoint and
  compact/compressed evidence paths; do not restore full-history writes as a
  diagnostic workaround.
- Flat visual bounds and rotated bounding-box corners falsely rejected a round
  bowl. Use recorded compiled physical geometry bounds and the existing rubric;
  do not relax containment tolerances to manufacture a pass.
- Calling `forceContextLoss()` when disposing a preview destroyed the reusable
  canvas for the next viewer. Verify preview-to-ready replacement in the real
  browser, not just static scene loading.
- Blind grasp probes, old green CI and polished equipment screenshots did not
  establish release readiness. Reuse the proven acceptance entry points and
  require evidence for each current gate.
