"""Writes THIRD-PARTY-NOTICES.md: every third-party component the project ships, with its license.

    python scripts/third_party_notices.py

Run from the repository root with Go on PATH and `npm ci` done in web/. The
Go modules compiled into the programs and the npm packages built into the
client get their full license texts, because those licenses ask for the
notice to travel with every copy and a compiled program carries none. The
Python packages are listed with their licenses: a bundle installs them with
pip, and each keeps its own license files in its .dist-info directory.
"""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSE_NAMES = re.compile(r"^(licen[sc]e|copying|notice|unlicense)([.-].*)?$", re.I)
OWN = "github.com/andy-dam/tracen-replay"

# Components that are not Go modules or npm packages. Each bundle carries the
# license texts of the ones it contains, at the place named here.
OTHER = [
    ("FFmpeg", "LGPL-2.1-or-later in the desktop bundles, LGPL-3.0-or-later in the website image (built with OpenSSL). "
     "Built from the unmodified release by desktop/ffmpeg/build.sh, desktop/ffmpeg/build-macos.sh and the Dockerfile, "
     "without GPL or nonfree parts. License text and source link: ffmpeg/LICENSE.txt in a bundle, /opt/tracen/FFMPEG-LICENSE.txt in the image.",
     "https://ffmpeg.org"),
    ("dav1d", "BSD-2-Clause. Linked into FFmpeg. Text in the same file as FFmpeg's.", "https://code.videolan.org/videolan/dav1d"),
    ("zlib", "Zlib. Linked into FFmpeg.", "https://zlib.net"),
    ("OpenSSL", "Apache-2.0. Linked into the website image's FFmpeg only.", "https://www.openssl.org"),
    ("CPython (python-build-standalone)", "PSF-2.0, with the licenses of the libraries it bundles. Texts in the bundle's python directory.",
     "https://github.com/astral-sh/python-build-standalone"),
    ("RapidOCR and the PP-OCR models it downloads", "Apache-2.0.", "https://github.com/RapidAI/RapidOCR"),
    ("ONNX Runtime", "MIT.", "https://github.com/microsoft/onnxruntime"),
    ("Nunito and M PLUS Rounded 1c fonts", "OFL-1.1. Texts below, under the @fontsource packages.", "https://fontsource.org"),
]

PYTHON = """PyYAML (MIT), antlr4-python3-runtime (BSD-3-Clause), certifi (MPL-2.0), charset-normalizer (MIT), colorlog (MIT),
flatbuffers (Apache-2.0), idna (BSD-3-Clause), numpy (BSD-3-Clause and others), omegaconf (BSD-3-Clause),
onnxruntime and onnxruntime-directml (MIT), opencv-python (Apache-2.0; its wheel bundles FFmpeg libraries under LGPL-2.1
and lists them in its own LICENSE-3RD-PARTY.txt), packaging (Apache-2.0 or BSD-2-Clause), pillow (MIT-CMU), protobuf (BSD-3-Clause),
psutil (BSD-3-Clause), pyclipper (MIT), rapidocr (Apache-2.0), requests (Apache-2.0), shapely (BSD-3-Clause; its wheel bundles
GEOS under LGPL-2.1), six (MIT), tqdm (MPL-2.0 and MIT), urllib3 (MIT)."""


def license_texts(directory: Path) -> list[tuple[str, str]]:
    found = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and LICENSE_NAMES.match(path.name):
            found.append((path.name, path.read_text(encoding="utf-8", errors="replace").strip()))
    return found


def go_modules():
    # The programs are built for three systems, and each pulls in modules of its own.
    modules = {}
    for system in ("windows", "darwin", "linux"):
        out = subprocess.run(["go", "list", "-deps", "-f", "{{with .Module}}{{.Path}}\t{{.Version}}\t{{.Dir}}{{end}}", "./cmd/..."],
                             cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, GOOS=system)).stdout
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0] and parts[2] and not parts[0].startswith(OWN):
                modules[parts[0]] = (parts[1], Path(parts[2]))
    return sorted(modules.items())


def npm_packages():
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text(encoding="utf-8"))
    rows = []
    for key, meta in lock["packages"].items():
        if not key or meta.get("dev") or meta.get("devOptional"):
            continue
        directory = ROOT / "web" / key
        if not directory.is_dir():
            continue
        name = key.split("node_modules/")[-1]
        rows.append((name, meta.get("version", ""), meta.get("license", ""), directory))
    return sorted(rows)


def main():
    lines = ["# Third-Party Notices", "",
             "Tracen Replay is licensed under PolyForm Noncommercial 1.0.0 (LICENSE.md). It ships the third-party components below, "
             "each under its own license. This file is written by scripts/third_party_notices.py.", "",
             "## Programs, Models and Fonts", ""]
    for name, terms, url in OTHER:
        lines += [f"- **{name}.** {terms} {url}"]
    lines += ["", "## Python Packages in the Analyzer", "", PYTHON, "",
              "Each package's own license files are in its .dist-info directory inside a bundle or an image.", "",
              "## Go Modules Compiled into the Programs", ""]
    missing = []
    for path, (version, directory) in go_modules():
        texts = license_texts(directory)
        if not texts:
            # A module inside a larger repository keeps its license at the repository's root.
            parent = directory.parent
            while not texts and parent != parent.parent and "pkg" in parent.parts:
                texts = license_texts(parent) if parent.is_dir() else []
                parent = parent.parent
        if not texts:
            missing.append(path)
        lines += [f"### {path} {version}", ""]
        for name, text in texts:
            lines += [f"{name}:", "", "```", text, "```", ""]
    lines += ["## npm Packages Built into the Client", ""]
    for name, version, spdx, directory in npm_packages():
        texts = license_texts(directory)
        if not texts:
            missing.append(name)
        lines += [f"### {name} {version} ({spdx})", ""]
        for file_name, text in texts:
            lines += [f"{file_name}:", "", "```", text, "```", ""]
    (ROOT / "THIRD-PARTY-NOTICES.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    print(f"wrote THIRD-PARTY-NOTICES.md ({len(lines)} lines)")
    if missing:
        print("no license file found for:", ", ".join(missing))


if __name__ == "__main__":
    main()
