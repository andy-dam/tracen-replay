#!/bin/sh
# Builds the ffmpeg the desktop application ships: ffmpeg.exe, ffprobe.exe
# and their shared libraries for 64-bit Windows, cross-compiled on Linux.
#
# The general-purpose builds weigh 330 MB because they carry every external
# encoder library (x265, AV1 encoders, ...). The application only decodes a
# recording, takes frames out of it as images and probes files, so this build
# keeps everything that is part of ffmpeg itself (every built-in decoder,
# container, filter and image encoder) and leaves the external libraries out.
# The one external library kept is dav1d: ffmpeg has no software AV1 decoder
# of its own. No GPL or nonfree part is enabled, so the result is LGPL.
#
#   sh desktop/ffmpeg/build.sh [output-directory]
#
# Needs: gcc libc6-dev gcc-mingw-w64-x86-64 nasm make pkg-config meson ninja-build curl
# xz-utils bzip2 zip (the workflow and the Dockerfile next to this install them).
set -eu
FFMPEG_VERSION="${FFMPEG_VERSION:-9.0.1}"
DAV1D_VERSION="${DAV1D_VERSION:-1.5.1}"
out="$(mkdir -p "${1:-out}" && cd "${1:-out}" && pwd)"
work="$(mktemp -d)"
prefix="$work/prefix"
host=x86_64-w64-mingw32
jobs="$(nproc)"

cd "$work"
curl -fsSL "https://code.videolan.org/videolan/dav1d/-/archive/$DAV1D_VERSION/dav1d-$DAV1D_VERSION.tar.bz2" | tar xj
curl -fsSL "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" | tar xJ

cat > cross.txt <<CROSS
[binaries]
c = '$host-gcc'
cpp = '$host-g++'
ar = '$host-ar'
strip = '$host-strip'
windres = '$host-windres'
[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
CROSS
(cd "dav1d-$DAV1D_VERSION" && meson setup build --cross-file ../cross.txt --prefix "$prefix" --libdir lib \
	--buildtype release --default-library static -Denable_tools=false -Denable_tests=false >/dev/null \
	&& ninja -C build install >/dev/null)

cd "ffmpeg-$FFMPEG_VERSION"
PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig" ./configure \
	--prefix="$prefix" --arch=x86_64 --target-os=mingw32 --cross-prefix="$host-" \
	--pkg-config=pkg-config --pkg-config-flags=--static \
	--enable-shared --disable-static --disable-debug --disable-doc \
	--disable-ffplay --disable-network --disable-autodetect \
	--disable-pthreads --enable-w32threads \
	--enable-libdav1d \
	--extra-ldflags="-static-libgcc" \
	--extra-version="tracen-slim"
make -j"$jobs" >/dev/null
make install >/dev/null

pack="$work/ffmpeg-slim-win64"
mkdir -p "$pack"
cp "$prefix"/bin/ffmpeg.exe "$prefix"/bin/ffprobe.exe "$prefix"/bin/*.dll "$pack"/
"$host-strip" "$pack"/*.exe "$pack"/*.dll
{
	echo "ffmpeg $FFMPEG_VERSION with dav1d $DAV1D_VERSION, built by desktop/ffmpeg/build.sh of Tracen Replay."
	echo "Licensed under the GNU Lesser General Public License, version 2.1 or later; the text follows."
	echo "Source: https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
	echo "dav1d (BSD 2-clause): https://code.videolan.org/videolan/dav1d"
	echo
	cat COPYING.LGPLv2.1
	echo
	cat "../dav1d-$DAV1D_VERSION/COPYING"
} > "$pack/LICENSE.txt"
(cd "$work" && zip -q -r "$out/ffmpeg-slim-win64.zip" ffmpeg-slim-win64)
ls -la "$out/ffmpeg-slim-win64.zip"
