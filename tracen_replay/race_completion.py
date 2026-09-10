"""Source-bound recognition of the race completion animation.

Race-result rows describe the detail panel and are intentionally kept as the
authoritative race identity.  This module only adds timing evidence when an
already-known completed race has a short run of exact, large, centered ordinal
readings immediately before its detail panel.

The OCR rows are correlated views of a source frame.  Two rows count as
independent only when their timestamps, evidence paths, and (when available)
image fingerprints are distinct.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable, Mapping


# Coordinates are in the full OCR coordinate space.  NeuralReader adds 148 to
# the gameplay crop's x coordinates.  The centered completion badge in the
# source run is approximately [411, 567, 690, 784]; the result-list ranks are
# small and the decorative result-panel rank is above this vertical band.
MIN_CONFIDENCE = 97.0
MIN_OBSERVATIONS = 2
MAX_SEQUENCE_STEP_MS = 1_000
MAX_ASSOCIATION_LAG_MS = 10_000

_ORDINAL_RE = re.compile(r"([1-9][0-9]{0,2})(st|nd|rd|th)")
_CONTINUOUS_SCREENS = frozenset(("", "race_result", "unknown"))


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _ordinal_number(text):
    """Return the ordinal's integer when *text* is an exact ordinal."""

    if not isinstance(text, str) or text != text.strip():
        return None
    match = _ORDINAL_RE.fullmatch(text)
    if match is None:
        return None
    number = int(match.group(1))
    suffix = match.group(2)
    if 10 < number % 100 < 14:
        expected = "th"
    else:
        expected = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return number if suffix == expected else None


def _large_completion_box(box):
    """Check the reusable geometry of the centered completion badge."""

    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    if not all(_finite_number(value) for value in box):
        return False
    left, top, right, bottom = (float(value) for value in box)
    width = right - left
    height = bottom - top
    center_x = (left + right) / 2
    if left >= right or top >= bottom:
        return False
    return (
        380 <= left <= 500
        and 620 <= right <= 735
        and 500 <= top <= 700
        and 700 <= bottom <= 850
        and 200 <= width <= 360
        and 130 <= height <= 300
        and 500 <= center_x <= 610
        and 0.65 <= width / height <= 2.2
    )


def _neural_lines(reading):
    """Get neural OCR lines from the report's correlated reading shape."""

    if not isinstance(reading, Mapping):
        return ()
    ocr = reading.get("ocr")
    neural = ocr.get("neural") if isinstance(ocr, Mapping) else None
    if isinstance(neural, Mapping):
        neural = neural.get("lines", ())
    if isinstance(neural, (list, tuple)):
        return neural
    return ()


def _fingerprint(reading, neural):
    """Use an optional image fingerprint to reject copied frames."""

    for source in (reading, neural):
        if not isinstance(source, Mapping):
            continue
        for key in ("gameplay_sha256", "source_frame_sha256", "image_sha256"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _candidate(reading, line, line_index):
    if not isinstance(reading, Mapping) or not isinstance(line, Mapping):
        return None
    timestamp = reading.get("source_timestamp_ms")
    evidence = reading.get("evidence")
    confidence = line.get("confidence")
    text = line.get("text")
    if type(timestamp) is not int or timestamp < 0:
        return None
    if not isinstance(evidence, str) or not evidence:
        return None
    if not _finite_number(confidence) or float(confidence) < MIN_CONFIDENCE:
        return None
    if float(confidence) > 100:
        return None
    placing = _ordinal_number(text)
    if placing is None or not _large_completion_box(line.get("box")):
        return None
    screen = reading.get("screen")
    if screen is not None and (
        not isinstance(screen, str) or screen.casefold() not in _CONTINUOUS_SCREENS
    ):
        return None
    box = list(line["box"])
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "text": text,
        "confidence": float(confidence),
        "box": box,
        "placing": placing,
        "image_fingerprint": _fingerprint(reading, reading.get("ocr", {}).get("neural") if isinstance(reading.get("ocr"), Mapping) else None),
        "line_index": line_index,
    }


