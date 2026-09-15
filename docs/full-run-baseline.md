# Full-run evaluation baseline

Status: **evaluation complete, with documented recognition and evidence gaps**. This evaluation supplements the earlier [bounded integration acceptance](first-go-acceptance.md); that acceptance was not a complete source-based accuracy grade. Completion evidence and limits are recorded in [the requirement audit](full-run-baseline-audit.md).

The subject is the entire original `2026-09-08 01-59-24.mp4` recording, from 00:00 through 31:06.333. It already influenced development, so results must be called a development baseline rather than held-out accuracy. Only the gameplay pane supplies reference evidence; the auxiliary side log is excluded.

The analyzer is frozen at commit `978f1d6f25d4164df4deb92ede2340522bf224dc`, with individual hashes for 100 Python files. The frozen report SHA-256 is `fe54e02719bc5f40982e37a1e79eb0337477efd82ff4304343c04ee4e7e288cd`. Local manifests, original labels, screenshot hashes, graders and section scorecards are under `.local/full-run-baseline-v1/`.

Source labels are recorded before that section's predictions are inspected and then sealed. Any later source corrections must remain explicit amendments. Chronological contact sheets locate transitions; numeric and text claims require readable source evidence. Sampling coverage does not prove native-frame recall.

| Section | Current review status |
| --- | --- |
| 00:00–03:00 | Sealed, graded and documented, including startup and numeric balance checks |
| 03:00–05:00 | Sealed, graded and documented |
| 05:00–08:00 | Sealed, graded and documented |
| 08:00–16:00 | Sealed, graded and documented, including purchases, arithmetic and explicit source corrections |
| 16:00–22:00 | Sealed and graded with explicit source corrections and disclosed reference-label balance gaps |
| 22:00–24:00 | Sealed, graded and documented |
| 24:00–31:06.333 | Sealed, graded and documented |

No overall accuracy percentage is claimed. Sections have different denominators for action identity, outcome status, individual effects, purchase costs, literal frame readings, accepted checkpoints and consumer ledger states. Those measures remain separate.

The [combined scorecard](full-run-baseline-scorecard.md) reproduces all seven section grades. The [timestamped gap register](full-run-baseline-gaps.md) collects reviewed omissions, partial results, evidence limits and Go presentation requirements. Across the labeled examples, action identities match 73/73, aggregate purchase costs match 79/79, and accepted numeric channel snapshots match 166/195. All 54 source-labeled training success results remain report-unknown. These are separate measures, not complete event attribution or whole-run recognition accuracy.

One confirmed numerical omission illustrates the distinction: the [16:02.750 receipt](../.local/full-recording/v1/gameplay/part-008-frame-000012.png) awards Passion +10, but the corresponding accepted event omits it. Both accounting and the consumer ledger preserve an unexplained +10 across 15:14.250–16:10.750, mark the comparison unresolved, and retain `complete_event_history: false`. The consistency check detects this omission. This does not establish that it detects every missing or misattributed event; offsetting errors could still balance.

The independently labeled states also expose that same +10 across the review partition at 16:00: Passion changes from 81 at 15:23.750 to 91 at 16:10.000 with no corresponding report award. Across all six partition boundaries, 65/66 numeric field intervals balance against the report; this is the only residual. The source anchor interval and report accounting interval have different endpoints and must not be presented as identical checks.

A proposed speed error was rejected during source QA: [the 20:15.500 confirmation](../.local/full-recording/v1/gameplay/part-010-frame-000063.png) distinguishes nominal Speed +26 from the effective +15 gain across 1,200, and [the 20:19.500 receipt](../.local/full-recording/v1/gameplay/part-010-frame-000079.png) awards +15. The analyzer's +15 is correct. The original sealed reference remains preserved while its correction is recorded separately.

## Confirmed integration requirements

- Read recovered opening states from the consumer ledger. A missing exact-frame OCR reading does not necessarily mean a missing turn state.
- Resolve `source_ref` for effects absent from convenience summaries. Two Light Hello performance awards in the 22:00–24:00 section require this. Referenced effects and summaries must not be counted twice.
- Preserve incomplete identity, ambiguous candidates and unknown outcomes. Correct action identity does not imply a verified success/failure result.
- Preserve acquisition-level name conflicts. One final-section song has competing spellings despite a false ledger `conflicts_present` flag; alternatives describe one acquisition.
- Support multiple actions in an unresolved phase. The finale contains six actions in one declared coarse segment; it must not be presented as a fully reconstructed ordinary turn.
- Display unexplained balance changes and missing states. Matching final totals does not establish that every intervening cause was recognized.

These are requirements for report presentation in Go. They do not require fixing every recognition gap before beginning the application. The gap register is the remaining analyzer backlog. Go integration can begin with these representation requirements as its acceptance criteria; this baseline does not assert complete turn-by-turn causal reconstruction.

## Completion checklist

- [x] Preserve the original recording identity, analyzer hashes and frozen report.
- [x] Complete source review across all assigned intervals, including startup and finale.
- [x] Verify the evidence for reported wrong or missing numeric/identity results, with explicit source amendments.
- [x] Grade states, committed actions, stats/SP, performance, purchases, hints, energy and observable conditions, with explicit ungraded/unobservable areas.
- [x] Check numeric transitions and distinguish receipt-supported accounting from state-derived closure.
- [x] Combine section results into a reproducible scorecard without duplicate boundary occurrences.
- [x] Publish the timestamped gap register and classify Go contract blockers versus later analyzer work.
- [x] Audit the final scope, source seals, score denominators, evidence links and reproduction commands.

The evaluation deliverables are complete. All 36 reproduction commands passed with unchanged grading artifacts and metrics; the final link/evidence and occurrence audits passed. Analyzer remediation and Go implementation are outside this completed goal.

For an artifact-integrity snapshot, run:

```powershell
.venv/Scripts/python.exe analyzer/lab/baseline_status.py .local/full-run-baseline-v1 --output .local/full-run-baseline-v1/status.json
```

This command checks hashes and artifact presence. It explicitly does not certify visual correctness or mark the baseline complete.
