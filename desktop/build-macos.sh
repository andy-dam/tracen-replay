#!/bin/sh
# Builds the macOS desktop application for Apple silicon: "Tracen
# Replay.app" with everything inside it (the native window with the client,
# a standalone Python with the analyzer and its pinned wheels, the OCR
# models, the learned card reader and the project's small ffmpeg), and a
# disk image to hand out. The counterpart of build-windows.ps1.
#
#   sh desktop/build-macos.sh [version] [output-directory]
#
# Needs, on an Apple silicon Mac: Go, Node, the wails CLI
# (go install github.com/wailsapp/wails/v2/cmd/wails@v2.16.0) and the Xcode
# command line tools. Run from the repository root.
set -eu
version="${1:-dev}"
out="${2:-dist/macos}"
PYTHON_TAG="${PYTHON_TAG:-20260901}"
PYTHON_VERSION="${PYTHON_VERSION:-3.13.15}"
PYTHON_SHA256="${PYTHON_SHA256:-d3904bd6a072246e07aa0bdadee9a14e80521e42a943c0848059feb16a2816dc}"
FFMPEG_ZIP="${FFMPEG_ZIP:-https://github.com/andy-dam/tracen-replay/releases/download/ffmpeg-slim-9.0.1/ffmpeg-slim-macos-arm64.zip}"

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
	echo "this builds on an Apple silicon Mac; this machine is $(uname -s) $(uname -m)" >&2
	exit 1
fi
mkdir -p "$out"
out="$(cd "$out" && pwd)"
cache="$out/cache"
mkdir -p "$cache"
app="$out/Tracen Replay.app"
rm -rf "$app"

echo "== client and the desktop application ($version)"
(cd web && npm ci && npm run build)
# The build folder is not tracked; the application's icon is.
mkdir -p cmd/tracen-desktop/build
cp desktop/icon/appicon.png cmd/tracen-desktop/build/appicon.png
(cd cmd/tracen-desktop && wails build -clean -s -trimpath -platform darwin/arm64 -ldflags "-X main.version=$version")
cp -R "cmd/tracen-desktop/build/bin/Tracen Replay.app" "$app"
res="$app/Contents/Resources"

echo "== standalone Python $PYTHON_VERSION"
py_tar="$cache/cpython-$PYTHON_VERSION+$PYTHON_TAG-aarch64-apple-darwin-install_only_stripped.tar.gz"
if [ ! -f "$py_tar" ]; then
	curl -fsSL -o "$py_tar" "https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_TAG/$(basename "$py_tar")"
fi
echo "$PYTHON_SHA256  $py_tar" | shasum -a 256 -c - >/dev/null
tar -xzf "$py_tar" -C "$res"
python="$res/python/bin/python3"

echo "== analyzer and its wheels"
cp -R analyzer "$res/analyzer"
rm -rf "$res/analyzer/lab" "$res/analyzer/tests"
"$python" -m pip install --no-warn-script-location --disable-pip-version-check -c docker/constraints.txt "$res/analyzer[vision]"
"$python" -c "import onnxruntime; print('onnxruntime providers:', onnxruntime.get_available_providers())"

echo "== OCR models"
models="$res/models"
mkdir -p "$models"
"$python" -c "from tracen_replay.vision import NeuralReader; print(sorted(NeuralReader(r'$models').models))"
(cd "$models" && shasum -a 256 -c "$root/docker/models.sha256")

echo "== ffmpeg"
ff_zip="$cache/$(echo "$FFMPEG_ZIP" | sed -e 's#^https\{0,1\}://##' -e 's#[^A-Za-z0-9.-]\{1,\}#_#g')"
if [ ! -f "$ff_zip" ]; then
	curl -fsSL -o "$ff_zip" "$FFMPEG_ZIP"
fi
rm -rf "$cache/ffmpeg"
mkdir -p "$cache/ffmpeg" "$res/ffmpeg"
unzip -q "$ff_zip" -d "$cache/ffmpeg"
ff_dir="$(dirname "$(find "$cache/ffmpeg" -name ffmpeg -type f | head -1)")"
cp "$ff_dir/ffmpeg" "$ff_dir/ffprobe" "$ff_dir/LICENSE.txt" "$res/ffmpeg/"
chmod +x "$res/ffmpeg/ffmpeg" "$res/ffmpeg/ffprobe"
"$res/ffmpeg/ffmpeg" -hide_banner -version | head -1

echo "== notices"
cp LICENSE.md THIRD-PARTY-NOTICES.md README.md "$res/"
echo "$version" > "$res/VERSION"
find "$res" -name "__pycache__" -type d -prune -exec rm -rf {} +

echo "== signature"
# Not an Apple developer signature: an ad hoc one, which Apple silicon
# requires of every program before it will run at all. Programs inside
# Resources are signed one by one first; the application last.
find "$res/ffmpeg" "$res/python" -type f \( -perm -u+x -o -name "*.dylib" -o -name "*.so" \) -print0 \
	| xargs -0 -n 50 codesign --force --sign - >/dev/null 2>&1 || true
codesign --force --deep --sign - "$app"
codesign --verify --deep "$app"

echo "== disk image"
stage="$cache/stage"
rm -rf "$stage"
mkdir -p "$stage"
cp -R "$app" "$stage/"
ln -s /Applications "$stage/Applications"
cp desktop/README-bundle-macos.md "$stage/Read me first.md"
dmg="$out/TracenReplay-macos-arm64-$version.dmg"
rm -f "$dmg"
hdiutil create -volname "Tracen Replay" -srcfolder "$stage" -ov -format UDZO "$dmg" >/dev/null
rm -rf "$stage"
echo "built $dmg ($(du -h "$dmg" | cut -f1)); the application alone is $(du -sh "$app" | cut -f1)"
