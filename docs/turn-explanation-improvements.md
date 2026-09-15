# Turn explanation improvements

This milestone follows [core accounting improvements](core-accounting-improvements.md). It improves the Python analyzer's observed turn boundaries and evidence attribution before Go integration. All three recordings remain development data; an untouched full run is still needed for independent validation.

## Full-report results

The starting reports and source labels were preserved. Each complete report was reconstructed from its existing observations plus bounded, source-linked recovery frames.

These are individual resource-field comparisons between turn openings, not an accuracy percentage or a count of missing events:

| Recording | Missing endpoint comparisons, before → after | Unresolved attribution, before → after |
| --- | ---: | ---: |
| Original | 68 → 58 | 16 → 8 |
| Second | 114 → 66 | 16 → 6 |
| Third | 89 → 42 | 10 → 1 |
| Total | **271 → 166** | **42 → 15** |

No unexplained arithmetic residual was introduced. The final turn comparisons contain 1,785 balanced fields using direct observations and 443 balanced fields involving derived changes. Better endpoint coverage can increase the derived count by making a previously unmeasurable comparison possible.

For whole-turn metrics, eligibility is a calendar-dated turn with exactly one attributed committed action. All eleven fields—five stats, skill points, and five performance currencies—must qualify. There are 60 eligible turns in each recording. Startup, undated finale, and ambiguous action windows are still included in the separate field-gap inventory; they are not silently counted as successful turns.

| Eligible turn metric | Original, before → after | Second, before → after | Third, before → after | Total |
| --- | ---: | ---: | ---: | ---: |
| All eleven opening/closing fields observed | 56 → 58 | 49 → 57 | 51 → 60 | **156 → 175 / 180** |
| All eleven numeric changes balanced with direct evidence | 10 → 10 | 6 → 9 | 12 → 17 | **28 → 36 / 180** |
| All eleven balanced, with some derived changes | 35 → 43 | 34 → 43 | 33 → 42 | **102 → 128 / 180** |

**Direct numeric balance is not verified complete event history.** It does not prove that every hint, condition, energy change, or offsetting pair of effects was recognized. The independently verified complete-history count remains unmeasured (`null`), rather than an invented accuracy score.

All 234 selected actions, their success/unknown/failure classifications, accepted stat checkpoints, numeric event amounts, and stat interval totals are unchanged. All **2,503 canonical effects are retained**, with none added or removed. The improvement comes from recovered state evidence and better handling of existing gain observations, not an asserted increase in complete event recall. Performance accounting changes reflect newly observed panels.

All 20 partial source references retain their prior grading results. The audit verifies 22 unchanged label/amendment artifacts, 10 preserved baseline artifacts, actual opening-value pointers, allowed additive enrichment, and all 508 new training/boundary source samples. Separate checks bind the final reports to the current analyzer code and verify the three recording-file hashes. These checks establish provenance and regression safety, not independent recognition accuracy.

**The final test suite passed: 1,302 tests ran, with one skipped.** Regressions cover mixed training component phases, duplicate timestamps, conflicting and partial snapshots, cache/source tampering, acquisition warnings, and auditor rejection of mutated observations or unsupported opening claims. No Go implementation was changed.

## What changed

1. **Performance panels have their own identity checks.** The persistent sidebar can establish balances even when the broader screen is classified as unknown. Both heading words, all five labels, row geometry, confidence, and source-read caps must match. Caps can change with scenario progression; each balance must fit its own observed cap. Existing contradictory values prevent enrichment. Preview and awarded gains retain their existing separate rules.
2. **Partial snapshots retain independently supported fields.** When no complete opening is available, the ledger can preserve one actual partial frame if each included field repeats at another source timestamp nearby. It never constructs a full tuple by combining different frames. Missing fields stay absent. Conflicting values, intervening events or purchases, and uncertain calendar boundaries prevent unsafe corroboration.
3. **Short source probes corroborate fast transitions.** Missing or partial openings with evidence of a visible panel request up to ±200 ms around the most readable pre-action row, bounded by the turn and committed action. Four windows across the second and third recordings covered 1.4 seconds at 60 FPS, producing 84 source samples and 71 new state-only timestamps. These rows feed the ledger, not event grouping or action recognition.
4. **Training badge conflicts use source observations.** Bounded native-frame probes supply additional readings of disputed gain digits. Brief strict-prefix readings can be resolved only with repeated complete digits at distinct timestamps and without evidence of a separate component phase. Balances and expected rewards are not inputs to that decision. The three runs added 424 source samples and 84 new training-observation timestamps. Unresolved alternatives remain visible.
5. **Committed lesson prices remain explicitly derived.** A receipt-linked purchase with matching named offer and request proofs can contribute its source-priced debit. It requires repeated proof timestamps before the receipt and an unconflicted acquisition. Browsing a price is insufficient. This basis is `committed_offer_cost_derived`, not an observed balance change.
6. **Acquisition uncertainty reaches the timeline.** A conflicting acquired name flags its canonical event and linked transaction entries, with source references and observed alternatives. The HTML report shows a visible name warning and a deduplicated list of alternatives. Unrelated transactions do not inherit the flag. Numeric awards remain separate from a disputed name.

