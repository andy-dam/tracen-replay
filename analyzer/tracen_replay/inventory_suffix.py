"""Detect the small circle suffixes on visible skill names.

The final career-summary OCR frequently returns a skill name without its
``○``/``◎`` marker.  This module reads only the pixels around the OCR box and
returns a marker class when several thresholded views agree.  It deliberately
does not use a skill-name catalog or infer a missing marker from a name.

``box`` uses the full-recording coordinate convention used by the OCR
pipeline.  ``pane`` is the gameplay crop, whose left edge is at x=148 in
those coordinates.
"""

from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Any, Sequence


GAMEPLAY_X_OFFSET = 148

# The lower thresholds preserve the dark ring while the higher thresholds
# expose anti-aliased edges.  Starting at 185 avoids the low-threshold merge
# of the two rings seen in the real double-circle references.
_THRESHOLDS = (185, 195, 205, 215, 225)
_MIN_VOTES = 3
_POSITION_TOLERANCE = 3.0
_MIN_LEFT_GAP = 4
_MIN_INNER_ASPECT = 0.90
_MIN_SINGLE_HOLE_RATIO = 0.70


def _box_values(box: Sequence[Any]) -> tuple[int, int, int, int] | None:
    """Return a finite integer box, or ``None`` for malformed input."""

    try:
        values = tuple(float(value) for value in box)
    except (TypeError, ValueError):
        return None
    if len(values) != 4 or not all(isfinite(value) for value in values):
        return None
    left, top, right, bottom = (round(value) for value in values)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _candidates(gray: Any, box: tuple[int, int, int, int], threshold: int) -> list[tuple[str, float, float]]:
    """Find geometrically plausible circle contours for one threshold.

    A single circle has a large enclosed center; the double marker's inner
    ring leaves a materially smaller largest enclosed contour.  The ratio is
    measured from contours rather than from a fixed name position so the
    detector remains useful when OCR boxes vary by a few pixels.
    """

    import cv2
    import numpy as np

    _, top, right, bottom = box
    pane_right = right - GAMEPLAY_X_OFFSET
    # OCR boxes may end before, on, or after the marker.  The symmetric search
    # window handles all three cases while remaining within the same row.
    x_start = max(0, pane_right - 48)
    x_end = min(gray.shape[1], pane_right + 38)
    y_start = max(0, top - 9)
    y_end = min(gray.shape[0], bottom + 9)
    if x_end <= x_start or y_end <= y_start:
        return []

    mask = (gray[y_start:y_end, x_start:x_end] < threshold).astype(np.uint8) * 255
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []

    found: list[tuple[str, float, float]] = []
    roi_height, roi_width = mask.shape[:2]
    row_center = (top + bottom) / 2.0
    for index, contour in enumerate(contours):
        if hierarchy[0][index][3] != -1:
            continue

        x, y, width, height = cv2.boundingRect(contour)
        area = float(cv2.contourArea(contour))
        # Final-summary markers are 12-16 px at the recorded scale.  The
        # slightly wider bound also covers high-contrast anti-aliased frames.
        if not (10 <= width <= 20 and 10 <= height <= 20):
            continue
        if not (0.8 <= width / height <= 1.2):
            continue
        # A contour touching the search ROI may be a clipped marker.  It is
        # safer to abstain than to call a partial ring a real suffix.
        if x <= 0 or y <= 0 or x + width >= roi_width or y + height >= roi_height:
            continue
        if area < 0.4 * width * height:
            continue

        center_x = x + width / 2.0
        center_y = y + height / 2.0
        absolute_center_y = y_start + center_y
        if abs(absolute_center_y - row_center) > 9.0:
            continue
        relative_left = x_start + x - pane_right
        if not (-42 <= relative_left <= 38):
            continue

        # Locate a centered child contour.  A nearby letter can create a
        # child in the hierarchy, so both its center and its area matter.
        holes: list[float] = []
        for child, child_contour in enumerate(contours):
            if hierarchy[0][child][3] != index:
                continue
            child_x, child_y, child_width, child_height = cv2.boundingRect(child_contour)
            if abs(child_x + child_width / 2.0 - center_x) <= 3.0 and abs(
                child_y + child_height / 2.0 - center_y
            ) <= 3.0:
                if child_width >= 5 and child_height >= 5:
                    # A terminal O/o can create a plausible contour and
                    # child hole, but its anti-aliased bowl is measurably
                    # taller than the circular marker's inner opening.
                    inner_aspect = child_width / child_height
                    if not _MIN_INNER_ASPECT <= inner_aspect <= 1.2:
                        continue
                    holes.append(float(cv2.contourArea(child_contour)))
        if not holes:
            continue

        hole_ratio = max(holes) / area
        # Very small child contours are rounded letters or UI noise.  Real
        # single and double markers in the source references are separated
        # from those by this lower bound.
        if not 0.18 <= hole_ratio <= 1.3:
            continue
        kind = "single_circle" if hole_ratio >= 0.58 else "double_circle"
        if kind == "single_circle" and hole_ratio < _MIN_SINGLE_HOLE_RATIO:
            # The outline of a text glyph is thinner than the solid circular
            # suffix in the source frames.  Keep this as a pixel-shape check,
            # rather than using the OCR text or a skill catalog.
            continue

        # A marker is separated from the final text glyph by a small visible
        # gap.  Looking only at rows occupied by the candidate avoids making
        # assumptions about the name's length or its absolute position.
        left_start = max(0, x - 32)
        left_gaps: list[int] = []
        for row in range(y, y + height):
            dark_left = np.flatnonzero(mask[row, left_start:x])
            if dark_left.size:
                left_gaps.append(x - (left_start + int(dark_left[-1])) - 1)
        if left_gaps and min(left_gaps) < _MIN_LEFT_GAP:
            continue
        found.append((kind, x_start + center_x, absolute_center_y))
    return found


