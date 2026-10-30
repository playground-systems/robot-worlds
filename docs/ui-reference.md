# Editor reference: Figma

On October 30, 2026, we walked through the signed-in [Figma editor](https://www.figma.com/) in an isolated study file. The walkthrough covered file/layer navigation, object creation and selection, the contextual inspector, a selected-object agent request, activity disclosure, Undo and version history. The temporary file was moved to recoverable trash afterward; existing files were untouched.

The agent request changed one rectangle's fill to `#4D7CFE` while preserving its displayed position and 300 × 200 dimensions. Selection appeared as a context chip above the prompt. The response linked the affected object, disclosed its work behind an expandable row and offered Undo. Undo restored the original fill and displayed an explicit confirmation.

## Structure to follow closely

| Observed Figma pattern | Robot Worlds adaptation |
| --- | --- |
| Narrow vertical navigation rail | World, Agent, Assets and Runs |
| Left panel switches between file/layers and agent chat | Saved versions and physical objects, or the text-driven scene workflow |
| Canvas occupies the full central area | Scene inspection with compact view/fit controls |
| Selection changes the right inspector | Read-only object identity, pose, dimensions and physical properties |
| Selected layer is attached to the prompt | Selected persistent object ID becomes explicit edit scope |
| Prompt anchored at the bottom of the agent panel | Follow-up edits stay beside the scene instead of shrinking it vertically |
| Compact activity disclosure and inline result actions | Measured validation, computed diffs and opening the previous immutable version |
| Version history occupies the right panel | Parent/version inspection alongside the canvas |

Follow the panel proportions, spacing, dark surfaces, restrained blue selection accent and information hierarchy. Keep Robot Worlds branding and terminology. The floating toolbar contains implemented scene-view controls, not decorative drawing tools. Object property inspection does not introduce the graphical layout editor excluded from the MVP.

Screenshots of the actual walkthrough are stored locally under `.robot-worlds/ui-reference/`; they are research evidence, not redistributed product assets. The original Robot Worlds screenshots under `.robot-worlds/screenshots/current/` show the preceding committed layout.

## Browser verification

The implementation was checked against a saved 15-object workshop and an existing evaluation report. Object selection updated the inspector and attached its ID to the agent composer; panel switching preserved the draft; version history remained accessible. Reports occupy the full workspace without showing stale object properties. The default scene view is the interactive layout, with native physical renders available through the bottom toolbar.

Screenshots are saved locally under `.robot-worlds/screenshots/figma-layout/`. The preview server and browser tab were stopped after checking. No new model call or simulator run was needed for this UI verification.
