# Architecture

The application is three parts: the service, the client and the analyzer.
See [local-app.md](local-app.md) for how to build and run them, and
[analysis-job.md](analysis-job.md) for the worker contract between the
service and the analyzer.

## The three parts

**The service** is the Go binary `tracen` (`cmd/tracen`, package `main`,
plus `internal/*`). It serves the HTTP API and the browser client from one
process, keeps a SQLite database of accounts, uploads, jobs and reports, and
runs the analyzer as a child process for each submitted recording.

**The client** is the Vue application under `web/`. It is built once with
Vite and embedded into the binary (`internal/webassets`); the service serves
it at `/`. The client only ever talks to the service's `/api` routes
(`web/src/api.ts`) and knows recordings, jobs and reports by the server-issued
ids the service hands it, never by a file path.

**The analyzer** (`the worker`) is the Python package `tracen_replay` under
`analyzer/`. The service runs it as
`python -m tracen_replay.analysis_job <recording> --output <run dir> ...`,
one recording per invocation. It does the recognition and accounting; the
service treats its `report.json` as opaque and reads only `timeline.json`.

## How a recording becomes a report

1. The browser uploads a video to `POST /api/recordings`. The service streams
   it to `<data>/recordings/<user>/`, hashes it, and stores a `recordings`
   row (`internal/jobs.Recording`).
2. The browser submits an analysis with `POST /api/jobs`, naming the upload's
   id. `internal/jobs.Manager.Submit` resolves the id to the caller's own
   upload, checks the queue limit, creates a `jobs` row in status `queued`,
   and creates `<data>/jobs/<job id>/`.
3. `Manager.Run` pulls one queued job at a time and starts the worker
   (`internal/runner.Exec`) with the command `internal/worker.Command`
   builds: the recording, an output directory
   (`<data>/jobs/<job id>/run/`), the OCR worker counts, the model
   directory, `--prune-frames`, `--prune-working-data` (unless the service
   was started with `-keep-working-data`), and `--owner-pid` set to the
   service's own process id.
4. While the worker runs, its stderr progress lines are parsed
   (`internal/worker.ParseProgress`) and written into the job row (stage,
   OCR processed/total); `internal/jobs.Hub` fans the same updates out to
   `GET /api/jobs/{id}/events` subscribers over server-sent events. Full
   stderr is copied to `<data>/jobs/<job id>/worker.log`.
5. On exit, the service decodes the worker's single stdout JSON object
   (`internal/worker.Interpret`), verifies the report file's hash and the
   timeline's schema (`internal/worker.Verify`), and loads the timeline
   (`internal/timeline.Load`). A job that passes becomes `succeeded` or
   `completed_with_stage_failures` and a `reports` row is created pointing at
   `report.json` and `timeline.json` inside the run directory; any other
   outcome leaves the job `failed`, `cancelled` or `interrupted` with an
   `error` code and message.
6. The client reads the report through `GET /api/reports/{id}/...` routes,
   which serve data built from `timeline.json` (turns, entries, summary) plus
   the recording for playback (`GET /api/recordings/{id}/video`, served with
   range requests) or an extracted frame when the recording is not on this
   machine.

## Process model

- **One analyzer job at a time.** `Manager.Run` loops over the queue and
  starts exactly one worker process; a second submission waits in the queue.
- **Queue limit.** `Submit` counts jobs in status `queued`
  (`internal/store.CountByStatus`) and rejects a new submission with
  `QueueFullError` (HTTP 429, code `queue_full`) once the count reaches
  `-queue`.
- **Cancellation.** `Manager.Cancel` on a queued job marks it `cancelled`
  directly. On a running job it cancels the job's context; `runner.Exec.Run`
  then kills the worker's whole process tree and waits up to its kill grace
  (five seconds by default) before returning.
