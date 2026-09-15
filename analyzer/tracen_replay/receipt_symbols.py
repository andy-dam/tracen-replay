"""Recover a visible skill-circle suffix from one gameplay receipt.

The game renders a small ``○`` after some skill names.  The compact dialogue
font can make that glyph look like a terminal ``O`` to OCR. Hint receipts
require the following evidence before correction:

* the target is a confident, complete hint receipt whose OCR either emits a
  standalone uppercase ``O`` or leaves the suffix as whitespace before the
  sentence period;
* source gameplay pixels contain one stable circular ring immediately before
  the receipt's final punctuation.

The complete receipt supplies the name. A separate skill card is optional:
it can disappear before the receipt does, and need not be recorded at all.

The same checks also cover a receipt whose name wraps onto a second OCR line.
The accepted representation joins those two source lines and retains the
original parts in provenance metadata so the downstream parser can see one
receipt without guessing a name.

Inheritance spark receipts use a separate shape: an explicit O or whitespace
slot before ``spark activated!``, plus an isolated source-pixel ring at that
slot's fixed-layout position. They need no hint heading. Ordinary unmarked
receipts, double markers, conflicting rings and obscured lines stay unchanged.

The detector does not consult a skill catalog, expected text, or another
receipt.  ``annotate`` binds the decision to the original gameplay-pixel hash
and source metadata, preserves the original OCR text and confidence, and
returns the input unchanged when any structural condition is absent.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


GAMEPLAY_X_OFFSET = 148
SINGLE_CIRCLE = "○"
DOUBLE_CIRCLE = "◎"
_THRESHOLDS = (175, 185, 195, 205, 215, 225)
_MIN_VOTES = 5
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# Keep the omitted-glyph form explicit: the OCR must leave a separator before
# the period. A no-space ``Name.`` is a different receipt shape and abstains.
_HINT_RECEIPT = re.compile(r"^Gained (\d+) hint level\(s\) for (.+?)(?: O| )\.$")
_HINT_PREFIX = re.compile(r"^Gained \d+ hint level\(s\) for .+$")
_SPARK_RECEIPT = re.compile(r"^(\S(?:.*?\S)?)(?: O| ) spark activated!$")


def _box(box: Sequence[Any]) -> tuple[int, int, int, int] | None:
    try:
        values = tuple(float(value) for value in box)
    except (TypeError, ValueError, OverflowError):
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    left, top, right, bottom = (round(value) for value in values)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _image_and_gray(pane: Any):
    import cv2
    import numpy as np

    if getattr(pane, "size", None) != (810, 1080):
        return None, None
    try:
        image = np.asarray(pane.convert("RGB"))
    except (AttributeError, TypeError, ValueError):
        return None, None
    if image.ndim != 3 or image.shape != (1080, 810, 3):
        return None, None
    return image, cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _open_ring_center(gray: Any, center: Sequence[Any], threshold: int) -> bool:
    x, y = (round(value) for value in center)
    inner = gray[y - 5:y + 5, x - 5:x + 5]
    return inner.shape == (10, 10) and float((inner < threshold).mean()) <= 0.10


def _ring_candidates(gray: Any, box: tuple[int, int, int, int], threshold: int) -> list[dict[str, Any]]:
    """Find ring-like components near the terminal end of one receipt line."""

    import cv2
    import numpy as np

    left, top, right, bottom = box
    pane_right = right - GAMEPLAY_X_OFFSET
    # The OCR box contains the final period, so the marker is normally 8–28
    # pixels to its left. Keep a narrow right-edge search to reject ordinary O
    # letters embedded in names or prose.
    x_start = max(0, pane_right - 52)
    x_end = min(gray.shape[1], pane_right + 3)
    y_start = max(0, top - 5)
    y_end = min(gray.shape[0], bottom + 5)
    if x_end <= x_start or y_end <= y_start:
        return []

    mask = (gray[y_start:y_end, x_start:x_end] < threshold).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    row_center = (top + bottom) / 2.0
    found: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        # Dialogue circles are materially wider than the 10–12 px O glyphs in
        # this layout. Bounds are deliberately strict; clipped or merged text
        # must abstain.
        if not (13 <= width <= 20 and 13 <= height <= 21):
            continue
        if not 0.76 <= width / height <= 1.24:
            continue
        if not 25 <= area <= 145:
            continue
        absolute_left = x_start + x
        absolute_top = y_start + y
        center_y = absolute_top + height / 2.0
        if abs(center_y - row_center) > 5.5:
            continue
        right_gap = pane_right - (absolute_left + width)
        if not 2 <= right_gap <= 34:
            continue
        component = labels[y : y + height, x : x + width] == index
        occupancy = float(component.mean())
        # A ring has a pale center and ink around its perimeter. This rejects
        # filled decorative dots and merged cursor/text components.
        inner = component[3:-3, 3:-3]
        if inner.size == 0 or float(inner.mean()) > 0.28:
            continue
        border = np.concatenate((component[:3].ravel(), component[-3:].ravel(),
                                 component[3:-3, :3].ravel(), component[3:-3, -3:].ravel()))
        if float(border.mean()) < 0.10:
            continue
        # The source circle is a thin open ring (<=.36 occupancy at the
        # accepted thresholds).  A normal Arial ``O`` at this scale is a
        # thicker 14x15 bowl (>.38 occupancy); reject it even when a sentence
        # period happens to follow it.
        if not 0.10 <= occupancy <= 0.38:
            continue
        # The circle is followed by the receipt's sentence period. Requiring
        # that separate, lower baseline component prevents a normal terminal
        # O from voting as a marker when an OCR box has a little right padding.
        punctuation_left = absolute_left + width + 1
        punctuation_right = min(gray.shape[1], punctuation_left + 12)
        punctuation_top = max(0, top - 2)
        punctuation_bottom = min(gray.shape[0], bottom + 4)
        punctuation = gray[punctuation_top:punctuation_bottom, punctuation_left:punctuation_right] < threshold
        punctuation_count, _, punctuation_stats, _ = cv2.connectedComponentsWithStats(punctuation.astype(np.uint8), 8)
        has_period = False
        for p_index in range(1, punctuation_count):
            p_x, p_y, p_width, p_height, p_area = (int(value) for value in punctuation_stats[p_index])
            if not (1 <= p_width <= 8 and 2 <= p_height <= 8 and 2 <= p_area <= 40):
                continue
            p_center_y = punctuation_top + p_y + p_height / 2.0
            if center_y < p_center_y <= bottom + 3:
                has_period = True
                break
        if not has_period:
            continue
        center = [absolute_left + width / 2.0, center_y]
        # Connected-component geometry alone misses a disconnected inner ring.
        # Keep that ambiguity visible so another threshold cannot outvote it.
        found.append(dict(
            kind="single_circle" if _open_ring_center(gray, center, threshold) else "nonempty_ring_center",
            symbol=SINGLE_CIRCLE,
            box=[absolute_left, absolute_top, absolute_left + width, absolute_top + height],
            center=center,
            threshold=threshold,
        ))
    return found


def detect_circle_marker(pane: Any, line_box: Sequence[Any]) -> dict[str, Any] | None:
    """Return a stable pixel observation for a terminal open circle.

    ``line_box`` uses full-frame OCR coordinates while ``pane`` is the
    810x1080 gameplay crop. A clipped, merged, multiple, or unstable candidate
    returns ``None``. The result is a pixel observation only; it does not name
    the skill.
    """

    normalized = _box(line_box)
    if normalized is None:
        return None
    image, gray = _image_and_gray(pane)
    if gray is None:
        return None

    observations: list[dict[str, Any]] = []
    for threshold in _THRESHOLDS:
        candidates = _ring_candidates(gray, normalized, threshold)
        if any(candidate['kind'] != 'single_circle' for candidate in candidates):
            return None
        if len(candidates) == 1:
            observations.append(candidates[0])
    if len(observations) < _MIN_VOTES:
        return None

    anchor = observations[len(observations) // 2]["center"]
    cluster = [item for item in observations
               if max(abs(item["center"][i] - anchor[i]) for i in (0, 1)) <= 2.5]
    if len(cluster) < _MIN_VOTES:
        return None
    # Do not choose a candidate when another ring-like component is visible at
    # any threshold. Multiple terminal markers are ambiguous.
    if any(len(_ring_candidates(gray, normalized, threshold)) > 1 for threshold in _THRESHOLDS):
        return None
    result = dict(cluster[len(cluster) // 2])
    result.update(thresholds=list(_THRESHOLDS), votes=len(cluster), method="strict_terminal_ring_geometry",
                  coordinate_space="gameplay_crop")
    return result


def _verify_proof(raw: Mapping[str, Any], pane: Any, proof: Mapping[str, Any]) -> str:
    if not isinstance(proof, Mapping):
        raise ValueError("Receipt-symbol proof metadata is required.")
    image, _ = _image_and_gray(pane)
    if image is None:
        raise ValueError("Receipt-symbol proof requires an 810x1080 gameplay crop.")
    actual_gameplay = hashlib.sha256(image.tobytes()).hexdigest()
    expected_gameplay = proof.get("gameplay_sha256")
    if expected_gameplay != actual_gameplay or raw.get("gameplay_sha256") != actual_gameplay:
        raise ValueError("Receipt-symbol gameplay pixels do not match the OCR observation.")
    for key in ("source_timestamp_ms", "evidence", "source_frame_sha256"):
        if key not in proof or key not in raw or proof[key] != raw[key]:
            raise ValueError(f"Receipt-symbol proof metadata mismatch: {key}.")
    # Full-recording cached rows historically carry the source-frame hash but
    # not the recording hash. When a caller has the latter, require it on both
    # sides as well so a proof cannot be moved between recordings.
    raw_source = raw.get("source_sha256")
    proof_source = proof.get("source_sha256")
    if raw_source is not None or proof_source is not None:
        if (not isinstance(raw_source, str) or not isinstance(proof_source, str)
                or raw_source != proof_source or not _HEX64.fullmatch(raw_source)):
            raise ValueError("Receipt-symbol source recording hash mismatch.")
    if not _HEX64.fullmatch(str(proof["source_frame_sha256"])):
        raise ValueError("Receipt-symbol source-frame hash is malformed.")
    evidence_hash = proof.get("evidence_sha256")
    if not isinstance(evidence_hash, str) or not _HEX64.fullmatch(evidence_hash):
        raise ValueError("Receipt-symbol evidence hash is required.")
    for path_key, hash_key in (("evidence_path", "evidence_sha256"), ("source_frame_path", "source_frame_sha256")):
        path_value = proof.get(path_key)
        if path_value is None:
            continue
        path = Path(path_value)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != proof[hash_key]:
            raise ValueError(f"Receipt-symbol proof file mismatch: {path_key}.")
    return actual_gameplay


def _confident(line: Mapping[str, Any]) -> bool:
    confidence = line.get('confidence')
    return type(confidence) in (int, float) and 95 <= confidence <= 100


def _readable_receipt(line: Mapping[str, Any]) -> bool:
    box = _box(line.get('box', ()))
    return (_confident(line)
            and not line.get('overlay_occluded') and box is not None
            and 250 <= box[0] < box[2] <= 850 and 770 <= box[1] < box[3] <= 1000)


def _hint_match(text: Any):
    match = _HINT_RECEIPT.fullmatch(_clean(text))
    return match if match and not any(symbol in match.group(2) for symbol in ('○', '◯', '◎')) else None


def _spark_match(text: Any):
    # A double space or explicit standalone O occupies the missing-marker
    # slot. Ordinary complete names without that slot are not rewritten.
    match = _SPARK_RECEIPT.fullmatch(text.strip()) if isinstance(text, str) else None
    return match if match and not any(s in match.group(1) for s in (SINGLE_CIRCLE, DOUBLE_CIRCLE)) else None


def spark_slot_geometry(line_box: Sequence[Any], center: Sequence[Any]) -> dict[str, Any] | None:
    """Bind a marker to the fixed receipt tail in the supported 1080p layout."""
    box = _box(line_box)
    if box is None or not isinstance(center, (list, tuple)) or len(center) != 2:
        return None
    if any(type(value) not in (int, float)
           or (type(value) is float and not math.isfinite(value)) for value in center):
        return None
    left, top, right, bottom = box
    x, y = center
    distance = right - GAMEPLAY_X_OFFSET - x
    # Source markers lie 168--171px before the OCR right edge. Ten pixels
    # around the fixed 170px tail allow box padding without searching letters
    # elsewhere in the name. Other fonts/layouts must establish their own slot.
    if not (250 <= left < right <= 850 and 770 <= top < bottom <= 970
            and left - GAMEPLAY_X_OFFSET < x < right - GAMEPLAY_X_OFFSET
            and top <= y <= bottom and abs(y - (top + bottom) / 2) <= 5.5
            and 160 <= distance <= 180):
        return None
    return dict(ocr_line_box=list(box), ocr_line_coordinate_space='source_frame',
                slot_distance_px=distance)


def detect_spark_circle(pane: Any, line_box: Sequence[Any]) -> dict[str, Any] | None:
    """Read a ring before the fixed 'spark activated!' tail in the 1080p layout.

    Reuse the inventory detector's ring-versus-letter geometry. Search views
    share pixels and never count as separate observations. The tail distance
    limits the search to the marker slot, not other letters in the skill name.
    """
    from .inventory_suffix import _candidates, _THRESHOLDS as thresholds

    box = _box(line_box)
    if box is None:
        return None
    left, top, right, bottom = box
    if not (250 <= left < right <= 850 and 770 <= top < bottom <= 970):
        return None
    _, gray = _image_and_gray(pane)
    if gray is None:
        return None
    votes = []
    for threshold in thresholds:
        found = []
        for offset in (128, 160, 192):
            for kind, x, y in _candidates(gray, (left, top, right - offset, bottom), threshold):
                # Inspect a wider neighborhood solely to reject competing
                # rings. Only the shared narrow slot can accept a marker.
                if not (130 <= right - GAMEPLAY_X_OFFSET - x <= 205
                        and left - GAMEPLAY_X_OFFSET < x
                        and abs(y - (top + bottom) / 2) <= 5.5):
                    continue
                # A disconnected inner ring can share an outer contour with
                # a single marker. Require a clear center in the receipt's
                # source pixels rather than trusting the contour class alone.
                if kind != 'single_circle' or not _open_ring_center(gray, [x, y], threshold):
                    return None
                if not any(old[0] == kind and max(abs(old[1] - x), abs(old[2] - y)) <= 3 for old in found):
                    found.append((kind, x, y))
        if len(found) > 1:
            return None
        if found:
            votes.append(dict(kind=found[0][0], center=list(found[0][1:]), threshold=threshold))
    if len(votes) < 3:
        return None
    anchor = votes[len(votes) // 2]
    if any(v['kind'] != anchor['kind'] or max(abs(v['center'][i] - anchor['center'][i])
                                            for i in (0, 1)) > 3 for v in votes):
        return None
    # Double markers need their own receipt-layout validation; do not borrow
    # an inventory-only observation to claim a different spark identity.
    geometry = spark_slot_geometry(box, anchor['center'])
    if anchor['kind'] != 'single_circle' or geometry is None:
        return None
    return dict(kind='single_circle', symbol=SINGLE_CIRCLE, center=anchor['center'],
                threshold_observations=votes, votes=len(votes),
                method='inline_spark_ring_geometry', coordinate_space='gameplay_crop',
                **geometry)


def _hint_text(match: re.Match[str], symbol: Mapping[str, Any]) -> str:
    return f"Gained {match.group(1)} hint level(s) for {_clean(match.group(2))} {symbol['symbol']}."


def _symbol_observation(raw: Mapping[str, Any], proof: Mapping[str, Any], symbol: Mapping[str, Any]) -> dict[str, Any]:
    observation = dict(
        symbol,
        source_timestamp_ms=raw["source_timestamp_ms"],
        evidence=raw["evidence"],
        gameplay_sha256=raw["gameplay_sha256"],
        source_frame_sha256=raw["source_frame_sha256"],
        evidence_sha256=proof["evidence_sha256"],
    )
    if raw.get("source_sha256") is not None:
        observation["source_sha256"] = raw["source_sha256"]
    return observation


def _line_box_union(first: Mapping[str, Any], second: Mapping[str, Any]) -> list[int] | None:
    first_box = _box(first.get("box", ()))
    second_box = _box(second.get("box", ()))
    if first_box is None or second_box is None:
        return None
    return [
        min(first_box[0], second_box[0]),
        min(first_box[1], second_box[1]),
        max(first_box[2], second_box[2]),
        max(first_box[3], second_box[3]),
    ]


def _is_hint_continuation(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Match the narrow two-line layout used by ``vision.parse``."""

    if not _readable_receipt(first) or not _readable_receipt(second):
        return False
    first_text = _clean(first.get("text"))
    second_text = _clean(second.get("text"))
    if not _HINT_PREFIX.fullmatch(first_text) or re.search(r"[.!?]$", first_text):
        return False
    first_box = _box(first.get("box", ()))
    second_box = _box(second.get("box", ()))
    if first_box is None or second_box is None:
        return False
    if not 0 < second_box[1] - first_box[1] < 40:
        return False
    if abs(second_box[0] - first_box[0]) > 15:
        return False
    if len(second_text.split()) > 4 or not re.search(r"[.!]$", second_text):
        return False
    # Keep the same guard as the parser: a complete second-line receipt must
    # never be treated as a name continuation.
    from .gameplay import effects_from_lines

    return not effects_from_lines([dict(second, text=second_text)])


