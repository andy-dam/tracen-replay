"""Source-bound observations of lesson cards and their performance prices.

Lesson menus are offers, not purchases.  This adapter reads the card that is
visible while the ``Lessons`` menu is open and records the five price slots
without attaching any debit or award semantics.  A caller must join an offer
to a later confirmation and receipt separately.

The base neural observation remains immutable.  Refinement output is intended
to live beside it, with the same source-row fingerprint, gameplay-pixel hash,
crop hashes, and OCR model identity.  A cropped reading is accepted only for
an exact decimal token at the strict confidence threshold; missing, clipped,
or ambiguous slots remain unknown.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


POLICY = "source_bound_lesson_offer_prices_v1"
VERSION = 1
PANE_LEFT = 148
PANE_SIZE = (810, 1080)
FULL_WIDTH = PANE_LEFT + PANE_SIZE[0]
CURRENCIES = ("dance", "passion", "vocal", "visual", "composure")
MIN_TITLE_CONFIDENCE = 97.0
MIN_LABEL_CONFIDENCE = 95.0
MIN_PRICE_CONFIDENCE = 97.0
LETTER_O_AS_ZERO = frozenset({"O", "o"})
MAX_PRICE = 999
MIN_VARIANT_CONSENSUS = 2
PRICE_OCR_VARIANTS = ("gray_autocontrast", "gray_autocontrast_3x")
PRICE_READING_VARIANTS = ("raw",) + PRICE_OCR_VARIANTS

# The row is laid out in the full gameplay coordinate system.  The crop boxes
# stored in a sidecar additionally carry pane-local coordinates, because the
# evidence PNG is the 810x1080 gameplay pane rather than the 1920px capture.
PRICE_COLUMNS = ((455, 515), (530, 590), (605, 670), (685, 745), (760, 830))
PRICE_Y_PADDING = 8
TITLE_BAND_BEFORE = 215
TITLE_BAND_AFTER = 120

_LABEL = "performance point cost"
_DECIMAL = re.compile(r"[0-9]{1,3}\Z")


class LessonOfferError(ValueError):
    """Raised when a lesson-offer refinement is stale or malformed."""


def fingerprint(value: Any) -> str:
    """Hash a JSON-compatible value using the repository refinement convention."""

    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise LessonOfferError("Lesson offer value is not JSON serializable.") from exc
    return hashlib.sha256(encoded).hexdigest()


def file_fingerprint(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gameplay_fingerprint(image_or_path: Any) -> str:
    """Hash decoded RGB pane pixels, independently of the image file hash."""

    if isinstance(image_or_path, (str, Path)):
        from .frame_cache import rgb_digest

        digest, size = rgb_digest(image_or_path)
        if size != PANE_SIZE:
            raise LessonOfferError("Lesson offer evidence must be an 810x1080 gameplay pane.")
        return digest
    image = image_or_path.convert("RGB")
    if image.size != PANE_SIZE:
        raise LessonOfferError("Lesson offer evidence must be an 810x1080 gameplay pane.")
    return hashlib.sha256(image.tobytes()).hexdigest()


def _number(value: Any, label: str) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Check integer bounds before calling ``math.isfinite``: an adversarial
    # JSON integer can be too large for the float conversion used by that
    # function and must be rejected rather than raising OverflowError.
    if isinstance(value, int):
        return value if -10000 <= value <= 10000 else None
    if not math.isfinite(value) or not -10000 <= value <= 10000:
        return None
    return value


def _box(value: Any, *, allow_outside: bool = False) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    numbers = [_number(item, "box") for item in value]
    if any(item is None for item in numbers):
        return None
    result = [int(item) for item in numbers]
    if any(float(item) != value[index] for index, item in enumerate(result)):
        return None
    left, top, right, bottom = result
    if left >= right or top >= bottom:
        return None
    if not allow_outside and not (0 <= left < right <= FULL_WIDTH and 0 <= top < bottom <= PANE_SIZE[1]):
        return None
    return result


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return float(value) if 0 <= value <= 100 else None
    if not math.isfinite(value):
        return None
    return float(value) if 0 <= value <= 100 else None


def _line_text(line: Any) -> str:
    if not isinstance(line, dict) or not isinstance(line.get("text"), str):
        return ""
    return " ".join(line["text"].split()).strip()


def _line_is_valid(line: Any, *, minimum_confidence: float = 0) -> bool:
    return (
        isinstance(line, dict)
        and bool(_line_text(line))
        and _box(line.get("box")) is not None
        and (_confidence(line.get("confidence")) or 0) >= minimum_confidence
    )


def _center(box: list[int] | tuple[int, ...]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _within_y(center: float, low: float, high: float) -> bool:
    return low <= center <= high


def _source_lines(raw: dict[str, Any]) -> list[Any]:
    lines = raw.get("lines")
    if isinstance(lines, list):
        return lines
    # This fallback is useful for parsed snapshots while keeping the source
    # line list itself untouched.  A neural sidecar normally supplies lines.
    nested = raw.get("ocr")
    if isinstance(nested, dict) and isinstance(nested.get("neural"), list):
        return nested["neural"]
    return []


def _source_screen(raw: dict[str, Any]) -> str | None:
    """Return an explicit screen or derive it from a neural observation."""

    if isinstance(raw.get("screen"), str):
        return raw["screen"]
    try:
        from .vision import parse
        screen = parse(raw).get("screen")
    except (AttributeError, KeyError, TypeError, ValueError):
        screen = None
    if isinstance(screen, str):
        return screen

    # Minimal source rows used by the portable adapter can carry the stable
    # Lessons header and prompt without the auxiliary fields required by the
    # full vision classifier.  Recover the same screen gate from those source
    # anchors rather than making the two card selectors diverge.  Explicit
    # committed/result markers still veto preview classification.
    header = _line_text({"text": raw.get("header")}).casefold()
    if header != "lessons":
        return None
    if raw.get("current_grid") is True or raw.get("result_grid") is True:
        return None
    if any(raw.get(key) is True for key in ("committed", "applied", "awarded", "purchase_committed")):
        return None
    phase = _line_text({"text": raw.get("phase")}).casefold()
    if phase and phase != "preview":
        return None
    normalized = {_line_text(line).casefold() for line in _source_lines(raw)}
    if "select a technique or song to learn." not in normalized:
        return None
    if any("technique learned" in text or "lesson learned" in text for text in normalized):
        return None
    return "lesson_selection"


def _price_boxes(label_box: list[int]) -> tuple[list[int], list[list[int]]] | None:
    """Return full and pane-local crop boxes for one cost row."""

    left, top, right, bottom = label_box
    if not (250 <= left < right <= 500 and 300 <= top < bottom <= 950):
        return None
    y0 = top - PRICE_Y_PADDING
    y1 = bottom + PRICE_Y_PADDING
    if y0 < 0 or y1 > PANE_SIZE[1]:
        return None
    full = [[x0, y0, x1, y1] for x0, x1 in PRICE_COLUMNS]
    local = [[x0 - PANE_LEFT, y0, x1 - PANE_LEFT, y1] for x0, x1 in PRICE_COLUMNS]
    if any(_box(item, allow_outside=False) is None for item in full):
        return None
    if any(_box(item, allow_outside=False) is None for item in local):
        return None
    return full, local


def _title_groups(lines: list[Any], label_box: list[int]) -> list[list[tuple[int, dict[str, Any]]]]:
    """Find possible title line groups above one cost label.

    This uses only source geometry and OCR confidence.  It deliberately does
    not compare names to a game catalog or to a later confirmation.
    """

    _, label_center_y = _center(label_box)
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(lines):
        box = _box(line.get("box")) if isinstance(line, dict) else None
        if box is None or not _line_text(line):
            continue
        # Effect/result rows such as ``Speed +4`` can sit in the vertical
        # band above the next cost label.  They are never card titles.  Keep
        # this exclusion in the shared source selector so the rich adapter
        # and the numeric refiner cannot disagree on card identity.
        if "+" in _line_text(line):
            continue
        center_x, center_y = _center(box)
        if not (260 <= box[0] <= 700 and center_x <= 720):
            continue
        if not _within_y(center_y, label_center_y - TITLE_BAND_BEFORE,
                         label_center_y - TITLE_BAND_AFTER):
            continue
        if (_confidence(line.get("confidence")) or 0) < 60:
            continue
        candidates.append((index, line))
    candidates.sort(key=lambda item: (_box(item[1]["box"])[1], _box(item[1]["box"])[0]))
    groups: list[list[tuple[int, dict[str, Any]]]] = []
    for item in candidates:
        box = _box(item[1]["box"])
        if not groups:
            groups.append([item])
            continue
        previous = _box(groups[-1][-1][1]["box"])
        current = groups[-1]
        gap = box[1] - previous[3]
        same_left = abs(box[0] - _box(current[0][1]["box"])[0]) <= 24
        combined_height = max(previous[3], box[3]) - min(_box(current[0][1]["box"])[1], box[1])
        previous_center_y = (previous[1] + previous[3]) / 2
        current_center_y = (box[1] + box[3]) / 2
        # OCR can split one long title into adjacent horizontal fragments on
        # the same baseline (for example ``Vocal Training`` + ``Basics``).
        # Treat that as one title only when the fragments touch horizontally
        # and share the same vertical band; unrelated candidates remain
        # separate groups and make the card unknown.
        horizontal_fragment = (
            abs(current_center_y - previous_center_y) <= 12
            and -20 <= box[0] - previous[2] <= 40
        )
        if ((0 <= gap <= 18 and same_left) or horizontal_fragment) and combined_height <= 60:
            current.append(item)
        else:
            groups.append([item])
    return groups


def _anchor(line: dict[str, Any], index: int) -> dict[str, Any]:
    return dict(index=index, text=_line_text(line), confidence=_confidence(line.get("confidence")),
                box=_box(line.get("box")))


def _candidate_signature(card: dict[str, Any]) -> dict[str, Any]:
    title = card.get("title") or {}
    cost_label = card.get("cost_label") or {}
    return dict(
        card_index=card["card_index"],
        title_line_indices=card.get("title_line_indices", []),
        cost_label_line_index=card.get("cost_label_line_index"),
        title_text=title.get("text"),
        title_confidence=title.get("confidence"),
        title_box=title.get("box"),
        cost_label_text=cost_label.get("text"),
        cost_label_confidence=cost_label.get("confidence"),
        cost_label_box=cost_label.get("box"),
        source_price_boxes=card.get("source_price_boxes", []),
        crop_price_boxes=card.get("crop_price_boxes", []),
    )


def is_mergeable_offer_candidate(card: Any) -> bool:
    """Return whether one source candidate has a stable rich-card identity.

    ``find_offer_cards`` deliberately retains candidates whose title or cost
    label is ambiguous so the refinement sidecar can explain the missing
    observation.  The rich offer adapter cannot attach such a candidate to a
    purchase-shaped preview envelope.  Both layers therefore use this one
    source-selection predicate when deciding which card indices may be
    merged; a rejected source candidate is still validated and retained for
    audit, but it is not treated as an unknown card in the rich result.
    """

    if not isinstance(card, dict) or not isinstance(card.get("title"), dict):
        return False
    reasons = card.get("unknown_reasons", [])
    return isinstance(reasons, list) and "ambiguous_cost_label" not in reasons


def find_offer_cards(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return source-geometry lesson-card candidates from one base row.

    Only a genuine ``lesson_selection`` row is eligible.  The returned cards
    can be incomplete; ``status`` and ``unknown_reasons`` make that explicit.
    """

    if not isinstance(raw, dict) or _source_screen(raw) != "lesson_selection":
        return []
    lines = _source_lines(raw)
    labels: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(lines):
        text = _line_text(line).lower()
        if text != _LABEL:
            continue
        if _box(line.get("box")) is None or (_confidence(line.get("confidence")) or 0) < 60:
            continue
        labels.append((index, line))
    labels.sort(key=lambda item: (_center(_box(item[1]["box"]))[1], item[0]))
    cards: list[dict[str, Any]] = []
    for card_index, (label_index, label_line) in enumerate(labels):
        label_box = _box(label_line["box"])
        assert label_box is not None
        reasons: list[str] = []
        label_confidence = _confidence(label_line.get("confidence")) or 0
        if label_confidence < MIN_LABEL_CONFIDENCE:
            reasons.append("uncertain_cost_label")
        duplicate = any(
            other_index != label_index
            and abs(_center(_box(other_line["box"]))[1] - _center(label_box)[1]) < 24
            for other_index, other_line in labels
        )
        if duplicate:
            reasons.append("ambiguous_cost_label")
        groups = _title_groups(lines, label_box)
        title_group = groups[0] if len(groups) == 1 else None
        if title_group is None:
            reasons.append("ambiguous_or_missing_title")
            title_lines: list[tuple[int, dict[str, Any]]] = []
        else:
            title_lines = title_group
            if any((_confidence(line.get("confidence")) or 0) < MIN_TITLE_CONFIDENCE for _, line in title_lines):
                reasons.append("uncertain_title")
        title = None
        if title_lines:
            title = dict(
                line_indices=[index for index, _ in title_lines],
                text=" ".join(_line_text(line) for _, line in title_lines),
                confidence=min(_confidence(line.get("confidence")) or 0 for _, line in title_lines),
                box=[
                    min(_box(line["box"])[0] for _, line in title_lines),
                    min(_box(line["box"])[1] for _, line in title_lines),
                    max(_box(line["box"])[2] for _, line in title_lines),
                    max(_box(line["box"])[3] for _, line in title_lines),
                ],
            )
        geometry = _price_boxes(label_box)
        if geometry is None:
            reasons.append("clipped_or_invalid_cost_row")
            # Retain five explicit unknown slots even when the row cannot be
            # cropped safely.  ``None`` is a deliberate provenance value: it
            # prevents a downstream consumer from mistaking a clamped crop
            # for a complete numeric observation.
            source_boxes: list[list[int] | None] = [None] * len(CURRENCIES)
            crop_boxes: list[list[int] | None] = [None] * len(CURRENCIES)
        else:
            source_boxes, crop_boxes = geometry
        card = dict(
            card_index=card_index,
            title_line_indices=[index for index, _ in title_lines],
            cost_label_line_index=label_index,
            title=title,
            cost_label=_anchor(label_line, label_index),
            source_price_boxes=source_boxes,
            crop_price_boxes=crop_boxes,
            geometry_sha256=None,
            status="unknown" if reasons else "pending_prices",
            unknown_reasons=list(dict.fromkeys(reasons)),
        )
        card["geometry_sha256"] = fingerprint(_candidate_signature(card))
        cards.append(card)
    return cards


