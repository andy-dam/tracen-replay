# Dialogue choice reconstruction

`tracen_replay.choice_evidence` supplies development-stage choice reconstruction in `gameplay_tracking.dialogue_choices`. Run `python -m tracen_replay.refine_choices RUN_DIRECTORY`, then rebuild with `full_recording --reparse-only`. Choice observations are stored separately in `choice-refinement/`; the loader and evidence verifier check their raw OCR and gameplay screenshot hashes. The additional pass currently covers base observations classified as unknown, not every native inspection frame.

`observe(pane, lines)` accepts only the isolated 810×1080 gameplay crop. It finds white card interiors independently of OCR, joins wrapped lines within each card, and requires readable text for every detected card before establishing a menu. It also records text on filled green selection cards and paired yellow selection marks at the two card edges. A colored badge on one card edge is insufficient. These observations alone do not identify a selected option. Card detection is still a layout heuristic, so this completeness check is not proof that every possible menu layout is supported.

`reconstruct(observations)` requires a repeated recent option set and selection marks aligned with exactly one option. It retains the offered text from the stable menu, even if the selected-card animation obscures that text. A transient subset of the menu does not replace the repeated full menu, but readable card text outside the prior option set invalidates that association. This prevents a later response at the same screen height from selecting a stale option. Selection time is an observed animation timestamp; click time remains unknown. Single-option dialogue responses have a separate kind from multi-option choices. Rewards are never inputs to selection inference.

Version 2 observations replace the original text-strip detector. Re-running `refine_choices` validates and preserves each version 1 artifact in `choice-refinement-v1/` before generating its replacement. The report loader rejects version 1 artifacts with a regeneration instruction. Original OCR, screenshots, references and historical report snapshots are retained.

Reviewing six previously unreviewed candidates exposed one missing wrapped option and two false associations with later dialogue responses. The source-guided regression fixture records the corrected two-option menu, three other supported selections, and abstention for the two stale associations. This checks known candidates, not missed-choice recall.

The initial source-guided check reconstructs two multi-option decisions in the separate recording's doctor event, matching the previously preserved choices reference. The brief single response remains unresolved at base sampling. This is a development result for one event, not a recall measurement or a completed choice-validation gate.

Next work:

- Extend provenance-backed observations to dense choice-inspection frames when base sampling misses brief responses.
- Sample the brief single-response transition more densely; do not weaken repeated-menu evidence to force recognition.
- Run `python -m tracen_replay.choice_evaluate REFERENCE REPORT --evidence-root RUN_DIRECTORY --output SCORE_FILE`. The evaluator checks missed/extra choices in the reference evidence window, response type, options and selection evidence. Keep that bounded scope separate from full choice recall.
- Validate unrelated events, menus, canceled/changed selections, moving cards and false yellow-mark matches before exposing verified choices in the report.
- Establish full source-selected choice coverage separately from these selected development examples.
