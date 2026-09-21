# syntax=docker/dockerfile:1

# Three images from one file. The last stage, app, is the whole application:
# the Go service with the browser client embedded, the Python analyzer,
# ffmpeg and the OCR models. The worker stage underneath it is the analyzer
# alone, for a service that starts each analysis as a container
# (`docker build --target worker`). The site stage is the service without
# an analyzer, for a deployment whose analyses run elsewhere
# (`docker build --target site`). OCR runs on the CPU provider, which is
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
ARG VERSION=dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/ ./cmd/
COPY internal/ ./internal/
COPY --from=client /src/internal/webassets/dist/ ./internal/webassets/dist/
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/tracen ./cmd/tracen

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
COPY docker/constraints.txt docker/models.sha256 LICENSE.md THIRD-PARTY-NOTICES.md /opt/tracen/
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

# The site alone (`docker build --target site`), for a deployment whose
# analyses run in `tracen worker` processes elsewhere (-shared-queue): the
# service with the client, ffmpeg and ffprobe for the upload check and the
# viewer's frames, and nothing of Python. It is a tenth of the app image (46
# MB compressed against 417), so a container that was scaled to nothing
# pulls it in two seconds. Two helper stages feed it.

# What the analyzer of this build says it is. The site reads the answer from
# a file (-analyzer-version-file), so a report is still compared with the
# analyzer the workers run, without an interpreter in the image.
FROM worker AS identity
RUN python -X utf8 -m tracen_replay.analysis_job --worker-version > /analyzer-version.json \
	&& grep -q code_digest /analyzer-version.json

# ffmpeg and ffprobe as two static programs: everything that is part of
# ffmpeg itself, zlib, dav1d for AV1, and TLS, because with an object store
# the service hands them a signed https address of the recording, for the
# upload check and for the viewer's frames. No external encoder libraries.
# Debian's package would bring 400 MB of libraries with it. Built on Alpine:
# a static program made with musl still resolves host names, one made with
# glibc does not. The sources are checked against their hashes, and the
# build cache keeps this stage until one of these lines changes.
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 AS ffmpeg
ARG FFMPEG_VERSION=9.0.1
ARG FFMPEG_SHA256=cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635
ARG DAV1D_VERSION=1.5.1
ARG DAV1D_SHA256=401813f1f89fa8fd4295805aa5284d9aed9bc7fc1fdbe554af4292f64cbabe21
RUN apk add --no-cache build-base curl xz nasm pkgconf meson ca-certificates \
	zlib-dev zlib-static openssl-dev openssl-libs-static linux-headers
WORKDIR /build
RUN curl -fsSL -o dav1d.tar.xz "https://downloads.videolan.org/pub/videolan/dav1d/${DAV1D_VERSION}/dav1d-${DAV1D_VERSION}.tar.xz" \
	&& echo "${DAV1D_SHA256}  dav1d.tar.xz" | sha256sum -c - \
	&& curl -fsSL -o ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
	&& echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - \
	&& tar xJf dav1d.tar.xz && tar xJf ffmpeg.tar.xz
RUN cd "dav1d-${DAV1D_VERSION}" \
	&& meson setup build --prefix /build/prefix --libdir lib --buildtype release --default-library static \
		-Denable_tools=false -Denable_tests=false \
	&& meson install -C build
RUN cd "ffmpeg-${FFMPEG_VERSION}" \
	&& PKG_CONFIG_PATH=/build/prefix/lib/pkgconfig ./configure --prefix=/build/prefix --pkg-config-flags=--static \
		--enable-static --disable-shared --disable-debug --disable-doc --disable-ffplay \
		--disable-autodetect --enable-libdav1d --enable-zlib --enable-openssl --enable-version3 \
		--extra-version=tracen-site --extra-ldflags=-static \
	&& make -j"$(nproc)" && make install \
	&& strip /build/prefix/bin/ffmpeg /build/prefix/bin/ffprobe \
	&& /build/prefix/bin/ffmpeg -hide_banner -decoders | grep -q " png " \
	&& /build/prefix/bin/ffmpeg -hide_banner -protocols | grep -q "^ *https$" \
	&& { echo "ffmpeg ${FFMPEG_VERSION} with dav1d ${DAV1D_VERSION}, zlib and OpenSSL, built by this repository's Dockerfile."; \
		echo "GNU Lesser General Public License, version 3 or later; source https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"; \
		echo "dav1d: BSD 2-clause. OpenSSL: Apache License 2.0 (https://www.openssl.org). zlib: zlib license."; \
		echo; cat COPYING.LGPLv3; echo; cat "../dav1d-${DAV1D_VERSION}/COPYING"; } > /build/prefix/FFMPEG-LICENSE.txt

# The three programs are fully static, so the base only has to bring a shell
# for the entrypoint and the certificates for TLS to Azure.
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 AS site
ENV PORT=8765 \
	TRACEN_DATA=/data
RUN apk add --no-cache ca-certificates \
	&& mkdir -p /data /opt/tracen
COPY --from=ffmpeg /build/prefix/bin/ffmpeg /build/prefix/bin/ffprobe /usr/local/bin/
COPY --from=ffmpeg /build/prefix/FFMPEG-LICENSE.txt /opt/tracen/
COPY LICENSE.md THIRD-PARTY-NOTICES.md /opt/tracen/
COPY --from=identity /analyzer-version.json /opt/tracen/analyzer-version.json
COPY --from=service /out/tracen /usr/local/bin/tracen
COPY docker/entrypoint-site.sh /usr/local/bin/tracen-entrypoint
RUN chmod +x /usr/local/bin/tracen-entrypoint
VOLUME /data
EXPOSE 8765
ENTRYPOINT ["/usr/local/bin/tracen-entrypoint"]

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