def detect(pane: Any, box: Sequence[Any]) -> str | None:
    """Return ``single_circle``/``double_circle`` when pixel evidence agrees.

    The detector requires at least three of five correlated threshold views
    to produce one geometrically consistent candidate.  Conflicting classes,
    multiple candidates, or clipped/ambiguous contours return ``None``.
    Returning ``None`` means unobserved; it never means that the skill lacks a
    suffix.
    """

    normalized = _box_values(box)
    if normalized is None:
        return None

    try:
        import cv2

        import numpy as np

        image = np.asarray(pane.convert("RGB"))
        if image.ndim != 3 or image.shape[2] != 3:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    except (AttributeError, ImportError, TypeError, ValueError):
        return None

    observations: list[tuple[str, float, float]] = []
    for threshold in _THRESHOLDS:
        candidates = _candidates(gray, normalized, threshold)
        # A threshold that sees more than one plausible marker is ambiguous;
        # it contributes no vote rather than selecting by proximity or name.
        if len(candidates) == 1:
            observations.append(candidates[0])
    if len(observations) < _MIN_VOTES:
        return None

    class_counts = Counter(kind for kind, _, _ in observations)
    most_common = class_counts.most_common()
    if not most_common or most_common[0][1] < _MIN_VOTES:
        return None
    if len(most_common) > 1 and most_common[0][1] == most_common[1][1]:
        return None
    kind = most_common[0][0]

    same_class = [(x, y) for candidate_kind, x, y in observations if candidate_kind == kind]
    # Use a median anchor so a single high-threshold contour that grows by a
    # few pixels cannot move the accepted cluster.  The cluster must still
    # contain a strict majority of the required votes.
    median_x = sorted(x for x, _ in same_class)[len(same_class) // 2]
    median_y = sorted(y for _, y in same_class)[len(same_class) // 2]
    cluster = [
        (x, y)
        for x, y in same_class
        if abs(x - median_x) <= _POSITION_TOLERANCE and abs(y - median_y) <= _POSITION_TOLERANCE
    ]
    if len(cluster) < _MIN_VOTES:
        return None

    # A minority class with multiple votes is evidence that thresholding is
    # unstable, so keep the result conservative even when one class wins.
    if any(count >= 2 for other, count in class_counts.items() if other != kind):
        return None
    return kind


__all__ = ["detect"]
