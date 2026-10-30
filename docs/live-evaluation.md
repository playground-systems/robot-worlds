# Watch a policy work

Start an evaluation from the local workbench. The canvas switches to a native spectator camera, with a compact status card for the current action and simulated time. Inspect shows observed rubric milestones. When execution ends, the workbench opens the independently graded report and recorded replay.

The spectator view is separate from the policy's camera. It never appears in policy observations, advances no physics, and publishes at most four frames per wall-clock second. Model inference pauses simulation time. Rendering failures disable the preview while preserving execution and grading evidence. A milestone observed during the run is provisional: final-state conditions can fail later.

The SDK emits `live_frame`, `policy_started`, `action_started`, `step` and `task_progress` through the existing `on_event` callback when rendering is enabled. `live_frame` identifies the latest `episode-NNN/live.png` under the run directory; it contains no embedded image bytes. The rolling image supports watching, while the saved trace, action frames and replay support later inspection.

For development on a remote simulation host, `tools/demo/run_remote_mission.py` relays bounded model decisions over SSH and can mirror live frames into a local workbench with `--live-root`. It transfers the complete run evidence before marking that local job complete. This harness requires the documented experiment guard on the remote host; it is not a general compute provisioning service.

The native viewer uses estimated surface finishes, procedural flooring and studio lighting. These affect appearance, not collision geometry, friction, mass or rubric thresholds. The actor camera remains 640 × 480; the spectator camera is 960 × 720.

Future spectator frames ray-test the scene before placing the camera. The camera moves closer when a wall blocks its preferred position and tries another angle if that would create an unusable close-up. Scene walls remain present and the actor camera is unchanged. Existing recorded replay images retain their original viewpoints.
