"""Source-bound refinement for quantities on race-result item badges.

The general detector can miss a badge or return a weak reading while the
result screen is still visible.  This module re-reads only the fixed badge
regions from gameplay pixels.  It emits a provenance-bound artifact for a
later pipeline integration; it does not change the general vision parser or
infer item identity.

Views at different scales are correlated views of one source frame.  A slot
is accepted only when the same parsed quantity is observed at the production
confidence threshold on at least two distinct source timestamps.  The
production parser currently accepts 97 confidence, so this refinement keeps
that gate even though an exploratory probe may report candidates above 95.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image

from .refine_contrast import fingerprint
from .race_section_layout import (
    HEADER_LEFT as SECTION_HEADER_LEFT,
    HEADER_RIGHT as SECTION_HEADER_RIGHT,
    SectionLayoutError,
    augment_slot_specs,
    is_policy as is_section_layout_policy,
    policy as section_layout_policy,
    resolve as resolve_section_layout,
    slot as section_layout_slot,
)
from .vision import NeuralReader, parse


MIN_CONFIDENCE = 97.0
MIN_DISTINCT_TIMESTAMPS = 2
MAX_GROUP_SPAN_MS = 2_000
GAMEPLAY_X_OFFSET = 148
SCALE_FACTORS = (1, 2, 3)
RESAMPLE_NAME = "LANCZOS"
OUTPUT_DIRNAME = "race-quantity-refinement"
OBSERVATION_DIRNAME = "observations"
REJECTED_DIRNAME = "rejected"
CAPTURE_MANIFEST_NAME = "capture.json"
DETECTOR_PADDING = 2
FALLBACK_INTERIOR_INSET = 3
FALLBACK_FOREGROUND_RATIO = 0.04
FALLBACK_LUMA_THRESHOLD = 225
FALLBACK_SATURATION_THRESHOLD = 24
FALLBACK_QUANTITY_TEXT_RELATIVE_BOX = (0.50, 0.10, 0.95, 1.00)

# A detector-backed slot may be absent from the cached OCR even though its
# badge is visible.  New artifacts record this policy so strict validation can
# distinguish them from older artifacts, whose detector-missing slots were
# intentionally left without observations.
FIXED_SLOT_FALLBACK_POLICY = {
    "detector_fallback": "fixed_slot_geometry",
    "fallback_requires_layout_guard": True,
    "fallback_requires_pixel_guard": True,
    "fallback_quantity_crop": "lower_right_relative_box",
    "fallback_quantity_crop_relative_box": list(FALLBACK_QUANTITY_TEXT_RELATIVE_BOX),
}

# This policy is repeated in every generated artifact so a later integration
# cannot accidentally treat the exploratory 95-confidence probe as a
# production decision.  Scale views remain correlated observations of one
# source frame and never satisfy the frame-count gate by themselves.
REFINEMENT_POLICY = {
    "production_minimum_confidence": MIN_CONFIDENCE,
    "exploratory_minimum_confidence": 95.0,
    "exploratory_95_results_are_not_accepted": True,
    "minimum_distinct_source_timestamps": MIN_DISTINCT_TIMESTAMPS,
    "scaled_views_count_as_independent_frames": False,
    "item_identity_verified": False,
    "list_complete": False,
}

# Stable badge slots are deliberately read twice when the section-relative
# layout is used: once with the complete badge retained for longer quantities,
# and once with a lower-right quantity-focused crop that avoids the artwork
# behind short quantities.  The two views are correlated observations of the
# same source frame; the confidence and distinct-PTS gates still apply.
QUANTITY_CROP_VARIANT_POLICY = {
    "name": "stable_badge_quantity_focus_v1",
    "version": 1,
    "variants": {
        "badge": {
            "role": "broad",
            "relative_box": [0.0, 0.0, 1.0, 1.0],
        },
        "quantity_focus": {
            "role": "focused",
            "relative_box": [0.39, 0.09, 0.89, 1.0],
        },
    },
    "broad_variant": "badge",
    "focused_variant": "quantity_focus",
    "focused_variant_max_digits": 1,
    "multi_digit_requires_broad_support": True,
    "scaled_views_count_as_independent_frames": False,
}
QUANTITY_FOCUS_RELATIVE_BOX = (0.39, 0.09, 0.89, 1.0)

# Boxes in the source gameplay PNG (810x1080).  ``full_box`` is the same
# location in the original 1920x1080 coordinate system used by neural.py and
# the parsed row facts.  Keeping both coordinate systems explicit prevents an
# item and a bonus badge from collapsing into one position.
SLOT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "slot": 0,
        "group": "items",
        "group_index": 0,
        "slot_id": "items-0",
        "source_box": (170, 695, 238, 734),
        "full_box": (318, 695, 386, 734),
        "strategy": "stable_badge_geometry",
    },
    {
        "slot": 1,
        "group": "items",
        "group_index": 1,
        "slot_id": "items-1",
        "source_box": (270, 695, 350, 734),
        "full_box": (418, 695, 498, 734),
        "strategy": "stable_badge_geometry",
    },
    {
        "slot": 2,
        "group": "items",
        "group_index": 2,
        "slot_id": "items-2",
        "source_box": (385, 695, 466, 734),
        "full_box": (533, 695, 614, 734),
        "strategy": "detector_box_padding_2",
    },
    {
        "slot": 3,
        "group": "bonus",
        "group_index": 0,
        "slot_id": "bonus-0",
        "source_box": (160, 855, 240, 897),
        "full_box": (308, 855, 388, 897),
        "strategy": "stable_badge_geometry",
    },
    {
        "slot": 4,
        "group": "bonus",
        "group_index": 1,
        "slot_id": "bonus-1",
        "source_box": (270, 855, 355, 897),
        "full_box": (418, 855, 503, 897),
        "strategy": "stable_badge_geometry",
    },
)

SLOT_BY_ID = {spec["slot_id"]: spec for spec in SLOT_SPECS}
SECTION_SLOT_SPECS = augment_slot_specs(SLOT_SPECS)
SECTION_SLOT_BY_ID = {spec["slot_id"]: spec for spec in SECTION_SLOT_SPECS}
ITEMS_HEADER_BOX = (250, 500, 830, 700)
BONUS_HEADER_BOX = (250, 700, 830, 840)
SECTION_LAYOUT_POLICY = section_layout_policy()
LEGACY_HEADER_BASELINES = {"Items": 612, "Bonus": 778}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _typed_timestamp(value: Any) -> bool:
    return type(value) is int and value >= 0


def _typed_confidence(value: Any) -> bool:
    return (type(value) is int or type(value) is float) and 0 <= float(value) <= 100


def _sha256_value(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))


def _mapping_equal(first: Any, second: Any) -> bool:
    """Compare JSON-shaped metadata without allowing mapping order to matter."""

    return _canonical(first) == _canonical(second)


def _center(box: Sequence[Any]) -> tuple[float, float]:
    return ((float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2)


def _within_box(box: Sequence[Any], container: Sequence[Any]) -> bool:
    try:
        x, y = _center(box)
    except (TypeError, ValueError, IndexError):
        return False
    return container[0] <= x <= container[2] and container[1] <= y <= container[3]


def _lines(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [line for line in raw.get("lines", []) if isinstance(line, Mapping)]


def _header(raw: Mapping[str, Any], text: str, box: Sequence[int]) -> Mapping[str, Any] | None:
    matches = [
        line
        for line in _lines(raw)
        if str(line.get("text", "")).strip().casefold() == text.casefold()
        and float(line.get("confidence", 0)) >= MIN_CONFIDENCE
        and _within_box(line.get("box", ()), box)
    ]
    return max(matches, key=lambda line: float(line.get("confidence", 0)), default=None)


def layout_guard(
    raw: Mapping[str, Any],
    row: Mapping[str, Any] | None = None,
    *,
    section_anchor_policy: bool = False,
) -> bool:
    """Require a usable race-result layout.

    The default is the legacy fixed-layout guard used by existing artifacts:
    both ``Items`` and ``Bonus`` headers must be visible.  New generation can
    opt into the section-relative policy, where a source-visible ``Items``
    section is sufficient and the optional ``Bonus`` section contributes no
    slots when absent.  The parsed ``visible_item_quantities`` list remains a
    partial observation in either mode.
    """

    if row is None:
        try:
            row = parse(raw)
        except (KeyError, TypeError, ValueError, AttributeError):
            return False
    if section_anchor_policy:
        try:
            return resolve_section_layout(raw, row, SLOT_SPECS) is not None
        except (SectionLayoutError, KeyError, TypeError, ValueError):
            return False
    if row.get("screen") != "race_result":
        return False
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return False
    if not isinstance(facts.get("visible_item_quantities"), list):
        return False
    if facts.get("item_rewards_complete") is not False:
        return False
    return _header(raw, "Items", ITEMS_HEADER_BOX) is not None and _header(raw, "Bonus", BONUS_HEADER_BOX) is not None


def _legacy_fixed_geometry_usable(raw: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    """Return whether the old fixed crops are source-compatible for a frame.

    ``layout_guard`` intentionally remains broad for validating historical
    artifacts.  Generation uses this narrower check before selecting that
    legacy path, so a shifted two-section result is resolved by the declared
    section-relative policy instead of silently reading the old y positions.
    """

    if not layout_guard(raw, row):
        return False
    for name, region in (("Items", ITEMS_HEADER_BOX), ("Bonus", BONUS_HEADER_BOX)):
        header = _header(raw, name, region)
        if header is None or not _within_box(
            header.get("box", ()),
            (SECTION_HEADER_LEFT, region[1], SECTION_HEADER_RIGHT, region[3]),
        ):
            return False
        try:
            if abs(float(header["box"][3]) - LEGACY_HEADER_BASELINES[name]) > 8:
                return False
        except (TypeError, ValueError, IndexError):
            return False

    # A high-confidence quantity line in the old result bands must belong to
    # one of the canonical slots.  This prevents a shifted row from being
    # treated as fixed merely because both broad header windows still match.
    for line in _lines(raw):
        try:
            confidence = float(line.get("confidence", 0))
            center_y = _center(line.get("box", ()))[1]
        except (TypeError, ValueError, IndexError):
            if _quantity(line.get("text")) is not None:
                return False
            continue
        if confidence < MIN_CONFIDENCE or _quantity(line.get("text")) is None:
            continue
        if 500 <= center_y <= 940 and not any(
            _within_box(line.get("box", ()), spec["full_box"]) for spec in SLOT_SPECS
        ):
            return False
    return True


def _quantity(text: Any) -> int | None:
    match = re.fullmatch(r"[x×]\s*(\d{1,6})", str(text or "").strip(), re.IGNORECASE)
    return int(match[1]) if match else None


def _slot_for_box(box: Sequence[Any]) -> str | None:
    for spec in SLOT_SPECS:
        if _within_box(box, spec["full_box"]):
            return spec["slot_id"]
    return None


def _present_slots(row: Mapping[str, Any], section_layout: Mapping[str, Any] | None = None) -> set[str]:
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return set()
    result = set()
    for entry in facts.get("visible_item_quantities", []):
        if isinstance(entry, Mapping) and isinstance(entry.get("box"), Sequence):
            if section_layout is not None:
                resolved = section_layout.get("resolved_slots")
                if isinstance(resolved, Mapping):
                    for slot_id, slot in resolved.items():
                        if isinstance(slot, Mapping) and _same_position(entry.get("box", ()), slot.get("resolved_full_box", ())):
                            result.add(str(slot_id))
                    continue
            slot_id = _slot_for_box(entry["box"])
            if slot_id:
                result.add(slot_id)
    return result


def _dynamic_spec(spec: Mapping[str, Any], section_layout: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Copy canonical slot metadata with the per-frame resolved geometry."""

    if section_layout is None:
        return dict(spec)
    resolved = section_layout_slot(section_layout, str(spec.get("slot_id")))
    if resolved is None:
        return None
    result = dict(spec)
    result["full_box"] = tuple(resolved["resolved_full_box"])
    result["source_box"] = tuple(resolved["resolved_source_box"])
    return result