def has_eligible_receipt(lines: Sequence[Mapping[str, Any]]) -> bool:
    """Return whether OCR has a receipt shape worth opening source pixels for.

    This is only a cheap shape gate. It deliberately does not inspect pixels,
    companion labels, or confidence beyond the same minimum used by
    ``annotate``. Keeping it here prevents callers from drifting away from
    the accepted single-line and wrapped forms; ``annotate`` remains the
    authority that can actually accept a correction.
    """

    materialized = [line for line in lines if isinstance(line, Mapping)]
    for line in materialized:
        if not _confident(line) or not isinstance(line.get("text"), str):
            continue
        if _hint_match(line["text"]) or _spark_match(line["text"]):
            return True
    for first, second in zip(materialized, materialized[1:]):
        if not _is_hint_continuation(first, second):
            continue
        combined = f"{first['text'].strip()} {second['text'].strip()}"
        if _hint_match(combined):
            return True
    return False


def annotate(raw: Mapping[str, Any], pane: Any, proof: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a source-proven ``○`` correction to one OCR observation.

    The returned mapping is a copy. Every accepted line retains its original
    confidence and gets ``original_text``, ``visual_symbol_observation``, and
    ``text_normalization`` provenance fields. If no readable complete receipt
    has a stable pixel marker, the copied observation is unchanged.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("Receipt-symbol OCR observation is required.")
    _verify_proof(raw, pane, proof)
    lines = [dict(line) for line in raw.get("lines", ())]
    changed = False
    for index, line in enumerate(lines):
        if not _confident(line) or not isinstance(line.get("text"), str):
            continue
        spark = _spark_match(line['text'])
        if spark and not line.get('overlay_occluded'):
            symbol = detect_spark_circle(pane, line.get('box', ()))
            if symbol is not None:
                lines[index] = dict(line, text=f"{_clean(spark.group(1))} {SINGLE_CIRCLE} spark activated!",
                                    original_text=line['text'], original_symbol_text=line['text'],
                                    visual_symbol_observation=_symbol_observation(raw, proof, symbol),
                                    text_normalization='spark_circle_suffix')
                changed = True
            continue
        match = _hint_match(line["text"])
        if not match or not _readable_receipt(line):
            continue
        symbol = detect_circle_marker(pane, line.get("box", ()))
        if symbol is None:
            continue
        original = line["text"]
        fixed = _hint_text(match, symbol)
        observation = _symbol_observation(raw, proof, symbol)
        lines[index] = dict(line,
                            text=fixed,
                            original_text=original,
                            # ``vision.parse`` shares the existing song
                            # symbol provenance field when it exposes a
                            # corrected line. Keep both names so this
                            # receipt-specific correction remains compatible
                            # with that parser contract while retaining the
                            # clearer generic original_text field above.
                            original_symbol_text=original,
                            visual_symbol_observation=observation,
                            text_normalization="receipt_circle_suffix")
        changed = True

    # OCR sometimes wraps a long skill name.  Normalize the two source lines
    # into one parser-visible line only after the continuation, complete receipt,
    # and terminal ring have all passed the same evidence gates.  The source
    # line dictionaries remain available under ``wrapped_receipt_parts``.
    index = 0
    while index + 1 < len(lines):
        first, second = lines[index], lines[index + 1]
        if not _is_hint_continuation(first, second):
            index += 1
            continue
        original_text = f"{first['text'].strip()} {second['text'].strip()}"
        match = _hint_match(original_text)
        if not match:
            index += 1
            continue
        merged_box = _line_box_union(first, second)
        if merged_box is None:
            index += 1
            continue
        symbol = detect_circle_marker(pane, second.get("box", ()))
        if symbol is None:
            index += 1
            continue
        observation = _symbol_observation(raw, proof, symbol)
        merged = dict(
            first,
            text=_hint_text(match, symbol),
            box=merged_box,
            # The merged receipt is supported by both OCR lines. Preserve the
            # weaker confidence so joining a clean suffix cannot overstate
            # the observation's certainty.
            confidence=min(first["confidence"], second["confidence"]),
            original_text=original_text,
            original_symbol_text=original_text,
            visual_symbol_observation=observation,
            text_normalization="receipt_circle_suffix_wrapped",
            wrapped_receipt_parts=[dict(first), dict(second)],
        )
        lines[index : index + 2] = [merged]
        changed = True
        index += 1
    return dict(raw, lines=lines) if changed else dict(raw, lines=lines)


__all__ = ["annotate", "detect_circle_marker", "detect_spark_circle", "has_eligible_receipt"]
