# Whole-recording review workflow

Generate a review queue from a completed gameplay-only report:

```powershell
python -m tracen_replay.review_queue RUN_DIRECTORY --coverage RUN_DIRECTORY/source-review-coverage.json
```

Omit `--coverage` when no sampled-reference coverage manifest exists. This schedules the entire source for review; it does not claim previous work was absent. The command writes `review-queue.json` and `review-queue.html` beside the report. Serve that directory to open the evidence links. The report hash identifies the input snapshot; regenerate after report changes.

The command reruns recording verification, then collects nonzero stat/performance/fan residuals, unresolved intervals, missing or multiple dated actions, source coverage failures, conflicting readings, obscured and unparsed receipts, missing transaction costs, incomplete skill assignments, and unverified race items or active bonuses. Training previews are never promoted to completed actions. Global inventory and recall gaps remain explicit.

Overlapping windows are merged with 1.5 seconds of context on each side. Every finding remains attached, including repeated observations. An obscured or unparsed reading may already be recovered elsewhere; these are review candidates, not asserted missed effects. The queue does not automatically dismiss them or count them as resolved. Evidence links point to existing files inside the run directory; missing direct evidence falls back to nearby source observations and does not establish the finding's truth.

Review the underlying event and adjacent readable frames, record a disposition with supporting evidence, and investigate unresolved transitions at native frame rate only when necessary. Do not rerun the same unsuccessful OCR probe without a materially different evidence method.

## Saved review decisions

```powershell
python -m tracen_replay.review_decisions RUN_DIRECTORY --finding finding-00001 --status recovered --rationale "Explain the reviewed evidence and exact recovered effect" --evidence gameplay/PROOF.png
```

The command appends to `review-decisions.json`; regenerate the queue afterward. Decisions bind the source and report hashes, finding content, original finding proofs, and supporting proof hashes. Changed or missing evidence and report changes make the decision stale and reopen the finding. Duplicate decisions are not silently overwritten; replacement/history adjudication is not yet automated.

Only `recovered` leaves the pending queue. `confirmed_issue` and `unobservable` remain visible. Recovered findings stay in the JSON and the HTML's saved-recoveries section. Pending footage is recomputed from individual findings so resolving one warning cannot hide its unresolved neighbor. Source-review coverage and readiness are never changed by dispositions. Hash validity establishes that the decision still refers to the same artifacts, not that the reviewer's interpretation was correct.

Nine fragments from the source-reviewed 94-second friendship receipt were explicitly marked recovered using the later readable native frame and the passing effect reference. That initially reduced the separate recording to 338 pending findings across 76 windows, totaling 309.264 seconds of footage. The subsequent race-quantity report rebuild correctly invalidated those nine report-bound decisions: the current queue has 347 pending findings and nine stale decisions. Their historical evidence remains saved. This is a real-data check of persistence and invalidation, not automatic adjudication of the whole recording. Replacement decision history remains future work.

## Alternate evidence suggestions

The queue links obscured receipts to alternate frames only when the exact text matches an accepted effect, one event contains the finding, and at least two distinct alternate timestamps support the same typed effect through that event's field evidence. Covered recipient names, conflicting events, malformed text, ambiguous event membership, and partial coverage of a multi-line finding receive no suggestion. No OCR confidence is changed and no fuzzy name repair is attempted.

Suggestions are navigation aids requiring visual review. They do not change pending counts, saved decisions, source coverage, or readiness. Current reports yield one suggestion in the original recording and three in the separate recording, all for pending findings. This conservative first pass has limited coverage; it does not resolve most warnings.

Incomplete inventory is also localized to the observed summary frames, with visible name, level, and variant status included in one review item. Unknown details are not claims that a suffix or level is absent. Conflicting panel names and conflicting detail values produce separate evidence-linked items. This adds one inventory review item to each current recording; the global completeness gate remains open.

## Source review remains separate

Triage based on predictions cannot discover every omission. A separate source-selected sweep partitions uncovered source into contiguous blocks of up to two minutes, configurable with `--sweep-seconds`. It uses source duration and hash-validated existing reference manifests, never predicted event density or the triage list. A block is only a navigation unit; generating it does not mean its frames were viewed or establish native-frame recall.

Use larger contiguous sections to review the sequence of actions, event receipts and state changes. Preserve expected source labels before comparing predictions. Use dense inspection for ambiguous transitions. Existing ten-second labels remain regression fixtures; do not discard or expand their claimed scope.

Report review burden as both finding count and the union of queued footage durations. Footage length is not elapsed human review time, and source-sweep/triage durations overlap: do not add them together. A clean queue does not close full-recall, inventory, active-bonus or independent-validation gates.

## First measured queue

On the current development reports, the original recording has 129 findings merged into 64 windows (252.049 seconds of footage). The separate recording has 347 findings merged into 77 windows (312.398 seconds). Neither has remaining numeric ledger residuals or missing dated actions; the candidates concern OCR conflicts/obstructions, unparsed receipts, race items and active bonuses. These counts are candidates before adjudication, not error counts or a product accuracy score.

The separate recording retains 245 seconds of sampled effect-reference coverage. Its remaining source sweep has 15 blocks, totaling 1531.717 seconds. No fresh held-out evaluation is implied. Both readiness flags remain false.
