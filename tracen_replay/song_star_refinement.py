"""Source-bound recovery of outlined stars in wrapped song receipts.

The general receipt OCR can keep the words on both lines while dropping the
small outlined ``☆`` between the continuation and its closing quotes.  This
adapter is deliberately narrow: it only considers two adjacent, aligned
``Learned the song`` lines, requires an outlined contour with one hole and ten
alternating turns at two thresholds, and independently rereads the visible
text crops from the same gameplay pane.  A saved observation can therefore be
replayed without OCR; changing the source pixels, line geometry, or either
independent text witness fails closed.

The sidecar does not use a song catalogue.  It records both original OCR lines
and inserts the symbol only when the source image proves it.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image

from .refine_contrast import fingerprint
from .song_symbol_refinement import _title_coverage


VERSION = 1
POLICY = "wrapped_song_outlined_star_v1"
STAR_THRESHOLDS = (165, 185)
_PREFIX = 'Learned the song "'
_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 &'!?,-]{2,100}$")
_CONTINUATION_RE = re.compile(
    r"^(?P<title>[A-Za-z][A-Za-z0-9 &'!?,-]{0,39})(?P<quote>[\"”])(?P<stop>[.!])$"
)
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _confident(value):
    return type(value) in (int, float) and math.isfinite(value) and 90 <= value <= 100


def _witness_confident(value):
    """Require a high-confidence independent crop reread."""

    return type(value) in (int, float) and math.isfinite(value) and 95 <= value <= 100


def _box(value, *, width=810, height=1080):
    """Return a validated gameplay-pane box, or ``None``."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(item) is not int for item in value):
        return None
    left, top, right, bottom = value
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        return None
    return [left, top, right, bottom]


def _line_box(line):
    """Convert a full-capture OCR box to a checked gameplay-pane box."""

    if not isinstance(line, dict):
        return None
    value = line.get("box")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(item) is not int for item in value):
        return None
    left, top, right, bottom = value
    if not (148 <= left < right <= 958 and 770 <= top < bottom <= 1000):
        return None
    return [left - 148, top, right - 148, bottom]


def _prefix(line):
    if not isinstance(line, dict) or not isinstance(line.get("text"), str):
        return None
    if not _confident(line.get("confidence")) or not line["text"].startswith(_PREFIX):
        return None
    text = line["text"][len(_PREFIX):]
    if not _PREFIX_RE.fullmatch(text) or text[-1] in " !?,-":
        return None
    # A prefix must actually be open.  A recognized complete receipt belongs
    # to the ordinary parser and is never changed by this sidecar.
    if '"' in text or "”" in text or text[-1] in ".!":
        return None
    return text


def _continuation(line):
    if not isinstance(line, dict) or not isinstance(line.get("text"), str):
        return None
    if not _confident(line.get("confidence")):
        return None
    match = _CONTINUATION_RE.fullmatch(line["text"])
    if match is None:
        return None
    title = match["title"]
    if len(title.split()) > 4 or title[-1] in " !?,-":
        return None
    return match


def _adjacent_pair(lines, index):
    """Return source text matches for one immediately adjacent line pair."""

    if not isinstance(lines, list) or not 0 <= index + 1 < len(lines):
        return None
    first, second = lines[index], lines[index + 1]
    prefix = _prefix(first)
    continuation = _continuation(second)
    first_box = _line_box(first)
    second_box = _line_box(second)
    if prefix is None or continuation is None or first_box is None or second_box is None:
        return None
    if not (0 < second_box[1] - first_box[1] < 40):
        return None
    # The two OCR boxes should describe the same left-aligned receipt.  The
    # immediate-index requirement above rejects an unrelated line between them.
    if abs(second_box[0] - first_box[0]) > 15:
        return None
    return dict(prefix=prefix, continuation=continuation,
                prefix_box=first_box, continuation_box=second_box)


