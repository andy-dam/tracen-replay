# Go API and repository plan (draft, 2026-09-14)

Status: implementation started 2026-09-14 with the user's go-ahead (see the
status section at the end); the G9 judgement of the analyzer is still pending
acceptance ([milestone](milestone-go-app.md)). This document answers three
questions asked while the v35 validation runs: is the Python worker contract
stable enough to design against, what does the Go service look like, and how is
the repository organised.

## 1. What Go may depend on (the worker contract)

The Go service depends on exactly four things, all documented in
[analysis-job.md](analysis-job.md) and unchanged since v30:

| Surface | Stability | Go's use |
| --- | --- | --- |
| Command line: `python -m tracen_replay.analysis_job SOURCE --output DIR --workers N --model-dir M --prune-frames` | stable (v30+); `--fps` and reparse flags exist but the app does not need them | build the argv, never a shell string |
| Terminal JSON on stdout (`schema_version: tracen-replay/analysis-job-v1`): `status` (`succeeded`, `completed_with_stage_failures`, `failed`), `report_path`, `report_sha256`, `source_sha256`, `evidence_root`, `timeline_path`, `stage_failures`, `pruned_frames`, `error.code/message`; exit codes 0/1/2 | stable (v30+) | the only success criterion: one valid object, status, hash of the report file re-computed and compared |
| `timeline.json` (`tracen-replay/timeline-v1`): `source`, `recognition`, `turns[]` (id, label, phase, calendar_value, start_ms, end_ms, window_kind, action_status, opening stats/performance, per-field accounting), `entries[]` (id, kind, turn_id, first/last_seen_ms, changes per channel with `amount` and `basis`, compact `detail`), `summary` (field_status_counts, action_statuses, entry_counts, stage_failures) | stable v1; additive changes only, versioned by `schema_version` | the whole viewer payload: run list, turn navigator, entries, uncertainty display |
| Progress lines on stderr: `{"stage":"stage_done","name":...,"wall_s":...}` and `{"stage":"ocr","processed":n,"total":m}` | stable format (v30+); stage names may be added | job progress: stage name and elapsed; a percentage only for the OCR line |

Not a contract: the internal structure of `report.json` (150-180 MB, OCR-rich,
changes with every recognition fix). Go stores it, hashes it, serves it as a
download, and never parses it beyond `schema_version`. Everything the browser
needs comes from `timeline.json`. If the viewer later needs something the
timeline lacks, the fix is to extend timeline-v1 (additively) in Python, not
to read the report in Go.

Evidence images: the application runs the worker with `--prune-frames`, so no
screenshot paths exist after a job. The viewer shows a frame by asking Go to
extract it from the recording at the entry's timestamp (`ffmpeg -ss`), cached
under the job. This replaces the `evidence/{path}` endpoint in the milestone
table and removes the 11-16 GB of per-run frames entirely.

Two small worker additions are worth making before Go starts, both additive:

- `worker_version` in the terminal JSON (package version plus the code digest
  of the analyzer), so Go can pin the worker it was tested with and refuse an
  unexpected one.
- A `--dense-workers N` argument (today derived as `workers - 1`), so the
  memory ceiling is an explicit configuration on the Go side.

Verdict: stable enough. The fields Go consumes were designed for Go in v30 and
survived five candidates without change; the running validation (v35, creator
C, the untouched set) exercises the worker, not the contract.

## 2. Go service architecture

Constraints from the measurements: one full career run takes 40-45 minutes,
uses the GPU for the whole OCR and reread phases, and peaks near 20 GB of RAM
(main process 1-2 GB, four OCR workers 3-4 GB each, three dense workers up to
5-6 GB each, in phases). So: one job at a time per machine, a bounded queue,
and the worker count as configuration. That shapes everything below.

Libraries (standard library first; each addition has one job):

| Need | Choice | Why |
| --- | --- | --- |
| HTTP | `net/http` with Go 1.22+ method/pattern routing | no framework needed for ~12 endpoints; middleware is three functions |
| Logging | `log/slog` | structured logs for job lifecycle; ships with Go |
| Persistence | `modernc.org/sqlite` (pure Go, no cgo) with embedded migrations | one file, transactions, queries for job lists; the same schema moves to Postgres/Azure SQL later. Replaces the "atomic JSON files" idea in the milestone doc; the interface stays the same |
| Process control | `os/exec` + `golang.org/x/sys` (Windows Job Objects; `Setpgid` on Linux) | the worker spawns OCR pools; cancellation must kill the tree on both platforms |
| Progress to browser | Server-Sent Events over `net/http` | one-way stream of the stage lines; no WebSocket library |
| API description | hand-written `api/openapi.yaml`, optional `oapi-codegen` for server types | the frontend and the Go handlers share one description; generation is optional |
| Config | flags + env (`TRACEN_*`) via a small `internal/config` | no library |
| Tests | `testing`, golden files under `testdata/`, `httptest` | fake runner for state machine tests; one real Python run for integration |

