"""Source-pixel localization for animated training gain badges.

The result cards used by the normal reader contain two useful but different
views of a training award.  The tight ``gain.<field>`` crop is usually the
best OCR source, while the wider card can be contaminated by a large animated
overlay.  A small orange connected-component crop gives the recognizer a
bounded third view of the signed badge without asking it to interpret a
balance or a result counter.

This module has two deliberately separate boundaries:

* :func:`localize_training_badges` reads pixels and returns source-bound
  observations.  Its recognizer is injected so fresh OCR and tests use the
  same geometry without smuggling an expected amount into the reader.
* :func:`apply`/ :func:`load` validate a persisted observation against the
  original raw record, decoded gameplay pixels, crop hash, and optional
  captured source frame before attaching it to a working raw record.

The localized observation is a supplemental ``localized_gain`` family.  It is
never a replacement for the existing clipping and phase guards; the crop
resolver only uses it when a source-localized value agrees with a canonical
tight gain and the competing value belongs to a broader overlapping crop.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


VERSION = 1
SCHEMA = "tracen-replay/training-badge-localization-v1"
PANE_BOUNDS = (148.0, 0.0, 958.0, 1080.0)
PANE_OFFSET = 148

# Full gameplay coordinates.  The decoded reader pane starts at x=148.  These
# are the same card geometries used by NeuralReader's ordinary gain crops.
FIELDS = ("speed", "stamina", "power", "guts", "wit", "skill_points")
GAIN_BOXES = {
    "speed": (300, 832, 414, 890),
    "stamina": (498, 832, 610, 890),
    "power": (696, 832, 812, 890),
    "guts": (300, 950, 414, 1008),
    "wit": (498, 950, 610, 1008),
    "skill_points": (696, 950, 812, 1008),
}
WIDE_GAIN_BOXES = {
    "speed": (250, 812, 462, 909),
    "stamina": (448, 812, 660, 909),
    "power": (646, 812, 858, 909),
    "guts": (250, 930, 462, 1027),
    "wit": (448, 930, 660, 1027),
    "skill_points": (646, 930, 858, 1027),
}

# The source card's gold chevron occupies the lower part of each broad box.
# Looking at the stable badge band keeps the chevron and most moving artwork
# out of the component graph while retaining the complete signed text.
BADGE_BAND_TOP = 25
BADGE_BAND_BOTTOM = 73
COMPONENT_MIN_AREA = 15
COMPONENT_MIN_WIDTH = 4
COMPONENT_MIN_HEIGHT = 4
GROUP_MAX_HORIZONTAL_GAP = 16
GROUP_MAX_VERTICAL_GAP = 8
GROUP_MIN_WIDTH = 30
GROUP_MIN_HEIGHT = 18
GROUP_MIN_PIXELS = 100
OCR_MIN_CONFIDENCE = 97.0
_SIGNED_RE = re.compile(r"^\s*\+\s*(\d{1,3})\s*$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Training badge {name} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Training badge {name} must be finite.")
    return number


def _box(value: Any, *, name: str = "") -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"Training badge {name} box is invalid.")
    result = [_number(item, name=f"{name} box coordinate") for item in value]
    left, top, right, bottom = result
    if not left < right or not top < bottom:
        raise ValueError(f"Training badge {name} box is empty.")
    if not (
        PANE_BOUNDS[0] <= left < right <= PANE_BOUNDS[2]
        and PANE_BOUNDS[1] <= top < bottom <= PANE_BOUNDS[3]
    ):
        raise ValueError(f"Training badge {name} box leaves gameplay bounds.")
    return result


def _integer_box(value: Sequence[float], *, name: str = "") -> tuple[int, int, int, int]:
    box = _box(value, name=name)
    # Localizer boxes are produced by integer pixel components.  Persisting a
    # non-integer box would make the crop hash dependent on a caller's slice
    # coercion, so reject it at the source-proof boundary.
    if any(float(item) != int(item) for item in box):
        raise ValueError(f"Training badge {name} box must use integer pixels.")
    return tuple(int(item) for item in box)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _confidence_percent(value: Any) -> float:
    """Normalize recognizer scores to the repository's 0..100 convention."""

    number = _number(value, name="confidence")
    if 0.0 <= number <= 1.0:
        number *= 100.0
    if not 0.0 <= number <= 100.0:
        raise ValueError("Training badge confidence is outside 0..100.")
    return number