def _candidates(readings):
    found = []
    for reading in readings if isinstance(readings, Iterable) else ():
        for index, line in enumerate(_neural_lines(reading)):
            item = _candidate(reading, line, index)
            if item is not None:
                found.append(item)
    found.sort(key=lambda item: (item["source_timestamp_ms"], item["evidence"], item["line_index"]))

    # One source reading can be repeated in a cache, and a copied frame can be
    # assigned a second timestamp.  Neither is a second observation.
    # A timestamp with two different valid ordinals is ambiguous.  Do not let
    # sort order select one of them before sequence association gets a chance
    # to reject the frame.
    ordinal_by_timestamp = {}
    for item in found:
        ordinal_by_timestamp.setdefault(item["source_timestamp_ms"], set()).add(item["placing"])
    ambiguous_timestamps = {
        timestamp for timestamp, ordinals in ordinal_by_timestamp.items() if len(ordinals) > 1
    }

    unique = []
    timestamps = set()
    evidence = set()
    fingerprints = set()
    for item in found:
        if item["source_timestamp_ms"] in ambiguous_timestamps:
            continue
        fingerprint = item["image_fingerprint"]
        if item["source_timestamp_ms"] in timestamps or item["evidence"] in evidence:
            continue
        if fingerprint is not None and fingerprint in fingerprints:
            continue
        timestamps.add(item["source_timestamp_ms"])
        evidence.add(item["evidence"])
        if fingerprint is not None:
            fingerprints.add(fingerprint)
        unique.append(item)
    return unique, ambiguous_timestamps


def _has_sequence_break(previous, current, all_candidates, ambiguous_timestamps):
    if current["source_timestamp_ms"] - previous["source_timestamp_ms"] > MAX_SEQUENCE_STEP_MS:
        return True
    start = previous["source_timestamp_ms"]
    end = current["source_timestamp_ms"]
    if any(start < timestamp < end for timestamp in ambiguous_timestamps):
        return True
    for item in all_candidates:
        timestamp = item["source_timestamp_ms"]
        if start < timestamp < end and item["placing"] != current["placing"]:
            return True
    return False


def _runs(candidates, all_candidates, ambiguous_timestamps):
    runs = []
    current = []
    for item in candidates:
        if not current or not _has_sequence_break(
            current[-1], item, all_candidates, ambiguous_timestamps
        ):
            current.append(item)
            continue
        if len(current) >= MIN_OBSERVATIONS:
            runs.append(current)
        current = [item]
    if len(current) >= MIN_OBSERVATIONS:
        runs.append(current)
    return runs


def _continuous_tail(run, detail_time, readings, ambiguous_timestamps):
    """Require inspected source rows from the chosen run through the detail."""

    if not run or detail_time <= run[-1]["source_timestamp_ms"]:
        return False
    start_time = run[0]["source_timestamp_ms"]
    rows = []
    for reading in readings:
        if not isinstance(reading, Mapping):
            continue
        timestamp = reading.get("source_timestamp_ms")
        if type(timestamp) is not int or not start_time <= timestamp <= detail_time:
            continue
        rows.append(reading)
    rows.sort(key=lambda reading: reading["source_timestamp_ms"])
    if not rows or rows[0]["source_timestamp_ms"] != start_time:
        return False
    if rows[-1]["source_timestamp_ms"] != detail_time:
        return False
    detail_screen = rows[-1].get("screen")
    if not isinstance(detail_screen, str) or detail_screen.casefold() != "race_result":
        return False
    previous_time = None
    for reading in rows:
        timestamp = reading["source_timestamp_ms"]
        if timestamp in ambiguous_timestamps:
            return False
        if previous_time is not None and timestamp - previous_time > MAX_SEQUENCE_STEP_MS:
            return False
        screen = reading.get("screen")
        if screen is not None and (
            not isinstance(screen, str) or screen.casefold() not in _CONTINUOUS_SCREENS
        ):
            return False
        for item in _candidates_for_reading(reading):
            if item["placing"] != run[0]["placing"]:
                return False
        previous_time = timestamp
    return True


def _candidates_for_reading(reading):
    return [
        item
        for index, line in enumerate(_neural_lines(reading))
        for item in [_candidate(reading, line, index)]
        if item is not None
    ]