def _score_percent(score: Any) -> float | None:
    if isinstance(score, bool) or score is None or isinstance(score, (str, bytes)):
        return None
    # RapidOCR commonly returns NumPy scalar scores.  Convert those through
    # ``float`` while still rejecting huge Python integers before conversion.
    if isinstance(score, int) and not 0 <= score <= 100:
        return None
    try:
        numeric = float(score)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or not 0 <= numeric <= 100:
        return None
    value = numeric * 100 if 0 <= numeric <= 1 else numeric
    if not 0 <= value <= 100:
        return None
    return round(value, 4)


def _price_reading(
    text: Any,
    confidence: Any,
    *,
    crop_box: list[int] | None,
    source_box: list[int] | None,
    crop_hash: str,
    geometry_hash: str,
    forced_reason: str | None = None,
    preprocess: str | None = None,
    recognizer_rgb_sha256: str | None = None,
    normalize_letter_o: bool = True,
) -> dict[str, Any]:
    normalized = text.strip() if isinstance(text, str) else None
    digit_normalization = None
    if normalize_letter_o and normalized in LETTER_O_AS_ZERO:
        # The price slots hold digits only.  The recognizer sometimes emits
        # the letter O for a lone zero glyph; record the substitution.
        digit_normalization = dict(observed_text=normalized, rule="letter_o_to_zero")
        normalized = "0"
    score = _score_percent(confidence)
    value = None
    reason = forced_reason
    if reason is None:
        if not normalized:
            reason = "missing_digit"
        elif not _DECIMAL.fullmatch(normalized):
            reason = "ambiguous_digit"
        elif int(normalized) > MAX_PRICE:
            reason = "out_of_range_digit"
        elif score is None:
            reason = "invalid_confidence"
        elif score < MIN_PRICE_CONFIDENCE:
            reason = "low_confidence"
        else:
            value = int(normalized)
    result = dict(
        text=normalized,
        confidence=score,
        value=value,
        status="accepted" if value is not None else "unknown",
        unknown_reason=None if value is not None else reason,
        crop_box=list(crop_box) if crop_box is not None else None,
        source_box=list(source_box) if source_box is not None else None,
        # This is always the unmodified source crop.  A preprocessing view
        # gets a separate hash below so validation can reproduce it.
        crop_rgb_sha256=crop_hash,
        box_sha256=fingerprint(dict(crop_box=crop_box, source_box=source_box)),
        geometry_sha256=geometry_hash,
        coordinate_space="gameplay_pane",
    )
    if digit_normalization is not None:
        result["digit_normalization"] = digit_normalization
    if preprocess is not None:
        result["preprocess"] = preprocess
        if recognizer_rgb_sha256 is not None:
            result["recognizer_rgb_sha256"] = recognizer_rgb_sha256
    return result