def gameplay_fingerprint(pane: Any) -> str:
    """Hash decoded RGB gameplay pixels, independent of image encoding."""

    rgb = _rgb_array(pane)
    return hashlib.sha256(rgb.tobytes()).hexdigest()


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _rgb_array(pane: Any):
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - production has RapidOCR's numpy
        raise ValueError("Training badge localization requires numpy.") from exc
    if hasattr(pane, "convert"):
        array = np.asarray(pane.convert("RGB"))
    else:
        array = np.asarray(pane)
        if array.ndim == 3 and array.shape[2] == 4:
            array = array[:, :, :3]
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("Training badge localization expects RGB gameplay pixels.")
    if tuple(array.shape[:2]) != (1080, 810):
        raise ValueError("Training badge localization expects an 810x1080 gameplay pane.")
    if array.dtype != np.uint8:
        array = array.astype(np.uint8)
    return array


def _orange_mask(rgb):
    """Return a conservative mask for the orange signed badge glyphs."""

    red = rgb[:, :, 0].astype(float)
    green = rgb[:, :, 1].astype(float)
    blue = rgb[:, :, 2].astype(float)
    return (
        (red >= 170)
        & (red > 1.3 * green)
        & (green > 1.1 * blue)
        & (blue < 160)
    ).astype("uint8")


def _component_stats(mask) -> list[tuple[int, int, int, int, int]]:
    """Return connected components with a small dependency-free fallback."""

    try:
        import cv2

        _count, _labels, stats, _centers = cv2.connectedComponentsWithStats(mask)
        return [tuple(int(value) for value in item) for item in stats[1:]]
    except ImportError:  # pragma: no cover - exercised only without OpenCV
        import numpy as np
        from collections import deque

        active = mask.astype(bool)
        height, width = active.shape
        seen = np.zeros_like(active, dtype=bool)
        result: list[tuple[int, int, int, int, int]] = []
        for top in range(height):
            for left in range(width):
                if not active[top, left] or seen[top, left]:
                    continue
                seen[top, left] = True
                queue = deque([(left, top)])
                points: list[tuple[int, int]] = []
                while queue:
                    x, y = queue.popleft()
                    points.append((x, y))
                    for nx in range(max(0, x - 1), min(width, x + 2)):
                        for ny in range(max(0, y - 1), min(height, y + 2)):
                            if active[ny, nx] and not seen[ny, nx]:
                                seen[ny, nx] = True
                                queue.append((nx, ny))
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                result.append((min(xs), min(ys), max(xs) - min(xs) + 1,
                              max(ys) - min(ys) + 1, len(points)))
        return result


def _accepted_components(mask) -> list[tuple[int, int, int, int, int]]:
    return [
        tuple(int(value) for value in item)
        for item in _component_stats(mask)
        if item[4] >= COMPONENT_MIN_AREA
        and item[2] >= COMPONENT_MIN_WIDTH
        and item[3] >= COMPONENT_MIN_HEIGHT
    ]


def _group_components(parts: Iterable[tuple[int, int, int, int, int]]) -> list[dict[str, int]]:
    remaining = [tuple(int(value) for value in item) for item in parts]
    groups: list[dict[str, int]] = []
    while remaining:
        group = [remaining.pop()]
        changed = True
        while changed:
            changed = False
            for part in remaining[:]:
                x, y, width, height, _area = part
                if any(
                    max(x, gx) - min(x + width, gx + gwidth)
                    <= GROUP_MAX_HORIZONTAL_GAP
                    and max(y, gy) - min(y + height, gy + gheight)
                    <= GROUP_MAX_VERTICAL_GAP
                    for gx, gy, gwidth, gheight, _ in group
                ):
                    group.append(part)
                    remaining.remove(part)
                    changed = True
        left = min(item[0] for item in group)
        top = min(item[1] for item in group)
        right = max(item[0] + item[2] for item in group)
        bottom = max(item[1] + item[3] for item in group)
        pixels = sum(item[4] for item in group)
        if (
            right - left >= GROUP_MIN_WIDTH
            and bottom - top >= GROUP_MIN_HEIGHT
            and pixels >= GROUP_MIN_PIXELS
        ):
            groups.append({
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "pixels": pixels,
                "component_count": len(group),
            })
    return groups


def _center(box: Sequence[float]) -> tuple[float, float]:
    return ((float(box[0]) + float(box[2])) / 2.0,
            (float(box[1]) + float(box[3])) / 2.0)


