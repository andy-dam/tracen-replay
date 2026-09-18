# The application image and the worker image

[`Dockerfile`](../Dockerfile) builds two images. Its last stage, `app`, is
the whole application in one image: the Go service with the browser client
embedded, the Python analyzer, ffmpeg and the OCR models. The `worker` stage
underneath it is the analyzer alone, for a service that starts each analysis
as a container instead of a child process (see [the worker
image](#the-worker-image) below). Running the application anywhere other
than a development machine starts here, and the same images are what a
deployment would run (see [ci-cd.md](ci-cd.md) and
[deployment.md](deployment.md)).

## Build and run

```bash
docker compose up --build
```

The front page is then on <http://localhost:8765/>. Accounts, uploads,
analyses and reports work exactly as they do in the local build described in
[local-app.md](local-app.md).

The build has four stages: Vite builds the client into the Go package that
embeds it, the Go stage produces one static binary, the `worker` stage
installs the analyzer with its `vision` extra, ffmpeg and the models, and
the `app` stage adds the binary on top of the worker. `compose.yaml` names
the `app` target.

## What the container expects

| Setting | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8765` | the port the service listens on, on every interface in the container |
| `TRACEN_DATA` | `/data` | the database, uploads, job output and frame cache |
| arguments | none | passed on to `tracen`, so `-workers 2` and the other flags still work |

`compose.yaml` mounts a named volume on `/data`, so reports survive
`docker compose down`. The service's own defaults for the analyzer are set by
[`docker/entrypoint.sh`](../docker/entrypoint.sh): the interpreter in the
image, `/opt/tracen/analyzer` as the worker's working directory, and
`/opt/tracen/models` for the OCR models.

## The OCR device

The image installs the CPU provider, because DirectML is Windows-only. OCR is
then several times slower per frame than on this machine's GPU (see
[ocr-performance.md](ocr-performance.md)), and every worker is a process, so
the worker counts belong under the cores the container may use; `compose.yaml`
asks for two base workers and one dense worker.

Building with `--build-arg OCR_RUNTIME=cuda` replaces the CPU wheel with the
CUDA one, which brings its own NVIDIA runtime libraries; the container then
needs `--gpus` to reach a card.

## The models

The models download once, during the build, and the reader itself does the
downloading, so the image holds exactly the files its fingerprint names. Every
analysis the image runs is offline afterwards. A build therefore needs network
access, and it fails rather than producing an image that would download models
at run time.

The learned result-card reader is not in the image. It is an exported file
that is passed to the service with `-learned-reader`, so a container that
should use one mounts it and names it in the arguments.

## What is pinned

A rebuild from the same commit produces the same image, months later:

- **Base images** are named by digest in every `FROM`, with the tag beside
  the digest for the reader. `docker buildx imagetools inspect <tag>` gives
  the current digest of a tag; update all three together.
- **Python packages** are pinned by [`docker/constraints.txt`](../docker/constraints.txt),
  which pip takes as constraints when it installs the analyzer's `vision`
  extra. A requirement the extra drops is simply not installed; a new one is
  resolved freely until it is added. To move every package forward, build
  once without the file and copy `pip freeze` from the image back into it.
- **Model files** are checked against [`docker/models.sha256`](../docker/models.sha256)
  after the reader downloads them; a download that differs fails the build.
  The two hashes the reader's fingerprint names are among them.

The build context is under 2 MB: [`.dockerignore`](../.dockerignore) keeps
out `.local` (recordings, models, runs, session records), `.git`, the
virtual environment, `node_modules`, the built client, the lab and every
`.exe` and `.db`.

## Health

The image's `HEALTHCHECK` asks `/readyz` every 30 seconds after a 30-second
start period. `/readyz` answers 503 when any of the service's checks fails
(the interpreter, ffmpeg, the models, the analyzer), so an image with a
broken analyzer shows as unhealthy rather than merely up. `/healthz` answers
200 whenever the process serves, which is the liveness question; a platform
with separate probes should point liveness at `/healthz` and readiness and
startup at `/readyz`.

## Size

Measured on 2026-09-18:

| Image | Unpacked | Compressed (what a registry stores and a pull downloads) |
| --- | ---: | ---: |
| `tracen-replay-worker` | 1.51 GB | 424 MB |
| `tracen-replay` (application) | 1.53 GB | 430 MB |

`docker images` reports the unpacked size in every image store; `docker
image inspect` reports the compressed size under the containerd store and
the unpacked one under overlay2, so the CI step uses the former. The
application adds only the service binary (18 MB) on top of the worker.
Of the worker, ffmpeg and the libraries Debian installs with it are 466 MB,
the Python packages (opencv, onnxruntime, numpy, rapidocr and their
dependencies) 435 MB, the Python base image about 120 MB, the analyzer 32 MB
and the models 19 MB. A static ffmpeg build would take about 400 MB off; it
is not done, because it would mean trusting a third party's binary.

## In CI

The `image` job of [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
builds both targets on every push and pull request, asks the worker image
for its version, starts the application image and waits for `/readyz`, for
`/healthz` and for docker to report the container healthy, and writes both
sizes into the run's summary.

## What differs from the local build

The analyzer inside the image is a snapshot of the commit it was built from.
Locally the service runs the analyzer from the working tree, so an analyzer
change takes effect on the next analysis; in a container it takes a new image.

## The worker image

```bash
docker build --target worker -t tracen-replay-worker:local .
```

A container of this image is one analysis. Its entrypoint is the analyzer's
own command line from [analysis-job.md](analysis-job.md), so the arguments
given to the container are the analyzer's, source first:

```bash
docker run --rm -v /path/to/recording.mp4:/job/source/recording.mp4:ro -v /path/to/run:/job/run \
    tracen-replay-worker:local /job/source/recording.mp4 --output /job/run --workers 2 --model-dir /opt/tracen/models
```

The models are at `/opt/tracen/models` in the image, so `--model-dir` names
them; `--worker-version` alone answers the identity query. Nothing about the
container is specific to the service: progress is on stderr and the terminal
object on stdout, exactly as for a child process.

### The service with a worker image

The service starts each analysis as a container of the worker image when it
is given one:

```bash
tracen -worker-image tracen-replay-worker:local -workers 2 -dense-workers 1
```

`internal/runner.Container` then replaces `internal/runner.Exec` as the
job runner:

- The recording is mounted read-only as `/job/source/<name>`, the job's run
  directory (`<data>/jobs/<id>/run`) read-write as `/job/run`, and the
  learned reader named by `-learned-reader`, if any, read-only under
  `/job/reader`. The run directory is created by the service before the
  mount, because a directory docker creates for a bind mount belongs to
  root.
- The container gets the same analyzer arguments a child process gets, with
  those paths, and the OCR device through the same environment variable.
  `--owner-pid` is not passed: the service's process id means nothing
  inside the container.
- The worker's terminal object names paths under `/job/run`; the runner
  maps them back to the host run directory before the service verifies the
  report, so the job record and the report row hold host paths as before.
- Cancelling a job removes the container by name (`docker rm -f`), repeated
  until the docker client that started it has exited: a container outlives
  its client, so killing the client alone would leave the analysis running.
- Every container carries the label `tracen-replay.owner=<data directory>`.
  At startup, after interrupted jobs are marked, the service removes any
  container still running under its own label: those are the workers a
  service that died mid-job left behind.
- `docker run` exit codes 125, 126 and 127, with nothing on stdout, are
  reported as the job failing to start, with docker's own last line as the
  reason (a missing image, a daemon that is not running).

The readiness checks then cover `docker`, `ffmpeg` (frames are still cut on
the host) and `worker-image` (`docker image inspect` of the image) instead
of the interpreter, model directory and analyzer paths, and the analyzer's
identity comes from a `--worker-version` container. `-python`, `-workdir`
and `-model-dir` are unused in this mode.

| Flag | Default | Meaning |
| --- | --- | --- |
| `-worker-image` | unset | run analyses as containers of this image |
| `-docker` | `docker` | the docker command line client |
| `-worker-gpus` | unset | `docker run --gpus` value, e.g. `all`, for an image built with `OCR_RUNTIME=cuda` |
| `-worker-user` | unset | `docker run --user` value, e.g. `1000:1000`, so the run directory's files belong to the service's user on a Linux host rather than to root |

The service still runs one job at a time from its own queue; this mode
changes where the analysis runs, not how jobs are scheduled. The job the
service submits and the report it stores are the same as with a child
process, so the browser sees no difference.

Checked on this machine against Docker Desktop, with the service on the
host in this mode and a two-minute clip uploaded through the API: the job
ran in a container with the three mounts and the label, its 481 base frames
read on the CPU provider, and the report was stored and summarized with host
paths and the analyzer identity of the image in 7.5 minutes. Cancelling a
running job removed its container in under two seconds. Killing the service
mid-job left the container running, and the restarted service removed it as
it marked the job interrupted.