Explicitly not chosen: an ORM, a DI framework, a job framework, gRPC. There is
one worker command and one process; a queue table and a goroutine are enough.

Packages (module `github.com/andy-dam/tracen-replay` stays at the repo root):

```
cmd/tracen/            main: flags, wiring, serves API + embedded web assets
internal/config/       configuration loading and validation (paths, worker counts, queue size)
internal/api/          HTTP handlers, request/response DTOs, SSE; no business logic
internal/jobs/         job state machine (queued/running/succeeded/completed_with_stage_failures/failed/cancelled/interrupted), queue, restart recovery
internal/worker/       the Python contract: argv builder, stderr progress parser, stdout terminal-JSON decoder and validation, report hash check, version pin
internal/store/        SQLite access for jobs and reports; migrations embedded
internal/artifacts/    confined access to a job's directory (timeline, report download, log tails), frame extraction via ffmpeg with caching
internal/timeline/     typed model of timeline-v1, loading, turn/entry queries used by the API
internal/sources/      (removed 2026-09-15: recordings arrive only by upload, so the client never learns how they are stored)
web/                   frontend (see section 4); built output embedded into the binary via embed.FS for the local milestone
api/openapi.yaml       the HTTP contract
testdata/              synthetic worker outputs (stdout/stderr transcripts), a small timeline fixture, malformed cases
```

Job lifecycle: `POST /api/jobs` inserts a `queued` row with its own output
directory; a single runner goroutine takes the next queued job, marks it
`running` with the process id and start time, starts Python with the argv from
`internal/worker`, streams stderr lines into the SSE hub and a bounded log
file, and on exit reads exactly one stdout object. Success requires: exit 0,
`status` in the two success values, the report file present and its SHA-256
equal to `report_sha256`, `timeline.json` present and parseable as timeline-v1.
Anything else is `failed` with the worker's `error.code` or a Go-side code
(`bad_terminal_output`, `report_hash_mismatch`, `timeline_invalid`). On
startup, `running` rows whose process is gone become `interrupted`; queued rows
resume. Cancellation kills the process tree and records `cancelled`; a late
terminal object for a cancelled job is discarded.

## 3. Repository layout (monorepo)

Today the root holds the Python package (`tracen_replay/`, `scripts/`,
`tests/`, `pyproject.toml`), `docs/`, an unfinished `go.mod` + `internal/app`,
and about 700 untracked scratch files (`pytest-cache-files-*`, `tmp*`,
`debug_g08_*`, `.tmp-*`). Target layout:

```
tracen-replay/
  README.md, AGENTS.md, CLAUDE.md
  go.mod, go.sum                  Go module at the root (tooling-friendly)
  cmd/, internal/, api/, testdata/   Go service (section 2)
  worker/                         Python analyzer: tracen_replay/, scripts/, tests/, pyproject.toml
  web/                            frontend (section 4)
  docs/                           all design, contract and acceptance documents
  deploy/                         Dockerfile for api+worker (GPU host), compose file, CI workflows
  .local/                         gitignored: models, runs, evaluation records
```

Sequencing rules:

- Do not move the Python package until the G9 judgement is accepted and the
  final candidate is sealed. The sealed snapshots and evaluation scripts refer
  to `tracen_replay/` at the root; moving it changes the code manifest and
  every wrapper path. The move is the first commit of the Go milestone, done
  alone, with the snapshot tool updated in the same commit.
- Before that commit, delete the scratch clutter and add ignore rules
  (`/tmp*`, `/pytest-cache-files-*`, `/.tmp-*`, `/debug_*`, `/matrix-*`,
  `/*-probe-*`). None of it is referenced by tests or records.
- Replace `internal/app/server.go` (the abandoned scaffold) rather than
  extending it; the milestone document already says so.
