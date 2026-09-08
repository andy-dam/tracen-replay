# Full-recording analysis

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
python -m tracen_replay.skill_variants .local/full-recording/run-01
python -m tracen_replay.full_recording "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --reparse-only
python -m tracen_replay.verify_evidence "C:\path\to\recording.mp4" --output .local/full-recording/run-01
```

Run these commands sequentially for a complete report. The first command captures 120-second chunks at 4 FPS and caches per-frame OCR. The second examines detected training results and candidate result layouts at 30 FPS. The refinement commands check result totals with space for four-digit attributes and recheck uncertain receipt text with padded crops. Original observations remain available. `--reparse-only` checks cached observations and rebuilds the report with the current parser; `verify_evidence` separately checks the source, crop pixels and refinement provenance.

For an unresolved ordinary receipt, inspect a bounded window of at most five seconds. Times select source pixels; expected text and amounts are never OCR inputs:

```powershell
python -m tracen_replay.inspect_receipts "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --start-ms 79750 --end-ms 80750
python -m tracen_replay.refine_receipts .local/full-recording/run-01
python -m tracen_replay.verify_evidence "C:\path\to\recording.mp4" --output .local/full-recording/run-01
python -m tracen_replay.full_recording "C:\path\to\recording.mp4" --output .local/full-recording/run-01 --reparse-only
```

Receipt crop variants count as one frame. Agreement can recover cursor-obscured characters; disagreement invalidates the original reading without selecting an unsupported replacement. The final reparse includes the matching evidence-audit snapshot in the report.

Training inspection reads both ordinary and enlarged friendship-training gains. It also reads result totals when animation obscures the blue grid headers. Repeated result totals can support a gain relative to a nearby observed pre-training state. They are separate evidence from the later career-hub checkpoint being reconciled. Conflicts between animated gains and stable result totals remain in the report.

Inspection does not resolve every kind of discrepancy. A result that never becomes a candidate, an obscured event receipt, an unknown mechanic, or an incomplete skill list can still require further source review.

Currency refinements widen the five resource-counter crops to avoid clipped leading digits. Contrast views are correlated re-readings, not independent frames. `inspect_gaps` captures unresolved training windows at native 60 FPS; `refine_awards` checks enlarged gain animations there. Skill circle suffixes use local image geometry and abstain on disagreement. Original OCR observations remain immutable. Parser changes use `--reparse-only`; do not rerun all neural OCR just to rebuild a report.

## Artifacts and provenance

- `capture.json`: source SHA-256, duration, layout, decoded PTS, and the complete base sampling manifest.
- `neural/`: original OCR lines, numeric regions, model hashes, engine fingerprint, and source-frame hash.
- `gameplay/`: cropped evidence images.
- `training-inspection/`: denser source frames, PTS manifests, gameplay evidence, and versioned OCR observations.
- `outcome-refinement/`: original-observation hash and the accepted or rejected line rechecks.
- `report.json` and `index.html`: reconstructed observations, transactions, accounting, and verification status.
- `currency-refinement/` and `skill-variants/`: source-bound counter and suffix observations.
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

Race results preserve placing, course, fans, and uncertain item rewards separately from character stats. Concert results reference their aftermath receipts and previously queued bonus candidates. A queued bonus is not automatically marked as visually verified activation. Stat caps, performance caps, fans, hints, energy, and friendship are distinct effect types.

## Readiness

The verification report checks base sampling continuity, source endpoints, PTS consistency, missing OCR observations, and coverage in each minute. It also lists unresolved stat/resource intervals and incomplete transactions.

The Go application remains behind the reliability gates in [gameplay-only reconstruction](gameplay-only.md). A full source scan, balanced arithmetic, and successful development examples are not substitutes for complete action/effect review or validation on a separate recording.
