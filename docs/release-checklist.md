# Release acceptance

This is the launch checklist, not a claim that the alpha is complete. A checkbox requires saved execution evidence or a reviewable release artifact. The implementation remains on the draft PR until explicitly approved for merge.

| Workstream | Current state | Evidence needed to close |
| --- | --- | --- |
| Autonomous factory mission | In progress | Generated scene and task; sensor-only LLM retrieval, loaded inspection, transfer and stable delivery across multiple layouts/seeds |
| Generated visual fidelity | Open | Newly generated detailed facility with useful workcells and physical geometry, not a source-reference import |
| Progressive refinement | Partial | Useful quick preview followed by richer immutable version preserving task and entity identity |
| Interactive edit/repair | Partial | Several substantial live edits and controlled physical failures, independent diffs, unchanged unrelated state and task constraints |
| Ten environment/task scenarios | In progress | Independently checked generation/repair results and declared unsupported requirements for each scenario |
| Rubric trustworthiness | Partial | Intended task coverage plus wrong-object/order/stability/process and edit-weakening counterexamples |
| Robots and policy integration | Partial | Supported TIAGo/G1 envelopes, practical policy examples and real integrated mission evidence |
| Mac and NVIDIA usability | Partial | Bounded resources, cleanup and measured large-scene performance without overstating GPU physics |
| Workbench completion | In progress | Camera framing, cutaway/selection, progress/failure handling, scoped edits and evidence-linked replay verified in browser |
| Fresh installation/model access | In progress | Clean packaged installs, offline diagnostics, paid BYO model setup and first real result |
| OSS release preparation | In progress | Attribution inventory, contribution/support docs, packaging checks and reviewed versioned release artifacts |
| Demo and launch materials | Open | Honest recorded prompt→scene→edit/repair→robot→rubric workflow with reproducible artifacts and stated speedups/costs |

The remote demonstration harness keeps model credentials on the initiating machine and sends only bounded requests and returned outputs through an SSH subprocess. Its custom transport and source identity are recorded; it does not certify an arbitrary remote policy service. Internal prompts and engineering fixtures are not bundled scripted onboarding controllers.

Heavy simulations run on authorized remote compute while the development Mac is under memory pressure. Temporary compute uses an independent hard deadline, process leases, backups and verified cleanup within the approved spending cap.
