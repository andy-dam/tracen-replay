"""Resolve race-reward quantity rows relative to visible section anchors.

Race-result layouts are not vertically fixed.  In particular, a result may
show an ``Items`` section lower on the page while the optional ``Bonus``
section is absent.  This module turns only source-visible, high-confidence
section headers and ``xN`` quantity lines into a per-frame layout.  It never
uses an expected quantity to locate a badge and it never treats a missing
optional section as a zero-quantity section.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


POLICY_NAME = "section_relative_quantity_row_v1"
MIN_CONFIDENCE = 97.0
SOURCE_WIDTH = 1920
SOURCE_HEIGHT = 1080
GAMEPLAY_LEFT = 148
GAMEPLAY_RIGHT = 958
HEADER_LEFT = 240
HEADER_RIGHT = 650
ROW_Y_TOLERANCE = 24
HORIZONTAL_SLOT_TOLERANCE = 58
MAX_ROW_GAP_FROM_TRANSLATED_SLOT = 48
QUANTITY_RE = re.compile(r"[x\N{MULTIPLICATION SIGN}]\s*(\d{1,6})")

# These baselines describe the old fixed layout and are used only to translate
# canonical slot x positions into the row anchored by a source header.  The
# emitted resolved boxes always come from the source line when a line exists.
HEADER_BASELINE_BOTTOMS = {"items": 612, "bonus": 778}
SECTION_ORDER = ("items", "bonus")

# Some race rewards expose one additional item and one additional bonus slot.
# These are only eligible when a high-confidence quantity line places them in
# the current source frame; they are never materialized from an absent section
# or from an expected quantity.
EXTRA_SLOT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "slot": 5,
        "group": "items",
        "group_index": 3,
        "slot_id": "items-3",
        "source_box": (500, 695, 584, 734),
        "full_box": (648, 695, 732, 734),
        "strategy": "section_anchor_dynamic",
        "dynamic_only": True,
    },
    {
        "slot": 6,
        "group": "bonus",
        "group_index": 2,
        "slot_id": "bonus-2",
        "source_box": (380, 855, 474, 897),
        "full_box": (528, 855, 622, 897),
        "strategy": "section_anchor_dynamic",
        "dynamic_only": True,
    },
)


class SectionLayoutError(ValueError):
    """Raised when source anchors are ambiguous or cross section boundaries."""


def policy() -> dict[str, Any]:
    """Return the declared policy copied into new refinement artifacts."""

    return {
        "name": POLICY_NAME,
        "version": 1,
        "header_confidence": MIN_CONFIDENCE,
        "quantity_confidence": MIN_CONFIDENCE,
        "quantity_pattern": r"^[x\u00d7]\s*\d{1,6}$",
        "row_y_tolerance_px": ROW_Y_TOLERANCE,
        "max_row_gap_from_header_derived_px": MAX_ROW_GAP_FROM_TRANSLATED_SLOT,
        "horizontal_slot_tolerance_px": HORIZONTAL_SLOT_TOLERANCE,
        "header_x_range": [HEADER_LEFT, HEADER_RIGHT],
        "source_bounds": {
            "width": SOURCE_WIDTH,
            "height": SOURCE_HEIGHT,
            "gameplay_pane": [GAMEPLAY_LEFT, 0, GAMEPLAY_RIGHT, SOURCE_HEIGHT],
        },
        "required_context": ["race_result", "fans", "fans_gained"],
        "sections": {
            "items": {"required": False, "slots": 3, "max_slots": 4},
            "bonus": {"required": False, "slots": 2, "max_slots": 3, "optional": True},
        },
        "absent_section_behavior": "no_slots",
        "expected_quantity_used_for_layout": False,
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        return None
    numbers = [_number(part) for part in value]
    if any(part is None for part in numbers):
        return None
    left, top, right, bottom = (float(part) for part in numbers)
    if left < GAMEPLAY_LEFT or top < 0 or right > GAMEPLAY_RIGHT or bottom > SOURCE_HEIGHT or left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def _line_text(line: Mapping[str, Any]) -> str:
    return str(line.get("text", "")).strip()


def _confidence(line: Mapping[str, Any]) -> float:
    value = _number(line.get("confidence", 0))
    return value if value is not None else 0.0


def _quantity(line: Mapping[str, Any]) -> int | None:
    match = QUANTITY_RE.fullmatch(_line_text(line))
    return int(match[1]) if match else None


def _center(box: Sequence[float]) -> tuple[float, float]:
    return ((float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2)


def _slot_center(spec: Mapping[str, Any]) -> tuple[float, float]:
    box = _box(spec.get("full_box"))
    if box is None:
        raise SectionLayoutError("canonical slot geometry is invalid")
    return _center(box)


def _translated_box(
    spec: Mapping[str, Any],
    header_bottom: float,
    *,
    row_center_y: float | None = None,
) -> tuple[int, int, int, int]:
    box = _box(spec.get("full_box"))
    if box is None:
        raise SectionLayoutError("canonical slot geometry is invalid")
    group = str(spec.get("group", "")).casefold()
    baseline = HEADER_BASELINE_BOTTOMS[group]
    if row_center_y is None:
        delta = header_bottom - baseline
    else:
        # Keep each slot's canonical height while following the observed row.
        # The row was accepted only after being tied to this header, so this
        # avoids putting a fallback crop several pixels away from a rendered
        # badge whose OCR box has a different height.
        canonical_center_y = _center(box)[1]
        delta = row_center_y - canonical_center_y
    values = (box[0], box[1] + delta, box[2], box[3] + delta)
    if values[0] < 0 or values[1] < 0 or values[2] > SOURCE_WIDTH or values[3] > SOURCE_HEIGHT or values[0] >= values[2] or values[1] >= values[3]:
        raise SectionLayoutError("translated slot geometry leaves source bounds")
    return tuple(int(round(value)) for value in values)


def _source_box_from_full(box: Sequence[Any], x_offset: int = 148, padding: int = 2) -> tuple[int, int, int, int] | None:
    parsed = _box(box)
    if parsed is None:
        return None
    left, top, right, bottom = parsed
    values = (max(0, left - x_offset - padding), max(0, top - padding),
              min(810, right - x_offset + padding), min(SOURCE_HEIGHT, bottom + padding))
    if values[0] >= values[2] or values[1] >= values[3]:
        return None
    return tuple(int(round(value)) for value in values)


def _header_candidates(raw: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in raw.get("lines", []):
        if not isinstance(line, Mapping) or _line_text(line).casefold() != name.casefold():
            continue
        if _confidence(line) < MIN_CONFIDENCE:
            continue
        box = _box(line.get("box"))
        if box is None or box[0] < HEADER_LEFT or box[2] > HEADER_RIGHT:
            # OCR from the profile/sidebar or a malformed box cannot anchor
            # the gameplay result pane.  It is ignored as unrelated text.
            continue
        result.append({"text": _line_text(line), "confidence": _confidence(line), "box": list(box)})
    if len(result) > 1:
        raise SectionLayoutError(f"multiple {name} headers")
    return result


def _quantity_candidates(
    raw: Mapping[str, Any],
    header: Mapping[str, Any],
    next_top: float | None,
    expected_row_y: float,
) -> list[dict[str, Any]]:
    header_box = _box(header["box"])
    if header_box is None:
        raise SectionLayoutError("resolved header geometry is invalid")
    header_bottom = header_box[3]
    result: list[dict[str, Any]] = []
    for line in raw.get("lines", []):
        if not isinstance(line, Mapping) or _confidence(line) < MIN_CONFIDENCE:
            continue
        quantity = _quantity(line)
        if quantity is None:
            continue
        box = _box(line.get("box"))
        if box is None:
            # A quantity-like line outside the gameplay pane is unrelated UI;
            # it must not become a section slot or a row anchor.
            continue
        center_y = _center(box)[1]
        if center_y <= header_bottom:
            continue
        if next_top is not None and (center_y >= next_top or box[3] > next_top):
            continue
        if abs(center_y - expected_row_y) > MAX_ROW_GAP_FROM_TRANSLATED_SLOT:
            continue
        result.append({
            "text": _line_text(line),
            "quantity": quantity,
            "confidence": _confidence(line),
            "box": list(box),
        })
    return result


def _single_row(lines: list[dict[str, Any]], section: str) -> tuple[list[dict[str, Any]], float] | None:
    if not lines:
        return None
    centers = [_center(line["box"])[1] for line in lines]
    row_y = sum(centers) / len(centers)
    # Compare the complete span rather than deviation from the mean.  Two
    # adjacent rows can otherwise straddle the mean and look like one row
    # (for example centers 860 and 900 with a 24 px per-line tolerance).
    if max(centers) - min(centers) > ROW_Y_TOLERANCE:
        raise SectionLayoutError(f"multiple quantity rows in {section} section")
    return sorted(lines, key=lambda line: _center(line["box"])[0]), row_y


def _map_lines_to_slots(lines: list[dict[str, Any]], specs: list[Mapping[str, Any]], section: str) -> dict[str, dict[str, Any]]:
    by_slot: dict[str, dict[str, Any]] = {}
    for line in lines:
        line_center_x = _center(line["box"])[0]
        choices = []
        for spec in specs:
            slot_id = str(spec.get("slot_id"))
            slot_center_x = _slot_center(spec)[0]
            choices.append((abs(line_center_x - slot_center_x), slot_id))
        distance, slot_id = min(choices)
        if distance > HORIZONTAL_SLOT_TOLERANCE:
            raise SectionLayoutError(f"quantity line crosses {section} slot geometry")
        if slot_id in by_slot:
            raise SectionLayoutError(f"duplicate quantity line for {slot_id}")
        by_slot[slot_id] = line
    return by_slot


def _expected_row_center(specs: Sequence[Mapping[str, Any]], header_bottom: float) -> float:
    """Return the row center implied by this header and canonical slots."""

    if not specs:
        raise SectionLayoutError("section has no canonical slots")
    group = str(specs[0].get("group", "")).casefold()
    baseline_header_bottom = HEADER_BASELINE_BOTTOMS.get(group)
    if baseline_header_bottom is None:
        raise SectionLayoutError("section has no row baseline")
    canonical_row_center = sum(_slot_center(spec)[1] for spec in specs) / len(specs)
    return header_bottom + (canonical_row_center - baseline_header_bottom)


def augment_slot_specs(slot_specs: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    """Add bounded dynamic-only slots while preserving the caller's legacy set."""

    existing = {str(spec.get("slot_id")) for spec in slot_specs}
    result = list(slot_specs)
    result.extend(spec for spec in EXTRA_SLOT_SPECS if spec["slot_id"] not in existing)
    return tuple(result)


