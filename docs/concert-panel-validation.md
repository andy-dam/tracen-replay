# Concert bonus panels

Concert Info shows current bonuses on the left and planned values on the right. A single value is unchanged in that snapshot. Neither layout proves that the next concert occurred. The parser retains the raw text, confidence, box and normalized text under `concert_bonus_evidence`.

The support-chain level is a numeric slot. An isolated `O` immediately after `Lvl` is normalized to zero only inside the recognized Concert Info panel and above the existing confidence threshold. Longer malformed tokens are rejected. A missing or low-confidence level remains unknown. This normalization is an interpretation of OCR text, not an independent pixel observation or calibrated confidence improvement.

## Reviewed panels

Eight gameplay screenshots from the two development recordings were visually reviewed. The reference records both columns for friendship effectiveness, specialty priority and support-chain event frequency: 48 field values in total. Source timestamps, screenshot hashes and immutable OCR are preserved in `tests/fixtures/concert-panels.json`.

The separate recording's four panels show these current → planned values:

| Time | Friendship effectiveness (%) | Specialty priority | Support-chain level |
|---|---|---|---|
| 11:29.250 | 15 → 15 | 5 → 15 | 0 → 1 |
| 15:58.250 | 15 → 15 | 15 → 15 | 1 → 3 |
| 21:02.750 | 15 → 35 | 20 → 20 | 3 → 3 |
| 25:01.750 | 40 → 50 | 25 → 25 | 3 → 3 |

The original recording's four panels show:

| Time | Friendship effectiveness (%) | Specialty priority | Support-chain level |
|---|---|---|---|
| 11:01.750 | 10 → 15 | 10 → 15 | 0 → 0 |
| 20:51.250 | 15 → 40 | 25 → 25 | 3 → 3 |
| 24:51.250 | 45 → 65 | 25 → 25 | 3 → 3 |
| 25:57.000 | 45 → 65 | 25 → 30 | 3 → 5 |

Base OCR leaves the original first panel's level zero below the confidence threshold, giving 46/48 matches. A separate source-backed refinement now recovers the two missing values, bringing the reviewed panel score to 48/48 with no incorrect values. The initial evaluation is preserved separately from the refined score. Repeated-frame aggregation has a separate requirement: a readable value in one frame does not suffice to establish a later active snapshot.

The refinement uses three distinct base frames at 662000, 662250, and 662500 ms. Each yields numeric `Lvl 0` above the existing threshold after a documented crop/contrast pass. Their decoded PTS, source images, immutable panel OCR, screenshot hashes, panel anchors, and `2nd Concert` context are checked. Support frames must form a bounded sequence near the target panel. A visible current-to-planned transition, conflicting numeric readings, or missing provenance prevents application. The original uncertain `Lvl O` remains in the OCR record.

Run-local artifacts under `concert-panel-refinement` are applied during cached reparsing and checked by the evidence auditor, including their supporting files. The original run's stat, performance, and fan ledgers remain balanced after integration. No later active value is inferred from a pre-concert plan.

Automatic generation now reproduces this recovery. An initial contrast-only replay emitted no refinement because only two frames cleared the unchanged threshold. Investigation showed that the third accepted observation in the earlier probe used grayscale/autocontrast without added contrast; its preprocessing annotation has been corrected, with the original artifact archived. The generator now produces both views for every eligible frame and submits them together to consensus. Views from one timestamp count once, and conflicting accepted values cause abstention.

An isolated replay of all 16 base frames recognized as Concert Info panels generated one refinement and retained two later rejections. It recovered the same three supporting observations without receiving reference labels. Both the failed and successful reproduction artifacts remain preserved. Run `python -m tracen_replay.refine_concert_panels RUN_DIRECTORY` before cached reparsing. This verifies reproduction of the reviewed case, not general panel recall.

## Scope and remaining work

These panels were selected from detected screens, so this check does not measure missed-panel recall. The later active snapshots establish only the three displayed fields at their observed times. They do not establish all bonuses, full skill inventory, or final active values after the last concert. The final pre-concert plan cannot substitute for an observed final active panel. No readiness gate is closed by this score alone.

A subsequent source review of the separate recording covers 1583–1758 seconds with 175 one-second gameplay samples and 51 quarter-second samples around completion transitions. It finds the bonus-update receipt, but no current-bonus value panel in those samples. Completion screens show Attributes; the later skill screens are purchase carts or acquisition receipts, and Sparks are a separate system. An additional ending review checks all 75 quarter-second samples from 1758 seconds through the last sampled frame: the first owned-skill page appears, closes without scrolling, and does not reopen. These reviews support retaining unknown final bonuses and incomplete inventory. They do not prove absence between samples or establish full action/effect recall.

Run `python -m tracen_replay.concert_evaluate REFERENCE REPORT --evidence-root RUN_DIRECTORY --output SCORE_FILE` to check a panel reference against a report. The scorer separates missing values from incorrect values, checks source and screenshot identity, and penalizes swapped current/planned columns.

A later fixed-layout screening prototype classified all 24,915 native frames in
the two bounded post-fifth-concert spans. It found zero panel candidates and
582 uncertain frames in 20 groups. Review of the selected frames from every
uncertain group found other game screens, with no Concert Info panel. The eight
known panel fixtures all pass the detector, but they are calibration examples,
not an independent recall test. Unselected frames within longer groups and
frames classified as neither candidate nor uncertain remain outside visual
review. See the local `post-fifth-active-bonus-candidate-screening-adjudication-v1.json`.

A separate sensitivity check demonstrates a concrete blind spot: scaling the
eight calibration images to half brightness makes all eight fall outside both
candidate and uncertain categories. At 70% and 80% brightness, all eight remain
uncertain and would be queued for review. These are synthetic controls, not
observations of the recording. The test changes no thresholds and is preserved
in `concert-info-detector-fade-sensitivity-v1.json`. The screening result supports
continued unknown final bonus values; it cannot certify that no transient panel
appeared.