def _overlap(left: Sequence[float], right: Sequence[float]) -> float:
    left_x, left_y, left_right, left_bottom = [float(item) for item in left]
    right_x, right_y, right_right, right_bottom = [float(item) for item in right]
    width = max(0.0, min(left_right, right_right) - max(left_x, right_x))
    height = max(0.0, min(left_bottom, right_bottom) - max(left_y, right_y))
    return width * height


def _source_boxes(value: Any) -> list[list[float]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        value = value.get("boxes", value.get("animated_overlay_boxes", []))
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        try:
            result.append(_box(item, name="blocked source"))
        except ValueError:
            # A malformed suppression hint cannot authorize a crop.  It is
            # ignored for discovery, while persisted proof validation rejects
            # malformed candidate geometry itself.
            continue
    return result


def _scan_box(field: str) -> tuple[int, int, int, int]:
    broad = WIDE_GAIN_BOXES[field]
    return (
        broad[0],
        broad[1] + BADGE_BAND_TOP,
        broad[2],
        broad[1] + BADGE_BAND_BOTTOM,
    )


def _gate(*, header: Any, result_grid: Any, screen: Any, preview: Any) -> tuple[bool, str | None]:
    if preview is True:
        return False, "preview_screen"
    if isinstance(screen, str) and screen.strip().casefold() in {
        "training_preview", "lesson_selection", "lesson_confirmation",
        "skill_selection", "skill_confirmation", "skill_receipt",
    }:
        return False, "preview_or_menu_screen"
    header_text = _text(header).casefold()
    if not header_text.startswith("training"):
        return False, "training_header_not_proven"
    if result_grid is not True:
        return False, "training_result_grid_not_proven"
    return True, None


def discover_training_badge_crops(
    pane: Any,
    *,
    header: Any,
    result_grid: Any,
    screen: Any = None,
    preview: Any = False,
    blocked_boxes: Any = None,
) -> dict[str, Any]:
    """Find one unambiguous orange badge crop per stat-card field.

    The function only discovers geometry.  It never assigns a number from
    pixels and never picks a candidate by width or magnitude.  If a broad card
    contains two qualifying components, the field stays unresolved.
    """

    allowed, gate_reason = _gate(
        header=header, result_grid=result_grid, screen=screen, preview=preview,
    )
    result: dict[str, Any] = {
        "schema_version": SCHEMA,
        "version": VERSION,
        "status": "unresolved",
        "observations": [],
        "rejections": {},
        "policy": {
            "color_basis": "orange_connected_component",
            "badge_band_top_px": BADGE_BAND_TOP,
            "badge_band_bottom_px": BADGE_BAND_BOTTOM,
            "minimum_component_pixels": COMPONENT_MIN_AREA,
            "minimum_group_width_px": GROUP_MIN_WIDTH,
            "minimum_group_height_px": GROUP_MIN_HEIGHT,
            "minimum_group_pixels": GROUP_MIN_PIXELS,
            "ocr_min_confidence": OCR_MIN_CONFIDENCE,
            "uses_expected_amount": False,
            "uses_balance_arithmetic": False,
        },
    }
    if not allowed:
        result["status"] = "gated"
        result["rejections"]["_screen"] = gate_reason
        return result

    rgb = _rgb_array(pane)
    mask = _orange_mask(rgb)
    blocked = _source_boxes(blocked_boxes)
    for field in FIELDS:
        wide = WIDE_GAIN_BOXES[field]
        tight = GAIN_BOXES[field]
        scan = _scan_box(field)
        x0, y0, x1, y1 = scan
        # Convert full gameplay x coordinates to the isolated pane's x axis.
        strip = mask[y0:y1, x0 - PANE_OFFSET:x1 - PANE_OFFSET]
        groups = _group_components(_accepted_components(strip))
        candidates = []
        for group in groups:
            group_box = [
                float(wide[0] + group["left"]),
                float(y0 + group["top"]),
                float(wide[0] + group["right"]),
                float(y0 + group["bottom"]),
            ]
            center = _center(group_box)
            if tight[0] <= center[0] <= tight[2] and tight[1] <= center[1] <= tight[3]:
                candidates.append((group, group_box))
        if len(groups) != 1:
            result["rejections"][field] = (
                "ambiguous_components" if len(candidates) > 1 or len(groups) > 1
                else "no_unique_badge_component"
            )
            continue
        group, group_box = candidates[0] if candidates else (groups[0], None)
        if group_box is None:
            result["rejections"][field] = "component_outside_field_geometry"
            continue
        if any(_overlap(group_box, blocked_box) > 0.0 for blocked_box in blocked):
            result["rejections"][field] = "badge_component_occluded_by_source_overlay"
            continue
        left = max(int(group_box[0]) - 6, int(wide[0]))
        # Keep the small context margin inside the broad card.  The margin is
        # intentionally allowed to extend beyond the component scan band: a
        # recognizer needs the signed badge's anti-aliased edge pixels, while
        # the component itself remains bound to the stable band above.
        top = max(int(group_box[1]) - 6, int(wide[1]))
        right = min(int(group_box[2]) + 6, int(wide[2]))
        bottom = min(int(group_box[3]) + 6, int(wide[3]))
        if left >= right or top >= bottom:
            result["rejections"][field] = "badge_crop_empty"
            continue
        result["observations"].append({
            "field": field,
            "box": [left, top, right, bottom],
            "component_box": [int(value) for value in group_box],
            "scan_box": list(scan),
            "component_pixels": group["pixels"],
            "component_count": group["component_count"],
            "source_pixel_basis": "orange_connected_component_in_training_gain_card",
        })
    result["status"] = "candidate" if result["observations"] else "unresolved"
    return result


def _recognition_pair(value: Any) -> tuple[str, float]:
    if isinstance(value, Mapping):
        text = value.get("text", value.get("raw_text", ""))
        confidence = value.get("confidence", value.get("score"))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        text, confidence = value[0], value[1]
    else:
        raise ValueError("Training badge recognizer returned an invalid result.")
    return _text(text), _confidence_percent(confidence)


def _canonical_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "source_timestamp_ms", "evidence", "source_sha256", "gameplay_sha256",
        "source_frame_sha256", "source_frame_evidence", "source_frame_id",
        "evidence_sha256", "raw_sha256", "engine_fingerprint",
    ):
        if key not in metadata:
            continue
        value = metadata[key]
        if key == "source_timestamp_ms":
            if type(value) is int and value >= 0:
                result[key] = value
        elif isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def localize_training_badges(
    pane: Any,
    *,
    recognize: Callable[[Sequence[Any]], Iterable[Any]],
    header: Any,
    result_grid: Any,
    screen: Any = None,
    preview: Any = False,
    blocked_boxes: Any = None,
    source_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover and OCR localized training badges from one source pane.

    ``recognize`` receives only the discovered RGB crops converted to the
    caller's preferred image representation.  It returns one ``(text,
    confidence)`` pair or mapping per crop.  A malformed, weak, or unsigned
    result remains in ``rejections`` and is never turned into a numeric
    candidate.
    """

    discovery = discover_training_badge_crops(
        pane,
        header=header,
        result_grid=result_grid,
        screen=screen,
        preview=preview,
        blocked_boxes=blocked_boxes,
    )
    result = copy.deepcopy(discovery)
    result["observations"] = []
    result["metadata"] = _canonical_metadata(source_metadata)
    try:
        rgb = _rgb_array(pane)
        result["metadata"].setdefault("gameplay_sha256", hashlib.sha256(rgb.tobytes()).hexdigest())
        candidates = discovery.get("observations", [])
        crops = []
        for candidate in candidates:
            left, top, right, bottom = _integer_box(candidate["box"], name="localized")
            crops.append(rgb[top:bottom, left - PANE_OFFSET:right - PANE_OFFSET].copy())
        recognized = list(recognize(crops)) if crops else []
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        result["status"] = "unresolved"
        result["rejections"]["_recognizer"] = type(exc).__name__
        return result
    if len(recognized) != len(candidates):
        result["status"] = "unresolved"
        result["rejections"]["_recognizer"] = "result_count_mismatch"
        return result

    for candidate, crop, recognized_value in zip(candidates, crops, recognized):
        field = candidate["field"]
        try:
            text, confidence = _recognition_pair(recognized_value)
        except ValueError as exc:
            result["rejections"][field] = "invalid_recognizer_result"
            continue
        match = _SIGNED_RE.fullmatch(text)
        if match is None:
            result["rejections"][field] = "unsigned_or_clipped_badge_text"
            continue
        if confidence < OCR_MIN_CONFIDENCE:
            result["rejections"][field] = "localized_badge_confidence_below_floor"
            continue
        box = list(candidate["box"])
        observation = dict(candidate)
        observation.update({
            "text": text,
            "raw_text": text,
            "amount": int(match.group(1)),
            "confidence": round(confidence, 4),
            "crop_family": "localized_gain",
            "source_role": "amount_crop_candidate",
            "input_eligible": True,
            "canonical_eligible": True,
            "source_pixel_verified": True,
            "pixel_rgb_sha256": hashlib.sha256(crop.tobytes()).hexdigest(),
            "gameplay_sha256": result["metadata"].get("gameplay_sha256"),
            "localization_schema": SCHEMA,
            "source_observation_basis": "source_pixel_localized_training_badge",
            "normalization": "signed_amount",
            "suffix": "",
        })
        for key in (
            "source_timestamp_ms", "evidence", "source_sha256",
            "source_frame_sha256", "source_frame_evidence", "source_frame_id",
            "evidence_sha256", "raw_sha256", "engine_fingerprint",
        ):
            if key in result["metadata"]:
                observation[key] = result["metadata"][key]
        result["observations"].append(observation)

    # Discovery deliberately emits one crop per field.  A sidecar can carry
    # multiple records, so reject a conflicting localized value here rather
    # than allowing insertion order to choose the last crop.
    by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in result["observations"]:
        by_field[observation["field"]].append(observation)
    for field, values in by_field.items():
        if len({item["amount"] for item in values}) > 1:
            result["rejections"][field] = "conflicting_localized_badge_reads"
            result["observations"] = [item for item in result["observations"] if item["field"] != field]
    result["status"] = "localized" if result["observations"] else "unresolved"
    return result


def _observation_identity(observation: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        observation.get("field"), observation.get("source_timestamp_ms"),
        observation.get("evidence"), observation.get("box"),
        observation.get("pixel_rgb_sha256"), observation.get("amount"),
    )


def _validate_observation(observation: Mapping[str, Any], *, gameplay_sha256: str | None = None) -> dict[str, Any]:
    if not isinstance(observation, Mapping):
        raise ValueError("Training badge localization observation is invalid.")
    field = observation.get("field")
    if field not in FIELDS:
        raise ValueError("Training badge localization field is invalid.")
    text = _text(observation.get("text", observation.get("raw_text")))
    match = _SIGNED_RE.fullmatch(text)
    if match is None:
        raise ValueError("Training badge localization text is not a complete signed amount.")
    amount = observation.get("amount")
    if type(amount) is not int or amount != int(match.group(1)) or amount < 0 or amount > 999:
        raise ValueError("Training badge localization amount is inconsistent.")
    confidence = _confidence_percent(observation.get("confidence"))
    if confidence < OCR_MIN_CONFIDENCE:
        raise ValueError("Training badge localization confidence is below the source floor.")
    box = _integer_box(observation.get("box"), name="localized")
    component = _integer_box(observation.get("component_box"), name="component")
    scan = _integer_box(observation.get("scan_box"), name="scan")
    if not (
        GAIN_BOXES[field][0] <= (component[0] + component[2]) / 2 <= GAIN_BOXES[field][2]
        and GAIN_BOXES[field][1] <= (component[1] + component[3]) / 2 <= GAIN_BOXES[field][3]
        and scan[0] == _scan_box(field)[0]
        and scan[1] == _scan_box(field)[1]
        and scan[2] == _scan_box(field)[2]
        and scan[3] == _scan_box(field)[3]
    ):
        raise ValueError("Training badge localization component geometry is not field-bound.")
    wide = WIDE_GAIN_BOXES[field]
    if not (wide[0] <= box[0] <= box[2] <= wide[2] and wide[1] <= box[1] <= box[3] <= wide[3]):
        raise ValueError("Training badge localization crop leaves its source card.")
    if observation.get("crop_family") != "localized_gain":
        raise ValueError("Training badge localization crop family is invalid.")
    if observation.get("source_role") != "amount_crop_candidate":
        raise ValueError("Training badge localization source role is invalid.")
    if observation.get("input_eligible") is not True or observation.get("canonical_eligible") is not True:
        raise ValueError("Training badge localization candidate is not eligible.")
    if observation.get("source_pixel_verified") is not True:
        raise ValueError("Training badge localization lacks source-pixel proof.")
    if observation.get("source_observation_basis") != "source_pixel_localized_training_badge":
        raise ValueError("Training badge localization basis is invalid.")
    pixel_sha = observation.get("pixel_rgb_sha256")
    if not isinstance(pixel_sha, str) or _SHA256_RE.fullmatch(pixel_sha) is None:
        raise ValueError("Training badge localization crop hash is invalid.")
    declared_gameplay = observation.get("gameplay_sha256", gameplay_sha256)
    if not isinstance(declared_gameplay, str) or _SHA256_RE.fullmatch(declared_gameplay) is None:
        raise ValueError("Training badge localization gameplay hash is invalid.")
    if gameplay_sha256 is not None and declared_gameplay != gameplay_sha256:
        raise ValueError("Training badge localization gameplay hash disagrees with sidecar.")
    result = copy.deepcopy(dict(observation))
    result.update({
        "field": field,
        "text": text,
        "raw_text": text,
        "amount": amount,
        "confidence": round(confidence, 4),
        "box": list(box),
        "component_box": list(component),
        "scan_box": list(scan),
    })
    return result


def attach(raw: Mapping[str, Any], localization: Mapping[str, Any]) -> dict[str, Any]:
    """Attach validated in-memory localized observations without overwriting."""

    if not isinstance(raw, Mapping) or not isinstance(localization, Mapping):
        raise ValueError("Training badge localization inputs are invalid.")
    if localization.get("schema_version") != SCHEMA or localization.get("version") != VERSION:
        raise ValueError("Training badge localization schema is unsupported.")
    if localization.get("status") not in {"localized", "candidate"}:
        raise ValueError("Training badge localization is unresolved.")
    gameplay_sha = raw.get("gameplay_sha256")
    if not isinstance(gameplay_sha, str) or _SHA256_RE.fullmatch(gameplay_sha) is None:
        raise ValueError("Training badge localization raw gameplay hash is missing.")
    declared = localization.get("metadata", {}).get("gameplay_sha256") if isinstance(localization.get("metadata"), Mapping) else None
    if declared != gameplay_sha:
        raise ValueError("Training badge localization does not bind to raw gameplay pixels.")
    result = copy.deepcopy(dict(raw))
    regions = copy.deepcopy(result.get("regions", {}))
    if not isinstance(regions, dict):
        raise ValueError("Training badge localization raw regions are invalid.")
    observations = localization.get("observations")
    if not isinstance(observations, list):
        raise ValueError("Training badge localization observations are missing.")
    accepted: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for item in observations:
        observation = _validate_observation(item, gameplay_sha256=gameplay_sha)
        field = observation["field"]
        if field in seen_fields:
            raise ValueError("Training badge localization has duplicate field observations.")
        name = f"localized_gain.{field}"
        if name in regions:
            raise ValueError("Training badge localization would overwrite an existing region.")
        seen_fields.add(field)
        accepted.append(observation)
        regions[name] = copy.deepcopy(observation)
    result["regions"] = regions
    metadata = copy.deepcopy(dict(localization))
    metadata["observations"] = copy.deepcopy(accepted)
    result["training_badge_localization"] = metadata
    return result


def _read_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    path = Path(value)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Training badge localization sidecar is unreadable.") from exc
    if not isinstance(loaded, Mapping):
        raise ValueError("Training badge localization sidecar is not an object.")
    return loaded


def build_sidecar(
    raw: Mapping[str, Any],
    localization: Mapping[str, Any],
    *,
    evidence_path: str | Path,
    source_frame_path: str | Path | None = None,
    source_frame_id: str | None = None,
    source_frame_evidence: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the standard top-level sidecar consumed by cached replay.

    Fresh readers can attach ``localization`` directly to a raw record.  A
    bounded preparation pass needs the equivalent persisted envelope, with
    the immutable raw/evidence/frame hashes at the top level so replay
    manifests can validate it before invoking :func:`load`.
    """

    if not isinstance(raw, Mapping) or not isinstance(localization, Mapping):
        raise ValueError("Training badge localization inputs are invalid.")
    if localization.get("schema_version") != SCHEMA or localization.get("version") != VERSION:
        raise ValueError("Training badge localization schema is unsupported.")
    if localization.get("status") != "localized":
        raise ValueError("Training badge localization has no accepted observations.")
    timestamp = raw.get("source_timestamp_ms")
    evidence = raw.get("evidence")
    gameplay_sha = raw.get("gameplay_sha256")
    if type(timestamp) is not int or timestamp < 0:
        raise ValueError("Training badge localization raw timestamp is missing.")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("Training badge localization raw evidence is missing.")
    if not isinstance(gameplay_sha, str) or _SHA256_RE.fullmatch(gameplay_sha) is None:
        raise ValueError("Training badge localization raw gameplay hash is missing.")
    evidence_path = Path(evidence_path)
    try:
        from PIL import Image

        with Image.open(evidence_path) as image:
            pane = image.convert("RGB")
            if pane.size != (810, 1080):
                raise ValueError("Training badge localization evidence is not a gameplay pane.")
            if hashlib.sha256(pane.tobytes()).hexdigest() != gameplay_sha:
                raise ValueError("Training badge localization evidence pixels disagree with raw input.")
    except OSError as exc:
        raise ValueError("Training badge localization evidence is unreadable.") from exc
    source_hash = source_sha256 if source_sha256 is not None else raw.get("source_sha256")
    if source_hash is not None and (
        not isinstance(source_hash, str) or _SHA256_RE.fullmatch(source_hash) is None
    ):
        raise ValueError("Training badge localization source hash is invalid.")
    raw_source_hash = raw.get("source_sha256")
    if source_hash is not None and raw_source_hash is not None and source_hash != raw_source_hash:
        raise ValueError("Training badge localization source hash disagrees with raw input.")
    frame_hash = raw.get("source_frame_sha256")
    if frame_hash is not None and (
        not isinstance(frame_hash, str) or _SHA256_RE.fullmatch(frame_hash) is None
    ):
        raise ValueError("Training badge localization source-frame hash is invalid.")
    if frame_hash is not None:
        if source_frame_path is None or not Path(source_frame_path).is_file():
            raise ValueError("Training badge localization source-frame evidence is missing.")
        if file_fingerprint(source_frame_path) != frame_hash:
            raise ValueError("Training badge localization source-frame pixels disagree with raw input.")
    frame_evidence = source_frame_evidence
    if frame_evidence is None:
        candidate = raw.get("source_frame_evidence")
        frame_evidence = candidate if isinstance(candidate, str) else None
    raw_frame_evidence = raw.get("source_frame_evidence")
    if (
        isinstance(raw_frame_evidence, str)
        and frame_evidence is not None
        and frame_evidence != raw_frame_evidence
    ):
        raise ValueError("Training badge localization source-frame evidence disagrees with raw input.")
    metadata = _canonical_metadata(localization.get("metadata"))
    metadata.update(
        source_timestamp_ms=timestamp,
        evidence=evidence,
        evidence_sha256=file_fingerprint(evidence_path),
        gameplay_sha256=gameplay_sha,
        raw_sha256=fingerprint(raw),
    )
    if source_hash is not None:
        metadata["source_sha256"] = source_hash
    if frame_hash is not None:
        metadata["source_frame_sha256"] = frame_hash
    if frame_evidence is not None:
        metadata["source_frame_evidence"] = frame_evidence
    if source_frame_id is not None:
        metadata["source_frame_id"] = source_frame_id
    sidecar = {
        "schema_version": SCHEMA,
        "version": VERSION,
        "status": "localized",
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "evidence_sha256": metadata["evidence_sha256"],
        "gameplay_sha256": gameplay_sha,
        "raw_sha256": fingerprint(raw),
        "source_frame_id": source_frame_id,
        "source_frame_evidence": frame_evidence,
        "source_frame_sha256": frame_hash,
        "source_sha256": source_hash,
        "metadata": metadata,
        "observations": copy.deepcopy(localization.get("observations", [])),
        "policy": copy.deepcopy(localization.get("policy", {})),
    }
    # Exercise the exact cached-load validation before returning a sidecar.
    # This also canonicalizes the observation records under the same proof
    # boundary used by replay.
    checked = apply(
        raw,
        sidecar,
        evidence_path=evidence_path,
        source_frame_path=source_frame_path,
        source_frame_id=source_frame_id,
        source_frame_evidence=frame_evidence,
        original=raw,
    )
    sidecar["observations"] = copy.deepcopy(
        checked["training_badge_localization"]["observations"]
    )
    return sidecar