def _unknown_clipped_price(geometry_hash: str) -> dict[str, Any]:
    """Represent a price whose source row cannot be cropped safely."""

    return _price_reading(None, None, crop_box=None, source_box=None, crop_hash="",
                          geometry_hash=geometry_hash,
                          forced_reason="clipped_or_invalid_cost_row")


def _validate_source_reading(
    reading: Any,
    *,
    expected_field: str,
    expected_source: list[int],
    expected_crop: list[int],
    crop: Image.Image,
    crop_hash: str,
    geometry_hash: str,
) -> dict[str, Any]:
    """Recompute one retained OCR diagnostic from the same physical crop.

    ``source_readings`` is audit data, but it is still part of the accepted
    price proof.  Treating it as opaque allowed a caller to replace a weak
    reading with a fabricated consensus after the outer crop hash had passed.
    Every diagnostic therefore gets the same value/status/confidence parser as
    its selected reading and the recognizer hash is recomputed for its declared
    preprocessing view.
    """

    if not isinstance(reading, dict):
        raise LessonOfferError("Lesson offer source crop reading is malformed.")
    variant = reading.get("variant")
    if variant not in PRICE_READING_VARIANTS:
        raise LessonOfferError("Lesson offer source crop reading variant changed.")
    preprocess = None if variant == "raw" else variant
    expected_recognizer_hash = crop_hash
    if preprocess is not None:
        try:
            transformed = _preprocess_price_crop(crop, preprocess)
        except (TypeError, ValueError) as exc:
            raise LessonOfferError("Lesson offer source crop preprocessing changed.") from exc
        expected_recognizer_hash = hashlib.sha256(transformed.tobytes()).hexdigest()
    if reading.get("recognizer_rgb_sha256") != expected_recognizer_hash:
        raise LessonOfferError("Lesson offer source crop recognizer pixels changed.")
    parsed = _price_reading(
        reading.get("text"),
        reading.get("confidence"),
        crop_box=expected_crop,
        source_box=expected_source,
        crop_hash=crop_hash,
        geometry_hash=geometry_hash,
        preprocess=preprocess,
        recognizer_rgb_sha256=expected_recognizer_hash if preprocess is not None else None,
        # A sidecar written before the letter-O rule keeps its recorded
        # ambiguity; the rule applies to readings taken from now on.
        normalize_letter_o=reading.get("text") not in LETTER_O_AS_ZERO,
    )
    for key in ("text", "confidence", "value", "status", "unknown_reason"):
        if reading.get(key) != parsed.get(key):
            raise LessonOfferError(f"Lesson offer source crop reading {key} changed.")
    # ``variant`` is the stable diagnostic name in generated sidecars; the
    # parsed price stores it as ``preprocess`` only for transformed views.
    validated = {
        "variant": variant,
        "text": parsed.get("text"),
        "confidence": parsed.get("confidence"),
        "value": parsed.get("value"),
        "status": parsed.get("status"),
        "unknown_reason": parsed.get("unknown_reason"),
        "recognizer_rgb_sha256": expected_recognizer_hash,
    }
    return validated


