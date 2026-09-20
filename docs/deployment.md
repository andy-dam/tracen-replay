# Hosting later

The application runs as one local binary today (see
[architecture.md](architecture.md) and [local-app.md](local-app.md)). This
document is not a deployment plan; it records what the current design already
keeps hostable, and what would still have to change before it could run as a
hosted service.

## Already hostable

- **The client is a static bundle.** It is built once and can be served by
  the service, from a directory, or from any static host; the service runs
  API-only with the client's origin allowed for credentials (`-api-only`,
  `-allowed-origin`, `-cookie-samesite`). Sessions are a cookie, so the
  client and the API must share a site (one domain, any subdomains or
  ports) or run over HTTPS with `-cookie-samesite none`.
- **The client never learns storage layout.** The Vue client (`web/`) knows
  recordings, jobs and reports only by the server-issued ids the API returns
  (`web/src/api.ts`). It never receives or sends a file path, so moving
  uploads or job output to a different kind of storage does not change the
  client.
- **The worker contract is a process boundary.** The service calls the
  analyzer as a subprocess and reads back one JSON result over stdout plus
  progress lines over stderr (`internal/worker`, documented in
  [analysis-job.md](analysis-job.md)). Nothing about that contract assumes
  the worker runs on the same machine as the caller, only that the caller can
  start a process and read its output.
- **One process per analysis, deliberately.** The analyzer is started fresh
  for each job and exits when it is done, and a persistent worker would be a
  step backwards. What it would save is interpreter start, imports and one
  OCR session per worker, seconds against an analysis of twenty minutes or
  more; what it would cost is holding the memory an analysis needs (a main
  process peaking near 5 GB, each OCR worker near 2 GB) between jobs, and a
  protocol and lifecycle to maintain. Exiting returns all of it to the
  operating system. The same shape is what a serverless job billed by the
  second wants: a container that never scales to zero would spend a whole
  month's free grant in about half a day of idling, while a process per
  analysis spends it only on work. Hosting changes who starts that process,
  not the fact that it is one. The service starts it with no console of its
  own: Python is a console program, so a Windows host that runs the service
  without a console would otherwise flash up a window for every analysis,
  while the service reads the worker through pipes and has no use for one.
  The flag is Windows-only and lives behind the build tag that already splits
  process-tree handling; a Linux host or a container inherits file descriptors
  and has no desktop to put a window on.
- **Jobs and reports are rows in one database.** `internal/store` keeps
  `users`, `sessions`, `recordings`, `jobs`, `reports` and `corrections` in a
  single SQLite file today. The `internal/jobs.Store` and `auth.Store`
  interfaces that the rest of the service talks to name only their methods,
  not SQLite, so a different backing store implements the same interface.
- **Uploads are one directory.** Recordings are written under
  `<data>/recordings/<user>/` (`internal/api/uploads.go`) and read back by
  id; nothing else in the service depends on them being local files as
  opposed to objects fetched by key.
- **The OCR device is a worker flag.** `internal/worker.Command.Env` selects
  `auto`, `cpu`, `dml` or `cuda` through an environment variable
  (`TRACEN_REPLAY_OCR_DEVICE`); the worker does not assume it runs on the
  same hardware class as the service that starts it.

## First step: containers

The application image exists: one image with the client, the service, the
analyzer, ffmpeg and the OCR models on the CPU provider (DirectML is
Windows-only), with the data directory on a volume. The worker image exists
too: the analyzer alone, which the service starts as one container per
analysis when given `-worker-image`, through `internal/runner.Container`.
Both are described in [container.md](container.md). The worker is still
spawned by the service, on the same docker host; addressing a worker that
runs elsewhere is the last item below.

How a commit would become a running deployment — the image tag, the registry,
the revision and the rollback — is in [ci-cd.md](ci-cd.md).

## Abuse and limits

A hosted service is asked for two things it is not asked for on one
machine: to refuse what a stranger can do to it, and to spend no more than
its owner can afford for a year. Both are in the service today, as flags
whose defaults are set for a small hosted deployment; one machine used by its
owner turns the budgets off ([local-app.md](local-app.md) lists every flag).