def resolve(raw: Mapping[str, Any], row: Mapping[str, Any], slot_specs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Resolve visible section rows into canonical slot identities.

    ``None`` means no usable section/quantity row is source-visible.  A
    :class:`SectionLayoutError` means the source contains an ambiguous or
    cross-section layout and must be rejected rather than guessed.
    """

    if not isinstance(raw, Mapping) or not isinstance(row, Mapping):
        return None
    if row.get("screen") != "race_result":
        return None
    facts = row.get("facts")
    if not isinstance(facts, Mapping) or type(facts.get("fans")) is not int or type(facts.get("fans_gained")) is not int:
        return None
    if not isinstance(facts.get("visible_item_quantities"), list) or facts.get("item_rewards_complete") is not False:
        return None
    all_slot_specs = augment_slot_specs(slot_specs)
    specs_by_group: dict[str, list[Mapping[str, Any]]] = {name: [] for name in SECTION_ORDER}
    for spec in all_slot_specs:
        group = str(spec.get("group", "")).casefold()
        if group in specs_by_group:
            specs_by_group[group].append(spec)
    if any(not specs_by_group[group] for group in SECTION_ORDER):
        raise SectionLayoutError("canonical section slot geometry is incomplete")

    headers: dict[str, dict[str, Any]] = {}
    for name in SECTION_ORDER:
        candidates = _header_candidates(raw, name)
        if candidates:
            headers[name] = candidates[0]
    if "items" in headers and "bonus" in headers:
        if _box(headers["items"]["box"])[1] > _box(headers["bonus"]["box"])[1]:
            raise SectionLayoutError("section headers are out of order")

    ordered = sorted(headers.items(), key=lambda item: _box(item[1]["box"])[1])
    sections: dict[str, dict[str, Any]] = {}
    resolved_slots: dict[str, dict[str, Any]] = {}
    for index, (name, header) in enumerate(ordered):
        header_box = _box(header["box"])
        if header_box is None:
            raise SectionLayoutError("header geometry is invalid")
        next_top = None
        if index + 1 < len(ordered):
            next_top = _box(ordered[index + 1][1]["box"])[1]
            if next_top <= header_box[3]:
                raise SectionLayoutError("section anchors overlap")
        expected_row_y = _expected_row_center(specs_by_group[name], header_box[3])
        lines = _quantity_candidates(raw, header, next_top, expected_row_y)
        row_result = _single_row(lines, name)
        section = {
            "name": name,
            "header": header,
            "quantity_row": None,
            "slots": [],
            "status": "no_quantity_row",
        }
        if row_result is None:
            sections[name] = section
            continue
        row_lines, row_y = row_result
        line_by_slot = _map_lines_to_slots(row_lines, specs_by_group[name], name)
        section["status"] = "resolved"
        section["quantity_row"] = {
            "center_y": row_y,
            "line_count": len(row_lines),
            "line_boxes": [list(line["box"]) for line in row_lines],
        }
        for spec in sorted(specs_by_group[name], key=lambda item: int(item.get("group_index", 0))):
            slot_id = str(spec["slot_id"])
            line = line_by_slot.get(slot_id)
            if line is None and spec.get("dynamic_only") is True:
                # An extra slot is valid only when this frame's source pixels
                # supplied its own high-confidence placement line.
                continue
            translated = _translated_box(spec, header_box[3], row_center_y=row_y)
            if next_top is not None and translated[3] > next_top:
                raise SectionLayoutError(f"translated {name} slot crosses the next section")
            if line is not None:
                resolved_full_box = [int(round(part)) for part in line["box"]]
                source_box = _source_box_from_full(resolved_full_box)
                if source_box is None:
                    raise SectionLayoutError(f"resolved quantity box for {slot_id} leaves gameplay bounds")
                resolution = "section_anchor_line"
            else:
                resolved_full_box = list(translated)
                source_box = _source_box_from_full(resolved_full_box)
                if source_box is None:
                    raise SectionLayoutError(f"translated quantity box for {slot_id} leaves gameplay bounds")
                resolution = "section_anchor_fixed_slot"
            slot = {
                "slot_id": slot_id,
                "group": name,
                "group_index": int(spec["group_index"]),
                "slot": spec.get("slot"),
                "strategy": spec.get("strategy"),
                "canonical_source_box": list(spec.get("source_box", ())),
                "canonical_full_box": list(spec["full_box"]),
                "resolved_full_box": resolved_full_box,
                "resolved_source_box": list(source_box),
                "detected_line": line,
                "resolution": resolution,
            }
            section["slots"].append(slot)
            resolved_slots[slot_id] = slot
        sections[name] = section

    if not resolved_slots:
        return None
    return {
        "policy": policy(),
        "screen": "race_result",
        "race_identity": {"fans": facts["fans"], "fans_gained": facts["fans_gained"]},
        "sections": sections,
        "resolved_slots": resolved_slots,
        "visible_sections": [name for name in SECTION_ORDER if name in headers],
        "slots": sorted(resolved_slots.values(), key=lambda item: (item["group"], item["group_index"])),
    }


def slot(layout: Mapping[str, Any] | None, slot_id: str) -> Mapping[str, Any] | None:
    if not isinstance(layout, Mapping):
        return None
    slots = layout.get("resolved_slots")
    if not isinstance(slots, Mapping):
        return None
    value = slots.get(slot_id)
    return value if isinstance(value, Mapping) else None


def is_policy(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("name") == POLICY_NAME and value == policy()
