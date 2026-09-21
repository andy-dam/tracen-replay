"""Bounded local extraction with decoded presentation timestamps.

This module collects observations. It does not classify screens or infer actions.
"""

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .viewer import render

MAX_SECONDS = 120
MAX_FPS = 8
TIMEOUT_SECONDS = 180
PTS_PATTERN = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:([\d.eE+\-]+)")
TIME_BASE_PATTERN = re.compile(r"config in time_base:\s*(\d+)/(\d+)")


class PipelineError(Exception):
    """A readable failure without publishing a partial report."""


def run(command):
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS, check=True)
    except FileNotFoundError as exc:
        raise PipelineError(f"{command[0]} is missing. Install FFmpeg and put ffmpeg and ffprobe on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"{command[0]} exceeded the {TIMEOUT_SECONDS}-second processing limit.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "No decoder details.")[-2000:].strip()
        raise PipelineError(f"{command[0]} failed: {detail}") from exc


def finite_number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"{name} must be a finite number.") from exc
    if not math.isfinite(number):
        raise PipelineError(f"{name} must be a finite number.")
    return number


def probe(source):
    try:
        info = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)]).stdout)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        duration = finite_number(info["format"]["duration"], "Media duration")
        origin = finite_number(info["format"].get("start_time", 0), "Media start")
        width, height = int(video["width"]), int(video["height"])
    except (ValueError, KeyError, StopIteration, TypeError) as exc:
        raise PipelineError("Source must contain a video stream with known dimensions and duration.") from exc
    if duration <= 0 or width <= 0 or height <= 0 or width * height > 1920 * 1080:
        raise PipelineError("This local pilot accepts video up to 1920×1080 pixels with positive duration.")
    return info, video, duration, origin


def clear_partial_capture(directory):
    """Delete the images of a decode that was stopped before its manifest was written.

    A capture exists once its caller has written the manifest, after
    ``decode_frames`` returned. Images without one were left by a decode that
    was interrupted: the analysis was paused, or its process ended. They name
    no frame of any report, so the decode starts over on an empty directory
    and an analysis that is started again on its own output continues.
    """
    for path in Path(directory).iterdir():
        if path.is_file():
            path.unlink()


def decode_frames(source, directory, start, duration, fps, origin, attempts=2):
    """Decode one bounded interval into ``directory`` and return its frame rows.

    The decoder's showinfo log is the only source of presentation timestamps;
    once in a long run its line sequence has come out inconsistent with the
    images written, on a part that decodes cleanly again. Such a part is
    decoded once more from scratch before the run is given up.
    """
    for attempt in range(1, attempts + 1):
        try:
            return _decode_frames_once(source, directory, start, duration, fps, origin)
        except PipelineError as exc:
            if attempt == attempts or not str(exc).startswith("Decoder timestamp sequence is inconsistent"):
                raise
            for path in directory.glob("*.jpg"):
                path.unlink()