def _opening_quote_pairs(pane, line_box):
    """Find one stable pair of opening-quote components in a first line."""

    left, top, right, bottom = line_box
    x0, y0 = left, max(0, top - 2)
    x1, y1 = right, min(1080, bottom + 2)
    gray = cv2.cvtColor(np.asarray(pane.crop((x0, y0, x1, y1))), cv2.COLOR_RGB2GRAY)
    votes = []
    for threshold in STAR_THRESHOLDS:
        _, _, stats, _ = cv2.connectedComponentsWithStats(
            (gray < threshold).astype("uint8"), 8
        )
        candidates = []
        for values in stats[1:]:
            x, y, width, height, area = map(int, values)
            absolute_top = y + y0
            if not (1 <= width <= 4 and 3 <= height <= 9 and area >= 2):
                continue
            if not top <= absolute_top <= top + 13:
                continue
            candidates.append((x, y, width, height))
        pairs = []
        for first in candidates:
            for second in candidates:
                if second[0] <= first[0]:
                    continue
                if not (2 <= second[0] - first[0] <= 6):
                    continue
                if first[0] + first[2] > second[0]:
                    continue
                if abs(first[1] - second[1]) > 1 or abs(first[3] - second[3]) > 1:
                    continue
                pairs.append((first, second))
        if len(pairs) != 1:
            return None
        first, second = pairs[0]
        votes.append([
            x0 + first[0], y0 + min(first[1], second[1]),
            x0 + second[0] + second[2],
            y0 + max(first[1] + first[3], second[1] + second[3]),
        ])
    if any(max(abs(a - b) for a, b in zip(votes[0], vote)) > 2 for vote in votes[1:]):
        return None
    return votes[1]


def _alternating_turns(polygon):
    points = np.asarray(polygon, dtype=np.int64).reshape(-1, 2)
    if len(points) != 10:
        return False
    signs = []
    for index in range(len(points)):
        previous = points[index - 1]
        current = points[index]
        following = points[(index + 1) % len(points)]
        first = current - previous
        second = following - current
        cross = int(first[0] * second[1] - first[1] * second[0])
        if cross == 0:
            return False
        signs.append(cross > 0)
    return all(signs[index] != signs[(index + 1) % len(signs)] for index in range(len(signs)))


def _polygon_points(contour, epsilon=1.0):
    return [[int(point[0][0]), int(point[0][1])] for point in
            cv2.approxPolyDP(contour, epsilon, True)]


def _star_shape(crop, threshold):
    """Describe one strict outlined-star crop at one threshold."""

    if crop.size != (15, 14):
        return None
    gray = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2GRAY)
    mask = (gray < threshold).astype("uint8")
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None or len(contours) != 2:
        return None
    hierarchy = hierarchy[0]
    outer_indices = [index for index, relation in enumerate(hierarchy)
                     if relation[3] == -1]
    if len(outer_indices) != 1:
        return None
    outer_index = outer_indices[0]
    children = [index for index, relation in enumerate(hierarchy)
                if relation[3] == outer_index]
    if len(children) != 1:
        return None
    outer = contours[outer_index]
    hole = contours[children[0]]
    outer_box = list(map(int, cv2.boundingRect(outer)))
    hole_box = list(map(int, cv2.boundingRect(hole)))
    if outer_box != [0, 0, 15, 14]:
        return None
    outer_area = float(cv2.contourArea(outer))
    hole_area = float(cv2.contourArea(hole))
    if not (40 <= outer_area <= 120 and 0 < hole_area < outer_area
            and 0.35 <= hole_area / outer_area <= 0.9):
        return None
    polygon = _polygon_points(outer)
    if not _alternating_turns(polygon):
        return None
    moments = cv2.moments(outer)
    if not moments["m00"]:
        return None
    centroid = [round(float(moments["m10"] / moments["m00"]), 4),
                round(float(moments["m01"] / moments["m00"]), 4)]
    hole_moments = cv2.moments(hole)
    if not hole_moments["m00"]:
        return None
    hole_centroid = (float(hole_moments["m10"] / hole_moments["m00"]),
                     float(hole_moments["m01"] / hole_moments["m00"]))
    if cv2.pointPolygonTest(outer, hole_centroid, False) < 0:
        return None
    return dict(threshold=threshold, outer_area=round(outer_area, 4),
                hole_area=round(hole_area, 4), outer_box=outer_box,
                hole_box=hole_box, outer_polygon=polygon,
                hole_polygon=_polygon_points(hole), centroid=centroid)


