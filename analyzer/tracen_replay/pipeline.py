"""Probing a recording and decoding bounded intervals of it with their presentation timestamps.

This module reads media. It does not classify screens or infer actions.
"""

import json
import math
import re
import subprocess
from pathlib import Path


TIMEOUT_SECONDS = 180
# Every reading is taken in the coordinates of a 1920x1080 frame, the game at
# 1080p. A 16:9 recording of another size, 720p to 4K, has its frames scaled
# to it as they are decoded.
FRAME_SIZE = (1920, 1080)
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
    if duration <= 0:
        raise PipelineError("Source must have a positive duration.")
    if not 720 <= height <= 2160 or abs(width / height - 16 / 9) > 0.02:
        raise PipelineError(f"A recording must be 16:9, from 1280×720 to 3840×2160; this one is {width}×{height}.")
    return info, video, duration, origin


def frame_scale(width, height):
    """The decoder filter that brings a recording's frames to ``FRAME_SIZE``; empty when they already are."""
    if (width, height) == FRAME_SIZE:
        return ""
    return f"scale={FRAME_SIZE[0]}:{FRAME_SIZE[1]}:flags=lanczos"


def frame_rate(video):
    """The video stream's frame rate in frames per second, or None when ffprobe gives none."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        numerator, _, denominator = str(video.get(key) or "").partition("/")
        try:
            rate = float(numerator) / float(denominator or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(rate) and rate > 0:
            return rate
    return None


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


def decode_frames(source, directory, start, duration, fps, origin, *, scale, attempts=2):
    """Decode one bounded interval into ``directory`` and return its frame rows.

    ``scale`` is the recording's ``frame_scale``, so that every image written
    is a ``FRAME_SIZE`` frame.

    The decoder's showinfo log is the only source of presentation timestamps;
    once in a long run its line sequence has come out inconsistent with the
    images written, on a part that decodes cleanly again. Such a part is
    decoded once more from scratch before the run is given up.
    """
    for attempt in range(1, attempts + 1):
        try:
            return _decode_frames_once(source, directory, start, duration, fps, origin, scale)
        except PipelineError as exc:
            if attempt == attempts or not str(exc).startswith("Decoder timestamp sequence is inconsistent"):
                raise
            for path in directory.glob("*.jpg"):
                path.unlink()


def _decode_frames_once(source, directory, start, duration, fps, origin, scale):
    # Keep source PTS through showinfo, then reset output time for -t to bound work.
    # select preserves original frames; the fps filter would invent a new time grid.
    # showinfo timestamps originate from rational PTS. Decimal conversion can
    # put an exact 1/60-second step just below the rounded comparison threshold,
    # unintentionally selecting only every other frame at native frame rate.
    # Scaling comes after the selection, so only the frames kept are scaled.
    interval = max(0,1 / fps - 1e-7)
    filters = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval:.12f})',showinfo,setpts=PTS-STARTPTS"
    if scale:
        filters += f",{scale}"
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

