# The analysis job boundary

`tracen_replay.analysis_job` (`analyzer/tracen_replay/analysis_job.py`) wraps
the full-recording producer in a small machine-readable worker interface: one
process, one recording, progress on stderr, exactly one JSON result object on
stdout. The service calls it through `internal/worker.Command`, reads its
progress with `internal/worker.ParseProgress`, and interprets its result with
`internal/worker.Interpret` and `internal/worker.Verify`. See
[architecture.md](architecture.md) for how this fits into the service as a
whole.

## Command line

```
python -m tracen_replay.analysis_job <source> --output <run dir> [options]
```

`source` is a local video file; `--output` is the run directory the worker
writes into. The service always adds `-X utf8` to the interpreter invocation
and runs it with `WorkDir` (the directory containing the `tracen_replay`
package) as the working directory.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--fps` | `4.0` | base sampling rate, 1 to 8 FPS |
| `--workers` | `4` | OCR worker count, 1 to 8; also sizes the pooled reinterpretation of cached rows |
| `--dense-workers` | workers minus one, at least one | worker processes for the dense re-read OCR passes, 1 to 8; each stayed near 2 GB on a full career (see [ocr-performance.md](ocr-performance.md)) |
| `--model-dir` | `.local/models/rapidocr` | local OCR model directory |
| `--reparse-only` | off | use cached observations instead of starting OCR |
| `--rehydrate-frames` | off | with `--reparse-only`, decode the run's frame images and gameplay panes again before reparsing |
| `--learned-reader` | off | an exported learned result-card reader (ONNX); its reads are stored on each training result reading and the accounting uses one only where it equals an unexplained difference (see [analyzer-pipeline.md](analyzer-pipeline.md)) |
| `--replay-input-manifest` | none | a source-bound replay input manifest for cached parsing from a disposable cache clone; requires `--reparse-only` |
| `--replay-input-root` | `--output` | the disposable cache root the manifest reads from; must equal `--output` |
| `--prune-frames` | off | after the report is validated, delete the frame images under `--output` |
| `--prune-working-data` | off | after the report is validated, delete every directory under `--output` (frames, OCR caches, crops, recovery inputs); the top-level files stay |
| `--owner-pid` | none | process id that owns this job; the worker ends itself, with its own worker processes, once that process is gone |

The service always passes `--prune-frames`; it passes `--prune-working-data`
unless started with `-keep-working-data`, and always passes `--owner-pid`
with its own process id. `--replay-input-manifest` and `--replay-input-root`
are used by `analyzer/tools/replay_cached_recording.py` for an isolated
cached replay, not by the service.

The OCR device is not a command-line flag: it comes from the
`TRACEN_REPLAY_OCR_DEVICE` environment variable, `auto` (default), `cpu`,
`dml` or `cuda`. `auto` resolves to DirectML when onnxruntime provides it,
then CUDA, then the CPU provider. `internal/worker.Command.Env` sets this
variable from the service's `-ocr-device` flag; a machine without a
supported GPU still analyzes on the CPU, more slowly. The resolved device is
written to the OCR progress lines and to the report's `recognition.device`,
which the timeline carries.

### `--reparse-only` and `--replay-input-manifest`

`--reparse-only` rebuilds events, turn assignments, accounting and the
report from observations already cached under the run directory; it does not
start OCR and does not reuse the previous report's conclusions. It still
requires the source path so cached evidence can be checked against the
recording.

`--replay-input-manifest` (always with `--reparse-only`) consumes only the
manifest's declared source hashes, raw observations, inspection/recovery
rows and raw sidecar refinements; it never loads accepted report values.
`--replay-input-root` must equal `--output`, so emitted evidence stays in the
worker root.

`--rehydrate-frames` (also only with `--reparse-only`) decodes the run's frame
images and cuts its gameplay panes again, for a run whose images are gone.
Decoding the same recording at the same sampling reproduces the same bytes, so
the images are derived rather than stored: each part is decoded into a scratch
directory and accepted only when it comes back as exactly the frames that part
already recorded, and each pane is checked against the sha256 of the pixels its
own observation was read from. No manifest is ever rewritten, and a part or a
pane that does not reproduce raises instead of being written.

This restores only what a decode can produce. A stage's own crops are proved
by the bytes of their file, so a run pruned with `--prune-frames` has lost
those for good and will still fail its refinement stages; measured on a full
career, rehydration puts back about 10 GB and takes around ten minutes. The
reproducible-capture property is the useful part: a finished report's frames
can be cut again from the recording by timestamp.

### `--prune-frames` and `--prune-working-data`

Both run only after the report has passed validation. `--prune-frames`
deletes frame image files (`.png`, `.jpg`, `.jpeg`, `.webp`) anywhere under
the run directory and removes directories left empty; the report and the
timeline locate every fact by its source timestamp, so a pruned run can no
longer be re-parsed or have its evidence paths re-validated.
`--prune-working-data` goes further: it deletes every top-level directory
under the run root entirely (frames, per-frame OCR caches, crops, recovery
inputs, about 1 GB for a full career), keeping only the top-level files:
`report.json`, `timeline.json`, the viewer page, and the capture and
identity metadata. A run pruned this way cannot be re-parsed.

## Progress lines (stderr)

Progress is JSON objects, one per line, interleaved with the producer's own
warnings and tracebacks (`internal/worker.ParseProgress` ignores any line
that is not a JSON object with a `stage` field). Stage boundaries:

```json
{"stage": "stage_done", "name": "...", "wall_s": 12.3, "rss_mb": 512, "peak_rss_mb": 900}
```

A stage that failed but did not abort the run:

```json
{"stage": "stage_failed", "name": "...", "error": "..."}
```

The OCR stage is the only line with a meaningful percentage:

```json
{"stage": "ocr", "processed": 120, "total": 400}
```

`internal/worker.Progress.Percent` reports `100 * processed / total` only
for `stage: "ocr"` lines with `total > 0`.

## Result envelope (stdout)

On success or partial success the worker writes exactly one JSON object to
stdout:

```json
{
  "schema_version": "tracen-replay/analysis-job-v1",
  "status": "succeeded",
  "report_schema_version": "tracen-replay/full-recording-v1",
  "report_path": "C:\\path\\to\\run\\report.json",
  "report_sha256": "...",
  "source_sha256": "...",
  "evidence_root": "C:\\path\\to\\run",
  "full_source_processed": true,
  "fully_verified": false,
  "go_ready": false,
  "timeline_path": "C:\\path\\to\\run\\timeline.json",
  "worker_version": {"package": "0.1.0", "code_digest": "..."}
}
```

`status` is `succeeded`, `completed_with_stage_failures` (with a
`stage_failures` array of `{stage, error}`), or `failed` (with an `error`
object of `{code, message}` instead of the success fields). `worker_version`
(package version plus a SHA-256 digest of the analyzer's source) is present
on every terminal object, success or failure. When `--prune-frames` ran, the
object also carries `pruned_frames: {files, bytes}`; when
`--prune-working-data` ran, it also carries
`pruned_working_data: {directories, files, bytes}`.

`full_source_processed`, `fully_verified` and `go_ready` are report
verification results, separate from job completion: a successfully produced
report can still have any of them `false`. `go_ready` is not a computed
decision for the current milestone and remains `false`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | `status: succeeded` or `status: completed_with_stage_failures` |
| `1` | producer or report failure (`status: failed`) |
| `2` | input failure before the producer ran (`status: failed`) |
| `3` | the worker ended itself because `--owner-pid` is no longer running (used only with `--owner-pid`) |

## Error codes

Input failures (exit 2), from `JobInputError`: `invalid_arguments`,
`source_not_found`, `output_not_directory`, `invalid_fps`, `invalid_workers`,
`invalid_dense_workers`, `invalid_owner_pid`, `replay_manifest_not_found`,
`replay_root_mismatch`, `source_unreadable`, `invalid_paths`.

Producer and report failures (exit 1): `producer_failed`, `source_mismatch`,
`report_missing`, `report_stale`, `report_unreadable`, `invalid_report`,
`missing_turn_ledger`, `invalid_report_status`, `invalid_fully_verified`,
`invalid_go_ready`, `invalid_full_source_processed`, `invalid_evidence_root`,
`evidence_root_mismatch`, `evidence_outside_root`, `evidence_missing`.

A failed job never emits `report_sha256` or the other success fields. An
existing `report.json` that the producer did not update is reported as
`report_stale`. The wrapper hashes the source file before and after
production and requires that hash and `report.source.sha256` to agree, or it
reports `source_mismatch`.

On the Go side, `internal/worker.DecodeTerminal` and `Interpret` add their
own contract errors when the worker's output does not meet the boundary
described here: `bad_terminal_output`, `multiple_terminal_objects`,
`schema_mismatch`, `status_exit_mismatch`. `internal/worker.Verify` adds
`report_missing`, `report_hash_mismatch`, `timeline_missing` and
`timeline_invalid` when the files a completed result names do not check out.

## The output directory after a run

Every artifact of a job lives under its run directory (`--output`,
`<data>/jobs/<job id>/run/` in the service). After a successful run it
contains at least `report.json` and `timeline.json`; a viewer page and
capture/identity metadata are also written at the top level. Unless the run
was pruned, it also holds the sampled and re-read frame images, per-frame OCR
caches, crops and recovery inputs. `evidence_root` in the result envelope
names this directory; it must stay together with the report, since the
report's evidence paths resolve relative to it. The worker's own stderr is
not part of the run directory; the service copies it to
`<data>/jobs/<job id>/worker.log` separately.

The caches in that directory carry the reader's fingerprint, and the later
stages refuse a cache another reader wrote ("Receipt OCR model changed"). The
worker imports its code when a stage or a worker process starts, so editing
the analyzer while a job is in flight can change that fingerprint under the
run and fail a recovery stage. Run the analyzer from a copy of the
`tracen_replay` package when a run has to survive edits to the tree.