def _valid_crop_box(box: Sequence[Any], width: int = 810, height: int = 1080) -> tuple[int, int, int, int] | None:
    """Return a bounded integer crop box, or ``None`` for corrupt geometry."""

    if isinstance(box, (str, bytes)) or not isinstance(box, Sequence) or len(box) != 4:
        return None
    try:
        values = [float(value) for value in box]
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    left, top, right, bottom = (int(round(value)) for value in values)
    if left < 0 or top < 0 or right > width or bottom > height or left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def _fixed_slot_pixel_guard(gameplay_path: Path, source_box: Sequence[Any]) -> bool:
    """Require source pixels that look occupied before a fixed fallback crop.

    The guard is deliberately content-only: it does not recognize a quantity
    or compare against an expected item.  It rejects a fixed crop containing
    only the pale result-panel background and leaves OCR plus the normal
    confidence/timestamp consensus gate to decide the quantity.
    """

    box = _valid_crop_box(source_box)
    if box is None or not gameplay_path.is_file():
        return False
    try:
        with Image.open(gameplay_path) as image:
            gameplay = image.convert("RGB")
            if gameplay.size != (810, 1080):
                return False
            crop = gameplay.crop(box)
            inset = FALLBACK_INTERIOR_INSET
            if crop.width <= inset * 2 or crop.height <= inset * 2:
                return False
            pixels = crop.load()
            foreground = 0
            total = 0
            for y in range(inset, crop.height - inset):
                for x in range(inset, crop.width - inset):
                    red, green, blue = pixels[x, y]
                    luma = (red + green + blue) / 3
                    saturation = max(red, green, blue) - min(red, green, blue)
                    if luma < FALLBACK_LUMA_THRESHOLD or saturation > FALLBACK_SATURATION_THRESHOLD:
                        foreground += 1
                    total += 1
            return total > 0 and foreground / total >= FALLBACK_FOREGROUND_RATIO
    except (OSError, ValueError, TypeError):
        return False


def _fallback_quantity_crop_box(source_box: Sequence[Any]) -> tuple[int, int, int, int] | None:
    """Resolve the generic lower-right quantity crop inside a fixed slot."""

    box = _valid_crop_box(source_box)
    if box is None:
        return None
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    x0, y0, x1, y1 = FALLBACK_QUANTITY_TEXT_RELATIVE_BOX
    return _valid_crop_box((
        left + round(width * x0),
        top + round(height * y0),
        left + round(width * x1),
        top + round(height * y1),
    ))


def _relative_crop_box(
    source_box: Sequence[Any],
    relative_box: Sequence[Any],
) -> tuple[int, int, int, int] | None:
    """Translate a normalized crop box into one source gameplay box.

    The source box is always validated before translation.  Keeping this
    helper independent of OCR makes the resulting geometry auditable and
    prevents a crop variant from silently escaping the gameplay pane.
    """

    box = _valid_crop_box(source_box)
    if box is None or isinstance(relative_box, (str, bytes)) or not isinstance(relative_box, Sequence) or len(relative_box) != 4:
        return None
    try:
        relative = [float(value) for value in relative_box]
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in relative):
        return None
    x0, y0, x1, y1 = relative
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    return _valid_crop_box((
        left + round(width * x0),
        top + round(height * y0),
        left + round(width * x1),
        top + round(height * y1),
    ))


def _quantity_focus_crop_box(source_box: Sequence[Any]) -> tuple[int, int, int, int] | None:
    """Resolve the bounded lower-right crop used for short quantities."""

    return _relative_crop_box(source_box, QUANTITY_FOCUS_RELATIVE_BOX)


def _same_position(first: Sequence[Any], second: Sequence[Any]) -> bool:
    try:
        first_center = _center(first)
        second_center = _center(second)
    except (TypeError, ValueError, IndexError):
        return False
    return abs(first_center[0] - second_center[0]) <= 16 and abs(first_center[1] - second_center[1]) <= 16


def _recording_and_capture(root: Path) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]], str]:
    path = root / CAPTURE_MANIFEST_NAME
    if not path.is_file():
        raise ValueError("capture_missing")
    try:
        capture = _load_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("capture_malformed") from exc
    source = capture.get("source") if isinstance(capture, Mapping) else None
    frames = capture.get("frames") if isinstance(capture, Mapping) else None
    if not isinstance(source, Mapping) or not isinstance(source.get("sha256"), str) or not isinstance(frames, list):
        raise ValueError("capture_malformed")
    indexed = {row.get("id"): row for row in frames if isinstance(row, Mapping) and row.get("id")}
    if len(indexed) != len(frames):
        raise ValueError("capture_malformed")
    return capture, indexed, _sha256(path)


def _safe_path(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str):
        return None
    try:
        path = (root / value).resolve()
        if not path.is_relative_to(root.resolve()):
            return None
        return path
    except (OSError, TypeError, ValueError):
        return None


def _capture_frame_time(frame: Mapping[str, Any], capture: Mapping[str, Any]) -> int:
    """Return the millisecond PTS represented by one capture manifest row."""

    if type(frame.get("source_pts")) is not int:
        raise ValueError("Race quantity refinement capture source PTS is missing or invalid.")
    time_base = frame.get("time_base")
    match = re.fullmatch(r"(\d+)/(\d+)", str(time_base or ""))
    if not match or int(match[1]) <= 0 or int(match[2]) <= 0:
        raise ValueError("Race quantity refinement capture time base is missing or invalid.")
    origin = capture.get("source", {}).get("timeline_origin_seconds", 0)
    try:
        origin_fraction = Fraction(str(origin))
        decoded = Fraction(frame["source_pts"]) * Fraction(int(match[1]), int(match[2])) - origin_fraction
        milliseconds = float(decoded * 1000)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        raise ValueError("Race quantity refinement capture PTS cannot be evaluated.") from None
    if not math.isfinite(milliseconds):
        raise ValueError("Race quantity refinement capture PTS is not finite.")
    return round(milliseconds)


def _capture_row_for_observation(
    observation: Mapping[str, Any],
    capture: Mapping[str, Any],
    capture_rows: Mapping[str, Mapping[str, Any]],
    capture_manifest_sha256: str,
    root: Path,
) -> tuple[Mapping[str, Any], Path, Path]:
    """Validate an observation's recording and manifest identity.

    The returned paths are the decoded source frame and the gameplay crop.
    All paths remain relative to ``root`` and are checked against their stored
    content hashes by the caller.
    """

    frame_id = observation.get("frame_id")
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("Race quantity refinement observation frame identity is missing.")
    frame = capture_rows.get(frame_id)
    if frame is None:
        raise ValueError("Race quantity refinement observation is not present in capture manifest.")
    if observation.get("source_manifest_evidence") != CAPTURE_MANIFEST_NAME:
        raise ValueError("Race quantity refinement observation manifest binding is invalid.")
    if observation.get("source_manifest_sha256") != capture_manifest_sha256:
        raise ValueError("Race quantity refinement capture manifest changed.")
    timestamp = observation.get("timestamp_ms")
    if not _typed_timestamp(timestamp) or timestamp != frame.get("source_timestamp_ms"):
        raise ValueError("Race quantity refinement observation timestamp differs from capture.")
    if frame.get("id") != frame_id:
        raise ValueError("Race quantity refinement observation frame id differs from capture.")
    if _capture_frame_time(frame, capture) != timestamp:
        raise ValueError("Race quantity refinement observation PTS differs from capture.")
    if observation.get("source_pts") != frame.get("source_pts"):
        raise ValueError("Race quantity refinement observation source PTS differs from capture.")
    if observation.get("time_base") != frame.get("time_base"):
        raise ValueError("Race quantity refinement observation time base differs from capture.")

    source_evidence = frame.get("evidence")
    if observation.get("source_frame_evidence") != source_evidence:
        raise ValueError("Race quantity refinement source-frame evidence differs from capture.")
    source_path = _safe_path(root, source_evidence)
    gameplay_path = _safe_path(root, observation.get("gameplay_evidence"))
    if source_path is None or gameplay_path is None or not source_path.is_file() or not gameplay_path.is_file():
        raise ValueError("Race quantity refinement source evidence is missing.")
    source_hash = _sha256(source_path)
    if observation.get("source_frame_sha256") != source_hash:
        raise ValueError("Race quantity refinement source-frame hash mismatch.")
    if not _sha256_value(source_hash):
        raise ValueError("Race quantity refinement source-frame hash is invalid.")
    source_recording = capture.get("source", {}).get("sha256")
    if observation.get("source_recording_sha256") != source_recording:
        raise ValueError("Race quantity refinement recording binding changed.")
    return frame, source_path, gameplay_path