def _cyclic_vertex_distance(first, second):
    """Return max/mean distance for the best same-orientation cyclic shift."""

    first = np.asarray(first, dtype=float)
    second = np.asarray(second, dtype=float)
    if first.shape != (10, 2) or second.shape != (10, 2):
        return None
    candidates = []
    for shift in range(10):
        shifted = np.roll(second, shift, axis=0)
        distances = np.linalg.norm(first - shifted, axis=1)
        candidates.append((float(distances.max()), float(distances.mean())))
    return list(map(lambda value: round(value, 4), min(candidates)))


def _closing_quotes(pane, star_box, continuation_box):
    """Find exactly two small quote components immediately after the star."""

    x0 = star_box[2]
    x1 = min(810, continuation_box[2] + 1)
    y0 = max(0, star_box[1] - 2)
    y1 = min(1080, continuation_box[3] + 2)
    if x1 <= x0:
        return None
    gray = cv2.cvtColor(np.asarray(pane.crop((x0, y0, x1, y1))), cv2.COLOR_RGB2GRAY)
    votes = []
    for threshold in STAR_THRESHOLDS:
        _, _, stats, _ = cv2.connectedComponentsWithStats(
            (gray < threshold).astype("uint8"), 8
        )
        quotes = []
        for values in stats[1:]:
            x, y, width, height, area = map(int, values)
            absolute_top = y + y0
            if not (1 <= width <= 4 and 3 <= height <= 9 and area >= 2):
                continue
            if abs(absolute_top - star_box[1]) > 2:
                continue
            quotes.append((x, y, width, height))
        pairs = []
        for first in quotes:
            for second in quotes:
                if second[0] <= first[0]:
                    continue
                if not (2 <= second[0] - first[0] <= 6):
                    continue
                if first[0] + first[2] > second[0]:
                    continue
                if abs(first[1] - second[1]) > 2 or abs(first[3] - second[3]) > 2:
                    continue
                pairs.append((first, second))
        if len(pairs) != 1:
            return None
        first, second = pairs[0]
        votes.append([
            [x0 + first[0], y0 + first[1], x0 + first[0] + first[2], y0 + first[1] + first[3]],
            [x0 + second[0], y0 + second[1], x0 + second[0] + second[2], y0 + second[1] + second[3]],
        ])
    if any(max(abs(a - b) for a, b in zip(first, second)) > 2
           for first, second in zip(votes[0], votes[1])):
        return None
    return votes[1]


def _star_layout(pane, continuation_box):
    """Find and validate one candidate star in a continuation line."""

    left, top, right, bottom = continuation_box
    x0, x1 = max(0, left - 3), min(810, right + 8)
    y0, y1 = max(0, top - 6), min(1080, bottom + 6)
    gray = cv2.cvtColor(np.asarray(pane.crop((x0, y0, x1, y1))), cv2.COLOR_RGB2GRAY)
    candidate_boxes = set()
    for threshold in STAR_THRESHOLDS:
        mask = (gray < threshold).astype("uint8")
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        hierarchy = hierarchy[0]
        for index, contour in enumerate(contours):
            if hierarchy[index][3] != -1:
                continue
            children = [child for child, relation in enumerate(hierarchy)
                        if relation[3] == index]
            if len(children) != 1:
                continue
            box = cv2.boundingRect(contour)
            x, y, width, height = map(int, box)
            if not (10 <= width <= 20 and 14 <= height <= 20):
                continue
            absolute_x, absolute_y = x0 + x, y0 + y
            # The probe's crop trims the antialiasing row above and keeps the
            # common 15x14 symbol box.  It is derived from the visible outer
            # contour, not from a song name or expected text.
            crop_box = [absolute_x, absolute_y + 1, absolute_x + width,
                        absolute_y + 15]
            if not (0 <= crop_box[0] < crop_box[2] <= 810
                    and 0 <= crop_box[1] < crop_box[3] <= 1080):
                continue
            if crop_box[2] - crop_box[0] != 15 or crop_box[3] - crop_box[1] != 14:
                continue
            candidate_boxes.add(tuple(crop_box))
    found = []
    for candidate in sorted(candidate_boxes):
        crop_box = list(candidate)
        crop = pane.crop(tuple(crop_box))
        shapes = [_star_shape(crop, threshold) for threshold in STAR_THRESHOLDS]
        if any(shape is None for shape in shapes):
            continue
        vertex_distance = _cyclic_vertex_distance(
            shapes[0]["outer_polygon"], shapes[1]["outer_polygon"])
        centroid_distance = float(np.linalg.norm(
            np.asarray(shapes[0]["centroid"]) - np.asarray(shapes[1]["centroid"])))
        area_ratio = shapes[1]["outer_area"] / shapes[0]["outer_area"]
        if vertex_distance is None or vertex_distance[0] > 2.5 or vertex_distance[1] > 1.5:
            continue
        if centroid_distance > 1.5 or not 0.5 <= area_ratio <= 1.8:
            continue
        quotes = _closing_quotes(pane, crop_box, continuation_box)
        if quotes is None:
            continue
        found.append(dict(
            coordinate_space="gameplay_pane",
            symbol="☆",
            box=crop_box,
            crop_box=crop_box,
            thresholds=list(STAR_THRESHOLDS),
            geometry=shapes,
            stability=dict(vertex_distance=vertex_distance,
                           centroid_distance=round(centroid_distance, 4),
                           outer_area_ratio=round(area_ratio, 4)),
            closing_quote_boxes=quotes,
            method="outlined_star_one_hole_ten_alternating_turns",
        ))
    if len(found) != 1:
        return None
    return found[0]


