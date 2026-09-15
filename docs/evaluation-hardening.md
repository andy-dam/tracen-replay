# Evaluation and causal accounting hardening

This work follows the completed [original-run baseline](full-run-baseline.md). The baseline remains a historical result; new evaluator and analyzer results must be reported separately. All three existing recordings are development data.

Subsequent [core-accounting improvements](core-accounting-improvements.md) recover the Passion +10 receipt and one missing opening, and correct field-level conflict scoping. The figures and remaining-gap notes below describe this earlier milestone.

## Verified results

| Recording | Training actions | Explicit success before → after | Remaining unknown outcomes | Additional friendship awards |
| --- | ---: | ---: | ---: | ---: |
| Original (`v1`) | 59 | 0 → 32 | 27 | 5 |
| Second (`independent-01`) | 60 | 0 → 38 | 21 | 0 |
| Third (`independent-02`) | 57 | 0 → 29 | 27 | 0 |

The two already recognized failures remain failures. Success counts measure recovered metadata, not training-action accuracy. Across all three recording replays, selected actions, numeric event changes, stat interval totals, accepted checkpoints, lesson and skill purchases, races, dialogue choices and performance accounting are unchanged. No previously accepted effect was removed.

The five additional awards were checked against their full-size screenshots: Nishino Flower +7, Fine Motion +7, Light Hello +2, Agnes Tachyon +7 and Super Creek +7. Eight compact development fixtures preserve these and one observed success from each recording.

The shared evaluator compares 20 explicit reference files: 1,396 normalized rows from the original recording, 62 from the second and 85 from the third. These partial references and empty scopes cannot establish whole-video accuracy. The original gains 29 fully correct scored rows under the shared rules; no previously correct row regresses. The other two recordings' reference scores are unchanged. These shared scores use different timing rules from the preserved historical adjudicated scores and must not replace them.

**1,228 tests ran; the suite passed with one skipped.** The integrity audit reproduces final accounting and comparisons, verifies 239 preserved starting files, and confirms 132 historical baseline artifacts plus the recorded unrelated worktree changes remain unchanged.

## Delivered scope

1. A shared observation contract and one-to-one matcher use source-supported observation windows, field evidence, typed values and explicit ownership. Numeric agreement cannot choose an occurrence; ambiguity and adjudications remain explicit.
2. Tests cover missing effects, duplicates, wrong values and owners, repeated receipts, previews, canceled purchases, partial references and offsetting errors. Field correctness, occurrence correctness, coverage and arithmetic closure are distinct.
3. Adapters accept the existing full-run label/report structures and selected legacy action/effect reference formats. Source hashes, original values, corrections and historical manual-join records are preserved. Compact fixtures and reusable commands are in the repository.
4. Causal-accounting views cover observed stats/SP and all five performance currencies, with contribution identity, direct versus derived basis, missing endpoints and unresolved ownership. No award is synthesized from a residual.
5. Two mechanisms were selected and addressed: explicit training-success propagation and damaged fixed friendship-receipt wording. Large result banners now supply source-linked outcomes; gains alone do not establish success. Complete receipts can recover a single damaged fixed word while retaining the literal recipient, amount and original OCR text.
6. Before/after evaluation covers all three development recordings, preserving historical scores and reporting denominators, regressions and ungraded scope. Go and cloud work remain outside this milestone.

## Preservation and checkpoints

Before implementation, 239 existing files were copied and hash-bound under `.local/evaluation-hardening-v1/before/`. This includes the 100 baseline analyzer modules, the original evaluation artifacts and all three accepted report snapshots. `before/manifest.json` records their hashes and identifies unrelated existing changes that must remain untouched. Source videos and screenshots remain at their existing locations.

The bounded milestone is verified by the results above. It does not certify exhaustive recognition or a complete causal explanation of every turn.

## Shared matching rules

- Every observation belongs to one recording, category and phase. A preview, a committed action, an applied receipt and a purchase are distinct observations.
- Match occurrence identity before comparing values. Overlap must use the actual source-positive/field observation interval; shared parent-event timestamps alone must not move a receipt into an unrelated turn.
- Match each prediction at most once. If several maximum one-to-one assignments remain possible, retain ambiguity instead of choosing the assignment with convenient amounts.
- Use explicit source-established occurrence keys or recorded adjudication links to compare wrong identities; an unrelated correct recipient must not be consumed to explain a missing recipient.
- Keep reference incompleteness and prediction uncertainty visible. Unmatched predictions in an incompletely labeled scope are ungraded, not automatic false positives.
- A balanced total does not prove correct event identity, attribution, chronology or full source coverage.

The shared route lives in `tracen_replay/observation_evaluate.py`, `evaluation_adapters.py` and `evaluation_labels.py`. Original expected objects and unsupported fields are retained. Two source-B and 27 source-C source-QA records are converted into explicit overlays, preserving original values, reasons and screenshot hashes. The sealed labels remain unchanged. Historical manual joins remain preserved; the new evaluator does not silently treat all of those joins as automatic matches.

## Recognition safeguards

Training banners are accepted only in the result layout at sufficient confidence and size. Preview failure percentages are excluded. Conflicting success/failure evidence remains unknown and does not award projected performance gains. Events and selected training actions retain their banner timestamps and screenshots.

