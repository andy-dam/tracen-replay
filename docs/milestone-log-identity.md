# Milestone: log identity and outcome visibility

Track ordered log context across sampled frames, exclude entries already visible
before a checkpoint, and record when main-pane outcome text is observed. Exact
action/click timestamps remain unknown unless separately established.

**Status:** completed as a scoped development milestone on 2026-09-08.

## Measured development results

Fresh source-video runs over the same 212 seconds produced 848 frames, 16 stable
checkpoint spans, and 14 arithmetically balanced intervals. The former Stamina
−5 discrepancy is resolved without reference values entering the pipeline. A
second delayed Power +5 log entry is linked to its earlier main-pane outcome
using matching narrative text, with the association explicitly unverified.

The 38-reference stat evaluation remains unchanged: 84/84 accepted fields
correct, 14/14 complete readable screens, and 24/24 negative screens with no
accepted stats. These are sparse development references from one recording.
The 42-test regression suite passes, including both real OCR regressions.

The reports contain 24 provisional log occurrences and 16 sampled main-outcome
episodes. These are observation structures, not counts of actual game events;
some outcome episodes are fragments of the same visible result. Seven log
entries provide explicit training-option evidence, covering Speed, Stamina,
Power, and Wit. Other action types remain unknown. Training previews are still
reported separately and never supply completion evidence.

Single-run end-to-end processing took approximately 82, 165, and 3 seconds for
the opening, early-career, and concert clips. This is not a performance benchmark.

Local artifacts use `m2-final-opening`, `m2-final-early-career`, and
`m2-final-concert` under `.local/runs/`. Re-run the guide's extraction commands
with fresh directory names, then pass the three reports to
`python -m tracen_replay.evaluate tests/fixtures/stat-reference-v1.json`.

## Acceptance criteria

- Fix the historical Stamina +5 regression using saved raw OCR and fresh video
  processing, without reference values entering recognition.
- Match partial/scrolled entries using surrounding text and order, rather than
  equal delta vectors alone. Preserve equal changes in different contexts and
  two entries visible together.
- Expose reappearance after gaps as provisional. Retain raw OCR, occurrence IDs,
  first/last observations, and paired evidence for matching decisions.
- Do not borrow future delta fields into earlier accounting intervals.
- Record sampled main-outcome visibility spans; keep repeated episodes separated
  by empty readings and ambiguous cross-pane matches unassigned. Action times
  stay null. Preview options never become completion evidence.
- Re-run the three development clips and retain the stat-reference checks.
  Keep remaining discrepancies visible.

## Method and limits

The parser tolerates a standalone trailing `|` caused by an OCR card-border
artifact. It preserves raw text and still rejects prefixes, suffix words,
projected gains, and possible outcomes.

Line geometry also separates adjacent cards: a vertical gap exceeding 30 pixels
between delta rows breaks a block even if OCR missed the intervening heading.

The log tracker aligns normalized ordered text. A match requires unique text with
surrounding non-stat context; generic awarded-resource lines cannot supply the
context, while named friendship text can. A small missing-heading gap can be bridged when preceding context
matches on both sides. Training headings are normalized separately from icons.

Matching after more than 1,250 ms is marked `context_reappearance`. This is
provisional, not proof that an identical event could not have happened again.
Unmatched and delta-only entries remain unanchored. Identity remains unverified.
An entry already observed at or before the starting checkpoint is excluded
regardless of whether that exclusion balances the arithmetic.

Main outcomes form separate episodes. Consecutive compatible readings must share
a field and be at most 500 ms apart. Empty readings, conflicts, and larger gaps
break episodes. A unique matching log vector provides a provisional cross-pane
association; multiple candidates remain ambiguous. Sampled visibility does not
bound click time or guarantee continuous visibility between samples.

A delayed log entry can also be linked to an earlier main outcome when both its
delta vector and preceding narrative text match. This requires exactly one
candidate, narrative text observed within 1,500 ms before the main outcome, and
log visibility within 10 seconds after the last outcome sample. The association
remains unverified and retains three evidence links: narrative, outcome, and
later log. Equal gains alone cannot exclude a delayed entry as historical.

This is a single-recording development baseline. Identical histories, exact
clicks, unseen layouts, and full-run event recall remain unvalidated. Reappearing
context and main-pane OCR fragmentation can still cause incorrect associations
or repeated candidates. Arithmetic balance does not certify completeness.

See [the pipeline guide](local-pipeline.md) for reproduction. Regression tests use
`tests/fixtures/historical-log-ocr.json` and `tests/fixtures/delayed-outcome-ocr.json`,
raw-OCR excerpts of the failures, plus synthetic repeated entries, missing
context, gaps, and outcome timing.