def _layout(pane, first, second):
    """Return source geometry for a wrapped receipt, or ``None``."""

    pair = _adjacent_pair([first, second], 0)
    if pair is None:
        return None
    opening = _opening_quote_pairs(pane, pair["prefix_box"])
    if opening is None:
        return None
    prefix_crop = [opening[2] + 3, pair["prefix_box"][1], pair["prefix_box"][2],
                   pair["prefix_box"][3]]
    continuation = _star_layout(pane, pair["continuation_box"])
    if continuation is None:
        return None
    # The short text crop ends at the independently detected star.  It is
    # intentionally unable to include the star, quotes, or final period.
    continuation_crop = [pair["continuation_box"][0], pair["continuation_box"][1],
                         continuation["box"][0], pair["continuation_box"][3]]
    if not (_box(prefix_crop) and _box(continuation_crop)
            and continuation_crop[2] - continuation_crop[0] >= 20):
        return None
    return dict(coordinate_space="gameplay_pane",
                opening_quote_box=opening, prefix_crop_box=prefix_crop,
                continuation_crop_box=continuation_crop,
                star=continuation, star_crop_box=continuation["box"],
                closing_quote_boxes=continuation["closing_quote_boxes"])


def _coverage(crop, text):
    """Reuse strict title coverage while explicitly masking source punctuation."""

    if not isinstance(text, str) or not text:
        return None
    if not re.fullmatch(r"[A-Za-z]+(?:[ !?'!,.-]*[A-Za-z]+)*[ !?'!,.-]*", text):
        return None
    normalized = re.sub(r"[^A-Za-z ]", "", text)
    normalized = re.sub(r" +", " ", normalized).strip()
    if not normalized or not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", normalized):
        return None
    punctuation_count = sum(not (character.isalpha() or character == " ")
                            for character in text)
    if not punctuation_count:
        result = _title_coverage(crop, normalized)
        return None if result is None else dict(result, source_text=text,
                                                normalized_text=normalized,
                                                removed_punctuation=[])
    array = np.asarray(crop.convert("RGB")).copy()
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    removed = []
    letters = sum(character.isalpha() for character in text)
    expected_runs = letters + punctuation_count
    for threshold in (145, 165):
        ink = gray < threshold
        changes = np.diff(np.r_[False, ink.any(axis=0), False].astype("int8"))
        starts = np.where(changes == 1)[0]
        ends = np.where(changes == -1)[0]
        runs = [[int(start), int(end)] for start, end in zip(starts, ends)]
        if len(runs) != expected_runs:
            return None
        # Every source punctuation in this narrow text layout occupies one
        # connected column run.  Map its run after the letters preceding it;
        # spaces produce no ink run.
        punctuation_indices = []
        letter_count = 0
        punctuation_seen = 0
        for character in text:
            if character.isalpha():
                letter_count += 1
            elif character != " ":
                index = letter_count + punctuation_seen
                punctuation_indices.append(index)
                punctuation_seen += 1
        for index in punctuation_indices:
            start, end = runs[index]
            array[:, start:end] = 255
        removed.append([[runs[index][0], runs[index][1]] for index in punctuation_indices])
    masked = Image.fromarray(array, "RGB")
    result = _title_coverage(masked, normalized)
    if result is None:
        return None
    return dict(result, source_text=text, normalized_text=normalized,
                removed_punctuation=removed)