def _source_identity(root: Path, raw: Mapping[str, Any], raw_path: Path, capture_rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    frame_id = raw_path.stem
    capture_row = capture_rows.get(frame_id)
    gameplay_path = _safe_path(root, raw.get("evidence"))
    source_path = _safe_path(root, capture_row.get("evidence") if capture_row else None)
    if capture_row is None or gameplay_path is None or source_path is None or not gameplay_path.is_file() or not source_path.is_file():
        return None
    try:
        if raw.get("source_timestamp_ms") != capture_row.get("source_timestamp_ms"):
            return None
        if raw.get("source_frame_sha256") != _sha256(source_path):
            return None
    except (OSError, TypeError):
        return None
    return {
        "frame_id": frame_id,
        "capture_row": capture_row,
        "source_path": source_path,
        "gameplay_path": gameplay_path,
    }


def _raw_index(root: Path) -> list[tuple[Mapping[str, Any], Path]]:
    result = []
    for path in sorted((root / "neural").glob("*.json")):
        try:
            raw = _load_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(raw, Mapping) and isinstance(raw.get("source_timestamp_ms"), int):
            result.append((raw, path))
    return sorted(result, key=lambda pair: pair[0]["source_timestamp_ms"])


def _race_key(row: Mapping[str, Any]) -> tuple[Any, Any] | None:
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return None
    fans, gained = facts.get("fans"), facts.get("fans_gained")
    if type(fans) is not int or type(gained) is not int:
        return None
    return fans, gained


def _group_frames(eligible: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for item in sorted(eligible, key=lambda value: value["raw"]["source_timestamp_ms"]):
        timestamp = item["raw"]["source_timestamp_ms"]
        key = item["race_key"]
        if (
            not groups
            or groups[-1][0]["race_key"] != key
            or bool(groups[-1][0].get("section_layout")) != bool(item.get("section_layout"))
            or timestamp - groups[-1][-1]["raw"]["source_timestamp_ms"] > MAX_GROUP_SPAN_MS
        ):
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups


def _detector_line(raw: Mapping[str, Any], spec: Mapping[str, Any]) -> Mapping[str, Any] | None:
    candidates = []
    for line in _lines(raw):
        if _quantity(line.get("text")) is None:
            continue
        if _within_box(line.get("box", ()), spec["full_box"]):
            candidates.append(line)
    return max(candidates, key=lambda line: float(line.get("confidence", 0)), default=None)


def _detector_backed(spec: Mapping[str, Any]) -> bool:
    """Whether a slot's primary crop is located from a detector box."""

    return str(spec.get("strategy", "")).startswith("detector_box_padding")


def _source_crop_box(
    raw: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    allow_fixed_fallback: bool = False,
    gameplay_path: Path | None = None,
    section_layout: Mapping[str, Any] | None = None,
) -> tuple[tuple[int, int, int, int] | None, Mapping[str, Any] | None]:
    """Resolve a source crop, optionally using an occupied fixed slot box.

    The default preserves the legacy behavior for existing artifacts: a
    detector-backed slot with no detector line has no crop.  New generation
    explicitly opts into the fixed geometry fallback and must provide the
    gameplay pixels for the occupancy guard.
    """

    if section_layout is not None:
        resolved = section_layout_slot(section_layout, str(spec.get("slot_id")))
        if resolved is None:
            # The optional/absent section has no slots.  Never fall through to
            # the legacy fixed geometry for a slot that the source did not
            # expose in this frame.
            return None, None
        resolved_box = _valid_crop_box(resolved.get("resolved_source_box"))
        if resolved_box is None:
            return None, None
        detected_line = resolved.get("detected_line")
        if isinstance(detected_line, Mapping):
            # The detector line is the source-visible geometry.  Preserve its
            # exact box as proof while using the same two-pixel crop padding as
            # the legacy detector path.
            return resolved_box, detected_line
        effective = _dynamic_spec(spec, section_layout)
        if effective is None:
            return None, None
        if not _detector_backed(effective):
            return resolved_box, None
        if allow_fixed_fallback and gameplay_path is not None and \
                _fixed_slot_pixel_guard(gameplay_path, resolved_box):
            return _fallback_quantity_crop_box(resolved_box), None
        return None, None

    if not _detector_backed(spec):
        box = _valid_crop_box(spec.get("source_box"))
        return box, None
    detector = _detector_line(raw, spec)
    if detector is None:
        fallback = _valid_crop_box(spec.get("source_box"))
        if allow_fixed_fallback and fallback is not None and gameplay_path is not None and \
                _fixed_slot_pixel_guard(gameplay_path, fallback):
            return _fallback_quantity_crop_box(fallback), None
        return None, None
    box = detector.get("box")
    if isinstance(box, (str, bytes)) or not isinstance(box, Sequence) or len(box) != 4:
        return None, None
    try:
        values = [float(value) for value in box]
    except (TypeError, ValueError, OverflowError):
        return None, None
    if not all(math.isfinite(value) for value in values):
        return None, None
    left, top, right, bottom = (int(round(value)) for value in values)
    source_box = _valid_crop_box((
        max(0, left - GAMEPLAY_X_OFFSET - DETECTOR_PADDING),
        max(0, top - DETECTOR_PADDING),
        min(810, right - GAMEPLAY_X_OFFSET + DETECTOR_PADDING),
        min(1080, bottom + DETECTOR_PADDING),
    ))
    if source_box is None:
        return None, None
    return source_box, detector


def _source_crop_variants(
    raw: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    allow_fixed_fallback: bool = False,
    gameplay_path: Path | None = None,
    section_layout: Mapping[str, Any] | None = None,
    quantity_policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Resolve auditable crop views for one slot.

    Historical artifacts have one crop per scale and leave
    ``quantity_policy`` unset.  New section-relative artifacts add a focused
    view only for a detectorless stable badge, while retaining the complete
    badge as the broad view.  Detector-backed and fallback crops already have
    quantity-specific geometry and therefore keep one broad view.
    """

    source_box, detector = _source_crop_box(
        raw,
        spec,
        allow_fixed_fallback=allow_fixed_fallback,
        gameplay_path=gameplay_path,
        section_layout=section_layout,
    )
    if source_box is None:
        return []
    if quantity_policy is None:
        return [{"variant": None, "role": "broad", "source_box": source_box, "detector": detector}]
    if not _mapping_equal(quantity_policy, QUANTITY_CROP_VARIANT_POLICY):
        raise ValueError("Race quantity refinement quantity crop policy changed.")

    # The policy is scoped to section-relative generation.  Keeping a legacy
    # artifact's single crop here is necessary for backwards-compatible
    # validation and avoids rewriting frozen fixed-layout evidence.
    if section_layout is None:
        return [{"variant": None, "role": "broad", "source_box": source_box, "detector": detector}]
    if detector is not None:
        return [{"variant": "detector", "role": "broad", "source_box": source_box, "detector": detector}]
    if str(spec.get("strategy")) != "stable_badge_geometry":
        return [{"variant": "fallback", "role": "broad", "source_box": source_box, "detector": None}]

    focused_box = _quantity_focus_crop_box(source_box)
    if focused_box is None:
        raise ValueError("Race quantity refinement focused crop geometry is invalid.")
    return [
        {"variant": "badge", "role": "broad", "source_box": source_box, "detector": None},
        {"variant": "quantity_focus", "role": "focused", "source_box": focused_box, "detector": None},
    ]


def _crop_resolution(
    spec: Mapping[str, Any],
    detector: Mapping[str, Any] | None,
    *,
    section_layout: Mapping[str, Any] | None = None,
) -> str:
    """Name the geometry source recorded in a measurement."""

    if section_layout is not None:
        return "section_anchor_line" if detector is not None else "section_anchor_fixed_slot"
    if not _detector_backed(spec):
        return "fixed"
    return "detector" if detector is not None else "fixed_fallback_quantity_text"


def _scaled_crops(source_path: Path, box: Sequence[int]) -> dict[int, Image.Image]:
    with Image.open(source_path) as image:
        crop = image.convert("RGB").crop(tuple(box))
    return {
        scale: crop if scale == 1 else crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
        for scale in SCALE_FACTORS
    }


def _recognition_results(result: Any, count: int) -> list[dict[str, Any]]:
    texts = list(getattr(result, "txts", []) or [])
    scores = list(getattr(result, "scores", []) or [])
    return [
        {
            "text": str(texts[index]) if index < len(texts) else "",
            "confidence": round(float(scores[index]) * 100, 4) if index < len(scores) else 0.0,
        }
        for index in range(count)
    ]


def _recognize_crops(reader: Any, crops: Sequence[Image.Image]) -> list[dict[str, Any]]:
    custom = getattr(reader, "recognize_crops", None)
    if callable(custom):
        results = custom(crops)
        if len(results) != len(crops):
            raise ValueError("Custom OCR returned a different number of results than crops.")
        return [
            {"text": str(result.get("text", "")), "confidence": round(float(result.get("confidence", 0)), 4)}
            for result in results
        ]
    arrays = [reader.np.array(crop.convert("RGB"))[:, :, ::-1] for crop in crops]
    result = reader.engine.text_rec(reader.TextRecInput(img=arrays))
    return _recognition_results(result, len(crops))


def _variant_role(observation: Mapping[str, Any]) -> str:
    """Return the declared role of one crop view.

    Old observations have no variant metadata and are treated as broad views
    for backwards-compatible aggregation.  New artifacts validate this field
    against the immutable crop policy before aggregation.
    """

    return "focused" if observation.get("crop_variant_role") == "focused" else "broad"


def _frame_summary(observations: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_time: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in observations:
        by_time[int(observation["timestamp_ms"])].append(observation)
    frame_observations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for timestamp, views in sorted(by_time.items()):
        high = [
            view
            for view in views
            if float(view.get("confidence", 0)) >= MIN_CONFIDENCE and view.get("quantity") is not None
        ]
        values = sorted({int(view["quantity"]) for view in high})
        if len(values) > 1:
            conflict = {
                "timestamp_ms": timestamp,
                "quantities": values,
                "observations": [dict(view) for view in high],
            }
            conflicts.append(conflict)
            frame_observations.append({
                "timestamp_ms": timestamp,
                "status": "conflict",
                "view_count": len(views),
                "high_confidence_view_count": len(high),
                "quantities": values,
            })
            continue
        if len(values) == 1:
            selected = [view for view in high if int(view["quantity"]) == values[0]]
            # A focused crop is intentionally narrow enough to avoid badge
            # artwork for short values.  If it is the only high-confidence
            # evidence for a multi-digit value, it may have clipped the
            # left-hand digits.  Require a high-confidence broad view for
            # those values; otherwise leave the frame explicitly unresolved.
            focused_policy = any("crop_variant" in view for view in views)
            broad_high = [view for view in selected if _variant_role(view) == "broad"]
            broad_quantities = {
                int(view["quantity"])
                for view in views
                if _variant_role(view) == "broad" and type(view.get("quantity")) is int
            }
            if focused_policy and broad_quantities and broad_quantities != {values[0]}:
                frame_observations.append({
                    "timestamp_ms": timestamp,
                    "status": "clipping_risk",
                    "candidate_quantity": values[0],
                    "candidate_text": selected[0]["text"],
                    "candidate_confidence": min(float(view["confidence"]) for view in selected),
                    "view_count": len(views),
                    "high_confidence_view_count": len(high),
                    "broad_quantities": sorted(broad_quantities),
                    "reason": "focused_variant_conflicts_with_broad_reading",
                })
                continue
            if focused_policy and values[0] >= 10 and not broad_high:
                frame_observations.append({
                    "timestamp_ms": timestamp,
                    "status": "clipping_risk",
                    "candidate_quantity": values[0],
                    "candidate_text": selected[0]["text"],
                    "candidate_confidence": min(float(view["confidence"]) for view in selected),
                    "view_count": len(views),
                    "high_confidence_view_count": len(high),
                    "reason": "focused_variant_without_broad_support",
                })
                continue
            frame_observations.append({
                "timestamp_ms": timestamp,
                "status": "accepted_frame",
                "quantity": values[0],
                "text": selected[0]["text"],
                "confidence": min(float(view["confidence"]) for view in selected),
                "view_count": len(views),
                "high_confidence_view_count": len(high),
                "view_evidence": [view.get("crop_evidence") for view in views],
            })
        else:
            frame_observations.append({
                "timestamp_ms": timestamp,
                "status": "no_accepted_view",
                "view_count": len(views),
                "high_confidence_view_count": len(high),
            })
    return frame_observations, conflicts


def aggregate_slot(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate correlated scale views into a source-timestamp decision."""

    frame_observations, conflicts = _frame_summary(observations)
    accepted_frames = [item for item in frame_observations if item["status"] == "accepted_frame"]
    values = sorted({int(item["quantity"]) for item in accepted_frames})
    result: dict[str, Any] = {
        "status": "unresolved",
        "minimum_confidence": MIN_CONFIDENCE,
        "minimum_distinct_timestamps": MIN_DISTINCT_TIMESTAMPS,
        "frame_observations": frame_observations,
        "conflicts": conflicts,
        "accepted": None,
    }
    if conflicts or len(values) > 1:
        result["status"] = "conflict"
        if len(values) > 1:
            result["conflicts"].append({"quantities": values, "scope": "distinct_source_timestamps"})
        return result
    if len(values) != 1:
        result["status"] = "no_high_confidence_consensus"
        return result
    support = [item for item in accepted_frames if int(item["quantity"]) == values[0]]
    timestamps = sorted({int(item["timestamp_ms"]) for item in support})
    if len(timestamps) < MIN_DISTINCT_TIMESTAMPS:
        result["status"] = "insufficient_distinct_timestamps"
        return result
    result["status"] = "accepted"
    result["accepted"] = {
        "quantity": values[0],
        "text": support[0]["text"],
        "confidence": min(float(item["confidence"]) for item in support),
        "support_timestamps_ms": timestamps,
        "independent_frame_count": len(timestamps),
        "basis": "same_quantity_at_distinct_source_timestamps",
    }
    return result


def _measurement(
    root: Path,
    output_root: Path,
    reader: Any,
    identity: Mapping[str, Any],
    raw: Mapping[str, Any],
    raw_path: Path,
    capture_manifest_sha256: str,
    spec: Mapping[str, Any],
    source_box: Sequence[int],
    detector: Mapping[str, Any] | None,
    scale: int,
    crop: Image.Image,
    recognized: Mapping[str, Any],
    crop_resolution: str,
    resolved_full_box: Sequence[Any] | None = None,
    crop_variant: str | None = None,
    crop_variant_role: str | None = None,
) -> dict[str, Any]:
    frame_id = identity["frame_id"]
    suffix = f"-scale-{scale}"
    if crop_variant is not None:
        suffix += f"-{crop_variant}"
    crop_path = output_root / OBSERVATION_DIRNAME / frame_id / spec["slot_id"] / f"{frame_id}{suffix}.png"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(crop_path, format="PNG")
    source_path = Path(identity["source_path"])
    gameplay_path = Path(identity["gameplay_path"])
    text = str(recognized.get("text", ""))
    confidence = round(float(recognized.get("confidence", 0)), 4)
    measurement_full_box = resolved_full_box if resolved_full_box is not None else spec["full_box"]
    measurement = {
        "timestamp_ms": int(raw["source_timestamp_ms"]),
        "frame_id": frame_id,
        "group": spec["group"],
        "slot": spec["slot"],
        "group_index": spec["group_index"],
        "slot_id": spec["slot_id"],
        "strategy": spec["strategy"],
        "crop_resolution": crop_resolution,
        "text": text,
        "quantity": _quantity(text),
        "confidence": confidence,
        "view_scale": scale,
        "view_is_independent_frame": False,
        "source_frame_evidence": source_path.relative_to(root).as_posix(),
        "source_frame_sha256": _sha256(source_path),
        "source_pts": identity["capture_row"].get("source_pts"),
        "time_base": identity["capture_row"].get("time_base"),
        "gameplay_evidence": gameplay_path.relative_to(root).as_posix(),
        "gameplay_file_sha256": _sha256(gameplay_path),
        "source_crop_box": list(source_box),
        "full_box": [int(round(value)) for value in measurement_full_box],
        "detector_box": list(detector["box"]) if detector is not None else None,
        "crop_evidence": crop_path.relative_to(root).as_posix(),
        "crop_sha256": _sha256(crop_path),
        "raw_evidence": raw_path.relative_to(root).as_posix(),
        "raw_sha256": fingerprint(raw),
        "source_engine_fingerprint": raw.get("engine_fingerprint"),
        "source_model_sha256": copy.deepcopy(raw.get("model_sha256")),
        "source_manifest_evidence": CAPTURE_MANIFEST_NAME,
        "source_manifest_sha256": capture_manifest_sha256,
        "source_manifest_row_id": frame_id,
        "source_recording_sha256": identity["source_recording_sha256"],
        "reader_fingerprint": getattr(reader, "fingerprint", None),
        "model_sha256": copy.deepcopy(getattr(reader, "models", {})),
        "preprocessing": {
            "resize_scale": scale,
            "resize_resample": RESAMPLE_NAME,
            "output_mode": "RGB",
        },
    }
    if crop_variant is not None:
        measurement["crop_variant"] = crop_variant
        measurement["crop_variant_role"] = crop_variant_role
    return measurement


def _artifact_for_group(
    root: Path,
    output_root: Path,
    capture: Mapping[str, Any],
    capture_manifest_sha256: str,
    reader: Any,
    group: Sequence[Mapping[str, Any]],
    missing: Sequence[Mapping[str, Any]],
    *,
    base_item: Mapping[str, Any] | None = None,
    allow_fixed_fallback: bool = False,
) -> dict[str, Any]:
    base = base_item or group[0]
    base_raw = base["raw"]
    dynamic_layout = base.get("section_layout")
    dynamic_enabled = dynamic_layout is not None
    if dynamic_enabled:
        if not isinstance(dynamic_layout, Mapping):
            raise ValueError("Race quantity refinement section layout is malformed.")
        if not is_section_layout_policy(dynamic_layout.get("policy")):
            raise ValueError("Race quantity refinement section layout policy is invalid.")
        if any(
            not isinstance(item.get("section_layout"), Mapping)
            or not is_section_layout_policy(item["section_layout"].get("policy"))
            for item in group
        ):
            raise ValueError("Race quantity refinement section layout policy differs within group.")
    observations_by_slot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    expected_keys_by_slot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quantity_policy = QUANTITY_CROP_VARIANT_POLICY if dynamic_enabled else None
    for spec in missing:
        inputs: list[
            tuple[
                Mapping[str, Any],
                Mapping[str, Any] | None,
                tuple[int, int, int, int],
                int,
                Image.Image,
                str | None,
                str,
            ]
        ] = []
        for item in group:
            gameplay_path = Path(item["identity"]["gameplay_path"])
            item_layout = item.get("section_layout") if dynamic_enabled else None
            variants = _source_crop_variants(
                item["raw"], spec,
                allow_fixed_fallback=allow_fixed_fallback,
                gameplay_path=gameplay_path,
                section_layout=item_layout,
                quantity_policy=quantity_policy,
            )
            for variant in variants:
                source_box = variant["source_box"]
                detector = variant["detector"]
                crops = _scaled_crops(gameplay_path, source_box)
                for scale, crop in crops.items():
                    inputs.append((
                        item,
                        detector,
                        source_box,
                        scale,
                        crop,
                        variant["variant"],
                        variant["role"],
                    ))
                    key = {
                        "frame_id": item["identity"]["frame_id"],
                        "timestamp_ms": int(item["raw"]["source_timestamp_ms"]),
                        "slot_id": spec["slot_id"],
                        "view_scale": scale,
                    }
                    if variant["variant"] is not None:
                        key["crop_variant"] = variant["variant"]
                    expected_keys_by_slot[spec["slot_id"]].append(key)
        if not inputs:
            continue
        recognitions = _recognize_crops(reader, [item[4] for item in inputs])
        if len(recognitions) != len(inputs):
            raise ValueError("OCR returned a different number of results than crops.")
        for (item, detector, source_box, scale, crop, crop_variant, crop_variant_role), recognized in zip(inputs, recognitions):
            item_layout = item.get("section_layout") if dynamic_enabled else None
            resolved = section_layout_slot(item_layout, spec["slot_id"]) if item_layout is not None else None
            resolved_full_box = None
            if isinstance(resolved, Mapping):
                resolved_full_box = (
                    detector.get("box")
                    if detector is not None
                    else resolved.get("resolved_full_box")
                )
            observations_by_slot[spec["slot_id"]].append(
                _measurement(
                    root,
                    output_root,
                    reader,
                    item["identity"],
                    item["raw"],
                    item["raw_path"],
                    capture_manifest_sha256,
                    spec,
                    source_box,
                    detector,
                    scale,
                    crop,
                    recognized,
                    _crop_resolution(spec, detector, section_layout=item_layout),
                    resolved_full_box=resolved_full_box,
                    crop_variant=crop_variant,
                    crop_variant_role=crop_variant_role if crop_variant is not None else None,
                )
            )

    slots = []
    for spec in missing:
        observations = observations_by_slot.get(spec["slot_id"], [])
        summary = aggregate_slot(observations)
        slots.append({
            "slot": spec["slot"],
            "group": spec["group"],
            "group_index": spec["group_index"],
            "slot_id": spec["slot_id"],
            "strategy": spec["strategy"],
            "source_box": list(spec["source_box"]),
            "full_box": list(spec["full_box"]),
            "expected_observation_keys": expected_keys_by_slot.get(spec["slot_id"], []),
            "observations": observations,
            **summary,
        })

    source = capture["source"]
    artifact = {
        "version": 1,
        "source_sha256": source["sha256"],
        "source_recording_sha256": source["sha256"],
        "capture_manifest": CAPTURE_MANIFEST_NAME,
        "capture_manifest_sha256": capture_manifest_sha256,
        "base_frame_id": base["identity"]["frame_id"],
        "base_source_timestamp_ms": base_raw["source_timestamp_ms"],
        "base_raw_evidence": base["raw_path"].relative_to(root).as_posix(),
        "base_raw_sha256": fingerprint(base_raw),
        "base_gameplay_evidence": base_raw["evidence"],
        "race_key": list(base["race_key"]),
        "layout_guard": {
            "screen": "race_result",
            "items_header": "Items",
            "bonus_header": "Bonus",
            "visible_item_quantities_is_list": True,
            "item_rewards_complete": False,
        },
        "reader_fingerprint": getattr(reader, "fingerprint", None),
        "model_sha256": copy.deepcopy(getattr(reader, "models", {})),
        "minimum_confidence": MIN_CONFIDENCE,
        "minimum_distinct_timestamps": MIN_DISTINCT_TIMESTAMPS,
        "refinement_policy": copy.deepcopy(REFINEMENT_POLICY),
        "scale_factors": list(SCALE_FACTORS),
        "detector_padding": DETECTOR_PADDING,
        "crop_resolution_policy": copy.deepcopy(FIXED_SLOT_FALLBACK_POLICY) if allow_fixed_fallback else None,
        "identity_verified": False,
        "list_complete": False,
        "item_identity_verified": False,
        "item_rewards_complete": False,
        "source_frames": [
            {
                "frame_id": item["identity"]["frame_id"],
                "timestamp_ms": int(item["raw"]["source_timestamp_ms"]),
                "source_pts": item["identity"]["capture_row"].get("source_pts"),
                "time_base": item["identity"]["capture_row"].get("time_base"),
                "source_frame_evidence": Path(item["identity"]["source_path"]).relative_to(root).as_posix(),
                "source_frame_sha256": _sha256(Path(item["identity"]["source_path"])),
            }
            for item in group
        ],
        "slots": slots,
        "all_observations": [observation for slot in slots for observation in slot["observations"]],
    }
    if dynamic_enabled:
        artifact.update({
            "section_anchor_policy": copy.deepcopy(SECTION_LAYOUT_POLICY),
            "quantity_crop_policy": copy.deepcopy(QUANTITY_CROP_VARIANT_POLICY),
            "base_section_layout": copy.deepcopy(base["section_layout"]),
            "source_frame_layouts": {
                item["identity"]["frame_id"]: copy.deepcopy(item["section_layout"])
                for item in group
            },
        })
    return artifact


def _rejection(output_root: Path, frame_id: str, reason: str, details: Mapping[str, Any] | None = None) -> Path:
    value = {"version": 1, "frame_id": frame_id, "reason": reason}
    if details:
        value["details"] = dict(details)
    path = output_root / REJECTED_DIRNAME / f"{frame_id}.json"
    _save_json(path, value)
    return path


def generate(root: str | Path, *, reader_factory=NeuralReader, model_dir: str | Path = ".local/models/rapidocr", frame_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """Generate refinement artifacts for race-result groups with missing slots."""

    root = Path(root).resolve()
    if frame_ids is not None and (isinstance(frame_ids, (str, bytes)) or not frame_ids
            or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value) for value in frame_ids)):
        raise ValueError('Frame selection requires a nonempty list of frame IDs.')
    selected = set(frame_ids) if frame_ids is not None else None
    output_root = root / OUTPUT_DIRNAME
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        capture, capture_rows, capture_manifest_sha256 = _recording_and_capture(root)
    except ValueError as exc:
        reason = str(exc)
        rejection = _rejection(output_root, "capture", reason)
        summary = {"source_sha256": None, "artifacts": [], "rejected": [str(rejection)], "artifact_count": 0, "rejected_count": 1}
        _save_json(output_root / "generation-summary.json", summary)
        return summary

    eligible = []
    rejected = []
    for raw, raw_path in _raw_index(root):
        if selected is not None and raw_path.stem not in selected:
            continue
        identity = _source_identity(root, raw, raw_path, capture_rows)
        if identity is None:
            rejected.append(str(_rejection(output_root, raw_path.stem, "source_identity_mismatch")))
            continue
        identity = dict(identity, source_recording_sha256=capture["source"]["sha256"])
        try:
            row = parse(raw)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        # Preserve the fixed-layout contract only when the frame also matches
        # its narrow historical geometry.  The relative policy is selected for
        # shifted sections even when both broad legacy header windows happen to
        # match.
        if _legacy_fixed_geometry_usable(raw, row):
            section_layout = None
        else:
            try:
                section_layout = resolve_section_layout(raw, row, SLOT_SPECS)
            except (SectionLayoutError, KeyError, TypeError, ValueError) as exc:
                rejected.append(str(_rejection(
                    output_root,
                    raw_path.stem,
                    "section_layout_rejected",
                    {"error": str(exc)},
                )))
                continue
            if section_layout is None:
                continue
        race_key = _race_key(row)
        if race_key is None:
            rejected.append(str(_rejection(output_root, raw_path.stem, "race_key_missing")))
            continue
        eligible.append({
            "raw": raw,
            "raw_path": raw_path,
            "row": row,
            "identity": identity,
            "race_key": race_key,
            "section_layout": section_layout,
        })

    reader = None
    artifacts = []
    for group in _group_frames(eligible):
        # A slot may be present in one cached frame and absent in another due
        # to animation, occlusion, or a weak detector result.  Use the union
        # of *per-frame gaps* so the absent target frame still gets a chance
        # to produce a source-bound observation.  Applying a consensus remains
        # timestamp-gated below, so a support frame cannot manufacture a badge
        # on a frame that has no accepted source observation.
        if group[0].get("section_layout") is not None:
            missing = [
                spec for spec in SECTION_SLOT_SPECS
                if any(
                    section_layout_slot(item.get("section_layout"), spec["slot_id"]) is not None
                    and spec["slot_id"] not in _present_slots(item["row"], item.get("section_layout"))
                    for item in group
                )
            ]
        else:
            missing = [
                spec for spec in SLOT_SPECS
                if any(spec["slot_id"] not in _present_slots(item["row"]) for item in group)
            ]
        if not missing:
            continue
        if reader is None:
            reader = reader_factory(model_dir)
        # Each cached reading is independently keyed by its raw frame.  Reuse
        # the OCR/crop observations for the group, but bind the artifact's
        # base raw/evidence to the target frame so strict apply can verify it.
        try:
            group_artifact = _artifact_for_group(
                root, output_root, capture, capture_manifest_sha256, reader, group, missing,
                base_item=group[0],
                allow_fixed_fallback=True,
            )
        except (OSError, ValueError, KeyError, TypeError, Image.DecompressionBombError) as exc:
            frame_id = group[0]["identity"]["frame_id"]
            rejected.append(str(_rejection(output_root, frame_id, "refinement_abstained", {"error": str(exc)})))
            continue
        if not group_artifact["all_observations"]:
            frame_id = group[0]["identity"]["frame_id"]
            rejected.append(str(_rejection(output_root, frame_id, "no_source_crops")))
            continue
        for base in group:
            frame_id = base["identity"]["frame_id"]
            target = output_root / f"{frame_id}.json"
            if target.exists():
                rejected.append(str(_rejection(output_root, frame_id, "existing_artifact")))
                continue
            try:
                artifact = copy.deepcopy(group_artifact)
                base_raw = base["raw"]
                artifact.update(
                    base_frame_id=frame_id,
                    base_source_timestamp_ms=base_raw["source_timestamp_ms"],
                    base_raw_evidence=base["raw_path"].relative_to(root).as_posix(),
                    base_raw_sha256=fingerprint(base_raw),
                    base_gameplay_evidence=base_raw["evidence"],
                    race_key=list(base["race_key"]),
                )
                if base.get("section_layout") is not None:
                    artifact["base_section_layout"] = copy.deepcopy(base["section_layout"])
                _save_json(target, artifact)
                artifacts.append(str(target))
            except (OSError, ValueError, KeyError, TypeError, Image.DecompressionBombError) as exc:
                rejected.append(str(_rejection(output_root, frame_id, "refinement_abstained", {"error": str(exc)})))

    summary = {
        "source_sha256": capture["source"]["sha256"],
        "capture_manifest_sha256": capture_manifest_sha256,
        "artifacts": artifacts,
        "rejected": rejected,
        "artifact_count": len(artifacts),
        "rejected_count": len(rejected),
        "minimum_confidence": MIN_CONFIDENCE,
        "minimum_distinct_timestamps": MIN_DISTINCT_TIMESTAMPS,
        "selected_frame_ids": sorted(selected) if selected is not None else None,
    }
    _save_json(output_root / "generation-summary.json", summary)
    return summary


def _validate_capture_identity(
    root: Path,
    artifact: Mapping[str, Any],
) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]], str]:
    """Load and validate the immutable recording/capture identity.

    A refinement is useful only in the run directory that produced its source
    frames.  The capture manifest is therefore part of the refinement's
    provenance, rather than an incidental file used for convenience.
    """

    capture, capture_rows, manifest_hash = _recording_and_capture(root)
    source = capture.get("source", {})
    source_hash = source.get("sha256")
    if not _sha256_value(source_hash):
        raise ValueError("Race quantity refinement recording hash is missing or invalid.")
    if artifact.get("capture_manifest") != CAPTURE_MANIFEST_NAME:
        raise ValueError("Race quantity refinement capture manifest binding is invalid.")
    if artifact.get("capture_manifest_sha256") != manifest_hash:
        raise ValueError("Race quantity refinement capture manifest changed.")
    if artifact.get("source_sha256") != source_hash or artifact.get("source_recording_sha256") != source_hash:
        raise ValueError("Race quantity refinement recording binding changed.")

    identity_path = root / "identity.json"
    if identity_path.is_file():
        try:
            identity = _load_json(identity_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Race quantity refinement recording identity is malformed.") from exc
        if not isinstance(identity, Mapping) or identity.get("source_sha256") != source_hash:
            raise ValueError("Race quantity refinement recording identity changed.")

    return capture, capture_rows, manifest_hash


def _validate_source_frame_entry(
    entry: Mapping[str, Any],
    capture: Mapping[str, Any],
    capture_rows: Mapping[str, Mapping[str, Any]],
    manifest_hash: str,
    root: Path,
) -> tuple[Mapping[str, Any], Path]:
    """Validate one artifact source-frame record against ``capture.json``."""

    frame_id = entry.get("frame_id")
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("Race quantity refinement source-frame identity is missing.")
    frame = capture_rows.get(frame_id)
    if frame is None:
        raise ValueError("Race quantity refinement source frame is not in capture manifest.")
    if not _typed_timestamp(entry.get("timestamp_ms")) or entry.get("timestamp_ms") != frame.get("source_timestamp_ms"):
        raise ValueError("Race quantity refinement source-frame timestamp differs from capture.")
    if entry.get("source_pts") != frame.get("source_pts") or entry.get("time_base") != frame.get("time_base"):
        raise ValueError("Race quantity refinement source-frame PTS differs from capture.")
    if _capture_frame_time(frame, capture) != entry.get("timestamp_ms"):
        raise ValueError("Race quantity refinement source-frame PTS cannot be verified.")
    if entry.get("source_frame_evidence") != frame.get("evidence"):
        raise ValueError("Race quantity refinement source-frame evidence differs from capture.")
    source_path = _safe_path(root, frame.get("evidence"))
    if source_path is None or not source_path.is_file():
        raise ValueError("Race quantity refinement source-frame evidence is missing.")
    source_hash = _sha256(source_path)
    if entry.get("source_frame_sha256") != source_hash or not _sha256_value(source_hash):
        raise ValueError("Race quantity refinement source-frame hash mismatch.")
    if entry.get("source_manifest_evidence", CAPTURE_MANIFEST_NAME) != CAPTURE_MANIFEST_NAME:
        raise ValueError("Race quantity refinement source-frame manifest binding is invalid.")
    if entry.get("source_manifest_sha256", manifest_hash) != manifest_hash:
        raise ValueError("Race quantity refinement source-frame manifest changed.")
    return frame, source_path


def _validate_gameplay_pixels(
    source_path: Path,
    gameplay_path: Path,
    raw: Mapping[str, Any],
    observation: Mapping[str, Any],
    root: Path,
) -> None:
    """Verify the exact 810x1080 gameplay crop used by the OCR observation."""

    if not gameplay_path.is_file():
        raise ValueError("Race quantity refinement gameplay evidence is missing.")
    if observation.get("gameplay_file_sha256") != _sha256(gameplay_path):
        raise ValueError("Race quantity refinement gameplay proof hash mismatch.")
    try:
        with Image.open(source_path) as source_image, Image.open(gameplay_path) as gameplay_image:
            source = source_image.convert("RGB")
            gameplay = gameplay_image.convert("RGB")
            expected = source.crop((GAMEPLAY_X_OFFSET, 0, GAMEPLAY_X_OFFSET + 810, 1080))
            if gameplay.size != expected.size or gameplay.tobytes() != expected.tobytes():
                raise ValueError("Race quantity refinement gameplay crop pixels changed.")
            pixels_hash = hashlib.sha256(gameplay.tobytes()).hexdigest()
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "Race quantity refinement gameplay crop pixels changed.":
            raise
        raise ValueError("Race quantity refinement gameplay evidence is unreadable.") from exc
    if raw.get("gameplay_sha256") != pixels_hash:
        raise ValueError("Race quantity refinement raw gameplay pixels changed.")
    if raw.get("evidence") != observation.get("gameplay_evidence"):
        raise ValueError("Race quantity refinement gameplay evidence binding changed.")


def _validate_raw_observation(
    observation: Mapping[str, Any],
    spec: Mapping[str, Any],
    root: Path,
    capture: Mapping[str, Any],
    capture_rows: Mapping[str, Mapping[str, Any]],
    manifest_hash: str,
    *,
    allow_fixed_fallback: bool = False,
    section_layout: Mapping[str, Any] | None = None,
    quantity_policy: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Validate raw OCR, source pixels and the crop backing one measurement."""

    required = (
        "timestamp_ms", "frame_id", "slot_id", "text", "quantity", "confidence",
        "view_scale", "view_is_independent_frame", "source_frame_evidence",
        "source_frame_sha256", "source_pts", "time_base", "gameplay_evidence",
        "gameplay_file_sha256", "source_crop_box", "full_box", "crop_evidence",
        "crop_sha256", "raw_evidence", "raw_sha256", "source_engine_fingerprint",
        "source_model_sha256", "source_manifest_evidence", "source_manifest_sha256",
        "source_manifest_row_id", "source_recording_sha256", "reader_fingerprint",
        "model_sha256", "preprocessing",
    )
    if quantity_policy is not None:
        required += ("crop_variant", "crop_variant_role")
    if any(key not in observation for key in required):
        raise ValueError("Incomplete race quantity refinement observation provenance.")
    if observation.get("slot_id") != spec["slot_id"]:
        raise ValueError("Race quantity refinement observation slot binding changed.")
    if observation.get("slot") != spec["slot"] or observation.get("group") != spec["group"]:
        raise ValueError("Race quantity refinement observation group binding changed.")
    if observation.get("group_index") != spec["group_index"] or observation.get("strategy") != spec["strategy"]:
        raise ValueError("Race quantity refinement observation strategy binding changed.")
    resolved_slot = section_layout_slot(section_layout, str(spec["slot_id"])) if section_layout is not None else None
    if section_layout is not None and resolved_slot is None:
        raise ValueError("Race quantity refinement observation section slot is absent.")
    if section_layout is None and observation.get("full_box") != list(spec["full_box"]):
        raise ValueError("Race quantity refinement observation slot geometry changed.")
    if not _typed_confidence(observation.get("confidence")):
        raise ValueError("Race quantity refinement observation confidence is invalid.")
    if type(observation.get("quantity")) is not int and observation.get("quantity") is not None:
        raise ValueError("Race quantity refinement observation quantity is invalid.")
    if _quantity(observation.get("text")) != observation.get("quantity"):
        raise ValueError("Race quantity refinement observation text and quantity disagree.")
    scale = observation.get("view_scale")
    if type(scale) is not int or scale not in SCALE_FACTORS or observation.get("view_is_independent_frame") is not False:
        raise ValueError("Race quantity refinement observation view metadata is invalid.")
    preprocessing = observation.get("preprocessing")
    if not isinstance(preprocessing, Mapping) or preprocessing.get("resize_scale") != scale or \
            preprocessing.get("resize_resample") != RESAMPLE_NAME or preprocessing.get("output_mode") != "RGB":
        raise ValueError("Race quantity refinement observation preprocessing changed.")
    frame, source_path, gameplay_path = _capture_row_for_observation(
        observation, capture, capture_rows, manifest_hash, root,
    )
    raw_path = _safe_path(root, observation.get("raw_evidence"))
    if raw_path is None or not raw_path.is_file() or raw_path.suffix.casefold() != ".json" or raw_path.stem != observation.get("frame_id"):
        raise ValueError("Race quantity refinement raw evidence is missing or misbound.")
    if raw_path != (root / "neural" / f"{observation['frame_id']}.json").resolve():
        raise ValueError("Race quantity refinement raw evidence is outside the canonical OCR cache.")
    try:
        raw = _load_json(raw_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Race quantity refinement raw evidence is unreadable.") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("Race quantity refinement raw evidence is malformed.")
    if observation.get("raw_sha256") != fingerprint(raw):
        raise ValueError("Race quantity refinement raw evidence hash mismatch.")
    if raw.get("source_timestamp_ms") != observation.get("timestamp_ms") or raw.get("evidence") != observation.get("gameplay_evidence"):
        raise ValueError("Race quantity refinement raw observation identity changed.")
    if raw.get("source_frame_sha256") != observation.get("source_frame_sha256"):
        raise ValueError("Race quantity refinement raw source-frame binding changed.")
    if not isinstance(raw.get("engine_fingerprint"), str) or not raw.get("engine_fingerprint"):
        raise ValueError("Race quantity refinement raw engine fingerprint is missing.")
    if not isinstance(raw.get("model_sha256"), Mapping) or not raw.get("model_sha256"):
        raise ValueError("Race quantity refinement raw model binding is missing.")
    if observation.get("source_engine_fingerprint") != raw.get("engine_fingerprint") or \
            not _mapping_equal(observation.get("source_model_sha256"), raw.get("model_sha256")):
        raise ValueError("Race quantity refinement raw model binding changed.")
    if observation.get("source_manifest_row_id") != observation.get("frame_id"):
        raise ValueError("Race quantity refinement capture row binding changed.")
    if observation.get("source_recording_sha256") != capture.get("source", {}).get("sha256"):
        raise ValueError("Race quantity refinement recording binding changed.")
    artifact_reader_fingerprint = observation.get("reader_fingerprint")
    if not isinstance(artifact_reader_fingerprint, str) or not artifact_reader_fingerprint:
        raise ValueError("Race quantity refinement reader fingerprint is missing.")
    if not isinstance(observation.get("model_sha256"), Mapping) or not observation.get("model_sha256"):
        raise ValueError("Race quantity refinement model binding is missing.")

    variants = _source_crop_variants(
        raw,
        spec,
        allow_fixed_fallback=allow_fixed_fallback,
        gameplay_path=gameplay_path,
        section_layout=section_layout,
        quantity_policy=quantity_policy,
    )
    crop_variant = observation.get("crop_variant") if quantity_policy is not None else None
    matching_variants = [variant for variant in variants if variant.get("variant") == crop_variant]
    if len(matching_variants) != 1:
        raise ValueError("Race quantity refinement crop variant geometry changed.")
    selected_variant = matching_variants[0]
    expected_crop_box = selected_variant["source_box"]
    expected_detector = selected_variant["detector"]
    if expected_crop_box is None or observation.get("source_crop_box") != list(expected_crop_box):
        raise ValueError("Race quantity refinement source crop geometry changed.")
    expected_detector_box = list(expected_detector["box"]) if expected_detector is not None else None
    if observation.get("detector_box") != expected_detector_box:
        raise ValueError("Race quantity refinement detector geometry changed.")
    if quantity_policy is not None and observation.get("crop_variant_role") != selected_variant.get("role"):
        raise ValueError("Race quantity refinement crop variant role changed.")
    expected_resolution = _crop_resolution(spec, expected_detector, section_layout=section_layout)
    if allow_fixed_fallback:
        if observation.get("crop_resolution") != expected_resolution:
            raise ValueError("Race quantity refinement crop resolution changed.")
    elif "crop_resolution" in observation and observation.get("crop_resolution") != expected_resolution:
        raise ValueError("Race quantity refinement crop resolution changed.")
    if section_layout is not None:
        expected_full_box = (
            expected_detector.get("box")
            if expected_detector is not None
            else resolved_slot.get("resolved_full_box")
        )
        if not isinstance(expected_full_box, Sequence) or len(expected_full_box) != 4 or \
                observation.get("full_box") != [int(round(value)) for value in expected_full_box]:
            raise ValueError("Race quantity refinement observation resolved geometry changed.")
    crop_path = _safe_path(root, observation.get("crop_evidence"))
    if crop_path is None or not crop_path.is_file() or observation.get("crop_sha256") != _sha256(crop_path):
        raise ValueError("Race quantity refinement crop proof hash mismatch.")
    _validate_gameplay_pixels(source_path, gameplay_path, raw, observation, root)
    try:
        with Image.open(gameplay_path) as gameplay_image, Image.open(crop_path) as crop_image:
            gameplay = gameplay_image.convert("RGB")
            crop = crop_image.convert("RGB")
            expected = gameplay.crop(tuple(expected_crop_box))
            if scale != 1:
                expected = expected.resize((expected.width * scale, expected.height * scale), Image.Resampling.LANCZOS)
            if crop.size != expected.size or crop.tobytes() != expected.tobytes():
                raise ValueError("Race quantity refinement crop pixels changed.")
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "Race quantity refinement crop pixels changed.":
            raise
        raise ValueError("Race quantity refinement crop proof is unreadable.") from exc
    return raw


def _validate_provenance(row: Mapping[str, Any], artifact: Mapping[str, Any], raw: Mapping[str, Any] | None, root: Path | None) -> dict[str, dict[str, Any]]:
    """Validate strict provenance and return fresh, observation-derived summaries."""

    minimum = artifact.get("minimum_confidence")
    if not _typed_confidence(minimum) or float(minimum) < MIN_CONFIDENCE:
        raise ValueError("Race quantity refinement lowers the production confidence threshold.")
    fallback_policy = artifact.get("crop_resolution_policy")
    if fallback_policy is None:
        allow_fixed_fallback = False
    elif not _mapping_equal(fallback_policy, FIXED_SLOT_FALLBACK_POLICY):
        raise ValueError("Race quantity refinement fixed fallback policy changed.")
    else:
        allow_fixed_fallback = True
    quantity_policy = artifact.get("quantity_crop_policy")
    if quantity_policy is not None and not _mapping_equal(quantity_policy, QUANTITY_CROP_VARIANT_POLICY):
        raise ValueError("Race quantity refinement quantity crop policy changed.")
    section_anchor_metadata = artifact.get("section_anchor_policy")
    dynamic_section_policy = section_anchor_metadata is not None
    if dynamic_section_policy and not is_section_layout_policy(section_anchor_metadata):
        raise ValueError("Race quantity refinement section layout policy changed.")
    if raw is None or root is None:
        return {}
    root = Path(root).resolve()
    capture, capture_rows, manifest_hash = _validate_capture_identity(root, artifact)
    if artifact.get("base_raw_sha256") != fingerprint(raw) or artifact.get("base_gameplay_evidence") != raw.get("evidence"):
        raise ValueError("Race quantity refinement base source mismatch.")
    base_layout: Mapping[str, Any] | None = None
    if dynamic_section_policy:
        try:
            base_layout = resolve_section_layout(raw, row, SLOT_SPECS)
        except (SectionLayoutError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Race quantity refinement section layout guard failed.") from exc
        if base_layout is None:
            raise ValueError("Race quantity refinement section layout guard failed.")
    elif not layout_guard(raw, row):
        raise ValueError("Race quantity refinement layout guard failed.")
    row_key = _race_key(row)
    if row_key is None or artifact.get("race_key") != list(row_key):
        raise ValueError("Race quantity refinement race identity changed.")

    base_raw_path = _safe_path(root, artifact.get("base_raw_evidence"))
    if base_raw_path is None or not base_raw_path.is_file() or base_raw_path.stem != artifact.get("base_frame_id"):
        raise ValueError("Race quantity refinement base raw evidence is missing.")
    try:
        if fingerprint(_load_json(base_raw_path)) != fingerprint(raw):
            raise ValueError("Race quantity refinement base raw evidence changed.")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Race quantity refinement base raw evidence is unreadable.") from exc
    if not isinstance(artifact.get("reader_fingerprint"), str) or not artifact.get("reader_fingerprint"):
        raise ValueError("Race quantity refinement reader fingerprint is missing.")
    if not isinstance(artifact.get("model_sha256"), Mapping) or not artifact.get("model_sha256"):
        raise ValueError("Race quantity refinement model binding is missing.")
    if artifact.get("base_frame_id") != base_raw_path.stem or artifact.get("base_source_timestamp_ms") != raw.get("source_timestamp_ms"):
        raise ValueError("Race quantity refinement base frame identity changed.")

    source_frames = artifact.get("source_frames")
    if not isinstance(source_frames, list) or not source_frames:
        raise ValueError("Race quantity refinement source-frame provenance is missing.")
    validated_source_frames: dict[str, Mapping[str, Any]] = {}
    for entry in source_frames:
        if not isinstance(entry, Mapping):
            raise ValueError("Race quantity refinement source-frame provenance is malformed.")
        frame_id = entry.get("frame_id")
        if frame_id in validated_source_frames:
            raise ValueError("Race quantity refinement source-frame provenance is duplicated.")
        frame, _ = _validate_source_frame_entry(entry, capture, capture_rows, manifest_hash, root)
        validated_source_frames[frame_id] = frame
    if artifact.get("base_frame_id") not in validated_source_frames:
        raise ValueError("Race quantity refinement base frame is absent from source-frame provenance.")

    validated_section_layouts: dict[str, Mapping[str, Any]] = {}
    cached_raw_by_frame: dict[str, Mapping[str, Any]] = {}
    if dynamic_section_policy:
        stored_base_layout = artifact.get("base_section_layout")
        stored_frame_layouts = artifact.get("source_frame_layouts")
        if not isinstance(stored_base_layout, Mapping) or not isinstance(stored_frame_layouts, Mapping):
            raise ValueError("Race quantity refinement section layout provenance is missing.")
        if not _mapping_equal(stored_base_layout, base_layout):
            raise ValueError("Race quantity refinement base section layout changed.")
        if set(stored_frame_layouts) != set(validated_source_frames):
            raise ValueError("Race quantity refinement source section layout set changed.")
        for frame_id, frame in validated_source_frames.items():
            raw_path = (root / "neural" / f"{frame_id}.json").resolve()
            try:
                inside_root = raw_path.is_relative_to(root)
            except (OSError, ValueError):
                inside_root = False
            if not inside_root or not raw_path.is_file():
                raise ValueError("Race quantity refinement source raw cache is missing.")
            try:
                frame_raw = _load_json(raw_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Race quantity refinement source raw cache is unreadable.") from exc
            if not isinstance(frame_raw, Mapping) or frame_raw.get("source_timestamp_ms") != frame.get("source_timestamp_ms"):
                raise ValueError("Race quantity refinement source raw cache identity changed.")
            try:
                frame_row = parse(frame_raw)
                resolved = resolve_section_layout(frame_raw, frame_row, SLOT_SPECS)
            except (SectionLayoutError, KeyError, TypeError, ValueError) as exc:
                raise ValueError("Race quantity refinement source section layout changed.") from exc
            if resolved is None:
                raise ValueError("Race quantity refinement source section layout is absent.")
            if resolved.get("race_identity") != {
                "fans": artifact["race_key"][0],
                "fans_gained": artifact["race_key"][1],
            }:
                raise ValueError("Race quantity refinement source race identity changed.")
            stored_layout = stored_frame_layouts.get(frame_id)
            if not _mapping_equal(stored_layout, resolved):
                raise ValueError("Race quantity refinement source section layout changed.")
            validated_section_layouts[frame_id] = resolved
            cached_raw_by_frame[frame_id] = frame_raw

    # Reconstruct the expected source-frame/scale key set from the immutable
    # raw cache.  Checking only the artifact's copied key list would allow a
    # caller to delete observations and rewrite that list along with it.
    expected_keys_by_slot: dict[str, list[dict[str, Any]]] = {}
    specs_for_validation = SECTION_SLOT_SPECS if dynamic_section_policy else SLOT_SPECS
    for spec in specs_for_validation:
        keys: list[dict[str, Any]] = []
        for frame_id, frame in validated_source_frames.items():
            frame_raw = cached_raw_by_frame.get(frame_id)
            if frame_raw is None:
                raw_path = (root / "neural" / f"{frame_id}.json").resolve()
                if not raw_path.is_file():
                    raise ValueError("Race quantity refinement source raw cache is missing.")
                try:
                    frame_raw = _load_json(raw_path)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("Race quantity refinement source raw cache is unreadable.") from exc
                if not isinstance(frame_raw, Mapping) or frame_raw.get("source_timestamp_ms") != frame.get("source_timestamp_ms"):
                    raise ValueError("Race quantity refinement source raw cache identity changed.")
            gameplay_path = _safe_path(root, frame_raw.get("evidence"))
            frame_layout = validated_section_layouts.get(frame_id) if dynamic_section_policy else None
            variants = _source_crop_variants(
                frame_raw,
                spec,
                allow_fixed_fallback=allow_fixed_fallback,
                gameplay_path=gameplay_path,
                section_layout=frame_layout,
                quantity_policy=quantity_policy,
            )
            for variant in variants:
                for scale in SCALE_FACTORS:
                    key = {
                        "frame_id": frame_id,
                        "timestamp_ms": frame.get("source_timestamp_ms"),
                        "slot_id": spec["slot_id"],
                        "view_scale": scale,
                    }
                    if variant["variant"] is not None:
                        key["crop_variant"] = variant["variant"]
                    keys.append(key)
        expected_keys_by_slot[spec["slot_id"]] = keys

    slots = artifact.get("slots")
    all_observations = artifact.get("all_observations")
    if not isinstance(slots, list) or not isinstance(all_observations, list) or not all_observations:
        raise ValueError("Race quantity refinement observations are missing.")
    flattened: list[Mapping[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    seen_slots: set[str] = set()
    for slot in slots:
        if not isinstance(slot, Mapping):
            raise ValueError("Race quantity refinement slot is malformed.")
        slot_id = slot.get("slot_id")
        spec = (SECTION_SLOT_BY_ID if dynamic_section_policy else SLOT_BY_ID).get(slot_id)
        if spec is None or slot_id in seen_slots:
            raise ValueError("Race quantity refinement slot identity is invalid.")
        seen_slots.add(slot_id)
        if slot.get("slot") != spec["slot"] or slot.get("group") != spec["group"] or slot.get("group_index") != spec["group_index"] or \
                slot.get("strategy") != spec["strategy"] or slot.get("source_box") != list(spec["source_box"]) or \
                slot.get("full_box") != list(spec["full_box"]):
            raise ValueError("Race quantity refinement slot metadata changed.")
        observations = slot.get("observations")
        if not isinstance(observations, list):
            raise ValueError("Race quantity refinement slot observations are missing.")
        flattened.extend(observations)
        validated: list[Mapping[str, Any]] = []
        expected_keys = slot.get("expected_observation_keys")
        actual_keys = []
        for observation in observations:
            if not isinstance(observation, Mapping):
                raise ValueError("Race quantity refinement observation is malformed.")
            observation_layout = (
                validated_section_layouts.get(observation.get("frame_id"))
                if dynamic_section_policy
                else None
            )
            if dynamic_section_policy and observation_layout is None:
                raise ValueError("Race quantity refinement observation section layout is missing.")
            raw_observation = _validate_raw_observation(
                observation,
                spec,
                root,
                capture,
                capture_rows,
                manifest_hash,
                allow_fixed_fallback=allow_fixed_fallback,
                section_layout=observation_layout,
                quantity_policy=quantity_policy,
            )
            if observation.get("reader_fingerprint") != artifact.get("reader_fingerprint") or \
                    not _mapping_equal(observation.get("model_sha256"), artifact.get("model_sha256")):
                raise ValueError("Race quantity refinement model binding changed.")
            if observation.get("frame_id") not in validated_source_frames:
                raise ValueError("Race quantity refinement observation frame is outside source provenance.")
            if observation.get("frame_id") != Path(observation.get("raw_evidence", "")).stem:
                raise ValueError("Race quantity refinement raw frame binding changed.")
            if observation.get("frame_id") != artifact.get("base_frame_id"):
                # Support observations may be different frames, but they must
                # belong to the same recorded race result group represented by
                # the artifact's source-frame set.
                if observation.get("frame_id") not in validated_source_frames:
                    raise ValueError("Race quantity refinement support frame is outside the artifact group.")
            validated.append(observation)
            actual_key = {
                "frame_id": observation.get("frame_id"),
                "timestamp_ms": observation.get("timestamp_ms"),
                "slot_id": slot_id,
                "view_scale": observation.get("view_scale"),
            }
            if quantity_policy is not None:
                actual_key["crop_variant"] = observation.get("crop_variant")
            actual_keys.append(actual_key)
            if raw_observation.get("source_timestamp_ms") != observation.get("timestamp_ms"):
                raise ValueError("Race quantity refinement raw timestamp changed.")
        if not isinstance(expected_keys, list) or not _mapping_equal(expected_keys, actual_keys) or \
                not _mapping_equal(expected_keys, expected_keys_by_slot[slot_id]):
            raise ValueError("Race quantity refinement expected observation set changed.")
        recomputed = aggregate_slot(validated)
        for key in ("status", "minimum_confidence", "minimum_distinct_timestamps", "frame_observations", "conflicts", "accepted"):
            if not _mapping_equal(slot.get(key), recomputed.get(key)):
                raise ValueError("Race quantity refinement consensus changed.")
        summaries[slot_id] = recomputed
    if not _mapping_equal(all_observations, flattened):
        raise ValueError("Race quantity refinement observation index changed.")
    return summaries


def apply(row: Mapping[str, Any], artifact: Mapping[str, Any], *, raw: Mapping[str, Any] | None = None, root: str | Path | None = None) -> dict[str, Any]:
    """Apply only accepted quantities, without identity or completeness claims.

    Passing ``raw`` and ``root`` selects the production path.  That path
    requires the complete source-bound artifact and derives every accepted
    value again from its validated observations.  The small no-provenance form
    remains available for pure unit-level callers; it is deliberately not the
    path used by a recording analysis.
    """

    if not isinstance(row, Mapping) or not isinstance(artifact, Mapping):
        raise ValueError("Race quantity refinement requires mappings.")
    if (raw is None) != (root is None):
        raise ValueError("Race quantity refinement production apply requires both raw and root.")
    if artifact.get("section_anchor_policy") is not None and raw is None:
        raise ValueError("Race quantity refinement section-relative artifacts require production provenance.")
    summaries = _validate_provenance(row, artifact, raw, Path(root) if root is not None else None)
    if row.get("screen") != "race_result":
        return dict(row)
    facts = row.get("facts")
    if not isinstance(facts, Mapping) or not isinstance(facts.get("visible_item_quantities"), list):
        return dict(row)
    result = dict(row)
    new_facts = dict(facts)
    quantities = [dict(entry) if isinstance(entry, Mapping) else entry for entry in facts["visible_item_quantities"]]
    conflicts = list(new_facts.get("quantity_refinement_conflicts", []))
    for slot in artifact.get("slots", []):
        slot_id = slot.get("slot_id") if isinstance(slot, Mapping) else None
        accepted = summaries.get(slot_id, {}).get("accepted") if summaries else (slot.get("accepted") if isinstance(slot, Mapping) else None)
        if not isinstance(accepted, Mapping):
            continue
        if accepted.get("confidence", 0) < MIN_CONFIDENCE:
            continue
        timestamps = accepted.get("support_timestamps_ms", [])
        if len(set(timestamps)) < MIN_DISTINCT_TIMESTAMPS:
            continue
        quantity = accepted.get("quantity")
        if type(quantity) is not int or quantity < 0:
            continue
        if raw is not None and raw.get("source_timestamp_ms") not in timestamps:
            # A group consensus can be reused for provenance, but it cannot
            # manufacture a badge on a target frame where that frame itself
            # did not provide a high-confidence supporting reading.
            continue
        box = slot.get("full_box")
        if artifact.get("section_anchor_policy") is not None and raw is not None:
            # Dynamic layouts may move an entire section.  Use the validated
            # target-frame observation's actual OCR/fallback box; the slot's
            # canonical box remains metadata for slot identity only.
            target_observations = [
                observation
                for observation in slot.get("observations", [])
                if isinstance(observation, Mapping)
                and observation.get("timestamp_ms") == raw.get("source_timestamp_ms")
                and observation.get("quantity") == quantity
                and float(observation.get("confidence", 0)) >= MIN_CONFIDENCE
            ]
            if not target_observations:
                continue
            box = target_observations[0].get("full_box")
        if not isinstance(box, Sequence) or len(box) != 4:
            continue
        existing_indexes = [
            index
            for index, entry in enumerate(quantities)
            if isinstance(entry, Mapping) and _same_position(entry.get("box", ()), box)
        ]
        if existing_indexes:
            for index in existing_indexes:
                existing = quantities[index]
                if existing.get("quantity") != quantity:
                    conflicts.append({"slot_id": slot.get("slot_id"), "existing": existing.get("quantity"), "refined": quantity})
            continue
        quantities.append({
            "quantity": quantity,
            "name": None,
            "box": list(box),
            "raw_text": accepted.get("text"),
            "confidence": accepted.get("confidence"),
            "refinement": "race_quantity_source_consensus",
            "refinement_support_timestamps_ms": list(timestamps),
        })
    def position_key(item: tuple[int, Any]) -> tuple[Any, ...]:
        index, entry = item
        box = entry.get("box") if isinstance(entry, Mapping) else None
        try:
            if not isinstance(box, Sequence) or len(box) != 4:
                raise ValueError
            coordinates = tuple(float(value) for value in box)
            if not all(math.isfinite(value) for value in coordinates):
                raise ValueError
            # OCR's source order is row-major: top-to-bottom, then left-to-right.
            # A rendered badge can move a few pixels vertically between frames,
            # so classify it against the two fixed result rows before sorting by
            # its horizontal source position.
            center_y = (coordinates[1] + coordinates[3]) / 2
            row_centers = {
                0: sum((spec["full_box"][1] + spec["full_box"][3]) / 2 for spec in SLOT_SPECS if spec["group"] == "items") / 3,
                1: sum((spec["full_box"][1] + spec["full_box"][3]) / 2 for spec in SLOT_SPECS if spec["group"] == "bonus") / 2,
            }
            row = min(row_centers, key=lambda candidate: abs(center_y - row_centers[candidate]))
            return (0, row, coordinates[0], center_y, coordinates[2], coordinates[3], index)
        except (TypeError, ValueError, IndexError):
            return (1, index)
    quantities = [entry for _, entry in sorted(enumerate(quantities), key=position_key)]
    new_facts["visible_item_quantities"] = quantities
    new_facts["item_identity_verified"] = False
    new_facts["item_rewards_complete"] = False
    if conflicts:
        new_facts["quantity_refinement_conflicts"] = conflicts
    result["facts"] = new_facts
    return result


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="full-recording run root containing capture.json, neural/, and gameplay/")
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    parser.add_argument("--frame-id", action="append", dest="frame_ids", help="Limit OCR and support evidence to these frames; repeat for all desired adjacent samples.")
    args = parser.parse_args(argv)
    print(json.dumps(generate(args.root, model_dir=args.model_dir, frame_ids=args.frame_ids), ensure_ascii=False))


if __name__ == "__main__":
    main()
