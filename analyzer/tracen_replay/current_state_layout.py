"""Source-anchored detection of the committed main-stat panel.

The normal reader historically used a few blue pixels at the top of the
stat strip as its ``current_grid`` gate.  The career panel can be pink,
translucent, or otherwise lose those pixels while all of its labeled values
remain visible.  This module supplies a geometry-only replacement signal for
that case.

The detector is intentionally separate from numeric parsing.  It proves that
one source frame contains the main-stat bar by matching each label to a value
and cap in the same column.  The reader still obtains the numbers from its
fixed, source-local crops.  No totals, expected gains, timestamps, or
cross-frame state are used here.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .ocr_confidence import confidence_percent


VERSION = 1
SCHEMA = "tracen-replay/current-state-layout-v1"

PANE_BOUNDS = (148.0, 0.0, 958.0, 1080.0)
HEADER_BOUNDS = (148.0, 0.0, 450.0, 80.0)
# The career stat strip is below the dialogue/performance sidebar and above
# the action buttons.  Keeping the y-band distinct from result cards is what
# prevents a result screen from being classified as a current career bar.
# The panel shifts down during the final-race transition.  Keep label bounds
# separate from the full evidence band: a result card's labels begin below
# the label band, while the shifted current panel's value/cap rows extend
# below 785.
CURRENT_LABEL_BOUNDS = (250.0, 650.0, 850.0, 790.0)
CURRENT_PANEL_BOUNDS = (250.0, 650.0, 850.0, 835.0)

FIELDS = ("speed", "stamina", "power", "guts", "wit", "skill_points")
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
_PLAIN_NUMBER_RE = re.compile(r"^\d{1,4}$")
# Rank glyphs occasionally touch the Wit value (for example ``U1243``).  The
# detector records this as a numeric candidate for geometry only; fixed OCR
# crops remain responsible for deciding whether the value itself is usable.
_RANKED_NUMBER_RE = re.compile(r"^[A-Za-z]{1,2}\d{1,5}$")
_CAP_RE = re.compile(r"^(?:\d{1,4}\s*)?[/VYlI]\s*\d{1,4}$", re.I)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = tuple(_finite(item) for item in value)
    if any(item is None for item in values):
        return None
    left, top, right, bottom = values  # type: ignore[misc]
    if not (left < right and top < bottom):
        return None
    if not (
        PANE_BOUNDS[0] <= left < right <= PANE_BOUNDS[2]
        and PANE_BOUNDS[1] <= top < bottom <= PANE_BOUNDS[3]
    ):
        return None
    return left, top, right, bottom


def _center(box: Sequence[float]) -> tuple[float, float]:
    return ((float(box[0]) + float(box[2])) / 2.0,
            (float(box[1]) + float(box[3])) / 2.0)


def _text(line: Mapping[str, Any]) -> str:
    return " ".join(str(line.get("text", "")).split()).strip()


def _confidence(line: Mapping[str, Any]) -> float | None:
    return confidence_percent(line.get("confidence"))


def _in_bounds(box: Sequence[float], bounds: Sequence[float]) -> bool:
    x, y = _center(box)
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def _copy(line: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(line))
    result["box"] = list(line["box"])
    return result


def _header(lines: Sequence[Mapping[str, Any]], supplied: Any) -> dict[str, Any] | None:
    """Return a source line for a known career/training header.

    A caller-provided string is only a hint.  Positive proof always includes
    the actual same-frame OCR line, which prevents a free-form header from
    authorizing a panel on its own.
    """

    allowed = {"career", "training"}
    candidate_text = supplied.strip().casefold() if isinstance(supplied, str) else None
    candidates = []
    for line in lines:
        box = _box(line.get("box"))
        confidence = _confidence(line)
        text = _text(line).casefold()
        if (
            box is not None
            and confidence is not None
            and confidence >= 90.0
            and text in allowed
            and _in_bounds(box, HEADER_BOUNDS)
            and (candidate_text is None or text == candidate_text)
        ):
            candidates.append(line)
    if len(candidates) != 1:
        return None
    return _copy(candidates[0])


def _labels(lines: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {field: [] for field in FIELDS}
    for line in lines:
        box = _box(line.get("box"))
        confidence = _confidence(line)
        if box is None or confidence is None or confidence < 90.0:
            continue
        if not _in_bounds(box, CURRENT_LABEL_BOUNDS):
            continue
        field = _LABEL_TO_FIELD.get(_text(line).casefold())
        if field is not None:
            result[field].append(_copy(line))
    return result


def _value_kind(text: str) -> str | None:
    if _PLAIN_NUMBER_RE.fullmatch(text):
        return "plain_number"
    if _RANKED_NUMBER_RE.fullmatch(text):
        return "rank_prefixed_number"
    return None


def _candidate_lines(
    lines: Sequence[Mapping[str, Any]],
    label: Mapping[str, Any],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    label_box = label["box"]
    label_x, label_y = _center(label_box)
    candidates = []
    for line in lines:
        box = _box(line.get("box"))
        confidence = _confidence(line)
        if box is None or confidence is None:
            continue
        if not _in_bounds(box, CURRENT_PANEL_BOUNDS):
            continue
        x, y = _center(box)
        # Values/caps stay in the same column and below their label.  This
        # source geometry prevents the nearby goal/performance/sidebar numbers
        # from becoming a stat-bar proof.
        if abs(x - label_x) > 65.0 or not label_y + 8.0 <= y <= label_y + 70.0:
            continue
        text = _text(line)
        if kind == "value":
            if _value_kind(text) is None:
                continue
            # A rank glyph touching a value is a known detector degradation;
            # permit it to establish the row geometry at the conservative
            # source threshold, then let the fixed crop reader resolve the
            # number.  Plain values and all caps retain the normal 90 floor.
            minimum_confidence = 80.0 if _RANKED_NUMBER_RE.fullmatch(text) else 90.0
            if confidence < minimum_confidence:
                continue
        elif kind == "cap":
            if confidence < 90.0:
                continue
            if _CAP_RE.fullmatch(text) is None:
                continue
        else:
            raise ValueError(f"Unknown current-state candidate kind: {kind}")
        candidates.append(_copy(line))
    return candidates


def _best_below(
    candidates: Sequence[Mapping[str, Any]],
    *,
    label: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    label_x, label_y = _center(label["box"])
    scored = []
    for line in candidates:
        x, y = _center(line["box"])
        if previous is not None:
            _previous_x, previous_y = _center(previous["box"])
            if y <= previous_y:
                continue
        scored.append((y - label_y + abs(x - label_x) * 0.15, line))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    if len(scored) > 1 and scored[1][0] - scored[0][0] < 8.0:
        return None
    return scored[0][1]


def detect_current_state_layout(
    lines: Any,
    *,
    header: Any = None,
    current_grid: Any = False,
    result_layout: Any = None,
) -> dict[str, Any]:
    """Detect one source frame's committed main-stat layout.

    ``current_grid`` is an existing colour signal and is never trusted as
    proof.  Likewise, a positive result-layout proof vetoes this detector;
    result cards must continue through the result parser.  The positive path
    requires all five main-stat columns plus Skill Pts with same-frame values,
    and at least four explicit cap rows.  Numeric values are intentionally not
    returned by this detector.
    """

    rejected = {
        "schema_version": SCHEMA,
        "version": VERSION,
        "status": "rejected",
        "current_grid": False,
        "rejections": {},
    }
    if not isinstance(lines, list):
        rejected["rejections"]["lines_not_array"] = 1
        return rejected

    valid_lines: list[Mapping[str, Any]] = []
    for line in lines:
        if not isinstance(line, Mapping):
            rejected["rejections"]["line_not_object"] = rejected["rejections"].get("line_not_object", 0) + 1
            continue
        if _box(line.get("box")) is None:
            rejected["rejections"]["line_geometry"] = rejected["rejections"].get("line_geometry", 0) + 1
            continue
        if _confidence(line) is None:
            rejected["rejections"]["line_confidence"] = rejected["rejections"].get("line_confidence", 0) + 1
            continue
        valid_lines.append(line)

    if isinstance(result_layout, Mapping) and result_layout.get("result_grid") is True:
        rejected["rejections"]["applied_result_layout"] = 1
        return rejected

    header_observation = _header(valid_lines, header)
    if header_observation is None:
        rejected["rejections"]["career_training_header"] = 1

    labels = _labels(valid_lines)
    rows = []
    for field in FIELDS:
        field_labels = labels[field]
        if len(field_labels) != 1:
            if not field_labels:
                rejected["rejections"][f"missing_{field}_label"] = 1
            else:
                rejected["rejections"][f"ambiguous_{field}_label"] = len(field_labels)
            continue
        label = field_labels[0]
        value = _best_below(_candidate_lines(valid_lines, label, kind="value"), label=label)
        if value is None:
            rejected["rejections"][f"missing_{field}_value"] = 1
            continue
        cap = None
        if field != "skill_points":
            cap = _best_below(
                _candidate_lines(valid_lines, label, kind="cap"),
                label=label,
                previous=value,
            )
            if cap is None:
                rejected["rejections"][f"missing_{field}_cap"] = 1
                continue
        rows.append({
            "field": field,
            "label": label,
            "value": value,
            "cap": cap,
            "geometry": {
                "label_center": list(_center(label["box"])),
                "value_center": list(_center(value["box"])),
                "cap_center": list(_center(cap["box"])) if cap is not None else None,
            },
        })

    stat_rows = [row for row in rows if row["field"] != "skill_points"]
    if len(rows) != len(FIELDS):
        rejected["rejections"]["incomplete_current_panel"] = 1
        rejected["observed"] = {
            "label_count": sum(len(items) for items in labels.values()),
            "value_cap_row_count": len(rows),
            "stat_row_count": len(stat_rows),
        }
        return rejected
    if len(stat_rows) < 4:
        rejected["rejections"]["insufficient_capped_stat_rows"] = 1
        return rejected

    # A complete, source-local bar remains useful when the tiny top-left
    # header was missed by OCR (the late final-race frame is an example).
    # The panel geometry itself is the identity proof in that case.  A known
    # result-layout proof still vetoes it above, so this fallback cannot turn
    # applied result cards into current state.
    header_status = "same_frame_header" if header_observation is not None else "header_not_visible"

    return {
        "schema_version": SCHEMA,
        "version": VERSION,
        "status": "recognized",
        "current_grid": True,
        "basis": "same_frame_current_stat_bar_labels_values_and_caps",
        "header": header_observation,
        "header_status": header_status,
        "panel_geometry": {
            "bounds": list(CURRENT_PANEL_BOUNDS),
            "minimum_capped_stat_rows": 4,
            "rows": rows,
        },
        "field_geometry": rows,
        "observed": {
            "label_count": sum(len(items) for items in labels.values()),
            "value_cap_row_count": len(rows),
            "stat_row_count": len(stat_rows),
            "colour_signal": current_grid is True,
        },
        "rejections": rejected["rejections"],
    }


def is_current_state_layout(lines: Any, **kwargs: Any) -> bool:
    """Return whether :func:`detect_current_state_layout` recognizes a panel."""

    return detect_current_state_layout(lines, **kwargs).get("current_grid") is True


__all__ = [
    "SCHEMA",
    "VERSION",
    "detect_current_state_layout",
    "is_current_state_layout",
]
