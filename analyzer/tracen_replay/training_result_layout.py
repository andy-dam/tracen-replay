"""Source-anchored discovery of the applied training-result card layout.

The normal neural reader has a cheap colour probe for the result cards.  That
probe is useful when the cards are fully blue, but the applied cards can be
pink, red, grey, or covered by the friendship-training animation.  This
module provides the semantic geometry gate used before the reader schedules
result-card crops.

The detector deliberately uses only observations from one gameplay frame:

* the exact ``Training`` header at the top of the gameplay pane;
* one large ``SUCCESS``/``FAILURE`` banner (``SUCCES`` is the one-glyph
  clipped form seen during the animation); and
* at least two label-to-total pairs in the lower result-card area.

It does not interpret amounts, infer missing labels, sample neighbouring
frames, or use a recording-specific colour/value rule.  Returned geometry is
evidence for scheduling OCR only; semantic effects remain owned by the normal
result parser.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .layout import pane_box, place
from .ocr_confidence import confidence_percent


VERSION = 1
SCHEMA = "tracen-replay/training-result-layout-v1"

# On the PC pane. The header is pinned to the top left; the result banner and
# cards to the centre of the clear area.
HEADER_BOUNDS = (148.0, 0.0, 450.0, 80.0)
BANNER_BOUNDS = (250.0, 580.0, 850.0, 820.0)
RESULT_CARD_BOUNDS = (260.0, 760.0, 850.0, 1030.0)

# The label wording is the stable UI identity.  It is intentionally separate
# from the numeric total so a malformed or shifted number cannot authorize a
# result layout for a different card.
FIELD_LABELS = {
    "speed": frozenset(("speed",)),
    "stamina": frozenset(("stamina",)),
    "power": frozenset(("power",)),
    "guts": frozenset(("guts",)),
    "wit": frozenset(("wit",)),
    "skill_points": frozenset(("skill pts", "skill points")),
}
_LABEL_TO_FIELD = {
    label: field for field, labels in FIELD_LABELS.items() for label in labels
}
_TOTAL_RE = re.compile(r"^[A-Za-z]?\d{1,4}/\d{3,4}$")
_BANNER_RE = re.compile(r"^(?:SUCCESS!?|FAILURE!?|SUCCES!?)$", re.I)
_CLIPPED_BANNER_RE = re.compile(r"^SUCCES!?$", re.I)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # ``float(10**10000)`` raises OverflowError instead of producing an
    # infinity.  Layout evidence is untrusted OCR metadata, so malformed
    # numeric input must reject cleanly rather than escape the detector.
    try:
        value = float(value)
    except (OverflowError, ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    numbers = tuple(_finite(item) for item in value)
    if any(item is None for item in numbers):
        return None
    left, top, right, bottom = numbers  # type: ignore[misc]
    if not (left < right and top < bottom):
        return None
    pane = pane_box()
    if not (
        pane[0] <= left < right <= pane[2]
        and pane[1] <= top < bottom <= pane[3]
    ):
        return None
    return left, top, right, bottom


def _center(box: Sequence[float]) -> tuple[float, float]:
    return ((float(box[0]) + float(box[2])) / 2.0,
            (float(box[1]) + float(box[3])) / 2.0)


def _text(line: Mapping[str, Any]) -> str:
    return " ".join(str(line.get("text", "")).split()).strip()


def _confidence(line: Mapping[str, Any]) -> float | None:
    # Confidence is a percentage at the persisted OCR boundary.  Reusing the
    # shared validator rejects bools, non-finite values, and out-of-range
    # percentages consistently with the other source-layout readers.
    return confidence_percent(line.get("confidence"))


def _in_box(box: Sequence[float], bounds: Sequence[float]) -> bool:
    x, y = _center(box)
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def _line_copy(line: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(line))
    result["box"] = list(line["box"])
    return result


def _header(lines: Sequence[Mapping[str, Any]], supplied: Any) -> dict[str, Any] | None:
    if isinstance(supplied, str) and supplied.strip().casefold() == "training":
        candidates = [
            line for line in lines
            if _text(line).casefold() == "training"
            and (_confidence(line) or 0.0) >= 90.0
            and _in_box(line["box"], place(HEADER_BOUNDS, "tl"))
        ]
        if candidates:
            return _line_copy(max(candidates, key=lambda item: _confidence(item) or 0.0))
        # ``read_training`` can have a fixed-crop header that is not included
        # in its detector lines.  The supplied value is accepted only when
        # the caller also supplied a source line, keeping this helper tied to
        # source geometry rather than to a free-form string.
    candidates = [
        line for line in lines
        if _text(line).casefold() == "training"
        and (_confidence(line) or 0.0) >= 90.0
        and _box(line.get("box")) is not None
        and _in_box(line["box"], place(HEADER_BOUNDS, "tl"))
    ]
    if len(candidates) != 1:
        return None
    return _line_copy(candidates[0])


def _banner_candidates(lines: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}
    for line in lines:
        text = _text(line).upper()
        box = _box(line.get("box"))
        confidence = _confidence(line)
        if not _BANNER_RE.fullmatch(text):
            continue
        if box is None or not _in_box(box, place(BANNER_BOUNDS, "mc")):
            rejections["banner_geometry"] = rejections.get("banner_geometry", 0) + 1
            continue
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        # The preview Failure badge is a small label on the right.  Applied
        # result banners occupy the center and are materially taller.
        if width < 40.0 or height < 40.0:
            rejections["banner_too_small"] = rejections.get("banner_too_small", 0) + 1
            continue
        minimum = 95.0 if _CLIPPED_BANNER_RE.fullmatch(text) and text.startswith("SUCCES") else 90.0
        if confidence is None or confidence < minimum:
            rejections["banner_confidence"] = rejections.get("banner_confidence", 0) + 1
            continue
        observation = _line_copy(line)
        outcome = "success" if text.startswith("SUCCES") else "failure"
        observation.update(
            parsed_value=outcome.upper(),
            outcome=outcome,
            exact_banner=text in ("SUCCESS", "SUCCESS!", "FAILURE", "FAILURE!"),
        )
        candidates.append(observation)
    return candidates, rejections


def _field_labels(lines: Sequence[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for line in lines:
        box = _box(line.get("box"))
        confidence = _confidence(line)
        if box is None or confidence is None or confidence < 90.0:
            continue
        if not _in_box(box, place(RESULT_CARD_BOUNDS, "mc")):
            continue
        normalized = _text(line).casefold()
        field = _LABEL_TO_FIELD.get(normalized)
        if field is not None:
            result.append((field, _line_copy(line)))
    return result


def _totals(lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in lines:
        box = _box(line.get("box"))
        confidence = _confidence(line)
        if box is None or confidence is None or confidence < 90.0:
            continue
        if not _in_box(box, place(RESULT_CARD_BOUNDS, "mc")):
            continue
        compact = re.sub(r"\s+", "", _text(line))
        if _TOTAL_RE.fullmatch(compact) is None:
            continue
        observation = _line_copy(line)
        observation["normalized_text"] = compact
        result.append(observation)
    return result


def _pair_cards(
    labels: Sequence[tuple[str, dict[str, Any]]],
    totals: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    pairs: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}
    used_totals: set[int] = set()
    # Preserve detector order for deterministic proof output.  A label can
    # only bind to a total below it in the same card column; this prevents a
    # current sidebar number or a neighbouring card from being borrowed.
    for field, label in labels:
        label_box = label["box"]
        label_x, label_y = _center(label_box)
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for index, total in enumerate(totals):
            if index in used_totals:
                continue
            total_box = total["box"]
            total_x, total_y = _center(total_box)
            vertical_gap = total_y - label_y
            horizontal_gap = abs(total_x - label_x)
            if not (25.0 <= vertical_gap <= 85.0 and horizontal_gap <= 150.0):
                continue
            candidates.append((vertical_gap + horizontal_gap * 0.1, index, total))
        if not candidates:
            rejections["label_without_total"] = rejections.get("label_without_total", 0) + 1
            continue
        candidates.sort(key=lambda item: (item[0], item[1]))
        best_score, best_index, best_total = candidates[0]
        # Two totals in the same geometric slot are an ambiguity.  Keep the
        # field unresolved instead of choosing a value from OCR ordering.
        if len(candidates) > 1 and candidates[1][0] - best_score < 12.0:
            rejections["ambiguous_label_total"] = rejections.get("ambiguous_label_total", 0) + 1
            continue
        used_totals.add(best_index)
        total_box = best_total["box"]
        total_x, total_y = _center(total_box)
        pairs.append({
            "field": field,
            "label": label,
            "total": best_total,
            "geometry": {
                "label_center": [label_x, label_y],
                "total_center": [total_x, total_y],
                "vertical_gap_px": total_y - label_y,
                "horizontal_gap_px": total_x - label_x,
            },
        })
    return pairs, rejections


def detect_training_result_layout(
    lines: Any,
    *,
    header: Any = None,
    current_grid: Any = False,
    result_grid: Any = False,
) -> dict[str, Any]:
    """Detect a source-backed applied training-result layout.

    ``result_grid`` is an existing detector signal and is never trusted as
    proof by this function.  A positive result requires fresh same-frame
    header, banner, and card geometry.  ``current_grid=True`` always vetoes a
    positive result because a selectable training menu can coexist with
    partially faded result text during a transition.
    """

    if not isinstance(lines, list):
        return {
            "schema_version": SCHEMA,
            "version": VERSION,
            "status": "rejected",
            "result_grid": False,
            "rejections": {"lines_not_array": 1},
        }
    valid_lines: list[Mapping[str, Any]] = []
    rejections: dict[str, int] = {}
    for line in lines:
        if not isinstance(line, Mapping):
            rejections["line_not_object"] = rejections.get("line_not_object", 0) + 1
            continue
        if _box(line.get("box")) is None:
            rejections["line_geometry"] = rejections.get("line_geometry", 0) + 1
            continue
        if _confidence(line) is None:
            rejections["line_confidence"] = rejections.get("line_confidence", 0) + 1
            continue
        valid_lines.append(line)

    if current_grid is True:
        rejections["current_training_menu"] = 1
        return {
            "schema_version": SCHEMA,
            "version": VERSION,
            "status": "rejected",
            "result_grid": False,
            "rejections": rejections,
        }

    header_observation = _header(valid_lines, header)
    if header_observation is None:
        rejections["training_header"] = 1

    banners, banner_rejections = _banner_candidates(valid_lines)
    for key, value in banner_rejections.items():
        rejections[key] = rejections.get(key, 0) + value
    if len(banners) != 1:
        rejections["banner_count"] = 1

    labels = _field_labels(valid_lines)
    totals = _totals(valid_lines)
    pairs, pair_rejections = _pair_cards(labels, totals)
    for key, value in pair_rejections.items():
        rejections[key] = rejections.get(key, 0) + value

    distinct_fields = {pair["field"] for pair in pairs}
    if len(distinct_fields) < 2:
        rejections["insufficient_labeled_cards"] = 1

    # Some applied training sequences retain the committed result cards after
    # the large SUCCESS/FAILURE animation has left the pane.  The Shogi result
    # screen is an example: its Training header and four independent
    # label-to-ratio rows remain visible, while no outcome banner is present.
    # Treat this as a result layout with an unresolved outcome.  Requiring four
    # distinct stat cards and four ratios keeps a stray line or a menu preview
    # from becoming a committed result.  This branch is still vetoed above by
    # ``current_grid=True``.
    card_only = (
        header_observation is not None
        and len(banners) == 0
        and len(distinct_fields) >= 4
        and len(pairs) >= 4
        and len(totals) >= 4
    )

    if card_only:
        return {
            "schema_version": SCHEMA,
            "version": VERSION,
            "status": "recognized",
            "result_grid": True,
            "layout_kind": "committed_training_result_cards",
            "basis": "same_frame_training_result_card_geometry",
            "outcome_status": "banner_not_visible",
            "header": header_observation,
            "card_geometry": {
                "bounds": list(place(RESULT_CARD_BOUNDS, "mc")),
                "minimum_distinct_fields": 4,
                "rows": pairs,
            },
            "field_geometry": pairs,
            "observed": {
                "label_count": len(labels),
                "total_count": len(totals),
                "paired_card_count": len(pairs),
            },
            "rejections": {
                **rejections,
                "outcome_banner_not_visible": 1,
            },
        }

    if header_observation is None or len(banners) != 1 or len(distinct_fields) < 2:
        return {
            "schema_version": SCHEMA,
            "version": VERSION,
            "status": "rejected",
            "result_grid": False,
            "rejections": rejections,
            "observed": {
                "label_count": len(labels),
                "total_count": len(totals),
                "paired_card_count": len(pairs),
            },
        }

    banner = banners[0]
    return {
        "schema_version": SCHEMA,
        "version": VERSION,
        "status": "recognized",
        "result_grid": True,
        "basis": "same_frame_training_result_banner_and_card_geometry",
        "header": header_observation,
        "banner": banner,
        "card_geometry": {
            "bounds": list(place(RESULT_CARD_BOUNDS, "mc")),
            "minimum_distinct_fields": 2,
            "rows": pairs,
        },
        "field_geometry": pairs,
        "observed": {
            "label_count": len(labels),
            "total_count": len(totals),
            "paired_card_count": len(pairs),
        },
        "rejections": rejections,
    }


__all__ = [
    "SCHEMA",
    "VERSION",
    "detect_training_result_layout",
    "is_training_result_layout",
]
