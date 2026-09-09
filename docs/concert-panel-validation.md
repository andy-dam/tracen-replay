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

Automatic generation is still under validation. An isolated replay of all 16 base frames recognized as Concert Info panels reproduced the support-level text, but only two supporting crops cleared the unchanged confidence threshold. It correctly emitted no refinement. The 48/48 score therefore describes the current evidence-backed reports; it does not yet prove that a fresh automatic run reproduces the recovery. The difference between the original batched OCR probe and the generator must be resolved before claiming that capability.

## Scope and remaining work

These panels were selected from detected screens, so this check does not measure missed-panel recall. The later active snapshots establish only the three displayed fields at their observed times. They do not establish all bonuses, full skill inventory, or final active values after the last concert. The final pre-concert plan cannot substitute for an observed final active panel. No readiness gate is closed by this score alone.

Run `python -m tracen_replay.concert_evaluate REFERENCE REPORT --evidence-root RUN_DIRECTORY --output SCORE_FILE` to check a panel reference against a report. The scorer separates missing values from incorrect values, checks source and screenshot identity, and penalizes swapped current/planned columns.