def _read_exact(reader, crop):
    result = reader.engine.text_rec(
        reader.TextRecInput(img=[np.asarray(crop)[:, :, ::-1]])
    )
    texts = list(getattr(result, "txts", []))
    scores = list(getattr(result, "scores", []))
    if len(texts) != 1 or len(scores) != 1 or not isinstance(texts[0], str):
        return None
    value = float(scores[0])
    if 0 <= value <= 1:
        value *= 100
    if not math.isfinite(value):
        return None
    return dict(text=texts[0].strip(), confidence=round(value, 4))


def _pixels(raw, pane):
    if pane.size != (810, 1080) or hashlib.sha256(pane.tobytes()).hexdigest() != raw.get("gameplay_sha256"):
        raise ValueError("Song-star gameplay pixels changed.")


def _rooted_file(root, relative, label):
    """Resolve one capture-relative file without allowing path escape."""

    if not isinstance(relative, str) or not relative:
        raise ValueError(f"Song-star {label} path is missing.")
    root = Path(root).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Song-star {label} path leaves the recording root.") from exc
    if not path.is_file():
        raise ValueError(f"Song-star {label} evidence is missing.")
    return path


def prepare(raw, proof, reader):
    """Create a source-bound star sidecar using OCR only during preparation."""

    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        raise ValueError("Song-star source has no immutable lines.")
    proof = Path(proof)
    with Image.open(proof) as image:
        pane = image.convert("RGB")
        _pixels(raw, pane)
        observations = []
        for index in range(len(raw["lines"]) - 1):
            pair = _adjacent_pair(raw["lines"], index)
            if pair is None:
                continue
            layout = _layout(pane, raw["lines"][index], raw["lines"][index + 1])
            if layout is None:
                continue
            prefix_crop = pane.crop(tuple(layout["prefix_crop_box"]))
            continuation_crop = pane.crop(tuple(layout["continuation_crop_box"]))
            prefix_reading = _read_exact(reader, prefix_crop)
            continuation_reading = _read_exact(reader, continuation_crop)
            if (prefix_reading is None or continuation_reading is None
                    or prefix_reading["text"] != pair["prefix"]
                    or continuation_reading["text"] != pair["continuation"]["title"]
                    or not _witness_confident(prefix_reading["confidence"])
                    or not _witness_confident(continuation_reading["confidence"])):
                continue
            prefix_coverage = _coverage(prefix_crop, pair["prefix"])
            continuation_coverage = _coverage(continuation_crop, pair["continuation"]["title"])
            if prefix_coverage is None or continuation_coverage is None:
                continue
            min_confidence = round(min(
                raw["lines"][index]["confidence"], raw["lines"][index + 1]["confidence"],
                prefix_reading["confidence"], continuation_reading["confidence"]), 4)
            observations.append(dict(
                prefix_line_index=index,
                continuation_line_index=index + 1,
                prefix_line=deepcopy(raw["lines"][index]),
                continuation_line=deepcopy(raw["lines"][index + 1]),
                raw_lines=[deepcopy(raw["lines"][index]), deepcopy(raw["lines"][index + 1])],
                layout=layout,
                prefix_text=pair["prefix"],
                continuation_text=pair["continuation"]["title"],
                prefix_reading=prefix_reading,
                continuation_reading=continuation_reading,
                prefix_pixel_coverage=prefix_coverage,
                continuation_pixel_coverage=continuation_coverage,
                prefix_crop_sha256=hashlib.sha256(prefix_crop.tobytes()).hexdigest(),
                continuation_crop_sha256=hashlib.sha256(continuation_crop.tobytes()).hexdigest(),
                min_confidence=min_confidence,
            ))
    models = getattr(reader, "models", {})
    engine = getattr(reader, "fingerprint", None)
    return dict(version=VERSION, policy=POLICY, raw_sha256=fingerprint(raw),
                evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                gameplay_sha256=raw.get("gameplay_sha256"), models=models,
                model_sha256=models, engine_fingerprint=engine,
                independent_observations=False, observations=observations)