What a stranger cannot do:

- **Create accounts at will.** Registration is open, and what bounds it is
  that one address makes at most five accounts an hour and that an account
  buys nothing by itself: what costs money is bounded per account below,
  so a crowd of accounts from one person is a crowd of daily budgets, not
  a crowd of machines. Sign-ins stay limited to ten attempts per address
  per five minutes. Behind a reverse proxy the client is the address the
  proxy forwarded (`-trusted-proxy`), so one proxy does not make everyone
  one address. Should open registration ever be abused, `-registration
  closed` stops new accounts and `-registration invite` with `-invite-code`
  takes them by code (the API accepts an `invite` field; the sign-up form
  does not show one).
- **Reach another user's data.** Every recording, job and report is looked
  up under the signed-in user; another user's id is "not found". The client
  never names a path; every file the service opens is confined under its
  own directory. A 5xx tells the client one fixed sentence and the log the
  detail.
- **Fill the disk.** One upload is at most 3 GB; a user keeps at most five
  uploads and 8 GB; all uploads together at most 100 GB, past which uploads
  are refused as temporary; uploads are probed with ffprobe before they are
  recorded and refused when they are not a video, longer than three hours,
  larger than 4096x2304 or faster than 120 frames a second; uploads older
  than 14 days that nothing is analyzing are deleted by the hourly sweep
  (their reports stay); the extracted-frame cache is pruned to 2 GB; expired
  sessions are dropped.
- **Burn the workers.** A user has one analysis queued or running at a time
  and starts at most three a day; the service starts at most 24 a day; an
  analysis is stopped after four hours; frame extractions are at most 120 a
  minute per user and two at once; a worker container has 14 GB, 512
  processes and no network.
- **Talk the service into something.** Only the configured hosts are
  answered (`-allowed-host`); a cross-origin request that could change
  state is refused unless its origin is an allowed client; the session
  cookie is HttpOnly, SameSite and, over HTTPS, Secure; every response says
  what it is and refuses framing, and the client's page is pinned to its own
  origin by a content security policy; headers are read within ten seconds
  and bounded at 64 KiB, idle connections closed after two minutes, JSON
  bodies bounded at a few KiB.

Why these numbers. The analyses are the whole cost: one takes about an hour
of the GPU machine, two run at once, so 24 a day is half the machine's day
and leaves it usable, and three a day per user is a full career's worth of
retries. Five uploads of a career (about 1.5 GB each) is a week of play kept
for review; 14 days of retention with 100 GB of room holds more than the
daily budget can fill. The web tier itself serves static files and small
JSON, so its cost does not move with these numbers; what they bound is the
machine that analyzes and the disk it writes.

## What would have to change

- **A host name the service answers for.** The API accepts `localhost`,
  `127.0.0.1`, `::1` and the host named by `-addr` (`internal/api`), so a
  public name or a proxy in front of it is refused until the allowed hosts
  can be named.
- **Auth beyond local accounts.** Sessions today are a cookie checked against
  rows in the local database (`internal/auth`), sized for accounts on one
  machine, not for a multi-tenant or internet-facing deployment.
- **An object store for uploads and job outputs.** `recordings/<user>/` and
  `jobs/<id>/run/` are local directories on the machine running the service;
  a hosted deployment needs uploads and job outputs to live somewhere every
  service instance and worker can reach.
- **A queue that outlives one process.** `internal/jobs.Manager` runs one
  worker at a time from an in-process queue and recovers from a restart by
  marking in-flight jobs interrupted, never replaying them
  (`Manager.Recover`). A hosted deployment needs a queue that survives the
  service process itself and can hand a job to a different worker instance.
- **GPU workers.** The analyzer's OCR stage picks a device from the machine
  it runs on (`auto`, `cpu`, `dml`, `cuda`); running workers separately from
  the API means provisioning and addressing GPU-capable workers instead of
  spawning a local child process.