- One CI workflow with three jobs: Go (`go vet`, `go test -race`), Python
  (`unittest discover -s worker/tests`), web (build + typecheck). The Python
  job runs without models: the OCR tests already skip when models are absent.

## 4. Frontend: monorepo `web/`, not a second repository

Recommendation: keep it in this repository under `web/`.

- The frontend's only dependency is the HTTP contract in `api/openapi.yaml`
  and the timeline-v1 shape. A contract change and its consumer land in one
  commit and one review; two repositories turn every field rename into a
  coordinated release.
- Static hosts (Cloudflare Pages, Vercel, GitHub Pages) build from a
  subdirectory of a monorepo; a separate repository buys nothing for hosting.
- For the local milestone the built assets are embedded in the Go binary, so
  `web/` must be next to the Go code anyway.
- The one reason to split later is organisational (a separate team or a
  public frontend with a private backend). Neither applies now.

Stack: Vite + TypeScript (React or Svelte; either is fine at this size), a
generated client from `api/openapi.yaml`, and a build step whose output the
Go binary embeds. Development runs the Vite dev server with a proxy to the Go
API; production is one binary.

## 5. Deployment shape and costs

- Local milestone: one binary on the GPU machine, loopback only, one job at a
  time, recordings chosen from a configured folder.
- Hosted later: the API and the worker must share a machine with a GPU
  (DirectML on Windows or onnxruntime on Linux) and at least 24 GB of RAM for
  one concurrent job; the frontend is static; storage per completed run is the
  report (150-180 MB), the timeline (about 1 MB) and logs. The report can be
  compressed or dropped after a retention period because the timeline plus the
  recording reproduce every screenshot.
- The recording upload path (0.5-1 GB files) is the hosted milestone's first
  problem, not this one.

## Status 2026-09-14 (implementation started with the user's go-ahead)

Built and tested offline, Go 1.25, standard library plus `modernc.org/sqlite`
and `golang.org/x/sys`:

| Package | What it does | Tests |
| --- | --- | --- |
| `internal/worker` | argv builder, stderr progress parser, terminal-JSON decoder, exit-code interpretation, report hash and timeline verification | real transcripts from `testdata/worker` (a 553-line fresh-run stderr, a real succeeded object), crafted failures |
| `internal/timeline` | typed timeline-v1 model, turn/entry queries | synthetic fixture `testdata/timeline/mini.json`; the real v1 development timeline when present |
| `internal/store` | SQLite jobs and reports, embedded migrations, restart marking | round trips, queue order, reopen |
| `internal/jobs` | queue, single worker loop, cancel, interrupt, stale-completion discard, SSE hub | scripted runner: success, worker failure, contract violation, hash mismatch, queue limit, cancel before/during, shutdown, recover |
| `internal/runner` | `os/exec` runner, stderr streaming, stdout capture, process-tree kill (a Windows job object with kill-on-close, `taskkill /T` as the fallback; process group elsewhere). The worker also gets `--owner-pid` and ends itself when the service is gone, on every platform | a fake worker built from the test binary; a grandchild is proven dead after cancel and after the job handle closes (what a crash of the service does) |
| `internal/artifacts` | confined file access, log tail, ffmpeg frame extraction with cache | includes a real ffmpeg extraction from a generated clip |
| `internal/api` | the HTTP surface in section 2, host/origin checks, SSE | httptest against fakes and the fixture timeline |
| `cmd/tracen` | the binary: flags, readiness probes, graceful shutdown; `tracen import RUN_DIR` registers an existing bundle | smoke test below |

Smoke test on this machine: `tracen import` registered the accepted v1 bundle
(73 turns, 620 entries) in place; `tracen -sources C:\Users\andy-\Videos`
answered `/readyz` with every probe green, listed the reports and sources,
served turn summaries and turn-002's entries from the timeline, refused a
foreign `Host`, and extracted the frame at 29.833 s of the recording with
ffmpeg (a 213 KB JPEG of the training screen the entry describes).

Also done since: the browser client in `web/` (Vue 3 + TypeScript on Vite,
no router or state library; hash routes for runs, a report viewer and a job
view with the SSE progress stream), built into `internal/webassets/dist` and
embedded in the binary (`tracen` serves it at `/`, API paths untouched; only
`.gitkeep` is tracked, `npm run build` fills the directory). The worker's
`worker_version` and `--dense-workers` additions are in (sealed as v36, batch
queued). The user is choosing between Vue and Svelte; Vue was used because
the viewer is nested read-only panels with one live stream, which Vue's
single-file components handle plainly; the API client (`web/src/api.ts`) and
styles are framework-neutral if the choice changes.

