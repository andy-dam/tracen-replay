# Full-recording analysis

Support outings can complete without an energy award. For these, the analyzer requires repeated outing confirmation, the matching support-event narrative shortly afterward, a mood/friendship receipt, and repeated observations of the next calendar date. Intervening training/race/rest actions or a repeated return to the stat hub reject the association. This establishes the outing without inventing energy or a click timestamp.

Dialogue OCR also checks the source gameplay pixels for small green overlays on light receipt backgrounds. Run `python -m tracen_replay.refine_overlay OUTPUT` before reparsing to locate numeric phrases using OCR word positions. Their bounds include the space after “by,” where an obscured leading digit may have disappeared from the recognized text. A materially overlapping numeric phrase is withheld; when localization is unavailable, the check conservatively withholds the affected line. Original text, confidence and obstruction bounds remain in `facts.occluded_receipt_lines`. Other readable frames must supply the effect; the overlay check never fills a digit from a ledger residual. This detects the current gameplay profile's overlay, not every cursor. Original OCR caches remain unchanged, and proof pixels and alignment files are checked against their recorded hashes.

The numeric rule does not suppress nonnumeric friendship statuses. If matching padded receipt views recover a leading digit missing from the tight alignment crop, the existing receipt consensus can retain that reading; `resolved_receipt_occlusions` records this exception. The views still count as one correlated frame. A crop agreeing only on the same shortened number does not qualify, and no ledger value enters the comparison.

An explicit large `FAILURE` banner on a training result is distinct from the preview's failure probability. It preserves the completed training action, records failure evidence, and rejects lingering performance projections from that result window. Any separately displayed aftermath, such as recovered energy, remains a separate receipt. The reviewed source example is recorded in `tests/fixtures/failed-training-regression-v1.json`.

Wrapped text does not create another transaction. A lower-confidence full event caption may link a truncated title only within the same short receipt window and with overlapping observed effects; its candidate status and continuation evidence are retained. Aligned technique-name fragments are joined before matching a lesson confirmation. A separate stat receipt is never absorbed into the name.

The full-recording runner samples the source from beginning to end, then inspects short training animations more densely. It exports observed states, transaction evidence, and unresolved changes. Processing every sample does not by itself verify every action.

## Local setup

Install Python, FFmpeg, and the vision extra:

```powershell
python -m pip install -e ".[vision]"
```

The neural reader uses RapidOCR 3.9.2 with ONNX Runtime 1.29.0, PP-OCRv6 small text detection, and English PP-OCRv5 mobile recognition. The first invocation downloads model weights into `.local/models/rapidocr`. Subsequent recognition runs locally on the CPU. The video is never sent to an OCR service. No cloud account is required for this analysis stage.

The current layout profile requires an English 1920×1080 recording with the gameplay pane at `(148, 0, 958, 1080)`. Every recognition call receives only that 810×1080 crop.

## Run and investigate

```powershell
python -m tracen_replay.full_recording "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --workers 4
python -m tracen_replay.inspect_training "C:\path\to\recording.mp4" --output .local/full-recording/run-01
python -m tracen_replay.refine_results .local/full-recording/run-01
python -m tracen_replay.refine_outcomes .local/full-recording/run-01
python -m tracen_replay.refine_currencies .local/full-recording/run-01
python -m tracen_replay.refine_performance .local/full-recording/run-01
python -m tracen_replay.refine_contrast .local/full-recording/run-01
python -m tracen_replay.full_recording "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --reparse-only
python -m tracen_replay.inspect_gaps "C:\path\to\recording.mp4" --output .local/full-recording/run-01
python -m tracen_replay.refine_results .local/full-recording/run-01
python -m tracen_replay.refine_awards .local/full-recording/run-01
python -m tracen_replay.refine_performance .local/full-recording/run-01
python -m tracen_replay.skill_variants .local/full-recording/run-01
python -m tracen_replay.refine_skill_points .local/full-recording/run-01
python -m tracen_replay.full_recording "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --reparse-only
python -m tracen_replay.verify_evidence "C:\path\to\recording.mp4" --output .local/full-recording/run-01
```

Run these commands sequentially for a complete report. The first command captures 120-second chunks at 4 FPS and caches per-frame OCR. The second examines detected training results and candidate result layouts at 30 FPS. The refinement commands check result totals with space for four-digit attributes and recheck uncertain receipt text with padded crops. Original observations remain available. `--reparse-only` checks cached observations and rebuilds the report with the current parser; `verify_evidence` separately checks the source, crop pixels and refinement provenance.

