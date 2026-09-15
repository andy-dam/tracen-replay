# Core accounting improvements

This milestone follows [evaluation hardening](evaluation-hardening.md). It improves the existing Python analyzer and its accounting before Go integration. All three recordings remain development data.

The subsequent [turn explanation milestone](turn-explanation-improvements.md) records newer boundary coverage, attribution results, and remaining evidence limits. Counts below describe this earlier milestone.

## Results

The three complete reports were reconstructed and compared with preserved starting reports. The original's missing **Passion +10** is now supported by six directly readable receipt frames at **962317–962483 ms**, before the cursor obscures the amount. It is counted once. No amount was inferred from other currencies or a balance difference.

The original also gains one observed performance opening at **637750 ms**, with Dance/Passion/Vocal/Visual/Composure **89/51/56/52/85**. Those values all occur in one actual frame. Nearby partial frames corroborate every field; projected training gains are excluded.

These are individual field-comparison counts across observed turn windows, not numbers of complete or broken turns:

| Recording | Unresolved attribution, before → after | Missing endpoints, before → after | Unexplained residuals, before → after |
| --- | ---: | ---: | ---: |
| Original | 60 → 16 | 78 → 68 | 1 → 0 |
| Second | 62 → 16 | 114 → 114 | 0 → 0 |
| Third | 33 → 10 | 89 → 89 | 0 → 0 |
| Total | **155 → 42** | **281 → 271** | **1 → 0** |

Most of the attribution improvement fixes an accounting bug: a disagreement in one field blocked every contribution from that event. It is not an increase in OCR recall. Disputed fields remain disputed, including when their amounts fit the observed totals.

Final turn comparisons contain **1,679 balanced comparisons using direct observations** and **417 balanced comparisons involving derived changes**. Arithmetic balance still does not establish complete event history or exclude offsetting mistakes.

All 234 selected actions, their training-result classifications, accepted checkpoints, numeric event summaries, stat interval totals, lesson purchases, skill purchases, races and dialogue choices are preserved. Canonical accepted effect counts are **876 → 877**, **839 → 839**, and **787 → 787**. No previously accepted canonical effect was removed. Some observation windows/evidence lists can change without representing a different occurrence.

The same 20 partial source-reference files were graded before and after. No previously correct reference regressed. Their strict historical timing scores do not credit the new Passion evidence: the old label anchors 962750 ms, while the newly readable receipt frames precede that instant. The original labels remain unchanged. The additional source fixture tests the observed receipt directly; it is a development regression, not independent validation.

**1,254 tests ran; the suite passed with one skipped.** Full-report comparisons and source-frame integrity checks are separate from those tests.

## Implemented mechanisms

1. **Field-scoped conflicts.** Stats/SP and performance disagreements affect their own channels and fields. Named-effect, energy, mood, fan and condition conflicts do not invalidate unrelated numeric awards. Unknown or malformed conflict scopes remain conservative. Duplicate physical claims and unsupported timing still block accounting.
2. **Bounded numeric receipt recovery.** Visible numeric receipt captions with no accepted effect request short, denser samples. This pass uses text and source occurrences, not residuals, character-specific amounts or recording timestamps. Seventeen windows across the three recordings selected 20 seconds of footage, producing 602 source samples at 30 FPS. Promotion is limited to requested numeric fields inside one identified existing occurrence. Unowned or out-of-scope observations remain diagnostic evidence. Full OCR is retained in the cache, but unrelated names, states and action facts are not replaced. Persistent stat receipts remain deduplicated through supported OCR gaps, including repeated terminal punctuation.
3. **Complete snapshots corroborated by partial frames.** A missing opening may use one complete pre-action snapshot when every field repeats at another source timestamp within one second. Conflicting values, intervening events, purchases and uncertain calendar transitions veto this fallback. Separate frames never manufacture a complete tuple, and later states never fill an earlier opening.

Recovery honors the selected model directory, checks cached model/source/proof identities, and joins the evidence-integrity manifest. The default pass permits at most 20 new windows and 30 seconds of source footage. Budget-limited requests remain pending. `--reparse-only` consumes existing recovery observations without starting a new full-frame OCR pass.