Browser check 2026-09-14 05:20: the three accepted bundles were imported into
a scratch data directory and explored in Chrome (runs page, report summary,
turn navigator, opening states with endpoints, entries, frame extraction from
entry times, the unassigned view; 400 px and 1400 px; no console errors).
Two viewer changes came out of it: the entry table now shows a lesson's
performance cost with its basis, song/race/concert/skill-purchase details and
the raw text of non-stat effects (energy, hype, caps, level-ups) instead of
"reference only", and the record toggle is a button so it is reachable from
the keyboard; the active turn scrolls into view on deep links.

CI 2026-09-14 05:45: `.github/workflows/ci.yml` runs `go vet` and `go test`
(with the race detector on Linux) on Ubuntu and Windows, builds the browser
client, and runs the Python suite with the `vision` extra on both systems.
The Python job was rehearsed on a clean copy of the tracked tree (no `.local`
evidence, no OCR models): 2767 tests pass with 194 skips; nine test modules
that read preserved local evidence now skip instead of erroring when it is
absent. The startup command and the messages a user can expect are in
`docs/local-app.md`.

Real-worker exercise 2026-09-14 06:12 (scratch server, CPU OCR while the GPU
was busy): cancel during OCR, restart, forced kill of the service, restart;
all checks pass (details in `docs/milestone-go-app.md`, section 4). It found
one defect first: a forced kill of the service left the worker tree running;
fixed with a Windows job object (kill-on-close) in the runner and `--owner-pid`
in the worker (it ends itself when the owning process is gone; the portable
mechanism, also the Linux one).

Measured on this machine (imported bundles, warm process): service start to
`/readyz` ready 0.28 s; `/api/reports` 10 ms; a report summary 89 ms cold /
20 ms warm; the turn list 21 ms; one turn with its entries 13 ms; a frame
extracted by ffmpeg 1.7 s cold / 4 ms from the cache. Processing time through
the service still needs the real DirectML run.

Real Go-to-Python run 2026-09-14 09:30: creator B's 40-minute recording
submitted through the API, DirectML, four OCR workers, succeeded in 2580 s
with no skipped stage; the report equals the direct Python run of the same
recording in every recognized record (the ledger differs only by the finale
split of v39, which the worker picked up from a source edit during the run;
the run is repeated on the sealed v39 snapshot). The report is served by the
same viewer as the imported ones.

Retention: the worker's `--prune-working-data` (passed unless the service is
started with `-keep-working-data`) deletes the run's directories after
validation, so a finished job keeps the report, the timeline, the viewer page
and the log instead of about 1 GB of OCR caches.

Not done: the repository move of the Python package.

## 6. Containers (decided 2026-09-14)

Not for the local milestone. One image later, for the hosted milestone: a
multi-stage `deploy/Dockerfile` (Node build of `web/`, Go build of
`cmd/tracen`, Python runtime with ffmpeg and the OCR models) holding the API
and the worker together, because the API spawns the worker and reads its
files. The website needs no container of its own; it is embedded in the
binary. Prerequisite: the analyzer was validated on Windows with DirectML; a
Linux container runs onnxruntime with CUDA, a different execution provider,
so the batch and one fourth run must be repeated on that stack before a
container result is trusted.

Decision restated 2026-09-14 04:40 (user question): yes, the project will be
containerized, as one image (Go API + Python worker + embedded website +
ffmpeg + OCR models) in the deployment milestone. Local use on Windows stays
native.

## 7. OCR device fallback

The worker selects its OCR device from `TRACEN_REPLAY_OCR_DEVICE`: `auto`
(DirectML, then CUDA, whichever onnxruntime provides, otherwise CPU), `dml`,
`cuda` or `cpu`. A machine without a supported GPU therefore still analyzes
with the same models on the CPU provider, slower. Done 2026-09-14 (v37/v38):
the CUDA provider is wired (`vision.ocr_device`, `vision.engine_params`; the
CUDA switch enters the engine parameters only when CUDA is selected, so the
DirectML and CPU cache fingerprints of earlier runs are unchanged), the Go
service takes `-ocr-device` and passes it to the worker as that environment
variable, `/readyz` shows the configured device and each report's recognition
record carries the resolved one. Still to do: measure the CPU-only run time
once the GPU runs are finished, and validate CUDA on the container stack.
