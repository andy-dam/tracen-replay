# Analysis job boundary

`tracen_replay.analysis_job` wraps the existing full-recording producer in a
small machine-readable worker interface. It is useful for a local process now
and gives the future Go service one stable command boundary to call.

Run it with a source recording and a run directory:

```powershell
python -m tracen_replay.analysis_job "C:\path\to\recording.mp4" `
  --output "C:\path\to\run" --workers 4
```

The wrapper accepts the producer's bounded controls: `--fps`, `--workers`
(the OCR worker count; it also sizes the post-refinement reinterpretation of
the cached rows), `--dense-workers` (the worker processes of the dense
re-read OCR passes; default `--workers` minus one, at least one; each dense
worker can peak near 5-6 GB, so this is the memory knob of a run),
`--model-dir`, and `--reparse-only`, plus `--prune-frames`, which deletes the
frame images under the run directory after the report has been validated (the
report and the timeline locate every fact by its source timestamp; a pruned
run can no longer be re-parsed or have its evidence paths re-validated, so keep
the frames when a later parser pass over the same OCR is wanted). Reparse mode still requires the source
path so cached evidence can be checked against the recording; it uses cached
observations rather than starting OCR.

`--prune-working-data` goes further than `--prune-frames`: after the report
is validated it deletes every directory under the run root (frames, per-frame
OCR caches, crops and recovery inputs, about 1 GB for a full career), keeping
the top-level files: `report.json`, `timeline.json`, the viewer page and the
capture and identity metadata. The success envelope records the removed
directories, files and bytes as `pruned_working_data`. The Go service passes
it unless started with `-keep-working-data`. A run pruned this way cannot be
re-parsed; keep the working data when a later parser pass over the same OCR
is wanted.

`--owner-pid N` names the process that owns the job (the Go service passes
its own id). The worker watches it through psutil and, as soon as that
process is gone, kills every process it started and exits with status 3: the
owner was the only reader of the result, and an orphaned analysis would
otherwise keep the GPU and gigabytes of memory busy for an hour. A pid that
is not running when the job starts is rejected as `invalid_owner_pid`. Direct
command-line runs leave the flag out.

Here, saved observations include extracted gameplay frames, timestamps, OCR
text and geometry, and source-bound refinement readings. Reanalysis rebuilds
events, turn assignments, accounting, and the report from those observations;
it does not reuse the previous report's conclusions. It verifies the current
interpretation of saved evidence, not fresh decoding or OCR. Recognition
changes require fresh processing of the affected source frames. A fresh
end-to-end evaluation starts from the MP4 in a new run directory, and its
recognition accuracy must still be graded separately from worker success.

Fresh runs perform a bounded source-driven reread after the base OCR pass.
The producer discovers weak panel component and status-badge observations from
the raw cache, reuses one `NeuralReader` for those candidates, and writes only
source-bound `performance-panel-refinement` and `status-badge-refinement`
sidecars. The limits are controlled by `--max-auto-refinement-frames` and
`--max-status-refinement-frames`; unresolved candidates and budget exclusions
are recorded in `automatic-refinement.json` and in the report. Reparse mode
loads and validates existing sidecars through the same cache path, reports
missing refinements as unresolved, and never constructs an OCR reader.

Fresh runs also prepare source-bound hint-card recoveries from the refined
base readings before adding dense inspection rows. This uses the configured
model directory and preserves an existing cache through validation rather
than overwriting it. Reanalysis only loads existing hint-card recoveries;
it does not create missing ones or run the preparation OCR.

For a cache with nested inspection or recovery namespaces, pass the validated
source-bound manifest and use the same disposable directory for both input and
output:

```powershell
python -m tracen_replay.analysis_job "C:\path\to\recording.mp4" `
  --output "C:\path\to\worker-run" --reparse-only `
  --replay-input-manifest "C:\path\to\replay-input-manifest.json" `
  --replay-input-root "C:\path\to\worker-run"
```

The manifest mode consumes only its source hashes, raw observations, declared
inspection/recovery rows, and raw sidecar refinements. It never loads accepted
report values; `reference.accepted_report_sha256` is comparison metadata only.
The disposable worker root must be populated from a safe cache clone before
the command runs. `analyzer/tools/replay_cached_recording.py` accepts the same
manifest through `--replay-input-manifest` for an isolated cached replay whose
output directory may be separate from its input root.
That replay path also records the bounded automatic-refinement audit against
the manifest's base namespace and runs the source choice adapter before it
writes `candidate-report.json`; it remains OCR-free.

