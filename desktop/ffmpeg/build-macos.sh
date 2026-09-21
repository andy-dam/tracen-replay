#!/bin/sh
# The macOS counterpart of build.sh: the same small ffmpeg (everything that
# is part of ffmpeg itself, no external encoder libraries, dav1d for AV1;
# LGPL), built natively on a Mac for the machine's own architecture. The two
# programs are linked statically so the application can run them from inside
# its bundle without any library paths to fix.
#
#   sh desktop/ffmpeg/build-macos.sh [output-directory]
#
# Needs the Xcode command line tools, meson, ninja and pkg-config
# (brew install meson ninja pkg-config); on Intel, nasm as well.
set -eu
FFMPEG_VERSION="${FFMPEG_VERSION:-9.0.1}"
DAV1D_VERSION="${DAV1D_VERSION:-1.5.1}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
out="$(mkdir -p "${1:-out}" && cd "${1:-out}" && pwd)"
work="$(mktemp -d)"
prefix="$work/prefix"
arch="$(uname -m)"
jobs="$(sysctl -n hw.ncpu)"

cd "$work"
curl -fsSL "https://code.videolan.org/videolan/dav1d/-/archive/$DAV1D_VERSION/dav1d-$DAV1D_VERSION.tar.bz2" | tar xj
curl -fsSL "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" | tar xJ

(cd "dav1d-$DAV1D_VERSION" && meson setup build --prefix "$prefix" --libdir lib \
	--buildtype release --default-library static -Denable_tools=false -Denable_tests=false >/dev/null \
	&& ninja -C build install >/dev/null)

cd "ffmpeg-$FFMPEG_VERSION"
# Only this build's own prefix is searched, so nothing from Homebrew is
# linked in by accident.
PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig" ./configure \
	--prefix="$prefix" --pkg-config-flags=--static \
	--enable-static --disable-shared --disable-debug --disable-doc \
	--disable-ffplay --disable-network --disable-autodetect \
	--enable-libdav1d --enable-zlib \
	--extra-version="tracen-slim"
make -j"$jobs" >/dev/null
make install >/dev/null

name="ffmpeg-slim-macos-$arch"
pack="$work/$name"
mkdir -p "$pack"
cp "$prefix/bin/ffmpeg" "$prefix/bin/ffprobe" "$pack"/
strip "$pack/ffmpeg" "$pack/ffprobe"
# Stripping breaks the ad hoc signature the linker made, and Apple silicon
# refuses to run a program without one.
codesign --force --sign - "$pack/ffmpeg" "$pack/ffprobe"
# Anything but the system's own libraries here would not exist on another Mac.
if otool -L "$pack/ffmpeg" "$pack/ffprobe" | grep -v ':$' | grep -v -E '^\s*(/usr/lib/|/System/)'; then
	echo "the programs link against libraries outside the system" >&2
	exit 1
fi
"$pack/ffmpeg" -hide_banner -version | head -1
# zlib comes with macOS; without it configure succeeds and PNG silently goes.
"$pack/ffmpeg" -hide_banner -decoders | grep -q " png " || { echo "the build has no PNG decoder (zlib not found)" >&2; exit 1; }
{
	echo "ffmpeg $FFMPEG_VERSION with dav1d $DAV1D_VERSION, built by desktop/ffmpeg/build-macos.sh of Tracen Replay."
	echo "Licensed under the GNU Lesser General Public License, version 2.1 or later; the text follows."
	echo "Source: https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
	echo "dav1d (BSD 2-clause): https://code.videolan.org/videolan/dav1d"
	echo
	cat COPYING.LGPLv2.1
	echo
	cat "../dav1d-$DAV1D_VERSION/COPYING"
} > "$pack/LICENSE.txt"
(cd "$work" && zip -q -r "$out/$name.zip" "$name")
ls -la "$out/$name.zip"
