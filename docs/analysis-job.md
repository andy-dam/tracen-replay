# Analysis job boundary

`tracen_replay.analysis_job` wraps the existing full-recording producer in a
small machine-readable worker interface. It is useful for a local process now
and gives the future Go service one stable command boundary to call.

Run it with a source recording and a run directory:

```powershell
python -m tracen_replay.analysis_job "C:\path\to\recording.mp4" `
  --output "C:\path\to\run" --workers 4
```

The wrapper accepts the producer's bounded controls: `--fps`, `--workers`,
`--model-dir`, and `--reparse-only`. Reparse mode still requires the source
path so cached evidence can be checked against the recording; it uses cached
observations rather than starting OCR.

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
  "go_ready": false
}
```

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
resolve correctly; relative traversal, absolute paths, and symlinks that
resolve outside the run directory fail with `evidence_outside_root`. The
wrapper does not relocate or rewrite evidence.

The report contract permits unknown OCR values, partial inventory visibility,
and unresolved mechanics. The producer assembles the [turn ledger](turn-ledger.md)
from its observed records. This worker boundary checks that projection and the
verification status; successful execution does not establish complete semantic
recall.