def _preprocess_price_crop(crop: Image.Image, variant: str) -> Image.Image:
    """Create a deterministic recognition view of the same source crop."""

    if variant not in PRICE_OCR_VARIANTS:
        raise LessonOfferError("Lesson offer OCR preprocessing changed.")
    gray = ImageOps.autocontrast(ImageOps.grayscale(crop.convert("RGB")))
    if variant == "gray_autocontrast_3x":
        gray = gray.resize((gray.width * 3, gray.height * 3), Image.Resampling.BICUBIC)
    return gray.convert("RGB")


def _reader_outputs(reader: Any, images: list[Any]) -> list[tuple[Any, Any]]:
    if not images:
        return []
    try:
        input_type = reader.TextRecInput
        request = input_type(img=images)
        result = reader.engine.text_rec(request)
    except AttributeError:
        result = reader.engine(images)
    texts = list(getattr(result, "txts", []) or [])
    scores = list(getattr(result, "scores", []) or [])
    if len(texts) > len(images) or len(scores) > len(images):
        raise LessonOfferError("Lesson offer OCR returned too many readings.")
    return [
        (texts[index] if index < len(texts) else None,
         scores[index] if index < len(scores) else None)
        for index in range(len(images))
    ]


def build(
    pane: Image.Image,
    raw: dict[str, Any],
    evidence_sha256: str,
    reader: Any,
    *,
    source_frame_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a bounded sidecar from one gameplay-pane image.

    ``reader`` is called only for numeric crops belonging to source-geometry
    card candidates.  The method never receives expected card names, expected
    values, ledgers, or residuals.
    """

    if not isinstance(raw, dict):
        raise LessonOfferError("Lesson offer source row must be an object.")
    if not isinstance(evidence_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", evidence_sha256):
        raise LessonOfferError("Lesson offer evidence hash must be SHA-256.")
    pane = pane.convert("RGB")
    if pane.size != PANE_SIZE:
        raise LessonOfferError("Lesson offer refinement requires an 810x1080 gameplay pane.")
    gameplay_sha = gameplay_fingerprint(pane)
    if raw.get("gameplay_sha256") and raw["gameplay_sha256"] != gameplay_sha:
        raise LessonOfferError("Lesson offer gameplay pixels do not match the source row.")
    cards = find_offer_cards(raw)
    images: list[Any] = []
    # Keep the source crop object only in memory.  The sidecar carries hashes,
    # never image blobs, so every selected variant can be revalidated later.
    image_metadata: list[tuple[int, int, list[int], list[int], str, Image.Image]] = []
    import numpy as np

    for card in cards:
        for field_index, (source_box, crop_box) in enumerate(
            zip(card["source_price_boxes"], card["crop_price_boxes"])
        ):
            if source_box is None or crop_box is None:
                continue
            crop = pane.crop(tuple(crop_box))
            crop_hash = hashlib.sha256(crop.tobytes()).hexdigest()
            images.append(np.asarray(crop)[:, :, ::-1])
            image_metadata.append((card["card_index"], field_index, source_box, crop_box, crop_hash, crop))
    readings = _reader_outputs(reader, images) if images else []
    by_card: dict[int, list[dict[str, Any]]] = {card["card_index"]: [] for card in cards}
    baseline: dict[tuple[int, int], dict[str, Any]] = {}
    for metadata, (text, confidence) in zip(image_metadata, readings):
        card_index, field_index, source_box, crop_box, crop_hash, crop = metadata
        key = (card_index, field_index)
        price = _price_reading(
            text,
            confidence,
            crop_box=crop_box,
            source_box=source_box,
            crop_hash=crop_hash,
            geometry_hash=cards[card_index]["geometry_sha256"],
        )
        baseline[key] = price

    # The ordinary crop is authoritative when it clears the strict threshold.
    # Only unresolved slots get bounded same-frame views.  Two agreeing
    # transformed views are required before a weak digit becomes accepted;
    # their raw readings remain attached for auditability.
    retry_keys = [key for key, price in baseline.items() if price.get("value") is None]
    variant_reads: dict[tuple[int, int], list[dict[str, Any]]] = {key: [] for key in retry_keys}
    for variant in PRICE_OCR_VARIANTS:
        if not retry_keys:
            break
        variant_images = []
        variant_hashes: dict[tuple[int, int], str] = {}
        for metadata in image_metadata:
            card_index, field_index, _source_box, _crop_box, _crop_hash, crop = metadata
            transformed = _preprocess_price_crop(crop, variant)
            variant_images.append(np.asarray(transformed)[:, :, ::-1])
            variant_hashes[(card_index, field_index)] = hashlib.sha256(transformed.tobytes()).hexdigest()
        variant_outputs = _reader_outputs(reader, variant_images) if variant_images else []
        for metadata, (text, confidence) in zip(image_metadata, variant_outputs):
            card_index, field_index, source_box, crop_box, crop_hash, _crop = metadata
            key = (card_index, field_index)
            if key not in variant_reads:
                continue
            candidate = _price_reading(
                text,
                confidence,
                crop_box=crop_box,
                source_box=source_box,
                crop_hash=crop_hash,
                geometry_hash=cards[card_index]["geometry_sha256"],
                preprocess=variant,
                recognizer_rgb_sha256=variant_hashes[key],
            )
            variant_reads[key].append(candidate)

    for metadata in image_metadata:
        card_index, field_index, source_box, crop_box, crop_hash, _crop = metadata
        key = (card_index, field_index)
        price = baseline[key]
        diagnostics = [
            dict(
                variant="raw",
                text=price.get("text"),
                confidence=price.get("confidence"),
                value=price.get("value"),
                status=price.get("status"),
                unknown_reason=price.get("unknown_reason"),
                recognizer_rgb_sha256=crop_hash,
            )
        ]
        variants = variant_reads.get(key, [])
        diagnostics.extend(
            dict(
                variant=candidate.get("preprocess"),
                text=candidate.get("text"),
                confidence=candidate.get("confidence"),
                value=candidate.get("value"),
                status=candidate.get("status"),
                unknown_reason=candidate.get("unknown_reason"),
                recognizer_rgb_sha256=candidate.get("recognizer_rgb_sha256"),
            )
            for candidate in variants
        )
        accepted = [candidate for candidate in variants if candidate.get("value") is not None]
        grouped: dict[int, list[dict[str, Any]]] = {}
        for candidate in accepted:
            grouped.setdefault(candidate["value"], []).append(candidate)
        if price.get("value") is None and len(grouped) == 1:
            candidates = next(iter(grouped.values()))
            if len(candidates) >= MIN_VARIANT_CONSENSUS:
                selected = max(candidates, key=lambda item: item.get("confidence") or 0)
                price = copy.deepcopy(selected)
                price["source_readings"] = diagnostics
        # Retain the raw OCR result even when the unmodified crop was already
        # accepted.  The selected reading and its diagnostic then form one
        # source-bound observation: a caller may not rewrite the amount or
        # confidence while merely recomputing the outer sidecar hash.
        if "source_readings" not in price:
            price["source_readings"] = diagnostics
        baseline[key] = price

    for card in cards:
        by_card[card["card_index"]] = [
            baseline[(card["card_index"], field_index)]
            for field_index in range(len(CURRENCIES))
            if (card["card_index"], field_index) in baseline
        ]
    for card in cards:
        prices = by_card[card["card_index"]]
        # Invalid/clipped geometry has no crops and therefore no OCR readings.
        if len(prices) != len(CURRENCIES):
            prices = []
            for source_box, crop_box in zip(card["source_price_boxes"], card["crop_price_boxes"]):
                if source_box is None or crop_box is None:
                    prices.append(_unknown_clipped_price(card["geometry_sha256"]))
                else:
                    prices.append(_price_reading(None, None, crop_box=crop_box, source_box=source_box,
                                                 crop_hash="", geometry_hash=card["geometry_sha256"],
                                                 forced_reason="clipped_or_invalid_cost_row"))
        card["prices"] = [dict(field=field, **price) for field, price in zip(CURRENCIES, prices)]
        reasons = list(card.get("unknown_reasons", []))
        if card.get("title") is None:
            reasons.append("ambiguous_or_missing_title")
        for price in card["prices"]:
            if price["value"] is None:
                reasons.append(f"{price['field']}_{price['unknown_reason']}")
        card["unknown_reasons"] = list(dict.fromkeys(reasons))
        card["status"] = "complete" if not card["unknown_reasons"] and len(card["prices"]) == 5 else "unknown"
    source_model = raw.get("model_sha256")
    source_engine = raw.get("engine_fingerprint")
    source_frame_sha256 = raw.get("source_frame_sha256")
    source_frame_verified = False
    if source_frame_path is not None:
        actual_source_frame = file_fingerprint(source_frame_path)
        if source_frame_sha256 is not None and actual_source_frame != source_frame_sha256:
            raise LessonOfferError("Lesson offer source frame changed.")
        source_frame_sha256 = actual_source_frame
        source_frame_verified = True
    return dict(
        version=VERSION,
        policy=POLICY,
        stage="lesson_offer_refinement",
        source_timestamp_ms=raw.get("source_timestamp_ms"),
        evidence=raw.get("evidence"),
        evidence_sha256=evidence_sha256.lower(),
        gameplay_sha256=gameplay_sha,
        source_frame_evidence=raw.get("source_frame_evidence"),
        source_frame_sha256=source_frame_sha256,
        source_frame_verified=source_frame_verified,
        raw_sha256=fingerprint(raw),
        source_model_sha256=source_model,
        source_engine_fingerprint=source_engine,
        refinement_model_sha256=getattr(reader, "models", {}),
        refinement_engine_fingerprint=getattr(reader, "fingerprint", None),
        independent_observations=False,
        coordinate_spaces=dict(source="full_gameplay", crop="gameplay_pane"),
        cards=cards,
        cards_sha256=fingerprint(cards),
    )


def _require_sidecar(extra: Any) -> None:
    if not isinstance(extra, dict) or extra.get("version") != VERSION or extra.get("policy") != POLICY:
        raise LessonOfferError("Unsupported lesson offer refinement policy or version.")
    if extra.get("independent_observations") is not False:
        raise LessonOfferError("Lesson offer refinement cannot claim independent observations.")
    if not isinstance(extra.get("cards"), list) or extra.get("cards_sha256") != fingerprint(extra["cards"]):
        raise LessonOfferError("Lesson offer card contents changed.")
    if not isinstance(extra.get("coordinate_spaces"), dict):
        raise LessonOfferError("Lesson offer coordinate provenance is missing.")
    if extra["coordinate_spaces"] != dict(source="full_gameplay", crop="gameplay_pane"):
        raise LessonOfferError("Lesson offer coordinate provenance changed.")


def _validate_price(price: Any, expected_field: str, expected_source: list[int] | None,
                   expected_crop: list[int] | None,
                   pane: Image.Image, geometry_hash: str) -> dict[str, Any]:
    if not isinstance(price, dict) or price.get("field") != expected_field:
        raise LessonOfferError("Lesson offer price field order changed.")
    if price.get("source_box") != expected_source or price.get("crop_box") != expected_crop:
        raise LessonOfferError("Lesson offer price crop geometry changed.")
    if price.get("geometry_sha256") != geometry_hash:
        raise LessonOfferError("Lesson offer price geometry provenance changed.")
    if price.get("box_sha256") != fingerprint(dict(crop_box=expected_crop, source_box=expected_source)):
        raise LessonOfferError("Lesson offer price box hash changed.")
    if expected_source is None or expected_crop is None:
        if (price.get("source_box") is not None or price.get("crop_box") is not None
                or price.get("crop_rgb_sha256") != ""
                or price.get("preprocess") is not None
                or price.get("recognizer_rgb_sha256") is not None
                or "source_readings" in price):
            raise LessonOfferError("Lesson offer clipped price provenance changed.")
        parsed = _unknown_clipped_price(geometry_hash)
        if price.get("value") is not None or price.get("status") != "unknown" \
                or price.get("unknown_reason") != parsed["unknown_reason"]:
            raise LessonOfferError("Lesson offer clipped price was changed.")
        return parsed
    crop = pane.crop(tuple(expected_crop))
    crop_hash = hashlib.sha256(crop.tobytes()).hexdigest()
    if price.get("crop_rgb_sha256") != crop_hash:
        raise LessonOfferError("Lesson offer price crop pixels changed.")
    text = price.get("text")
    confidence = price.get("confidence")
    preprocess = price.get("preprocess")
    recognizer_hash = price.get("recognizer_rgb_sha256")
    if preprocess is None:
        if recognizer_hash is not None:
            raise LessonOfferError("Lesson offer unproven OCR preprocessing was added.")
        parsed = _price_reading(
            text,
            confidence,
            crop_box=expected_crop,
            source_box=expected_source,
            crop_hash=crop_hash,
            geometry_hash=geometry_hash,
            normalize_letter_o=text not in LETTER_O_AS_ZERO,
        )
    else:
        try:
            transformed = _preprocess_price_crop(crop, preprocess)
        except (TypeError, ValueError) as exc:
            raise LessonOfferError("Lesson offer OCR preprocessing changed.") from exc
        transformed_hash = hashlib.sha256(transformed.tobytes()).hexdigest()
        if recognizer_hash != transformed_hash:
            raise LessonOfferError("Lesson offer preprocessed crop pixels changed.")
        parsed = _price_reading(
            text,
            confidence,
            crop_box=expected_crop,
            source_box=expected_source,
            crop_hash=crop_hash,
            geometry_hash=geometry_hash,
            preprocess=preprocess,
            recognizer_rgb_sha256=transformed_hash,
            normalize_letter_o=text not in LETTER_O_AS_ZERO,
        )
    if isinstance(price.get("digit_normalization"), dict):
        parsed["digit_normalization"] = dict(price["digit_normalization"])
    if price.get("value") != parsed["value"] or price.get("status") != parsed["status"]:
        raise LessonOfferError("Lesson offer numeric observation was changed.")
    if price.get("unknown_reason") != parsed["unknown_reason"]:
        raise LessonOfferError("Lesson offer uncertainty reason was changed.")
    if "amount" in price and price.get("amount") != parsed.get("value"):
        raise LessonOfferError("Lesson offer numeric amount was changed.")
    if confidence is not None and parsed["confidence"] != confidence:
        raise LessonOfferError("Lesson offer confidence was changed.")
    if "source_readings" in price:
        readings = price["source_readings"]
        if not isinstance(readings, list) or not readings:
            raise LessonOfferError("Lesson offer source crop readings are malformed.")
        validated_readings = []
        variants = set()
        for reading in readings:
            variant = reading.get("variant") if isinstance(reading, dict) else None
            if variant in variants:
                raise LessonOfferError("Lesson offer source crop readings contain duplicate variants.")
            validated = _validate_source_reading(
                reading,
                expected_field=expected_field,
                expected_source=expected_source,
                expected_crop=expected_crop,
                crop=crop,
                crop_hash=crop_hash,
                geometry_hash=geometry_hash,
            )
            variants.add(validated["variant"])
            validated_readings.append(validated)
        if "raw" not in variants:
            raise LessonOfferError("Lesson offer source crop readings lack the raw diagnostic.")
        selected_variant = "raw" if preprocess is None else preprocess
        selected_reading = next(
            (item for item in validated_readings if item["variant"] == selected_variant),
            None,
        )
        if selected_reading is None:
            raise LessonOfferError("Lesson offer selected OCR reading is missing.")
        for key in ("text", "confidence", "value", "status", "unknown_reason"):
            if price.get(key) != selected_reading.get(key):
                raise LessonOfferError("Lesson offer selected OCR reading changed.")
        parsed["source_readings"] = validated_readings
    return parsed


def observe(
    pane: Image.Image,
    raw: dict[str, Any],
    extra: dict[str, Any],
    *,
    source_frame_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a sidecar against the immutable row and current source pixels."""

    _require_sidecar(extra)
    if extra.get("raw_sha256") != fingerprint(raw):
        raise LessonOfferError("Lesson offer source row changed.")
    if raw.get("source_timestamp_ms") != extra.get("source_timestamp_ms"):
        raise LessonOfferError("Lesson offer source timestamp changed.")
    if raw.get("evidence") != extra.get("evidence"):
        raise LessonOfferError("Lesson offer evidence path changed.")
    pane = pane.convert("RGB")
    if pane.size != PANE_SIZE or gameplay_fingerprint(pane) != extra.get("gameplay_sha256"):
        raise LessonOfferError("Lesson offer gameplay pixels changed.")
    if raw.get("gameplay_sha256") and raw["gameplay_sha256"] != extra.get("gameplay_sha256"):
        raise LessonOfferError("Lesson offer source gameplay hash changed.")
    if raw.get("source_frame_sha256") != extra.get("source_frame_sha256"):
        raise LessonOfferError("Lesson offer source-frame provenance changed.")
    expected_source_frame = extra.get("source_frame_sha256")
    source_frame_verified = False
    if source_frame_path is not None:
        if (not isinstance(expected_source_frame, str)
                or file_fingerprint(source_frame_path) != expected_source_frame):
            raise LessonOfferError("Lesson offer source-frame evidence changed or is missing.")
        source_frame_verified = True
    if raw.get("model_sha256") != extra.get("source_model_sha256"):
        raise LessonOfferError("Lesson offer source model provenance changed.")
    if raw.get("engine_fingerprint") != extra.get("source_engine_fingerprint"):
        raise LessonOfferError("Lesson offer source engine provenance changed.")
    candidates = find_offer_cards(raw)
    if len(candidates) != len(extra["cards"]):
        raise LessonOfferError("Lesson offer card count changed.")
    result_cards: list[dict[str, Any]] = []
    for candidate, stored in zip(candidates, extra["cards"]):
        if not isinstance(stored, dict) or stored.get("geometry_sha256") != candidate["geometry_sha256"]:
            raise LessonOfferError("Lesson offer card geometry changed.")
        if _candidate_signature(stored) != _candidate_signature(candidate):
            raise LessonOfferError("Lesson offer source anchors changed.")
        prices = stored.get("prices")
        if not isinstance(prices, list) or len(prices) != len(CURRENCIES):
            raise LessonOfferError("Lesson offer refinement requires five price slots.")
        validated_prices = [
            dict(field=field, **_validate_price(price, field, source_box, crop_box, pane,
                                                candidate["geometry_sha256"]))
            for field, source_box, crop_box, price in zip(
                CURRENCIES, candidate["source_price_boxes"], candidate["crop_price_boxes"], prices
            )
        ]
        reasons = list(candidate.get("unknown_reasons", []))
        if candidate.get("title") is None:
            reasons.append("ambiguous_or_missing_title")
        for price in validated_prices:
            if price["value"] is None:
                reasons.append(f"{price['field']}_{price['unknown_reason']}")
        reasons = list(dict.fromkeys(reasons))
        if stored.get("unknown_reasons") != reasons:
            raise LessonOfferError("Lesson offer card uncertainty reasons changed.")
        expected_status = "complete" if not reasons else "unknown"
        if stored.get("status") != expected_status:
            raise LessonOfferError("Lesson offer card status changed.")
        result_cards.append(dict(
            card_index=candidate["card_index"],
            title=copy.deepcopy(candidate["title"]),
            cost_label=copy.deepcopy(candidate["cost_label"]),
            prices=validated_prices,
            status=expected_status,
            unknown_reasons=reasons,
            source_price_boxes=copy.deepcopy(candidate["source_price_boxes"]),
            crop_price_boxes=copy.deepcopy(candidate["crop_price_boxes"]),
            geometry_sha256=candidate["geometry_sha256"],
        ))
    return dict(screen=_source_screen(raw), offers=result_cards,
                complete_offer_count=sum(card["status"] == "complete" for card in result_cards),
                offer_count=len(result_cards),
                refinement_policy=POLICY, independent_observations=False,
                source_frame_verified=source_frame_verified)


def apply(
    row: dict[str, Any],
    raw: dict[str, Any],
    extra: dict[str, Any],
    evidence_path: str | Path,
    *,
    source_frame_path: str | Path | None = None,
) -> dict[str, Any]:
    """Attach offer observations to a row without creating a purchase."""

    source_screen = _source_screen(raw)
    if not isinstance(row, dict) or row.get("screen") != source_screen:
        raise LessonOfferError("Lesson offer row/source screen mismatch.")
    evidence_path = Path(evidence_path)
    if not evidence_path.is_file() or file_fingerprint(evidence_path) != extra.get("evidence_sha256"):
        raise LessonOfferError("Lesson offer evidence file changed or is missing.")
    from .frame_cache import open_rgb
    observed = observe(open_rgb(evidence_path), raw, extra, source_frame_path=source_frame_path)
    if source_screen != "lesson_selection":
        return row
    facts = copy.deepcopy(row.get("facts", {}))
    if "lesson_offer_observations" in facts:
        raise LessonOfferError("Row already contains lesson offer observations.")
    facts["lesson_offer_observations"] = observed["offers"]
    facts["lesson_offer_refinement_provenance"] = dict(
        policy=POLICY, version=VERSION, raw_sha256=extra["raw_sha256"],
        evidence_sha256=extra["evidence_sha256"], gameplay_sha256=extra["gameplay_sha256"],
        source_frame_sha256=extra.get("source_frame_sha256"),
        source_frame_verified=observed.get("source_frame_verified", False),
        source_model_sha256=extra.get("source_model_sha256"),
        source_engine_fingerprint=extra.get("source_engine_fingerprint"),
        refinement_model_sha256=extra.get("refinement_model_sha256"),
        refinement_engine_fingerprint=extra.get("refinement_engine_fingerprint"),
        independent_observations=False, cards_sha256=extra["cards_sha256"],
    )
    return dict(row, facts=facts)


def _selected_neural_rows(root: Path, start_ms: int, end_ms: int, frame_ids: list[str]) -> list[tuple[Path, dict[str, Any]]]:
    if type(start_ms) is not int or type(end_ms) is not int or end_ms <= start_ms:
        raise LessonOfferError("Lesson offer generation requires a non-empty bounded time range.")
    wanted = set(frame_ids)
    selected: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((root / "neural").glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LessonOfferError(f"Invalid neural observation: {path.name}") from exc
        timestamp = raw.get("source_timestamp_ms")
        if type(timestamp) is not int or not start_ms <= timestamp < end_ms:
            continue
        if wanted and path.stem not in wanted:
            continue
        selected.append((path, raw))
    if wanted:
        found = {path.stem for path, _ in selected}
        missing = sorted(wanted - found)
        if missing:
            raise LessonOfferError("Requested lesson-offer frame is outside the bounded range: " + ", ".join(missing))
    return selected


def generate(root: str | Path, *, start_ms: int, end_ms: int, frame_ids: list[str] | None = None,
             reader: Any = None, model_dir: str | Path = ".local/models/rapidocr") -> dict[str, Any]:
    """Generate sidecars only for an explicitly bounded set of neural rows."""

    root = Path(root)
    selected = _selected_neural_rows(root, start_ms, end_ms, frame_ids or [])
    destination = root / "lesson-offer-refinement"
    destination.mkdir(parents=True, exist_ok=True)
    if reader is None and any(_source_screen(raw) == "lesson_selection" for _, raw in selected):
        from .vision import NeuralReader
        reader = NeuralReader(model_dir)
    summary = dict(stage="lesson_offer_refinement", start_ms=start_ms, end_ms=end_ms,
                   selected_rows=len(selected), written=0, skipped_existing=0, offer_rows=0)
    for path, raw in selected:
        if _source_screen(raw) != "lesson_selection":
            continue
        target = destination / path.name
        if target.exists():
            summary["skipped_existing"] += 1
            continue
        evidence = root / str(raw.get("evidence", ""))
        if not evidence.is_file():
            raise LessonOfferError(f"Missing lesson-offer gameplay evidence: {raw.get('evidence')}")
        source_frame_path = None
        if raw.get("source_frame_sha256") is not None:
            source_frame_evidence = raw.get("source_frame_evidence")
            if not isinstance(source_frame_evidence, str) or not source_frame_evidence:
                raise LessonOfferError("Lesson offer source-frame evidence path is required.")
            source_frame_path = (root / source_frame_evidence).resolve()
            try:
                source_frame_path.relative_to(root.resolve())
            except (OSError, RuntimeError, ValueError) as exc:
                raise LessonOfferError("Lesson offer source-frame evidence leaves the source root.") from exc
            if not source_frame_path.is_file():
                raise LessonOfferError(f"Missing lesson-offer source-frame evidence: {source_frame_evidence}")
        with Image.open(evidence) as image:
            extra = build(
                image.convert("RGB"),
                raw,
                file_fingerprint(evidence),
                reader,
                source_frame_path=source_frame_path,
            )
        with target.open("x", encoding="utf-8") as stream:
            json.dump(extra, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        summary["written"] += 1
        summary["offer_rows"] += len(extra["cards"])
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="recording analysis directory")
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--frame-id", action="append", dest="frame_ids", default=[])
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    args = parser.parse_args(argv)
    print(json.dumps(generate(args.output, start_ms=args.start_ms, end_ms=args.end_ms,
                              frame_ids=args.frame_ids, model_dir=args.model_dir)), flush=True)


if __name__ == "__main__":
    main()