def _valid_models(models):
    return (isinstance(models, dict) and bool(models)
            and all(isinstance(key, str) and key and isinstance(value, str)
                    and _SHA.fullmatch(value) for key, value in models.items()))


def _validate_observation(observation, source_lines, target_lines, pane):
    if not isinstance(observation, dict):
        raise ValueError("Song-star observation must be an object.")
    prefix_index = observation.get("prefix_line_index")
    continuation_index = observation.get("continuation_line_index")
    if (type(prefix_index) is not int or type(continuation_index) is not int
            or continuation_index != prefix_index + 1
            or not 0 <= prefix_index < len(source_lines)
            or continuation_index >= len(source_lines)):
        raise ValueError("Invalid song-star line indices.")
    source_prefix = source_lines[prefix_index]
    source_continuation = source_lines[continuation_index]
    if (source_prefix != observation.get("prefix_line")
            or source_continuation != observation.get("continuation_line")):
        raise ValueError("Song-star source lines changed.")
    if target_lines[prefix_index] != source_prefix:
        raise ValueError("Song-star prefix line has a prior refinement collision.")
    target_continuation = target_lines[continuation_index]
    expected_pair = _adjacent_pair([source_prefix, source_continuation], 0)
    if expected_pair is None:
        raise ValueError("Song-star source line is no longer eligible.")
    continuation_match = expected_pair["continuation"]
    fixed_text = (f'{continuation_match["title"]}☆{continuation_match["quote"]}'
                  f'{continuation_match["stop"]}')
    already_fixed = False
    if target_continuation != source_continuation:
        if not (target_continuation.get("text") == fixed_text
                and target_continuation.get("original_symbol_text") == source_continuation.get("text")):
            raise ValueError("Song-star continuation line has a prior refinement collision.")
        already_fixed = True
    layout = _layout(pane, source_prefix, source_continuation)
    if layout is None or layout != observation.get("layout"):
        raise ValueError("Song-star pixel geometry changed.")
    prefix_crop = pane.crop(tuple(layout["prefix_crop_box"]))
    continuation_crop = pane.crop(tuple(layout["continuation_crop_box"]))
    if (hashlib.sha256(prefix_crop.tobytes()).hexdigest() != observation.get("prefix_crop_sha256")
            or hashlib.sha256(continuation_crop.tobytes()).hexdigest() != observation.get("continuation_crop_sha256")):
        raise ValueError("Song-star source text crop changed.")
    if (observation.get("prefix_text") != expected_pair["prefix"]
            or observation.get("continuation_text") != continuation_match["title"]):
        raise ValueError("Song-star source text witness changed.")
    prefix_reading = observation.get("prefix_reading")
    continuation_reading = observation.get("continuation_reading")
    if (not isinstance(prefix_reading, dict) or not isinstance(continuation_reading, dict)
            or prefix_reading.get("text") != expected_pair["prefix"]
            or continuation_reading.get("text") != continuation_match["title"]
            or not _witness_confident(prefix_reading.get("confidence"))
            or not _witness_confident(continuation_reading.get("confidence"))):
        raise ValueError("Song-star OCR witness changed.")
    prefix_coverage = _coverage(prefix_crop, expected_pair["prefix"])
    continuation_coverage = _coverage(continuation_crop, continuation_match["title"])
    if (prefix_coverage is None or continuation_coverage is None
            or prefix_coverage != observation.get("prefix_pixel_coverage")
            or continuation_coverage != observation.get("continuation_pixel_coverage")):
        raise ValueError("Song-star glyph coverage changed.")
    expected_minimum = round(min(source_prefix["confidence"], source_continuation["confidence"],
                                 prefix_reading["confidence"], continuation_reading["confidence"]), 4)
    if observation.get("min_confidence") != expected_minimum:
        raise ValueError("Song-star minimum confidence changed.")
    return prefix_index, continuation_index, fixed_text, already_fixed