The third recording's previously singleton Career state around 622250 ms is now corroborated by readable native frames showing **319 / 251 / 361 / 225 / 444 / 554**. It is a sampled transition we had missed, not information absent from the video. The second recording's earlier partial performance panel now has a complete source tuple **159 / 52 / 53 / 69 / 68** around 1366450 ms.

Recovery preserves existing observations, primary evidence, and unrelated fields. Another OCR view of one physical timestamp cannot supply a second temporal vote. Cache reuse checks source, frame, timestamp, crop, and OCR provenance. `--reparse-only` leaves uncached requests pending without starting OCR. The default training probe budget is 24 new windows and 15 seconds of source; boundary recovery permits 12 windows and 6 seconds. Budget exhaustion is reported, not treated as recognition success.

## Remaining evidence limits

- **166 resource-field comparisons still lack an endpoint; 15 have unresolved attribution.** These are real limits of the current explanations. The detailed inventory separates missing entire openings from missing fields within a partial opening and from unavailable closing endpoints.
- **Readable low-confidence text remains.** In the second recording around 1583633 ms, the preview visibly shows Composure 56 beside projected `+19`. OCR merges the line with confidence below the acceptance threshold. The opening keeps Composure unknown; neither 75 nor the projected 19 is substituted.
- **Race entrant attributes need a verified identity link.** Several race-entry screens visibly contain five stats, but the runner carousel can show another entrant. Those values cannot safely become trainee stats without an own-runner identity guard. The reviewed panels do not show skill points or performance currencies. This is readable information with unresolved ownership, not wholly invisible footage.
- **A missing parse is not proof of missing footage.** Fifteen representative frames from the initial five turn windows across the three runs show no performance panel. This does not establish absence throughout each interval. Initial story/race windows and finale boundaries have different evidence limits; unsampled or unreviewed moments remain unreviewed. The third-run infirmary opening was recovered from a visible panel at 1371750 ms, before the confirmation and result, rather than borrowed from a later turn.
- **Some training gain digits still conflict.** Dense sampling cannot guarantee an unoccluded, stable badge. Conflicting digits and possible component phases remain disputed even if one amount would balance the totals.
- Hints, conditions, energy, friendship identities, skill-purchase costs, and optional concert information retain the limits documented in the earlier reports. This milestone preserves their existing effects and exposes acquisition-name uncertainty; it does not claim exhaustive new recognition of those channels.
- The three recordings influenced development. **Independent full-run validation remains outstanding.** No fourth untouched full recording was available.

The remaining opening gaps cover 23 states and 116 fields: 93 initial fields with limited source review, 10 readable race stats whose runner identity is unverified, two skill-point fields absent from reviewed runner cards, 10 performance fields absent from those reviewed cards, and one low-confidence Composure field. Missing openings can affect both neighboring comparisons. Another 27 fields lack a next endpoint in the terminal windows. These categories explain the 166 field-comparison gaps; they are not additional missing-event counts. The complete local categorization and source pointers are in `remaining-boundary-causes.json` and `.md` alongside the milestone artifacts.

These limits fit the [limited Go integration acceptance](first-go-acceptance.md). A consumer must expose unknown fields, conflicting alternatives, observed versus derived basis, actual observation timestamps, and source screenshots. It must not label an arithmetic balance as a complete explanation. No Go or cloud code was added by this milestone.

## Artifacts and reproduction

The local artifacts are under `.local/turn-explanations-v1/`. `before/` preserves the starting reports, and `baseline-manifest.json` records their original hashes. `native-v1/` contains source-bound training observations; `combined-v1/` adds performance-panel enrichment and boundary probes. **`final-v3/` is the accepted output directory**, containing full reconstructed reports, inventories, replay audits, source-reference comparisons, `integrity-audit.json`, and `source-and-code-integrity.json`. Earlier final/candidate directories are superseded. Screenshot paths resolve against each report's `evaluation_context.evidence_root`; these reports are not standalone evidence bundles.

Run the tests:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -q
```

Reproduce one report from existing caches into fresh paths:

```powershell
.venv/Scripts/python.exe scripts/prepare_training_gain_recovery.py .local/turn-explanations-v1/before/v1-report.json --config .local/evaluation-hardening-v1/v1-replay-config.json --output .local/turn-explanations-v1/recheck/v1-training.json --reparse-only
.venv/Scripts/python.exe scripts/prepare_boundary_state_recovery.py .local/turn-explanations-v1/before/v1-report.json --observations .local/turn-explanations-v1/recheck/v1-training.json --config .local/evaluation-hardening-v1/v1-replay-config.json --output .local/turn-explanations-v1/recheck/v1-combined.json --reparse-only
.venv/Scripts/python.exe scripts/replay_turn_explanations.py .local/turn-explanations-v1/before/v1-report.json --observations .local/turn-explanations-v1/recheck/v1-combined.json --config .local/evaluation-hardening-v1/v1-replay-config.json --output .local/turn-explanations-v1/recheck/v1-report.json
```

Use `independent-01-replay-config.json` for the second recording and `independent-02-replay-config-v3.json` for the third. Omit `--reparse-only` to permit bounded new probes. These commands reconstruct the full report from preserved observations and selected additional source frames; they do not rerun OCR over every frame of the video.

After rebuilding all three reports in a new directory, audit them with:

```powershell
.venv/Scripts/python.exe scripts/audit_turn_explanations.py .local/turn-explanations-v1/recheck --output .local/turn-explanations-v1/recheck/integrity-audit.json
```
