# syntax=docker/dockerfile:1

# Two images from one file. The last stage, app, is the whole application:
# the Go service with the browser client embedded, the Python analyzer,
# ffmpeg and the OCR models. The worker stage underneath it is the analyzer
# alone, for a service that starts each analysis as a container
# (`docker build --target worker`). OCR runs on the CPU provider, which is
# several times slower per frame than a GPU; build with OCR_RUNTIME=cuda for
# the CUDA wheel instead.

# The client is built straight into the Go package that embeds it.
FROM node:22-bookworm-slim AS client
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# One static binary; go:embed takes the client bundle with it.
FROM golang:1.25-bookworm AS service
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
FROM python:3.13-slim-bookworm AS worker
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
# Installed in place: one copy of the package, and the worker still starts
# with /opt/tracen/analyzer as its working directory.
RUN pip install --no-cache-dir -e "/opt/tracen/analyzer[vision]" \
	&& if [ "$OCR_RUNTIME" = "cuda" ]; then \
		pip uninstall -y onnxruntime \
		&& pip install --no-cache-dir "onnxruntime-gpu==${ONNXRUNTIME_GPU_VERSION}"; \
	fi
# The models download once, here, so every analysis the image runs is
# offline. The reader downloads them itself, so the image holds exactly the
# files its fingerprint names.
RUN mkdir -p /opt/tracen/models \
	&& python -c "from tracen_replay.vision import NeuralReader; print(sorted(NeuralReader('/opt/tracen/models').models))" \
	&& test -f /opt/tracen/models/PP-OCRv6_det_small.onnx \
	&& test -f /opt/tracen/models/en_PP-OCRv5_rec_mobile.onnx
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
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
	CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/healthz')"
ENTRYPOINT ["/usr/local/bin/tracen-entrypoint"]