Cross-recording checks caught an unsafe initial wording repair: click particles could damage both the separator before a recipient and the recipient itself, creating `Direct Akikawa` or `or Akikawa`. The final implementation requires source-validated alignment to localize the obstruction to that separator and independently retain the same recipient and amount. Those obstructed frames are rejected; the valid Light Hello recovery survives. No name or hidden digit is supplied from a roster or an accounting residual.

## Accounting report contract

`full_recording.assemble` adds `causal_accounting`:

| Field | Purpose |
| --- | --- |
| `contributions` | Canonical stats/SP and performance credits/debits with source pointers, evidence, times, basis and turn assignment |
| `comparisons` | Adjacent accepted checkpoint comparisons for all six stats/SP fields and five performance currencies |
| `turn_transitions` | Observed opening-to-next-opening comparisons and referenced causes; the final turn uses its observed closing state if available |
| `other_effects` | References for energy, mood, hints, conditions, caps and effects outside the reconciled numeric channels |
| `issues` | Summary/receipt disagreements, missing purchase evidence and duplicate physical effect claims |
| `unassigned_contribution_refs` | Contributions without a supported checkpoint assignment |

Direct receipts and training gains are separated from state-derived/state-constrained changes and observed-balance debits. Projected debits and unsupported summaries cannot close intervals. Effects crossing observed endpoints remain ambiguous. Missing endpoints and amounts remain unknown. Duplicate claims on the same physical evidence are flagged even if their totals could offset another missing award.

Action, song and purchase-reward summaries do not add another copy of a canonical event award. Lesson purchases contribute their debit. Checkpoint and turn comparisons are overlapping views over the same contribution references and must not be summed together.

The turn view exposes these **field-comparison counts**, not percentages of complete turns:

| Recording | Balanced direct observations | Balanced with derived changes | Unresolved attribution | Missing endpoints | Unexplained residual |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original | 528 | 125 | 60 | 78 | 1 |
| Second | 508 | 130 | 62 | 114 | 0 |
| Third | 556 | 125 | 33 | 89 | 0 |

Arithmetic closure does not independently verify every effect. State-derived gains cannot independently prove those same state transitions. Existing coarse finale windows remain coarse; no turn boundary is invented.

## Reproduction

Run `.venv/Scripts/python.exe -m unittest discover -s analyzer/tests -t analyzer -q` from the repository root.

The local artifact root is `.local/evaluation-hardening-v1/`. Final reports are `final/{recording}-report.json`; final comparisons are `final/{recording}-comparison-v3.json`. `final/integrity-audit.json` binds their hashes and reference inputs. Recording names are `v1`, `independent-01` and `independent-02`.

Reproduce a comparison or integrity audit into a new output file:

```powershell
.venv/Scripts/python.exe analyzer/lab/compare_hardening_reports.py --before .local/evaluation-hardening-v1/before/.local/full-recording/v1/go-integration-replay-v3/candidate-report.json --after .local/evaluation-hardening-v1/final/v1-report.json --corpus .local/evaluation-hardening-v1/v1-corpus.json --output .local/evaluation-hardening-v1/v1-comparison-recheck.json
.venv/Scripts/python.exe analyzer/lab/audit_evaluation_hardening.py --output .local/evaluation-hardening-v1/integrity-recheck.json
```

`analyzer/lab/evaluate_observations.py` grades one reference/report pair. `analyzer/lab/import_baseline_adjudications.py` converts historical source-QA records into overlays. `analyzer/tools/refresh_causal_accounting.py` refreshes only the derived accounting view.

`analyzer/tools/replay_cached_recording.py` reparses source-bound OCR and refinements, merges configured inspections and reconstructs a report into a new directory. It checks the video hash and forbids new OCR. Use `{recording}-replay-config.json` for the first two recordings and `independent-02-replay-config-v3.json` for the third:

```powershell
.venv/Scripts/python.exe analyzer/tools/replay_cached_recording.py .local/evaluation-hardening-v1/independent-02-replay-config-v3.json --output .local/evaluation-hardening-v1/third-full-cache-recheck
```

The third recording needs its training/native recovery manifests, relocated receipt cache, song/currency refinements and validated race-identity evidence. Omitting those historical inputs is not an equivalent comparison. The replay configurations make them explicit.

Initial complete base replays used immutable implementation snapshots. The corrected separator iteration reused those base outputs and reparsed every separator-repair candidate, then reran inspections and reconstruction; its audits declare that reuse. The third replay includes one additional cached observation at 158850 ms, which changes no selected action, numeric event, checkpoint or transaction. Earlier incomplete replay attempts remain diagnostic artifacts rather than final results.

Compact fixtures are `tests/fixtures/evaluation-hardening-occurrences.json`, `receipt-fixed-grammar-484750.json` and `receipt-boundary-obstructions.json`. Tests use them without reprocessing videos or requiring the local screenshot archive. Their visual checks occurred after prediction access and are development regressions, not held-out validation.

## Remaining gaps and Go requirements

The [baseline gaps](full-run-baseline-gaps.md) remain relevant. This milestone does not provide exhaustive event recognition, absolute energy tracking, complete skill inventory/hint history, every condition variant or complete concert mechanics. The original still has the unresolved Passion +10 receipt/accounting gap. Many training outcomes remain unknown because a usable result banner was not observed.

The Go consumer must preserve nulls, basis, ambiguity and evidence; display unresolved changes; resolve event source pointers; and count each canonical contribution once. It must distinguish a balanced comparison from complete causal accounting, and display missing endpoints and coarse windows. No Go or cloud implementation is included here.