def _decode_frames_once(source, directory, start, duration, fps, origin):
    # Keep source PTS through showinfo, then reset output time for -t to bound work.
    # select preserves original frames; the fps filter would invent a new time grid.
    # showinfo timestamps originate from rational PTS. Decimal conversion can
    # put an exact 1/60-second step just below the rounded comparison threshold,
    # unintentionally selecting only every other frame at native frame rate.
    interval = max(0,1 / fps - 1e-7)
    filters = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval:.12f})',showinfo,setpts=PTS-STARTPTS"
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info", "-copyts",
               "-ss", str(start), "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
               "-t", str(duration), "-vf", filters, "-fps_mode", "vfr", "-q:v", "2",
               "-threads", "2", str(directory / "%06d.jpg")]
    result = run(command)
    base_match = TIME_BASE_PATTERN.search(result.stderr)
    samples = PTS_PATTERN.findall(result.stderr)
    files = sorted(directory.glob("*.jpg"))
    if not files or not base_match or len(samples) < len(files):
        raise PipelineError("Decoder did not produce images with matching presentation timestamps.")
    numerator, denominator = map(int, base_match.groups())
    if numerator <= 0 or denominator <= 0:
        raise PipelineError("Invalid decoder time base.")
    frames = []
    for index, path in enumerate(files):
        sample_index, pts, _ = samples[index]
        if int(sample_index) != index:
            raise PipelineError(f"Decoder timestamp sequence is inconsistent (showinfo n={sample_index} for image {index}, "
                                f"{len(samples)} samples for {len(files)} images at {start:.3f}s).")
        # Use integer PTS/time_base rather than rounded showinfo pts_time text.
        timestamp = int(pts) * numerator / denominator - origin
        # Decoder/filter lookahead may log or emit a frame beyond the requested interval.
        if timestamp < start - 0.000001 or timestamp >= start + duration - 0.000001:
            path.unlink()
            continue
        source_ms = round(timestamp * 1000)
        frames.append({"id": f"frame-{index+1:06d}", "source_timestamp_ms": source_ms,
                       "clip_timestamp_ms": round((timestamp-start)*1000), "source_pts": int(pts),
                       "time_base": f"{numerator}/{denominator}", "evidence": f"frames/{path.name}",
                       "screen_label": None, "confidence": None, "origin": "automatic_frame_sampling"})
    if not frames or len(frames) > math.ceil(duration * fps) + 1:
        raise PipelineError("Extracted frame count is outside the requested bounds.")
    if any(a["source_timestamp_ms"] >= b["source_timestamp_ms"] for a, b in zip(frames, frames[1:])):
        raise PipelineError("Frame presentation timestamps must be strictly increasing.")
    return frames


