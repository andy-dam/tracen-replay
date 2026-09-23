"""Probing a recording and decoding bounded intervals of it with their presentation timestamps.

This module reads media. It does not classify screens or infer actions.
"""

import bisect
import json
import math
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from .layout import REFERENCE_PANE, Layout, working_size


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
        width, height = display_size(video)
    except (ValueError, KeyError, StopIteration, TypeError) as exc:
        raise PipelineError("Source must contain a video stream with known dimensions and duration.") from exc
    if duration <= 0:
        raise PipelineError("Source must have a positive duration.")
    frame_layout(width, height)
    return info, video, duration, origin


def display_size(video):
    """The width and height a video stream is shown at: its stored size, turned by any rotation flag.

    Phones and tablets often store a portrait recording sideways with a
    rotation flag; the decoder applies it, so the frames come out portrait.
    """
    width, height = int(video["width"]), int(video["height"])
    rotation = 0.0
    for item in video.get("side_data_list") or ():
        if isinstance(item, dict) and "rotation" in item:
            rotation = float(item["rotation"])
    if not rotation:
        rotation = float((video.get("tags") or {}).get("rotate") or 0)
    if round(rotation) % 180:
        width, height = height, width
    return width, height


def frame_layout(width, height):
    """The working frame and game area of a recording shown at ``width`` x ``height``.

    A portrait recording is the game filling the screen of a phone or tablet:
    the game area is the whole frame, scaled evenly so that a design unit has
    the PC pane's size. A 16:9 landscape recording is the PC client, whose
    game area is its pane; its frames are scaled to 1920x1080. Any other shape
    is refused, as is a game drawn smaller than it is at 720p on the PC.
    The margins the game keeps clear are fitted later, from the frames.
    """
    if height > width and 0.4 <= width / height <= 0.8:
        game = (width, height)
    elif height < width and abs(width / height - 16 / 9) <= 0.02:
        game = (width * REFERENCE_PANE[2] / 1920 - width * REFERENCE_PANE[0] / 1920, height)
    else:
        raise PipelineError(f"A recording must be the game filling a portrait screen, or the PC client "
                            f"in 16:9; this one is {width}×{height}.")
    unit = min(game[0] / 1080, game[1] / 1920)
    if unit < 0.375:
        raise PipelineError(f"The game in this {width}×{height} recording is drawn smaller than at 720p, too small to read.")
    if unit > 2.5:
        raise PipelineError(f"The game in this {width}×{height} recording is drawn larger than this analyzer reads.")
    if height > width:
        _, frame = working_size(width, height)
        return Layout(frame, (0, 0) + frame)
    return Layout((1920, 1080), REFERENCE_PANE)


def frame_scale(layout, width, height):
    """The decoder filter that scales a ``width`` x ``height`` recording's frames to the layout's frame; empty when they are that size."""
    if tuple(layout.frame) == (width, height):
        return ""
    return f"scale={layout.frame[0]}:{layout.frame[1]}:flags=lanczos"


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


def frame_times(source, video, origin):
    """The time of every frame of the video stream on the source timeline, in seconds, in order.

    Read from the container's packets, without decoding, and timed as the
    sampled frames are.
    """
    output = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts",
                  "-of", "csv=p=0", str(source)]).stdout
    base = Fraction(video["time_base"])
    return sorted(float(int(pts) * base) - origin for pts in output.split() if pts.lstrip("-").isdigit())


def source_gaps(frames, times, fps, duration_ms, frame_rate, origin=0):
    """The stretches the source recorded no frame in that account for a long wait between sampled frames.

    The sampler keeps the first frame at least one step after the last it
    kept, so it waits longer than a step and a source frame only where the
    source has no frame to keep. A phone's or tablet's screen recorder skips
    frames while the screen is still, and its recordings have many such
    stretches; a PC recording has none. Each is ``[last frame before, first
    frame after]`` in milliseconds, the recording's own start and end
    standing in at its ends. A wait the source's frames do not account for
    is left out, so the coverage audit still reports it. ``times`` are
    ``frame_times``; a sampled frame is timed exactly from its own PTS.
    """
    step = 1 / fps - 1e-7  # as decode_frames selects
    longest = 1000 / fps + (1000 / frame_rate if frame_rate else 0) + 1
    kept = [(float(frame["source_pts"] * Fraction(frame["time_base"])) - origin, frame["source_timestamp_ms"])
            for frame in frames]
    gaps = []
    if kept and times and kept[0][1] > longest and times[0] >= kept[0][0] - 1e-6:
        gaps.append([0, kept[0][1]])
    for (before, before_ms), (after, after_ms) in zip(kept, kept[1:]):
        if after_ms - before_ms <= longest:
            continue
        index = bisect.bisect_left(times, before + step)
        if 0 < index < len(times) and times[index] >= after - 1e-6:
            gaps.append([round(times[index - 1] * 1000), after_ms])
    if (kept and times and duration_ms - kept[-1][1] > longest
            and bisect.bisect_left(times, kept[-1][0] + step) == len(times)):
        gaps.append([max(round(times[-1] * 1000), kept[-1][1]), duration_ms])
    return gaps


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
    is a working frame of the recording's layout.

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
        source_ms = round(timestamp * 1000)
        # Decoder/filter lookahead may log or emit a frame beyond the requested
        # interval. An interval starts at a frame's whole millisecond, which a
        # coarse time base can put a fraction of one after the frame itself.
        if source_ms < round(start * 1000) or timestamp >= start + duration - 0.000001:
            path.unlink()
            continue
        frames.append({"id": f"frame-{index+1:06d}", "source_timestamp_ms": source_ms,
                       "clip_timestamp_ms": round((timestamp-start)*1000), "source_pts": int(pts),
                       "time_base": f"{numerator}/{denominator}", "evidence": f"frames/{path.name}",
                       "screen_label": None, "confidence": None, "origin": "automatic_frame_sampling"})
    # An interval can hold no frame of the recording: a screen recorder writes
    # none while the screen is still, and an interval a few milliseconds long
    # can fall between two frames. The decoder then yields only frames outside.
    if not frames:
        return frames
    if len(frames) > math.ceil(duration * fps) + 1:
        raise PipelineError("Extracted frame count is outside the requested bounds.")
    if any(a["source_timestamp_ms"] >= b["source_timestamp_ms"] for a, b in zip(frames, frames[1:])):
        raise PipelineError("Frame presentation timestamps must be strictly increasing.")
    return frames

