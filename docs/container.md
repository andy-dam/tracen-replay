# The application image

[`Dockerfile`](../Dockerfile) builds the whole application into one image: the
Go service with the browser client embedded, the Python analyzer, ffmpeg and
the OCR models. It is the first step towards running the application anywhere
other than a development machine, and the same image is what a deployment
would run (see [ci-cd.md](ci-cd.md) and [deployment.md](deployment.md)).

## Build and run

```bash
docker compose up --build
```

The front page is then on <http://localhost:8765/>. Accounts, uploads,
analyses and reports work exactly as they do in the local build described in
[local-app.md](local-app.md).

The build has three stages: Vite builds the client into the Go package that
embeds it, the Go stage produces one static binary, and the runtime stage
installs the analyzer with its `vision` extra, ffmpeg and the models.

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

## What differs from the local build

The analyzer inside the image is a snapshot of the commit it was built from.
Locally the service runs the analyzer from the working tree, so an analyzer
change takes effect on the next analysis; in a container it takes a new image.
