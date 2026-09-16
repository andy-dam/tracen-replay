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

Before any of the changes below, the application needs to run somewhere
other than a developer's machine: one image with the client, the service,
the analyzer, ffmpeg and the OCR models on the CPU provider (DirectML is
Windows-only), with the data directory on a volume; then a worker image the
service starts through a container runner instead of as a child process.
The steps are in the repository's TODO list.

## What would have to change

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

