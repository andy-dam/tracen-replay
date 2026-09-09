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

The source panels explicitly show Festive Miracle at level 4 in the original recording and level 5 in the separate recording, plus four and six single-circle suffixes respectively. The initial detail probe supported eight of those ten suffixes. Small crop-position offsets did not recover Corner Recovery or Winter Runner and were not added as accepted evidence.

That initial detail evaluation matched 4/5 explicitly labeled fields in the original and 6/7 in the separate recording, with no extra variant detections. Both levels matched; the two missing suffixes kept those detail evaluations failing despite passing older regressions.

The integrated `inventory_suffix.detect` now uses separated-ring geometry, inner-opening shape, and agreement across correlated threshold views. It recovers both missing markers while rejecting ordinary terminal O/o letters. Portable saved negative controls cover GO, Hello, Tokyo, and No at several font sizes; tests also cover real single/double circles, clipped markers, and ambiguous multiple candidates.

After rebuilding both recordings, the detail evaluation matches 5/5 and 7/7 fields respectively: ten circle suffixes and two levels, with no extra variants. The full suite passed 290 tests at integration, and frozen `regression-27` checks pass. This closes the labeled first-page detail gaps; off-screen ownership and complete inventory remain unverified.
