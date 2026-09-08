# Roadmap

Tracen Replay is in the design stage. All milestones below are pending.

## Initial scope

The first release will support H.264 MP4 clips from one validated 16:9 client layout and language. Initial admission limits are two minutes and 40 MiB per clip; these are application limits, not Azure storage limits.

Screen categories will start with training, event dialogs, race results, home/menu screens, and other. Final label definitions depend on the recording audit.

Analysis will distinguish visible facts from inferred events. A stat change without a recorded action will remain an unexplained change. The initial release will not recommend optimal actions or simulate races.

## 1. Recording fixtures and data dictionary

- [ ] Collect representative clips and document format, resolution, language, and visible fields.
- [ ] Label screen categories and ambiguous transitions.
- [ ] Define report fields and unknown-value behavior.
- [ ] Add small publishable fixtures; keep source recordings outside Git.

## 2. Application foundation

- [ ] Build a Go service and a report viewer using a clearly labeled sample report.
- [ ] Add health/readiness endpoints and source revision diagnostics.
- [ ] Deploy the container to Azure Container Apps.
- [ ] Verify a release and rollback.

## 3. Video processing

- [ ] Add bounded uploads, durable jobs, and a Python worker.
- [ ] Implement timestamped sampling and a template-matching baseline.
- [ ] Produce a timeline with representative frames.
- [ ] Export and reload a report without recomputation.
- [ ] Test malformed inputs, cancellation, and interrupted processing.

## 4. Hosted pipeline

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
