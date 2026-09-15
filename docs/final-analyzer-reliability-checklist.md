# Final analyzer reliability acceptance

This is the frozen scope for the final analyzer reliability goal. The starting
point is `.local/turn-explanations-v1/final-v3/`: 166 missing endpoint field
comparisons, 15 unresolved attribution comparisons, 175/180 eligible turns with
observed endpoints, 36/180 balanced with direct numeric evidence, and 128/180
balanced with some derived changes. These are development-recording metrics.

The checklist is fixed before recognition changes. Its original bytes and the
baseline artifacts are bound by `.local/final-reliability-v1/baseline-manifest.json`.
Progress and evidence belong in a separate acceptance-results file; this
checklist must not be rewritten around whatever happens to pass.

| Gate | Required outcome | Evidence required for acceptance |
| --- | --- | --- |
| G1 Baseline preservation | Preserve all three starting full reports, measured results, source labels, and existing unrelated work. | Copies and SHA-256 hashes; final unchanged-source/label audit. |
| G2 Frozen source evaluation | Before recognition tuning, freeze 24 source-reviewed turn transitions (eight per recording), covering training, races, purchases, hints, conditions, and energy. Include the known digit conflicts, overlapping performance counter, and ownership cases as additional targeted cases. | Case-selection manifest, screenshots/timestamps, observable scope and independently transcribed effects, immutable reference hashes, and a recorded freeze checkpoint. Missing or hidden content is not labeled as a zero effect. |
| G3 Numeric recognition | Investigate all 15 remaining attribution comparisons and the readable Composure/current-plus-projection case. Fix every reproducible readable failure using shared methods for crop disagreement, digit clipping, and animation/component phases. | Source-backed positive/negative regressions; all readable targeted cases pass; disagreements without sufficient evidence remain explicit. No amount is selected because it balances an interval. |
| G4 Boundaries and ownership | Investigate all 23 missing opening states and all 15 early T1-T5 source intervals, recover readable panels, prove ownership before promoting race-runner stats, and distinguish terminal observations from nonexistent next-turn endpoints. | Per-case source coverage and verdicts; runner-scope/trainee-scope negative tests; explicit applicability/visibility/terminal status; original 166-comparison metric retained alongside any new metrics. A single representative frame cannot establish interval-wide absence. |
| G5 Derived changes | Audit all 128 eligible turns with derived numeric accounting. Separate observed transaction-price derivations, visible-but-state-constrained candidates, and effects inferred from surrounding state differences. Fix recurring readable direct-evidence failures. | Every turn and every derived contribution mapped to its basis and source; grouped causes, source tests and before/after outcomes. Legitimate derivations remain labeled; unobserved effects are never manufactured. |
| G6 Event explanations | Preserve one canonical source-linked explanation for training, events, races, purchases, hints, conditions, and energy. Detect missing, duplicate, incorrect and incorrectly attributed effects, including offsetting mistakes with zero net residual. | Frozen-case semantic grades, explicit observation scope, wrong-turn/duplicate/cancellation regression tests, and consumer-facing evidence/uncertainty. Every reproducible readable core-case failure must be corrected; hidden content remains unknown. |
| G7 Full recordings | Rebuild all three full reports with the final implementation and compare against the preserved baseline and both old and new references. | Reproducible full-report runs, source/frame/code hashes, unchanged labels, all changed accepted actions/effects explained by source, and no unexplained regressions. An untouched full run is evaluated if available; otherwise independent validation remains explicitly outstanding. |
| G8 Python worker boundary | Verify the final Python producer/worker contract for Go consumption. Job completion must remain distinct from recognition completeness. | Real cached full-report worker executions and focused failure tests for partial results, stale outputs, invalid/source-changed input, evidence paths, timestamps, uncertainty and observed/derived fields. No Go implementation. |
| G9 Completion audit | Demonstrate that G1-G8 pass without redefining them. | Acceptance matrix with concrete artifact paths/hashes, final tests, remaining evidence limits, and explicit unresolved blockers. Documenting a readable failure alone does not pass a gate. |

Parallel work uses separate owners for source references, numeric mechanisms,
boundary/ownership mechanisms, derived-change analysis, evaluation, and worker
validation. Source-review workers may use preserved reports to locate intervals
but must transcribe labels from footage and evidence images. This is a new
development regression set, not an untouched recording or a claim of blind
model evaluation.

Recognition edits wait for G2's freeze. Investigation, baseline preservation,
evaluation infrastructure, and worker-contract tests can proceed before that
freeze. Any newly discovered reproducible readable failure within the frozen
core scope is added to the failure register without silently removing an
existing case or lowering its expected result.

The goal does not require inventing unavailable information or exhaustive
optional-detail recognition. It does require fixing identified readable core
failures. If evidence cannot establish an amount, owner, time, or effect,
record the precise limitation. If a required fix remains unimplemented or
unverified, leave the goal unfinished.
