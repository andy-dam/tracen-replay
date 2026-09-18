# syntax=docker/dockerfile:1

# Two images from one file. The last stage, app, is the whole application:
# the Go service with the browser client embedded, the Python analyzer,
# ffmpeg and the OCR models. The worker stage underneath it is the analyzer
# alone, for a service that starts each analysis as a container
# (`docker build --target worker`). OCR runs on the CPU provider, which is
# several times slower per frame than a GPU; build with OCR_RUNTIME=cuda for
# the CUDA wheel instead.

# Every base image is pinned to a digest, so a rebuild months later starts
# from the same bytes; the tag beside it says what the digest is. Update
# all three together with `docker buildx imagetools inspect <tag>`.

# The client is built straight into the Go package that embeds it.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS client
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# One static binary; go:embed takes the client bundle with it.
FROM golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS service
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/ ./cmd/
COPY internal/ ./internal/
COPY --from=client /src/internal/webassets/dist/ ./internal/webassets/dist/
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/tracen ./cmd/tracen

# The analyzer alone. A container of this stage takes the analyzer's own
# command line (docs/analysis-job.md), source first, with the recording and
# the run directory mounted under /job by internal/runner.Container.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS worker
# cpu (the default) or cuda. The CUDA wheel replaces the CPU one and brings
# its own NVIDIA runtime libraries, so the container needs --gpus to use it.
ARG OCR_RUNTIME=cpu
ARG ONNXRUNTIME_GPU_VERSION=1.29.0
ENV PYTHONUNBUFFERED=1 \
	PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
	&& apt-get install -y --no-install-recommends ffmpeg \
	&& rm -rf /var/lib/apt/lists/*
COPY analyzer/ /opt/tracen/analyzer/
COPY docker/constraints.txt docker/models.sha256 /opt/tracen/
# Installed in place: one copy of the package, and the worker still starts
# with /opt/tracen/analyzer as its working directory. The constraints pin
# every package to the version the image was last built with.
RUN pip install --no-cache-dir -c /opt/tracen/constraints.txt -e "/opt/tracen/analyzer[vision]" \
	&& if [ "$OCR_RUNTIME" = "cuda" ]; then \
		pip uninstall -y onnxruntime \
		&& pip install --no-cache-dir "onnxruntime-gpu==${ONNXRUNTIME_GPU_VERSION}"; \
	fi
# The models download once, here, so every analysis the image runs is
# offline. The reader downloads them itself, so the image holds exactly the
# files its fingerprint names, and each is checked against the hash the
# repository records: a changed download fails the build.
RUN mkdir -p /opt/tracen/models \
	&& python -c "from tracen_replay.vision import NeuralReader; print(sorted(NeuralReader('/opt/tracen/models').models))" \
	&& cd /opt/tracen/models \
	&& sha256sum -c /opt/tracen/models.sha256
WORKDIR /opt/tracen/analyzer
ENTRYPOINT ["python", "-X", "utf8", "-m", "tracen_replay.analysis_job"]

# The whole application on top of the worker: the service, with the client
# embedded, starts the same analyzer as a child process.
FROM worker AS app
ENV PORT=8765 \
	TRACEN_DATA=/data
COPY --from=service /out/tracen /usr/local/bin/tracen
COPY docker/entrypoint.sh /usr/local/bin/tracen-entrypoint
RUN chmod +x /usr/local/bin/tracen-entrypoint && mkdir -p /data
VOLUME /data
EXPOSE 8765
# /readyz answers 503 when any of the service's checks fails (the
# interpreter, ffmpeg, the models, the analyzer), so a broken image is
# unhealthy rather than merely up; /healthz is the liveness answer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
	CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/readyz')"
ENTRYPOINT ["/usr/local/bin/tracen-entrypoint"]
