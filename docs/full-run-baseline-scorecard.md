# Full-run baseline scorecard

**Status: all seven section grades assembled.** See [the completion audit](full-run-baseline-audit.md) for review scope and remaining evidence limits.

The original recording is 31:06.333 long and already influenced analyzer development. References use visible gameplay only. The analyzer and report remain frozen. See [evaluation scope](full-run-baseline.md) and [timestamped gaps](full-run-baseline-gaps.md).

| Source interval | Committed action kind/option | Success field supplied | Accepted numeric snapshots | Purchase aggregate costs |
| --- | ---: | ---: | ---: | ---: |
| [00:00–03:00](../.local/full-run-baseline-v1/source-a/scorecard.md) | 11/11 | 0/9 | 14/18 | 0/0 |
| [03:00–05:00](../.local/full-run-baseline-v1/middle-a/scorecard.md) | 10/10 | 0/9 | 18/20 | 0/0 |
| [05:00–08:00](../.local/full-run-baseline-v1/tail-a/scorecard.md) | 5/5 | 0/4 | 28/31 | 10/10 |
| [08:00–16:00](../.local/full-run-baseline-v1/source-b/scorecard.md) | 20/20 | 0/16 | 43/46 | 22/22 |
| [16:00–22:00](../.local/full-run-baseline-v1/source-c/scorecard.md) | 11/11 | 0/6 | 16/16 | 21/21 |
| [22:00–24:00](../.local/full-run-baseline-v1/tail-c/scorecard.md) | 9/9 | 0/7 | 17/20 | 0/0 |
| [24:00–31:06.333](../.local/full-run-baseline-v1/source-d/scorecard.md) | 7/7 | 0/3 | 30/44 | 26/26 |

Unknown success fields are abstentions: the selected training can be correct while its success metadata is unavailable. Numeric snapshots count stats/SP and performance as separate channels. A missing exact-frame reading can still be recovered by an accepted checkpoint; the consumer ledger can recover additional states from repeated readings. A 0/0 purchase result means no source transaction was observed, not perfect purchase recall.

| Section | Effect measure | Correct / labeled |
| --- | --- | ---: |
| source-a | Typed occurrence and base identity | 127/134 |
| source-a | Scalar amount across labeled effect types | 111/117 |
| middle-a | Full scored effect row, including required details | 93/97 |
| tail-a | Full scored effect row, including required details | 92/96 |
| source-b | Scalar effect core occurrence and identity | 243/251 |
| source-b | Scalar amount across labeled effect types | 215/220 |
| source-c | Typed occurrence and base identity | 159/164 |
| source-c | Scalar amount across labeled effect types | 138/140 |
| tail-c | Full scored effect row, including required details | 106/114 |
| source-d | Full scored effect row, including required details | 115/115 |

These effect measures have different requirements and are deliberately not added together. The section scorecards separately grade stat/SP, performance, hints, energy, friendship and observable conditions. Raw matcher failures resolved by documented occurrence joins or source corrections are not analyzer errors.

Remaining evaluation limits include ungraded source-label gaps, obscured amounts, partial owned-skill inventory and absolute energy/continuous condition state. The gap register distinguishes those limits from source-confirmed report omissions. Matched totals alone do not prove complete causal attribution or absence of offsetting errors.

## Reproduce

Run the grading commands in the linked section scorecards first, then assemble these tables from their JSON outputs:

```powershell
.venv/Scripts/python.exe scripts/build_baseline_scorecard.py .local/full-run-baseline-v1 --output .local/full-run-baseline-v1/combined-scorecard.json --markdown docs/full-run-baseline-scorecard.md
```

`combined-scorecard.json` records the input artifact hashes and metric definitions. The assembler verifies the frozen analyzer/report and source-hash bindings. It leaves incomplete sections pending and does not certify visual review completion.