def apply(
    raw: Mapping[str, Any],
    sidecar: Mapping[str, Any] | str | Path,
    *,
    evidence_path: str | Path | None = None,
    source_frame_path: str | Path | None = None,
    source_frame_id: str | None = None,
    source_frame_evidence: str | None = None,
    original: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and attach a persisted source-localized observation."""

    sidecar = _read_json(sidecar)
    if sidecar.get("schema_version") != SCHEMA or sidecar.get("version") != VERSION:
        raise ValueError("Training badge localization schema is unsupported.")
    if sidecar.get("status") not in {"localized", "candidate"}:
        raise ValueError("Training badge localization sidecar is unresolved.")
    metadata = sidecar.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    declared_raw_sha = sidecar.get("raw_sha256", metadata.get("raw_sha256"))
    if original is not None and declared_raw_sha is not None:
        if declared_raw_sha != fingerprint(original):
            raise ValueError("Training badge localization raw observation changed.")
    source_timestamp = metadata.get("source_timestamp_ms", sidecar.get("source_timestamp_ms"))
    evidence = metadata.get("evidence", sidecar.get("evidence"))
    gameplay_sha = metadata.get("gameplay_sha256", sidecar.get("gameplay_sha256"))
    if type(source_timestamp) is not int or source_timestamp < 0:
        raise ValueError("Training badge localization source timestamp is missing.")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("Training badge localization evidence path is missing.")
    if not isinstance(gameplay_sha, str) or _SHA256_RE.fullmatch(gameplay_sha) is None:
        raise ValueError("Training badge localization gameplay hash is missing.")
    if raw.get("source_timestamp_ms") != source_timestamp or raw.get("evidence") != evidence:
        raise ValueError("Training badge localization source identity disagrees with raw input.")
    if raw.get("gameplay_sha256") != gameplay_sha:
        raise ValueError("Training badge localization gameplay hash disagrees with raw input.")
    if evidence_path is None:
        raise ValueError("Training badge localization gameplay evidence is required.")
    evidence_path = Path(evidence_path)
    try:
        from PIL import Image

        with Image.open(evidence_path) as image:
            pane = image.convert("RGB")
            if pane.size != (810, 1080):
                raise ValueError("Training badge localization evidence is not a gameplay pane.")
            if hashlib.sha256(pane.tobytes()).hexdigest() != gameplay_sha:
                raise ValueError("Training badge localization gameplay pixels changed.")
            evidence_file_sha = file_fingerprint(evidence_path)
            declared_evidence_sha = metadata.get("evidence_sha256", sidecar.get("evidence_sha256"))
            if declared_evidence_sha is not None and declared_evidence_sha != evidence_file_sha:
                raise ValueError("Training badge localization evidence file changed.")
            rgb = _rgb_array(pane)
            observations = sidecar.get("observations")
            if not isinstance(observations, list) or not observations:
                raise ValueError("Training badge localization has no observations.")
            checked = []
            for item in observations:
                observation = _validate_observation(item, gameplay_sha256=gameplay_sha)
                left, top, right, bottom = _integer_box(observation["box"], name="localized")
                crop = rgb[top:bottom, left - PANE_OFFSET:right - PANE_OFFSET]
                if hashlib.sha256(crop.tobytes()).hexdigest() != observation["pixel_rgb_sha256"]:
                    raise ValueError("Training badge localization crop pixels changed.")
                checked.append(observation)
    except OSError as exc:
        raise ValueError("Training badge localization gameplay evidence is unreadable.") from exc
    declared_source_frame_id = metadata.get("source_frame_id", sidecar.get("source_frame_id"))
    if source_frame_id is not None and declared_source_frame_id not in (None, source_frame_id):
        raise ValueError("Training badge localization source frame identity changed.")
    expected_source_sha = metadata.get("source_frame_sha256", sidecar.get("source_frame_sha256"))
    if expected_source_sha is not None:
        if not isinstance(expected_source_sha, str) or _SHA256_RE.fullmatch(expected_source_sha) is None:
            raise ValueError("Training badge localization source-frame hash is invalid.")
        if source_frame_path is None:
            raise ValueError("Training badge localization source-frame evidence is required.")
        if file_fingerprint(source_frame_path) != expected_source_sha:
            raise ValueError("Training badge localization source-frame pixels changed.")
    expected_source_evidence = metadata.get("source_frame_evidence", sidecar.get("source_frame_evidence"))
    if expected_source_evidence is not None and source_frame_evidence not in (None, expected_source_evidence):
        raise ValueError("Training badge localization source-frame evidence identity changed.")
    prepared = dict(sidecar)
    prepared["metadata"] = dict(metadata)
    prepared["metadata"].update({
        "source_timestamp_ms": source_timestamp,
        "evidence": evidence,
        "gameplay_sha256": gameplay_sha,
        "evidence_sha256": file_fingerprint(evidence_path),
    })
    prepared["observations"] = checked
    return attach(raw, prepared)


def load(
    raw: Mapping[str, Any],
    sidecar: Mapping[str, Any] | str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """Loader alias used by cached replay sidecar dispatch."""

    return apply(raw, sidecar, **kwargs)


__all__ = [
    "SCHEMA",
    "VERSION",
    "FIELDS",
    "GAIN_BOXES",
    "WIDE_GAIN_BOXES",
    "discover_training_badge_crops",
    "localize_training_badges",
    "build_sidecar",
    "attach",
    "apply",
    "load",
    "gameplay_fingerprint",
    "file_fingerprint",
    "fingerprint",
]
