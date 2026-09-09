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

The original first panel's level zero remains below the confidence threshold. Its two reference values are counted as missing, not converted to passing unknowns. The other 46 values match. Repeated-frame aggregation has a separate requirement: a readable value in one frame does not suffice to establish a later active snapshot.

## Scope and remaining work

These panels were selected from detected screens, so this check does not measure missed-panel recall. The later active snapshots establish only the three displayed fields at their observed times. They do not establish all bonuses, full skill inventory, or final active values after the last concert. The final pre-concert plan cannot substitute for an observed final active panel. No readiness gate is closed by this score alone.

Run `python -m tracen_replay.concert_evaluate REFERENCE REPORT --evidence-root RUN_DIRECTORY --output SCORE_FILE` to check a panel reference against a report. The scorer separates missing values from incorrect values, checks source and screenshot identity, and penalizes swapped current/planned columns.
