# Dialogue choice reconstruction

`tracen_replay.choice_evidence` supplies development-stage choice reconstruction in `gameplay_tracking.dialogue_choices`. Run `python -m tracen_replay.refine_choices RUN_DIRECTORY`, then rebuild with `full_recording --reparse-only`. Base choice observations are stored separately in `choice-refinement/`; the loader and evidence verifier check their raw OCR and gameplay screenshot hashes. This pass covers base observations classified as unknown. Bounded dense inspection is available separately for brief transitions.

`observe(pane, lines)` accepts only the isolated 810×1080 gameplay crop. It finds white card interiors independently of OCR, joins wrapped lines within each card, and requires readable text for every detected card before establishing a menu. It also records text on filled green selection cards and paired yellow selection marks at the two card edges. A colored badge on one card edge is insufficient. These observations alone do not identify a selected option. Card detection is still a layout heuristic, so this completeness check is not proof that every possible menu layout is supported.

`reconstruct(observations)` requires a repeated recent option set and selection marks aligned with exactly one option. It retains the offered text from the stable menu, even if the selected-card animation obscures that text. A transient subset of the menu does not replace the repeated full menu, but readable card text outside the prior option set invalidates that association. This prevents a later response at the same screen height from selecting a stale option. Selection time is an observed animation timestamp; click time remains unknown. Single-option dialogue responses have a separate kind from multi-option choices. Rewards are never inputs to selection inference.

Version 2 observations replace the original text-strip detector. Re-running `refine_choices` validates and preserves each version 1 artifact in `choice-refinement-v1/` before generating its replacement. The report loader rejects version 1 artifacts with a regeneration instruction. Original OCR, screenshots, references and historical report snapshots are retained.

Reviewing six previously unreviewed candidates exposed one missing wrapped option and two false associations with later dialogue responses. The source-guided regression fixture records the corrected two-option menu, three other supported selections, and abstention for the two stale associations. This checks known candidates, not missed-choice recall.

The base source-guided check reconstructs two multi-option decisions in the separate recording's doctor event. A 60 FPS inspection of its brief single response supplies five readable menu observations and a selected-card observation, recovering the missing response without changing confidence or repetition requirements. The preserved reference then scores 3/3 with no extra choices in its evidence window. This is a development result for one event, not a full-recording recall measurement or a completed choice-validation gate.

For a transition requiring denser inspection, run:

```powershell
python -m tracen_replay.inspect_choices "SOURCE.mp4" --output RUN_DIRECTORY --start-ms 906750 --end-ms 907250 --fps 60
python -m tracen_replay.verify_evidence "SOURCE.mp4" --output RUN_DIRECTORY
python -m tracen_replay.full_recording "SOURCE.mp4" --output RUN_DIRECTORY --reparse-only
```

Choose the window from source review; the timestamps above are one example. Windows must be within the source, at most five seconds long, and sampled at 4–60 FPS. The inspector stores decoded frame manifests, source PTS, isolated gameplay crops, immutable OCR and versioned choice observations under `choice-inspection/`. `choice-inspection.json` indexes completed windows. Re-running a completed window validates its cached observations.

The report exposes dense observations under `choice_inspection` and merges them only into dialogue reconstruction. They do not add stat receipts, training actions or rewards. Duplicate timestamps cannot supply extra repetition evidence, and known-screen boundaries take precedence over unknown-screen choice observations. The evidence verifier includes the new manifest and its source/crop/refinement checks. Receipt or training inspection frames are not automatically treated as choice evidence.

Next work:

- Apply bounded inspection to other unresolved menu transitions and establish source-selected coverage. Dense inspection currently requires an explicit window; the pipeline does not yet schedule these windows automatically.
- Run `python -m tracen_replay.choice_evaluate REFERENCE REPORT --evidence-root RUN_DIRECTORY --output SCORE_FILE`. The evaluator checks missed/extra choices in the reference evidence window, response type, options and selection evidence. Keep that bounded scope separate from full choice recall.
- Validate unrelated events, menus, canceled/changed selections, moving cards and false yellow-mark matches before exposing verified choices in the report.
- Establish full source-selected choice coverage separately from these selected development examples.