def _proof_observation(item):
    result = {
        "source_timestamp_ms": item["source_timestamp_ms"],
        "evidence": item["evidence"],
        "text": item["text"],
        "confidence": item["confidence"],
        "box": item["box"],
    }
    if item["image_fingerprint"] is not None:
        result["image_fingerprint"] = item["image_fingerprint"]
    return result


def annotate(races, readings):
    """Return races enriched with source-bound completion timing evidence.

    Only races whose existing detail-panel row says ``completed_action`` is
    ``"race"`` and whose integer ``placing`` matches the ordinal animation are
    eligible.  A race without enough evidence is returned unchanged, which is
    the representation of an unknown completion time.
    """

    result = copy.deepcopy(races)
    if not isinstance(result, list):
        return result
    source_readings = list(readings) if isinstance(readings, Iterable) else []
    candidates, ambiguous_timestamps = _candidates(source_readings)
    all_candidates = list(candidates)
    used_observations = set()

    ordered_races = sorted(
        ((index, race) for index, race in enumerate(result) if isinstance(race, Mapping)),
        key=lambda pair: (
            pair[1]["first_seen_ms"]
            if type(pair[1].get("first_seen_ms")) is int
            else float("inf")
        ),
    )
    for index, race in ordered_races:
        if "completion_first_seen_ms" in race:
            continue
        if race.get("completed_action") != "race" or type(race.get("placing")) is not int:
            continue
        detail_time = race.get("first_seen_ms")
        if type(detail_time) is not int or detail_time < 0:
            continue
        previous_races = [
            previous
            for _, previous in ordered_races
            if previous is not race
            and previous.get("completed_action") == "race"
            and type(previous.get("first_seen_ms")) is int
            and previous["first_seen_ms"] < detail_time
        ]
        previous_end_times = []
        for previous in previous_races:
            previous_end = previous.get("last_seen_ms")
            if type(previous_end) is not int:
                previous_end = previous["first_seen_ms"]
            if type(previous_end) is int:
                previous_end_times.append(previous_end)
        lower_bound = max(previous_end_times, default=-1)
        eligible = [
            item
            for item in candidates
            if item["placing"] == race["placing"]
            and item["source_timestamp_ms"] > lower_bound
            and item["source_timestamp_ms"] < detail_time
            and detail_time - item["source_timestamp_ms"] <= MAX_ASSOCIATION_LAG_MS
            and (item["source_timestamp_ms"], item["evidence"]) not in used_observations
        ]
        runs = [
            run
            for run in _runs(eligible, all_candidates, ambiguous_timestamps)
            if _continuous_tail(run, detail_time, source_readings, ambiguous_timestamps)
        ]
        # The nearest valid sequence is the one that belongs to this detail
        # panel when several old animations have the same ordinal.
        runs.sort(key=lambda run: (run[-1]["source_timestamp_ms"], len(run)), reverse=True)
        for run in runs:
            keys = {(item["source_timestamp_ms"], item["evidence"]) for item in run}
            if keys & used_observations:
                continue
            observations = [_proof_observation(item) for item in run]
            target = result[index]
            target["completion_first_seen_ms"] = run[0]["source_timestamp_ms"]
            target["completion_last_seen_ms"] = run[-1]["source_timestamp_ms"]
            target["completion_evidence"] = [item["evidence"] for item in observations]
            target["completion_provenance"] = {
                "method": "large_centered_exact_ordinal_animation",
                "ordinal_text": run[0]["text"],
                "placing": run[0]["placing"],
                "observations": observations,
                "max_step_ms": max(
                    (right["source_timestamp_ms"] - left["source_timestamp_ms"]
                     for left, right in zip(run, run[1:])),
                    default=0,
                ),
                "association": "backward_from_existing_completed_race",
            }
            used_observations.update(keys)
            break
    return result


__all__ = [
    "MAX_ASSOCIATION_LAG_MS",
    "MAX_SEQUENCE_STEP_MS",
    "MIN_CONFIDENCE",
    "MIN_OBSERVATIONS",
    "annotate",
]
