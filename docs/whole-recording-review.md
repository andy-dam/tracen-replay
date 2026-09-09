# Whole-recording review workflow

Generate a review queue from a completed gameplay-only report:

```powershell
python -m tracen_replay.review_queue RUN_DIRECTORY --coverage RUN_DIRECTORY/source-review-coverage.json
```

Omit `--coverage` when no sampled-reference coverage manifest exists. This schedules the entire source for review; it does not claim previous work was absent. The command writes `review-queue.json` and `review-queue.html` beside the report. Serve that directory to open the evidence links. The report hash identifies the input snapshot; regenerate after report changes.

The command reruns recording verification, then collects nonzero stat/performance/fan residuals, unresolved intervals, missing or multiple dated actions, source coverage failures, conflicting readings, obscured and unparsed receipts, missing transaction costs, incomplete skill assignments, and unverified race items or active bonuses. Training previews are never promoted to completed actions. Global inventory and recall gaps remain explicit.

Overlapping windows are merged with 1.5 seconds of context on each side. Every finding remains attached, including repeated observations. An obscured or unparsed reading may already be recovered elsewhere; these are review candidates, not asserted missed effects. The queue does not automatically dismiss them or count them as resolved. Evidence links point to existing files inside the run directory; missing direct evidence falls back to nearby source observations and does not establish the finding's truth.

Review the underlying event and adjacent readable frames, record a disposition with supporting evidence, and investigate unresolved transitions at native frame rate only when necessary. Do not rerun the same unsuccessful OCR probe without a materially different evidence method. A future persisted disposition workflow must bind decisions to the report/evidence version so stale approvals cannot hide changed findings.

## Source review remains separate

Triage based on predictions cannot discover every omission. A separate source-selected sweep partitions uncovered source into contiguous blocks of up to two minutes, configurable with `--sweep-seconds`. It uses source duration and hash-validated existing reference manifests, never predicted event density or the triage list. A block is only a navigation unit; generating it does not mean its frames were viewed or establish native-frame recall.

Use larger contiguous sections to review the sequence of actions, event receipts and state changes. Preserve expected source labels before comparing predictions. Use dense inspection for ambiguous transitions. Existing ten-second labels remain regression fixtures; do not discard or expand their claimed scope.

Report review burden as both finding count and the union of queued footage durations. Footage length is not elapsed human review time, and source-sweep/triage durations overlap: do not add them together. A clean queue does not close full-recall, inventory, active-bonus or independent-validation gates.

## First measured queue

On the current development reports, the original recording has 129 findings merged into 64 windows (252.049 seconds of footage). The separate recording has 347 findings merged into 77 windows (312.398 seconds). Neither has remaining numeric ledger residuals or missing dated actions; the candidates concern OCR conflicts/obstructions, unparsed receipts, race items and active bonuses. These counts are candidates before adjudication, not error counts or a product accuracy score.

The separate recording retains 245 seconds of sampled effect-reference coverage. Its remaining source sweep has 15 blocks, totaling 1531.717 seconds. No fresh held-out evaluation is implied. Both readiness flags remain false.
