# Roadmap

Tracen Replay has a local evidence pipeline with experimental stat OCR and checkpoint accounting. General screen recognition, the web application, and hosted processing remain pending. The [local ledger milestone](milestone-local-ledger.md) defines the current development validation scope.

## Initial scope

The first release will support H.264 MP4 clips from one validated 16:9 client layout and language. Initial admission limits are two minutes and 40 MiB per clip; these are application limits, not Azure storage limits.

Screen categories will start with training, event dialogs, race results, home/menu screens, and other. Final label definitions depend on the recording audit.

Analysis will distinguish visible facts from inferred events. A stat change without a recorded action will remain an unexplained change. The initial release will not recommend optimal actions or simulate races.

## 1. Recording fixtures and data dictionary

- [x] Audit an initial recording locally and document format, layout, visible fields, and selected timestamped examples.
- [ ] Collect additional independent sessions and review the provisional annotations.
- [ ] Label screen categories and ambiguous transitions.
- [ ] Define report fields and unknown-value behavior.
- [ ] Add small publishable fixtures; keep source recordings outside Git.

## 2. Local video processing

- [x] Add a CLI for bounded intervals of local recordings.
- [x] Extract evidence frames with original presentation timestamps.
- [x] Export an offline gallery and JSON report with optional reference annotations.
- [x] Test variable-frame-rate timestamps, malformed media, output preservation, and failed-run cleanup.
- [x] Add experimental six-field OCR, repeated-reading checkpoints, and discrepancy accounting for the initial English 1080p layout.
- [x] Retry discrepancies against denser existing samples and explicit main-pane outcomes; preserve unresolved differences.
- [x] Keep training previews separate from explicit logged training outcomes.
- [x] Add a source-bound, timestamp-exact evaluator with separate accuracy and abstention metrics.
- [x] Preserve visibility gaps and expose provisional log deduplication decisions.
- [x] Track ordered log context and separate main-outcome visibility from action time.
- [ ] Evaluate checkpoint accuracy, log identity, and missed-event recovery on independent recordings.
- [ ] Implement a template-matching baseline and assemble recognized screen segments.
- [ ] Validate short-event recall and evidence quality against reviewed labels.

## 3. Application foundation

- [ ] Build a Go service and a report viewer using reports from the local pipeline.
- [ ] Add bounded local uploads and connect video processing to the viewer.
- [ ] Add health/readiness endpoints and source revision diagnostics.
- [ ] Verify that a short clip produces a useful local timeline before deployment.
- [ ] Add cancellation and recovery for background processing.

## 4. Hosted pipeline

- [ ] Deploy the working application to Azure Container Apps and verify release/rollback.
- [ ] Add authentication, ownership checks, and private Blob Storage.
- [ ] Persist runs and jobs in Azure SQL.
- [ ] Dispatch work through Queue Storage to Container Apps Jobs.
- [ ] Verify cloud execution independently of a local worker.
- [ ] Recover unsent jobs and reject stale or duplicate completions.
- [ ] Enforce admission limits, retention, and bounded retries.

## 5. Vision model

- [ ] Freeze recording-level train/validation/test splits.
- [ ] Compare template matching, frozen pretrained features, and fine-tuning.
- [ ] Report recognition quality, abstention coverage, latency, and memory use.
- [ ] Deploy a versioned model with a reproducible evaluation report.

## 6. Review and comparison

- [ ] Extract a limited set of visible stats and turn labels.
- [ ] Attach evidence and uncertainty to each event and field.
- [ ] Save corrections separately from predictions.
- [ ] Compare runs using explicit turn alignment and visible gaps.
- [ ] Evaluate the workflow with players and address usability issues.

## 7. Release

- [ ] Add local setup instructions and an end-to-end fixture command.
- [ ] Run offline checks for the API, worker, frontend, and report contract in CI.
- [ ] Define repeatable Azure deployment with Bicep.
- [ ] Exercise deployment rollback, job recovery, export, and teardown.
- [ ] Publish a demo, measured results, and supported-input limitations.

## Later work

Longer recordings, additional layouts, compact report imports, more event types, and improved comparison views depend on the initial pipeline's accuracy and resource measurements.