def apply(raw, extra, proof, original=None):
    """Replay a validated sidecar without invoking OCR or a language model."""

    original = raw if original is None else original
    if (not isinstance(raw, dict) or not isinstance(original, dict)
            or not isinstance(extra, dict) or type(extra.get("version")) is not int
            or extra.get("version") != VERSION
            or extra.get("policy") != POLICY
            or extra.get("raw_sha256") != fingerprint(original)
            or extra.get("evidence_sha256") != hashlib.sha256(Path(proof).read_bytes()).hexdigest()
            or extra.get("gameplay_sha256") != original.get("gameplay_sha256")
            or not _valid_models(extra.get("models"))
            or extra.get("model_sha256") != extra.get("models")
            or not isinstance(extra.get("engine_fingerprint"), str)
            or not _SHA.fullmatch(extra["engine_fingerprint"])
            or extra.get("independent_observations") is not False
            or not isinstance(extra.get("observations"), list)
            or not isinstance(raw, dict) or not isinstance(raw.get("lines"), list)
            or not isinstance(original, dict) or not isinstance(original.get("lines"), list)
            or len(raw["lines"]) != len(original["lines"])):
        raise ValueError("Song-star refinement provenance mismatch.")
    with Image.open(proof) as image:
        pane = image.convert("RGB")
        _pixels(original, pane)
        lines = deepcopy(raw["lines"])
        seen = set()
        for observation in extra["observations"]:
            prefix_index, continuation_index, fixed_text, already_fixed = _validate_observation(
                observation, original["lines"], lines, pane)
            if prefix_index in seen or continuation_index in seen:
                raise ValueError("Duplicate song-star line observation.")
            seen.update((prefix_index, continuation_index))
            source_line = original["lines"][continuation_index]
            star = observation["layout"]["star"]
            evidence = dict(star, title_evidence=deepcopy(observation),
                            source_timestamp_ms=original.get("source_timestamp_ms"),
                            evidence=original.get("evidence"),
                            source_frame_sha256=original.get("source_frame_sha256"),
                            gameplay_sha256=original.get("gameplay_sha256"),
                            evidence_sha256=extra["evidence_sha256"],
                            model_sha256=extra["models"],
                            engine_fingerprint=extra["engine_fingerprint"])
            refined_line = dict(
                source_line, text=fixed_text,
                confidence=min(source_line["confidence"],
                               observation["min_confidence"]),
                original_symbol_text=source_line.get("text"),
                visual_symbol_observation=evidence)
            if already_fixed:
                if lines[continuation_index] != refined_line:
                    raise ValueError("Song-star refined line provenance changed.")
            else:
                lines[continuation_index] = refined_line
    return dict(raw, lines=lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="recording analysis directory")
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    args = parser.parse_args(argv)
    from .vision import NeuralReader

    root = args.output
    capture = json.loads((root / "capture.json").read_text(encoding="utf-8"))
    frames = {frame["id"]: frame for frame in capture.get("frames", [])}
    reader = None
    destination = root / "song-star-refinement"
    destination.mkdir(exist_ok=True)
    observations = 0
    for source in sorted((root / "neural").glob("*.json")):
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not any(_adjacent_pair(raw.get("lines"), index)
                   for index in range(max(0, len(raw.get("lines", [])) - 1))):
            continue
        frame = frames.get(source.stem)
        if frame is None or frame.get("source_timestamp_ms") != raw.get("source_timestamp_ms"):
            raise ValueError("Song-star source timestamp does not match capture.")
        source_frame = _rooted_file(root, frame.get("evidence"), "source-frame")
        if hashlib.sha256(source_frame.read_bytes()).hexdigest() != raw.get("source_frame_sha256"):
            raise ValueError("Song-star source frame changed.")
        with Image.open(source_frame) as source_image:
            source_pane = source_image.convert("RGB").crop((148, 0, 958, 1080))
            _pixels(raw, source_pane)
        proof = _rooted_file(root, raw.get("evidence"), "gameplay")
        target = destination / source.name
        if target.exists():
            apply(raw, json.loads(target.read_text(encoding="utf-8")), proof)
            continue
        if reader is None:
            reader = NeuralReader(args.model_dir)
        extra = prepare(raw, proof, reader)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(extra, ensure_ascii=False, indent=2) + "\n")
        observations += len(extra["observations"])
    print(json.dumps(dict(stage="song_star_refinement", observations=observations)), flush=True)


if __name__ == "__main__":
    main()