- **Interruption recovery on restart.** `Manager.Recover` runs once at
  startup and marks every job still `running` from a previous process as
  `interrupted` (`internal/store.MarkInterrupted`); an interrupted job is
  never resubmitted automatically.
- **Owner-pid watchdog.** The worker command always carries `--owner-pid`
  with the service's own process id. The Python worker watches that pid with
  `psutil` and, once it is gone, kills every process it started and exits
  with status 3: if the service dies without shutting down cleanly, nobody
  would read the result and an orphaned analysis would otherwise keep the
  GPU and memory busy.
- **Windows job object.** On Windows, `internal/runner` assigns the worker
  process to a job object created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
  (`internal/runner/tree_windows.go`). Closing the job's handle, which
  happens whenever the service process ends, ends every process still in it;
  cancellation also calls `TerminateJobObject` directly, falling back to
  `taskkill /T /F` for a child that could not be assigned to the job object.

## Storage layout

Everything lives under the `-data` directory (default `.local/tracen-data`):

| Path | Contents |
| --- | --- |
| `tracen.db` | SQLite database: `users`, `sessions`, `recordings`, `jobs`, `reports`, `corrections` tables (`internal/store`) |
| `recordings/<user>/` | Uploaded video files, named by their server-issued id |
| `jobs/<job id>/run/` | The worker's output directory for one job: `report.json`, `timeline.json`, the viewer page, and (unless pruned) frame and OCR working data |
| `jobs/<job id>/worker.log` | The worker's full stderr for that job |
| `frames/` | On-demand frame cache (`internal/artifacts.Frames`), used when a report's recording is unavailable or a specific frame is requested |

## Security posture

- The server listens on a loopback address only (`-addr`, default
  `127.0.0.1:8765`); `ServeHTTP` rejects any request whose `Host` header is
  not in `AllowedHosts` (`localhost`, `127.0.0.1`, `::1`, plus the
  configured listen host) with `bad_host`, and rejects a cross-origin
  non-GET request with `bad_origin`.
- Passwords are hashed with PBKDF2-HMAC-SHA256, 600,000 iterations, a
  16-byte per-user salt (`internal/auth.HashPassword`); the hash never
  leaves the store.
- Sessions are an HttpOnly, `SameSite=Strict` cookie named `tracen_session`
  holding a random token; only the token's SHA-256 is stored
  (`internal/store`), so a copy of the database cannot be replayed as a
  login. Sign-in is rate limited per address.
- Every `/api` route except `/api/auth/*` requires a valid session
  (`unauthenticated`, HTTP 401 otherwise).
- Uploads, jobs and reports are scoped to the account that created them: a
  `recordings`, `jobs` or `reports` row with a `user_id` is visible only to
  that user (`internal/jobs.Manager.resolve`, `owns` in `internal/api`).
  Reports registered with `tracen import` (see `cmd/tracen/import.go`) carry
  no owner and are visible to every signed-in user.

## Packages

| Package | Role |
| --- | --- |
| `internal/api` | HTTP handlers: routes requests to the job manager, store, auth service and timeline documents; enforces host/origin checks and per-user visibility |
| `internal/auth` | User accounts and sessions: password hashing, session tokens, sign-in rate limiting |
| `internal/artifacts` | Confined access to a job's files: the report, the log tail, and frames extracted on demand |
| `internal/jobs` | The job lifecycle: admission, the durable queue, the single worker loop, cancellation and restart recovery |
| `internal/runner` | Starts the analyzer as a child process and kills its whole process tree on cancellation or shutdown |
| `internal/store` | The SQLite-backed store for jobs, reports, uploaded recordings, users and sessions |
| `internal/timeline` | Reads the compact `timeline.json` document the analyzer writes; the only analyzer output the browser-facing endpoints are built on |
| `internal/webassets` | Embeds the built browser client and serves it with a single-page fallback |
| `internal/worker` | The Go side of the analyzer contract: builds the worker command, parses its progress lines, decodes and verifies its terminal result |
