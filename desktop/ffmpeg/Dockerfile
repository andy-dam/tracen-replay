# The toolchain for desktop/ffmpeg/build.sh, for building the slim ffmpeg on
# any machine with Docker:
#   docker build -t tracen-ffmpeg desktop/ffmpeg
#   docker run --rm -v "$PWD/dist/ffmpeg:/out" tracen-ffmpeg
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils bzip2 zip \
	make nasm pkg-config meson ninja-build gcc libc6-dev gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 libz-mingw-w64-dev \
	&& rm -rf /var/lib/apt/lists/*
COPY build.sh /build.sh
CMD ["sh", "/build.sh", "/out"]