The wrapper sends producer progress and diagnostics to stderr. On success it
writes exactly one JSON object to stdout:

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
  "timeline_path": "C:\path\to\run\timeline.json",
  "worker_version": {"package": "0.1.0", "code_digest": "..."}
}
```

`worker_version` identifies the worker on every terminal object, successes
and failures alike: the installed package version and a SHA-256 over the
analyzer's source files. A caller can pin the worker it was validated with.

The OCR device comes from the `TRACEN_REPLAY_OCR_DEVICE` environment
variable: `auto` (default: DirectML when onnxruntime provides it, then CUDA,
then the CPU provider), or an explicit `dml`, `cuda` or `cpu`. A machine
without a supported GPU still analyzes with the same models on the CPU,
more slowly. The resolved device is written to the OCR progress lines and to
the report's `recognition.device`, which the timeline carries.

`timeline_path` names the compact timeline document written next to the
report: the turns with their opening states and per-field accounting status,
and every ledger entry with its timestamps, changes and accepted basis, without
image paths or OCR text (under 1 MB for a full career against 150-180 MB for
`report.json`). With `--prune-frames` the record also carries `pruned_frames`
(`files`, `bytes`).

Enrichment stages that fail (automatic refinement, hint-card preparation,
inspection loads, the bounded recoveries, event choices, causal accounting,
the timeline document, the viewer) do not abort the run: the producer records
each under `report.stage_failures` (`stage`, `error`, `traceback`), leaves that
stage's block out, and continues. The wrapper then reports
`status: completed_with_stage_failures` with the `stage`/`error` pairs, still
with exit code 0 and every success field; treat it as a completed job with
warnings. Capture, OCR, cached-reading loads, the turn ledger and the report
contract remain fatal; on a fatal error the producer writes
`report-partial.json` (the report assembled so far plus the failure) before
the wrapper emits `producer_failed`.

Progress lines on stderr are JSON objects. Stage boundaries emit
`{"stage": "stage_done", "name": ..., "wall_s": ..., "rss_mb": ..., "peak_rss_mb": ...}`
(memory fields when psutil is installed); the OCR stage emits
`{"stage": "ocr", "processed": n, "total": m, ...}`, which is the only line with
a meaningful percentage.

`status: succeeded` means that the producer returned, wrote `report.json`,
and the report passed the full-recording contract, including its versioned
turn ledger. Historical reports without that ledger remain readable through
the generic report validator, but the worker rejects them with
`missing_turn_ledger`. The three boolean fields
are report verification results. They remain separate from job completion, so
a successfully produced report can still have `fully_verified` or `go_ready`
set to `false`. The legacy `go_ready` field currently remains false; it is not
a computed decision for the bounded first-integration milestone. See
[the acceptance record](first-go-acceptance.md) for that decision and its limits.

Input, producer, and report failures each produce one JSON object with
`status: failed` and an `error` containing a stable `code` and human-readable
`message`. Input failures return exit code 2; producer and report failures
return exit code 1. A failed job never emits report hash or success fields.
An existing `report.json` must be updated or replaced by the producer; a
producer no-op is reported as `report_stale`. The wrapper hashes the requested
source before and after production and requires both that hash and
`report.source.sha256` to agree, otherwise it reports `source_mismatch`.

`evidence_root` is the absolute run directory that owns the report's relative
frame and observation evidence. Keep that directory with the report when
passing the result to another process. A report stored below a larger cached
evidence bundle must be invoked through a run directory whose evidence paths
resolve correctly. If a report includes `evaluation_context.evidence_root`, the
declared path must resolve to the worker's actual output root; a conflicting
declaration fails with `evidence_root_mismatch` and never redirects path
resolution. Relative traversal, absolute paths, and symlinks that resolve
outside the run directory fail with `evidence_outside_root`. A cited path that
resolves inside the root must also identify an existing file; missing files and
in-root directories fail with `evidence_missing`. Hash and verification
metadata, including nested `*_sha256` and `*_verified` values, are not evidence
paths. The wrapper does not relocate or rewrite evidence.

The report contract permits unknown OCR values, partial inventory visibility,
and unresolved mechanics. The producer assembles the [turn ledger](turn-ledger.md)
from its observed records. This worker boundary checks that projection and the
verification status; successful execution does not establish complete semantic
recall.

Dialogue menus are passed through the source choice adapter before report
assembly. It reuses the raw OCR lines and the matching gameplay crop, records
preview menus separately, and merges only bilateral or explicitly verified
selection witnesses into `gameplay_tracking.dialogue_choices`. The same
committed choice is therefore represented once in the derived ledger while the
full `event_choice_observations` envelope remains available to semantic
consumers.