Supplemental receipt OCR preserves the baseline title, primary screenshot, OCR, stats and unrelated facts. Alternate observations remain available separately, conflicting effect values survive, and accepted recovered amounts include their readable alternate screenshot in canonical field evidence. Additional OCR views of one timestamp do not count as independent temporal corroboration. The final review's two evidence-handling findings are covered by regressions and the full-report audit.

## Remaining gaps and Go requirements

- **42 field comparisons still have unresolved attribution; 271 lack an endpoint.** They remain core reliability gaps. Missing visible states and effects are defects, not optional polish.
- **417 balanced field comparisons involve derived changes.** Those transitions are not independently explained by receipts alone.
- The second recording's candidate performance opening has repeated partial observations but no complete pre-action snapshot. The third recording's race-day stats singleton lacks corroboration. Neither was promoted merely to improve coverage.
- Some energy receipts, hints, friendship identities, condition variants, skill-purchase costs and finale boundaries remain unresolved. Unpromoted recovery observations are not additional awards.
- All 75 unknown training-result classifications remain unknown. They are not 75 missing actions.
- Complete inventory, absolute energy and optional concert information cannot always be established from what the recording shows. Readable misses and genuinely unobserved information must remain distinct.
- **Independent full-run validation remains outstanding.** The three supplied runs have influenced development. The only other MP4 found in the Videos directory is a 78.7-second clip, not an additional full run. No untouched full run was available for this milestone.

Go may consume these reports under [the limited integration acceptance](first-go-acceptance.md). It must preserve missing values, direct/derived basis, disputed fields, source pointers and partial inventories; count each canonical contribution once; display opening timestamps and corroboration evidence; and distinguish processed recovery windows from recovered effects. It must not present balanced totals as a complete explanation. No Go or cloud implementation was added by this milestone.

## Artifacts and reproduction

The authoritative outputs are under the ignored local directory `.local/core-accounting-v1/final-v3/`: three reports, their replay audits, before/after comparisons, and `integrity-audit.json`. Starting reports are preserved under `before/` and hash-bound by `baseline-manifest.json`. Earlier candidate folders are diagnostic attempts, not accepted results.

Each report's `evaluation_context.evidence_root` identifies the corresponding original recording cache. These evaluation artifacts are not standalone bundles; their relative screenshot paths resolve against that root. New source samples live under each cache's `numeric-receipt-recovery/`. The earlier report's evidence-integrity snapshot is not carried forward as certification of newly captured frames.

The integrity audit verifies the starting reports, unrelated existing work, current report contracts and accounting, canonical effect retention, source-reference comparisons, 22 unchanged source-label/amendment files, all 602 recovery frames against source hashes/PTS/crop pixels, readable recovery proofs in canonical field evidence, and the reviewed fixture images. The compact fixture is `tests/fixtures/core-accounting-source-cases.json`.

Run tests:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -q
```

Prepare/reparse recovery observations and rebuild a report into new output paths:

```powershell
.venv/Scripts/python.exe scripts/prepare_receipt_recovery.py .local/core-accounting-v1/before/v1-report.json --config .local/evaluation-hardening-v1/v1-replay-config.json --output .local/core-accounting-v1/v1-recheck-input --reparse-only
.venv/Scripts/python.exe scripts/replay_core_accounting.py .local/core-accounting-v1/before/v1-report.json --observations .local/core-accounting-v1/v1-recheck-input/observations.json --config .local/evaluation-hardening-v1/v1-replay-config.json --output .local/core-accounting-v1/recheck/v1-report.json
```

Use `independent-01-replay-config.json` for the second recording and `independent-02-replay-config-v3.json` for the third. These rebuild from all preserved parsed observations plus source-checked recovery samples; they do not rerun OCR across entire videos. Omit `--reparse-only` to permit bounded new recovery samples. Full processing through `tracen_replay.full_recording` runs this recovery stage before final reconstruction.

To audit the three freshly rebuilt reports, supply their directory and a new audit-output path. Existing comparison files are checked for reproducibility; missing comparisons are written:

```powershell
.venv/Scripts/python.exe scripts/audit_core_accounting.py --final .local/core-accounting-v1/recheck --output .local/core-accounting-v1/recheck/integrity-audit.json
```