def attach_annotations(path, digest, frames, start, duration, fps):
    if path is None:
        return [], None
    if path.stat().st_size > 2 * 1024 * 1024:
        raise PipelineError("Annotation files must be no larger than 2 MiB.")
    payload = path.read_bytes()
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PipelineError("Annotation file is not valid JSON.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("source_sha256"), str) or data["source_sha256"].lower() != digest:
        raise PipelineError("Annotation source_sha256 does not match this recording.")
    rows = data.get("observations")
    if not isinstance(rows, list):
        raise PipelineError("Annotation observations must be a list.")
    observations = []
    for row in rows:
        if not isinstance(row, dict):
            raise PipelineError("Every annotation must be an object.")
        seconds = finite_number(row.get("timestamp_seconds"), "Annotation timestamp_seconds")
        if not start <= seconds < start + duration:
            continue
        for name in ("screen_label", "title", "note", "annotation_method"):
            if name in row and not isinstance(row[name], str):
                raise PipelineError(f"Annotation {name} must be text.")
        timestamp_ms = round(seconds * 1000)
        frame = min(frames, key=lambda f: abs(f["source_timestamp_ms"]-timestamp_ms))
        delta = frame["source_timestamp_ms"] - timestamp_ms
        # Only exact millisecond matches support carrying a manually checked label.
        # Nearby frames can be different screens at this run's pace.
        exact = delta == 0
        observations.append({"id": f"observation-{len(observations)+1:04d}",
                             "source_timestamp_ms": timestamp_ms, "clip_timestamp_ms": round((seconds-start)*1000),
                             "screen_label": row.get("screen_label"), "title": row.get("title", "Manual observation"),
                             "note": row.get("note", ""), "origin": "imported_annotation",
                             "annotation_method": row.get("annotation_method", "unspecified"),
                             "confidence": None, "event_start_ms": None, "event_end_ms": None,
                             "evidence": frame["evidence"] if exact else None,
                             "evidence_timestamp_ms": frame["source_timestamp_ms"] if exact else None,
                             "evidence_status": "matched_sample" if exact else "needs_exact_frame",
                             "nearest_sample_offset_ms": delta})
    observations.sort(key=lambda o: o["source_timestamp_ms"])
    return observations, hashlib.sha256(payload).hexdigest()


def analyze(source, output, start=0, duration=57, fps=4, annotations=None, track_stats=False, tesseract=None, gameplay_only=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    start, duration, fps = (finite_number(v, n) for v, n in ((start, "start"), (duration, "duration"), (fps, "fps")))
    if gameplay_only and track_stats:
        raise PipelineError('Choose --gameplay-only or legacy --track-stats, not both.')
    if start < 0 or not 0 < duration <= MAX_SECONDS or not 1 <= fps <= MAX_FPS:
        raise PipelineError("Use start >= 0, duration > 0 and <= 120, and fps between 1 and 8.")
    if not source.is_file():
        raise PipelineError("Source must be an existing local video file.")
    if output.exists():
        raise PipelineError("Output already exists. Choose a new directory to preserve previous results.")
    info, video, source_duration, origin = probe(source)
    if (track_stats or gameplay_only) and (video['width'], video['height']) != (1920, 1080):
        raise PipelineError('Stat tracking currently requires the English landscape 1920x1080 layout.')
    if start + duration > source_duration + 0.000001:
        raise PipelineError(f"Requested interval exceeds source duration ({source_duration:.3f}s).")
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Inherit workspace ACLs. Python 3.13+ mkdtemp's private Windows ACL can
    # exclude a restricted execution token even when its parent is writable.
    temporary = output.parent / f".tracen-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        (temporary / "frames").mkdir()
        frames = decode_frames(source, temporary / "frames", start, duration, fps, origin)
        observations, annotation_hash = attach_annotations(Path(annotations) if annotations else None,
                                                           digest, frames, start, duration, fps)
        report = {"schema_version": "tracen-replay/local-v0.1", "pipeline_version": __version__,
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "source": {"name": source.name, "sha256": digest, "size_bytes": source.stat().st_size,
                             "duration_ms": round(source_duration*1000), "timeline_origin_seconds": origin,
                             "width": video["width"], "height": video["height"], "codec": video.get("codec_name")},
                  "clip": {"source_start_ms": round(start*1000), "duration_ms": round(duration*1000)},
                  "sampling": {"method": "minimum_interval_on_decoded_pts", "requested_fps": fps,
                               "frame_count": len(frames), "guarantees_all_events": False},
                  "recognition": {"enabled": False, "model": None},
                  "annotation_sha256": annotation_hash, "frames": frames, "observations": observations,
                  "limitations": ["Screens and fields are not recognized automatically.",
                                  "Fixed-rate sampling can miss brief choices and results.",
                                  "Annotations are point observations, not event boundaries or completed-action claims.",
                                  "Imported labels remain separate from automatically captured frames."]}
        if gameplay_only:
            try:
                from .gameplay import track as track_gameplay
                report['gameplay_tracking'] = track_gameplay(report, temporary, tesseract, source, origin)
            except (ImportError, ValueError, subprocess.SubprocessError) as exc:
                raise PipelineError(f'Gameplay OCR failed: {exc}') from exc
            report['recognition'] = {'enabled': True, 'model': 'tesseract_gameplay_only_v1'}
            report['limitations'][0] = 'Experimental gameplay-only observations; unknown screens and unexplained changes remain explicit.'
        if track_stats:
            try:
                from .stats import track
                report['stat_tracking'] = track(report, temporary, tesseract)
            except ImportError as exc:
                raise PipelineError('Stat tracking requires Pillow. Install the analysis extra: python -m pip install -e ".[analysis]"') from exc
            except (ValueError, subprocess.SubprocessError) as exc:
                raise PipelineError(f'Stat OCR failed: {exc}') from exc
            report['limitations'][0] = 'Experimental stat OCR is enabled; general screen recognition is not implemented.'
        (temporary / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (temporary / "index.html").write_text(render(report), encoding="utf-8")
        # The destination must remain new; rename publishes a complete bundle together.
        if output.exists():
            raise PipelineError("Output appeared during processing; refusing to overwrite it.")
        for attempt in range(5):
            try:
                temporary.rename(output)
                break
            except PermissionError:
                # Windows scanners can briefly hold newly decoded files open.
                # Retry the same rename; never copy over an existing bundle.
                if output.exists() or attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
    finally:
        if temporary.exists():
            try:
                shutil.rmtree(temporary)
            except OSError as exc:
                warnings.warn(f"Could not remove incomplete output {temporary}: {exc}", stacklevel=2)
    return {"report": str(output / "report.json"), "gallery": str(output / "index.html"),
            "frames": len(frames), "annotations": len(observations)}
