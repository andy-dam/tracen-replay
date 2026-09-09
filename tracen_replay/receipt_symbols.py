"""Recover a visible skill-circle suffix from one gameplay receipt.

The game renders a small ``○`` after some skill names.  The compact dialogue
font can make that glyph look like a terminal ``O`` to OCR.  This module is
intentionally limited to the evidence shape that supports that correction:

* the target line is a confident, complete hint receipt ending in a standalone
  uppercase ``O``;
* a confident same-frame label above it contains the exact name without that
  suffix; and
* source gameplay pixels contain one stable circular ring immediately before
  the receipt's final punctuation.

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
_THRESHOLDS = (165, 185, 205, 225)
_MIN_VOTES = 3
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HINT_RECEIPT = re.compile(r"^Gained (\d+) hint level\(s\) for (.+) O\.$")


def _box(box: Sequence[Any]) -> tuple[int, int, int, int] | None:
    try:
        values = tuple(float(value) for value in box)
    except (TypeError, ValueError):
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
        found.append(dict(
            kind="single_circle",
            symbol=SINGLE_CIRCLE,
            box=[absolute_left, absolute_top, absolute_left + width, absolute_top + height],
            center=[absolute_left + width / 2.0, center_y],
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


def _matching_companions(lines: Sequence[Mapping[str, Any]], base: str, target: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    target_box = _box(target.get("box", ()))
    if target_box is None:
        return []
    _, target_top, target_right, _ = target_box
    matches = []
    for line in lines:
        if line is target or _clean(line.get("text")) != base or line.get("confidence", 0) < 95:
            continue
        companion_box = _box(line.get("box", ()))
        if companion_box is None:
            continue
        companion_left, _, companion_right, companion_bottom = companion_box
        if not 0 < target_top - companion_bottom <= 180:
            continue
        if min(companion_right, target_right) - max(companion_left, target_box[0]) < 20:
            continue
        # Lines in the neural cache inherit timestamp/evidence provenance from
        # the enclosing observation. Requiring those fields on every OCR word
        # would reject the actual source cache; ``_verify_proof`` binds the
        # enclosing observation before this structural check runs.
        required = ("text", "box", "confidence")
        if any(key not in line for key in required):
            continue
        matches.append(line)
    return matches


def annotate(raw: Mapping[str, Any], pane: Any, proof: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the proven terminal ``○`` correction to one OCR observation.

    The returned mapping is a copy. Every accepted line retains its original
    confidence and gets ``original_text``, ``visual_symbol_observation``, and
    ``text_normalization`` provenance fields. If no eligible line has a unique
    companion and a stable pixel marker, the copied observation is unchanged.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("Receipt-symbol OCR observation is required.")
    _verify_proof(raw, pane, proof)
    lines = [dict(line) for line in raw.get("lines", ())]
    changed = False
    for index, line in enumerate(lines):
        if line.get("confidence", 0) < 95 or not isinstance(line.get("text"), str):
            continue
        match = _HINT_RECEIPT.fullmatch(line["text"].strip())
        if not match:
            continue
        base = _clean(match.group(2))
        companions = _matching_companions(lines, base, line)
        if len(companions) != 1:
            continue
        symbol = detect_circle_marker(pane, line.get("box", ()))
        if symbol is None:
            continue
        original = line["text"]
        fixed = re.sub(r" O\.$", f" {symbol['symbol']}.", original)
        observation = dict(symbol,
                           source_timestamp_ms=raw["source_timestamp_ms"],
                           evidence=raw["evidence"],
                           gameplay_sha256=raw["gameplay_sha256"],
                           source_frame_sha256=raw["source_frame_sha256"],
                           evidence_sha256=proof["evidence_sha256"])
        if raw.get("source_sha256") is not None:
            observation["source_sha256"] = raw["source_sha256"]
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
    return dict(raw, lines=lines) if changed else dict(raw, lines=lines)


__all__ = ["annotate", "detect_circle_marker"]
