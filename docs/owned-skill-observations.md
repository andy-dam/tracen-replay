# Owned skills in the final summary

`gameplay_tracking.owned_skill_inventory` records repeated visible skill-card text from the final summary. This is separate from selected cart items, committed purchase bundles and inheritance sparks. A skill already owned does not become another purchase when it appears in the summary.

The supported layout requires the Skills, Inspiration and Career Info tabs plus at least three recognized final attributes. Cards occupy two columns. Wrapped names are joined within one card; a low-confidence fragment rejects the whole card rather than producing a confident partial name. Two distinct observations no more than 500 ms apart must agree before a name enters the inventory observations. Evidence retains timestamps, card position, original text, confidence and boxes.

`name_text` is the observed base text, not a verified full identity. Circle suffixes and unique-skill levels have separate observations and verification flags. An unread suffix stays unknown. The report always keeps inventory completeness false: this implementation does not establish scrolling coverage or off-screen ownership.

## Source review

The two supplied recordings each briefly show the first page with 14 skill cards. Their scrollbars visibly continue below that page. Base OCR retains 12 repeated card names in the original recording and 13 in the separate recording. The original lacks repeated accepted base readings for Pace Chaser Straightaways and Triple 7s; the separate run lacks repeated complete Front Runner Straightaways text. These are recognition gaps on visible cards, distinct from the off-screen inventory gap.

The separate run's two summary frames at 29:19.000 and 29:19.250 are preserved in `tests/fixtures/final-owned-cards.json`, with source identity and screenshot hashes. A readable Front Runner prefix in the second frame is not accepted because the following Straightaways fragment has low confidence. The earlier complete reading stays in per-frame evidence but cannot alone meet the repeat requirement.

This review does not establish complete inventory from the entire video. Earlier acquisition receipts and purchased bundles remain complementary evidence; automatic merging of prerequisites, upgrades and circle variants into a final complete inventory still requires validation.

## Focused panel recognition

`python -m tracen_replay.refine_inventory RUN_DIRECTORY` reads the skill panel in the existing summary frames and saves separate `inventory-refinement` observations. Reparse the recording to apply them. Each observation binds the immutable base OCR, gameplay image hash, timestamp, crop box, and model hashes. The original OCR is retained. Conflicting accepted names at a card position are withheld rather than silently overwritten.

The panel pass recovers the three missing base-name readings at the existing 95% threshold across distinct source timestamps. Its two crop observations per recording support 14 repeated visible names in each. Crops of the same frame do not count as temporal repetition. Off-screen inventory remains unverified, and inventory completeness remains false.

An earlier narrow line-crop probe improved Triple 7s but failed the two Straightaways fragments; those measurements remain local evidence rather than accepted replacements. Re-running detection on the contextual panel resolved the wrapped text without name catalogs, expected-label inputs, or confidence overrides.

Both rebuilt reports match 14/14 visually reviewed first-page base names with no extras. All 266 unit tests and the preserved `regression-24` checks pass. A scoped audit also checked all four panel refinements against their immutable OCR and decoded-frame gameplay pixels. These results establish first-page base-name recovery, not full inventory or fresh independent accuracy.

## Visible detail observations

An explicit high-confidence `Lvl N` inside a card is retained separately from its name. Circle suffixes use the existing isolated-circle geometry detector on the panel's final text line. Both details require agreement at distinct timestamps within 500 ms before their individual verification flag is set. Conflicting observed values remain unknown with the conflict retained. No detection is not evidence that a skill has no suffix or level.

The source panels explicitly show Festive Miracle at level 4 in the original recording and level 5 in the separate recording, plus four and six single-circle suffixes respectively. The initial detail probe supports eight of those ten suffixes; Corner Recovery in the original and Winter Runner in the separate recording remain unresolved. Small crop-position offsets did not recover them and were not added as accepted evidence.

The rebuilt detail evaluation matches 4/5 explicitly labeled fields in the original and 6/7 in the separate recording, with no extra variant detections. Both levels match; the two missing suffixes keep these detail evaluations failing. All 267 unit tests and frozen `regression-25` checks pass. Passing older regressions does not erase the new detail failures.
