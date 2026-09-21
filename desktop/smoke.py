"""A short analysis inside a built desktop bundle, run by the bundle's own Python.

    <bundle python> desktop/smoke.py <analyzer dir> <ffmpeg dir> <models dir> [device ...]

It draws a picture with known words, makes a few seconds of video from it
with the bundled ffmpeg, runs the analyzer on that video once per device
(cpu by default) and checks that the run finishes, writes a report and read
the words. Game footage cannot live in a public repository; this proves the
interpreter, the wheels, the models and ffmpeg work together on the machine
the bundle was built for.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

WORDS = ["Speed Lvl 3", "Exercise Bike", "Gained 2 hint levels"]


def main():
    analyzer, ffmpeg_dir, models = (Path(p).resolve() for p in sys.argv[1:4])
    devices = sys.argv[4:] or ["cpu"]
    suffix = ".exe" if os.name == "nt" else ""
    env = dict(os.environ, PATH=str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", ""))
    work = Path(tempfile.mkdtemp(prefix="tracen-smoke-"))
    picture = Image.new("RGB", (1920, 1080), (34, 40, 52))
    draw = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=72)
    for index, words in enumerate(WORDS):
        draw.text((260, 220 + 220 * index), words, fill=(255, 255, 255), font=font)
    picture.save(work / "picture.png")
    clip = work / "clip.mp4"
    subprocess.run([str(ffmpeg_dir / ("ffmpeg" + suffix)), "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
                    "-i", str(work / "picture.png"), "-t", "6", "-r", "30", "-c:v", "mpeg4", "-q:v", "3",
                    "-pix_fmt", "yuv420p", str(clip)], check=True, env=env)
    failed = []
    for device in devices:
        output = work / f"run-{device}"
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "tracen_replay.analysis_job", str(clip),
                                 "--output", str(output), "--workers", "2", "--dense-workers", "1",
                                 "--model-dir", str(models)], cwd=analyzer, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=dict(env, TRACEN_REPLAY_OCR_DEVICE=device))
        seconds = time.monotonic() - started
        problem = None
        report = output / "report.json"
        if result.returncode != 0:
            problem = f"exit {result.returncode}: {result.stderr.strip()[-600:]}"
        elif not report.is_file():
            problem = "no report.json"
        else:
            readings = json.loads(report.read_text(encoding="utf-8")).get("gameplay_tracking", {}).get("readings", [])
            read = {line.get("text", "") for row in readings for line in (row.get("ocr") or {}).get("neural", [])}
            missing = [words for words in WORDS if not any(words.lower() in text.lower() for text in read)]
            if not readings:
                problem = "the report has no readings"
            elif missing:
                problem = f"not read: {missing}; read instead: {sorted(read)[:8]}"
        print(f"{device}: {'FAILED, ' + problem if problem else 'ok'} ({seconds:.0f} s)", flush=True)
        if problem:
            failed.append(device)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