Repeat `refine_performance` after adding native training frames or changing result classification. It skips existing refinements and reads eligible frames that have not received currency recognition. Sampling a frame alone does not run every optional refinement.

Large event stat and currency animations can corroborate receipts: an exact field label, nearby large signed amount, corresponding receipt caption and at least three distinct frames spanning 50 ms are required. For stat animations, the caption may be visible in only one of those frames; the repeated labels and amounts must still pass recognition thresholds. A repeated stat animation can resolve shorter-prefix receipt readings. One other receipt outlier can be resolved only when at least three complete receipt readings spanning 50 ms also agree with the repeated animation. Original amounts and evidence remain recorded. Other numeric disagreements remain unresolved. Training projections and lesson offers are excluded.

For an unresolved ordinary receipt, inspect a bounded window of at most five seconds. Times select source pixels; expected text and amounts are never OCR inputs:

```powershell
python -m tracen_replay.inspect_receipts "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --start-ms 79750 --end-ms 80750
python -m tracen_replay.refine_receipts .local/full-recording/run-01
python -m tracen_replay.verify_evidence "C:\path\to\recording.mp4" --output .local/full-recording/run-01
python -m tracen_replay.full_recording "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --reparse-only
```

Receipt crop variants count as one frame. Agreement can recover cursor-obscured characters; disagreement invalidates the original reading without selecting an unsupported replacement. The final reparse includes the matching evidence-audit snapshot in the report.

### Bounded receipt sampling plans

The report can produce a source-bound plan for uncertain receipt observations
that are worth rereading. These are review targets, not confirmed missing events;
another frame may already support the same receipt. The planner uses occluded lines, unparsed receipt candidates,
and conflicting receipt observations as anchors, pads and merges nearby anchors,
and splits the result into windows of at most five seconds. Unresolved stat or
performance intervals affect ordering only; residual numbers and expected
labels are never supplied to OCR. A finite footage and frame budget leaves
deferred targets in the plan with their reasons. Existing receipt-inspection
windows at the requested sampling rate are skipped, so the same frames are not
resampled indefinitely. Optional Concert Info and inventory screens are not
receipt targets.

Create a dry-run plan without changing the report:

```powershell
python -m tracen_replay.receipt_sampling "C:\path\to\recording.mp4" --report .local/full-recording/run-01/report.json --output .local/full-recording/run-01/receipt-sampling-plan-v1.json
```

For a candidate report stored below a separate evidence root, provide that
root explicitly so the planner uses its `capture.json` and
`receipt-inspection.json`:

```powershell
python -m tracen_replay.receipt_sampling "C:\path\to\recording.mp4" --report .local/full-recording/run-01/candidate/report.json --evidence-root .local/full-recording/run-01
```

