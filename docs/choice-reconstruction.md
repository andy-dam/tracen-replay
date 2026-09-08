# Dialogue choice reconstruction

`tracen_replay.choice_evidence` is a development component, not yet part of the full-recording report pipeline.

`observe(pane, lines)` accepts only the isolated 810×1080 gameplay crop. It records OCR text supported by a broad white option-card interior and paired yellow selection marks at the two card edges. A colored badge on one card edge is insufficient. These observations alone do not identify a selected option.

`reconstruct(observations)` requires a repeated recent option set and selection marks aligned with exactly one option. It retains the offered text from the stable menu, even if the selected-card animation obscures that text. A transient partial menu does not replace the repeated full menu. Selection time is an observed animation timestamp; click time remains unknown. Single-option dialogue responses have a separate kind from multi-option choices. Rewards are never inputs to selection inference.

The initial source-guided check reconstructs two multi-option decisions in the separate recording's doctor event, matching the previously preserved choices reference. The brief single response remains unresolved at base sampling. This is a development result for one event, not a recall measurement or a completed choice-validation gate.

Next work:

- Integrate immutable pixel observations with source/proof hashes into the full-recording workflow and provenance verifier.
- Sample the brief single-response transition more densely; do not weaken repeated-menu evidence to force recognition.
- Add an evaluator for the existing choice reference, including missed/extra choices, response type and selection evidence.
- Validate unrelated events, menus, canceled/changed selections, moving cards and false yellow-mark matches before exposing verified choices in the report.
- Establish full source-selected choice coverage separately from these selected development examples.