Frame budgets use the decoder's conservative `ceil(window duration × FPS) +
1` bound, so the selected windows remain within the cap after timestamp
rounding.

After reviewing the immutable plan, execute it with the existing receipt
inspector:

```powershell
python -m tracen_replay.receipt_sampling --execute-plan .local/full-recording/run-01/receipt-sampling-plan-v1.json
```

The executor checks the source, capture, report and prior receipt-inspection
hashes before invoking OCR. It refuses stale plans and existing plan-output
paths, leaves `report.json` unchanged, and writes only the bounded
`receipt-inspection` artifacts for a later `--reparse-only` run. A new plan is
required after those inspection artifacts change.

Training inspection reads both ordinary and enlarged friendship-training gains. It also reads result totals when animation obscures the blue grid headers. Repeated result totals can support a gain relative to a nearby observed pre-training state. They are separate evidence from the later career-hub checkpoint being reconciled. Conflicts between animated gains and stable result totals remain in the report.

Inspection does not resolve every kind of discrepancy. A result that never becomes a candidate, an obscured event receipt, an unknown mechanic, or an incomplete skill list can still require further source review.

Currency refinements widen the five resource-counter crops to avoid clipped leading digits. Contrast views are correlated re-readings, not independent frames. `inspect_gaps` captures unresolved training windows at native 60 FPS; `refine_awards` checks enlarged gain animations there. Skill circle suffixes use local image geometry and abstain on disagreement. Original OCR observations remain immutable. Parser changes use `--reparse-only`; do not rerun all neural OCR just to rebuild a report.

The currency command also rechecks unresolved menu balances with two crops that exclude neighboring badges and separators. These observations are stored separately in `currency-padding-refinement/`. Agreement can recover a complete counter; conflicting values remain unknown.

## Artifacts and provenance

### Hint-card identity preparation

When a cursor obscures a hint receipt's skill name, repeated readable skill cards
can establish the identity. Preparation requires matching event context and
geometry across adjacent source frames, repeated receipt-prefix OCR proving the
hint amount, and pixel evidence for the circle rank. It preserves the original
receipt spelling; a catalog name, ledger residual, or repeated misspelling does
not supply missing evidence.

After the last parser/refinement rebuild, prepare a new cache:

```powershell
python -m tracen_replay.hint_card_cache .local/full-recording/run-01/report.json --output .local/full-recording/run-01/hint-card-recovery.json
python -m tracen_replay.full_recording "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --reparse-only
```

Optional `--start-ms` and `--end-ms` bounds limit preparation to a source window.
Preparation invokes local OCR for receipt prefixes. Report replay loads the
cache without OCR and revalidates source frames, gameplay pixels, OCR sidecars,
prefix crop hashes, and the exact parsed rows in each candidate's span. A missing
cache leaves ordinary reconstruction available. A present invalid or stale cache
stops publication; it cannot silently drop prepared evidence. Preparation refuses
to overwrite an existing artifact. If parser or refinement changes invalidate a
cache, archive it, rebuild the report, then prepare a new cache from that final
state. Preserve the prior report and cache for comparison.

The cache supports base capture observations. Native inspection manifests
require a separate adapter. Wrapped receipts can combine a readable card with
literal receipt fragments and source-bound amount crops. The digit and its
following delimiter are checked together so a clipped multi-digit amount cannot
be accepted as one digit. Multiple crops of one frame are correlated evidence.
An unexposed circle rank stays undetermined; it is not evidence of an unranked skill.

Validated wrapped receipts enter copied observations before outcome grouping,
so an unparsed receipt does not itself force an event boundary. Observed screen
changes and unrelated narrative lines still block that association. Integration
checks the whole candidate batch for conflicting amounts or ranks. If a staged
candidate is rejected, reconstruction starts again from the original readings
without it; rejected evidence cannot leave an award in the timeline. The report
retains original candidate indices and rejection reasons. Raw readings stay
unchanged, and inheritance sparks never establish hint amounts.

### Source artifacts

For a single-line song receipt whose terminal music note was recognized as a
letter, run `python -m tracen_replay.song_symbol_refinement OUTPUT_DIRECTORY`,
then rebuild with `--reparse-only`. The pass verifies the source frame and exact
gameplay crop, requires note geometry and opening/closing quotes at two strict
intensity thresholds, and separately reads the title crop before the glyph.
That title must exactly match the original receipt's preceding text with at least
95 OCR confidence. Both thresholds must also expose one isolated letter-column
run per title letter, with gaps at the observed word boundaries; OCR agreement
alone cannot dismiss an extra glyph. This check currently supports alphabetic
titles with separated letters. A real trailing letter in the title crop, missing quotes,
uncertain text or unsupported geometry causes abstention. The cropped read is a
correlated view of the same source frame, not another independent observation.

The version 2 `song-symbol-refinement/` sidecars retain original OCR, crop hashes,
model fingerprint, symbol geometry and title glyph coverage. Replay and `verify_evidence` validate them
without OCR. Existing sidecars are checked rather than overwritten. The earlier
`song-symbols/` whitespace-only pass retains its original behavior.

For a wrapped song receipt with a dropped outlined star, run
`python -m tracen_replay.song_star_refinement OUTPUT_DIRECTORY` before rebuilding.
This separate pass requires aligned adjacent lines, closing quotes, and an
outlined star with one hole and ten alternating contour turns at two thresholds.
It rereads both text crops, checks their glyph coverage, and preserves the lower
confidence of the source lines and crop readings. Crop readings remain correlated
views of one frame. Filled stars, asterisks, open contours, uncertain text and
unsupported layouts cause abstention. The `song-star-refinement/` sidecars retain
both original lines, model and pixel hashes, text witnesses and geometry.
Cached replay and evidence verification validate these observations without OCR;
the joined song effect retains the complete original sentence and symbol proof.

When a corrected receipt differs from the earlier request's raw spelling, the
lesson association may use the symbol observation's preserved original text.
It requires repeated request and before/after debit evidence, keeps every
compatible request spelling, and rejects conflicting requests or canceled menu
returns. The corrected receipt does not verify the request's spelling. Costs
remain supported by observed balances, never by the corrected song name alone.

- `capture.json`: source SHA-256, duration, layout, decoded PTS, and the complete base sampling manifest.
- `neural/`: original OCR lines, numeric regions, model hashes, engine fingerprint, and source-frame hash.
- `gameplay/`: cropped evidence images.
- `training-inspection/`: denser source frames, PTS manifests, gameplay evidence, and versioned OCR observations.
- `outcome-refinement/`: original-observation hash and the accepted or rejected line rechecks.
- `report.json` and `index.html`: reconstructed observations, transactions, accounting, and verification status.
- `currency-refinement/` and `skill-variants/`: source-bound counter and suffix observations.
- `skill-points-refinement/`: recognition of the menu counter from two gameplay crops when text detection misses a small number. Both views are one frame's evidence; disagreement remains unresolved.
- `song-symbols/`: optional pixel observations of a music-note suffix omitted from a song receipt. Run `python -m tracen_replay.song_symbols OUTPUT_DIRECTORY`, then rebuild with `--reparse-only`. The pass requires an isolated note and closing quotes in the supported receipt layout; it preserves original OCR and never substitutes a song catalog name. Threshold variants are correlated views of one frame. Other fonts, clipped symbols, recognized letters and uncertain shapes remain unresolved.
- `native-inspection.json`: active native-rate windows and their source references.
- `receipt-inspection.json` and `receipt-inspection/`: bounded ordinary-receipt observations and crop variants, preserving original text and disagreements.
- `evidence-audit.json`: whole-source identity, decoded frame hashes, exact crop pixels and refinement hashes. Re-run this audit after changing evidence artifacts; it is an integrity snapshot, not a semantic accuracy score.

Keep all derivatives of one source recording in the same evaluation split. Model confidence and repeated agreement are evidence filters, not calibrated probabilities of correctness.

## Independent recording

Record another complete run in the supported layout at a normal playing pace, including final skill purchases and the results screen. Use a new output directory. Do not reuse a clip, crop or re-encode of the development run as an independent test.

Before examining its results, record the Git revision, model hashes and analysis commands. Run that revision first and preserve its report. Label fixed contiguous time ranges directly from the new video, including intervals where the analyzer reports nothing. Score missed receipts, extra receipts, incorrect values and unsupported claims separately. Missing UI evidence must remain unknown rather than receiving an assumed value.

If the new run reveals a bug, retain the original evaluation before fixing it. Once its examples guide development, subsequent results on it are regression results; another untouched source is needed for a fresh generalization claim.

## Transaction rules

Lesson receipts are matched to named confirmations. Their displayed costs are checked against before/after performance balances when available. Actual stat receipts remain separate from offered bonuses. A canceled confirmation does not create a purchase. Song names that wrap onto another line are joined only when the closing quotation and line geometry agree.

Skill charges require an acquisition receipt plus independent balance evidence. The menu counter can be a projected balance, so selecting, removing, and reselecting an item does not create multiple purchases. Visible confirmation names and menu-price transitions provide item candidates; an item-price sum matching the charge is reported separately from a complete, verified inventory. Symbol variants and off-screen items remain explicit gaps.

For consecutive purchases between shared actual checkpoints, the parser can verify charges jointly: each purchase needs a confirmation, receipt and repeated final projected counter, and the last counter must match the observed settled balance. Uncovered receipts, other observed SP changes, inconsistent charges or missing counter evidence prevent this assignment. Cart changes are scoped to their own receipt. The report explicitly labels intermediate balances as projected, not independently observed settled states.

Race results preserve placing, course, fans, and uncertain item rewards separately from character stats. Concert results reference their aftermath receipts and previously queued bonus candidates. A queued bonus is not automatically marked as visually verified activation. Stat caps, performance caps, fans, hints, energy, and friendship are distinct effect types.

## Readiness

The verification report checks base sampling continuity, source endpoints, PTS consistency, missing OCR observations, and coverage in each minute. It also lists unresolved stat/resource intervals and incomplete transactions.

The Go application remains behind the reliability gates in [gameplay-only reconstruction](gameplay-only.md). A full source scan, balanced arithmetic, and successful development examples are not substitutes for complete action/effect review or validation on a separate recording.
