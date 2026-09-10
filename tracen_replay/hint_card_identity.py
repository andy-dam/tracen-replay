"""Recover a skill-hint identity from a visible HINT card.

The dialogue receipt can lose its recipient name when the gameplay cursor
covers the line.  This module keeps that receipt text as raw evidence and
returns a separate, source-bound recovery candidate only when the same
standalone HINT card is visible at multiple source timestamps.

``recover`` consumes parsed gameplay rows and the directory containing their
810x1080 gameplay PNGs.  It never edits a row.  A candidate requires:

* one event-outcome row per timestamp with one HINT header, one card name, and
  one hint receipt amount;
* the exact card spelling and card geometry to persist through a contiguous
  episode;
* for the original complete-line path, ``inventory_suffix.detect`` must report
  the same visible suffix at two or more distinct timestamps and a prefix
  crop must corroborate the amount; or
* for a wrapped line, a standalone card and an amount-only crop must each be
  source-proven at every corroborating timestamp.  The rank may remain
  ``undetermined`` when no marker is visible.

All crops are created from source geometry and receive no expected name or
amount.  Wrapped prefix and continuation text is retained verbatim.

The prefix crop is an independent view of one source PNG, not another frame.
Only timestamps, never crops or OCR views, count toward corroboration.  The
reader and image results are cached within one call so a full recording does
not repeatedly decode or hash the same evidence file.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_SOURCE_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_HINT_HEADER_RE = re.compile(r"^\s*hint(?:\b|[.:])", re.IGNORECASE)
_GAINED_HINT_RE = re.compile(
    r"^\s*Gained\s*(?P<amount>\d+)\s+hint\s+level(?:\(s\)|s)?\s+for\s+(?P<name>.+?)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_HINT_UP_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+hint\s+(?:level|Lv\.?)\s+(?:went\s+up\s+by|increased\s+by)\s*(?P<amount>\d+)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_RECEIPT_Y_RANGE = (770.0, 1000.0)
_CARD_Y_RANGE = (640.0, 760.0)
_HEADER_Y_RANGE = (530.0, 680.0)
# Keep corroboration on adjacent source samples.  A missing or malformed
# intervening row is a boundary; it cannot be silently bridged.
_MAX_ROW_GAP_MS = 250
_MIN_CARD_CONFIDENCE = 95.0
_MIN_HEADER_CONFIDENCE = 90.0
_MIN_RECEIPT_CONFIDENCE = 90.0
_MIN_PREFIX_CONFIDENCE = 90.0
_WRAPPED_HINT_KIND = "wrapped_hint_receipt"
_WRAPPED_HINT_PREFIX_RE = re.compile(
    r"^\s*Gained\s*(?P<amount>\d{1,2})\s+hint\b(?P<body>.*)$",
    re.IGNORECASE,
)
_WRAPPED_AMOUNT_DELIMITER_BASIS = "amount_digit_followed_by_hint_delimiter_ocr"
_WRAPPED_AMOUNT_BASIS = "source_bound_amount_digit_crop_ocr"
_WRAPPED_IDENTITY_FRAGMENT_BASIS = "source_bound_identity_fragment_tail_ocr"
_WRAPPED_IDENTITY_FRAGMENT_GAP = 3.0
_WRAPPED_MAX_CONTINUATION_GAP = 40.0
_WRAPPED_MAX_LINE_MOVE = 40.0
_WORD_TOKEN_PATTERN = (
    r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?"
    r"(?:[-‐‑‒–—/&+][A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?)*"
)
_WORD_TOKEN_RE = re.compile(_WORD_TOKEN_PATTERN)


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _box(value: Any, *, full_frame: bool = True) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(_finite_number(item) for item in value):
        return None
    left, top, right, bottom = (float(item) for item in value)
    max_x = 1920.0 if full_frame else 810.0
    if not (0.0 <= left < right <= max_x and 0.0 <= top < bottom <= 1080.0):
        return None
    # OCR rows from this pipeline use full-frame coordinates.  Refuse boxes
    # outside the gameplay pane so a similarly shaped UI element cannot be
    # treated as the card or receipt.
    if full_frame and not (148.0 <= left < right <= 958.0):
        return None
    return left, top, right, bottom


def _box_list(value: Sequence[float]) -> list[float | int]:
    # Preserve integer-looking coordinates in provenance while accepting the
    # float boxes emitted by downstream callers.
    result: list[float | int] = []
    for item in value:
        result.append(int(item) if float(item).is_integer() else float(item))
    return result


def _center_y(box: Sequence[float]) -> float:
    return (box[1] + box[3]) / 2.0


def _geometry_compatible(
    first: Sequence[float],
    second: Sequence[float],
    *,
    height_tolerance: float = 8.0,
) -> bool:
    return (
        abs(first[0] - second[0]) <= 8.0
        and abs(first[1] - second[1]) <= 8.0
        and abs((first[2] - first[0]) - (second[2] - second[0])) <= 25.0
        and abs((first[3] - first[1]) - (second[3] - second[1])) <= height_tolerance
    )


def _timestamp(row: Mapping[str, Any]) -> int | None:
    value = row.get("source_timestamp_ms")
    if type(value) is not int or value < 0:
        return None
    return value


def _evidence(row: Mapping[str, Any]) -> str | None:
    value = row.get("evidence")
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    path = Path(value)
    if ".." in path.parts:
        return None
    return value.replace("\\", "/")


def _confidence(line: Mapping[str, Any]) -> float | None:
    value = line.get("confidence")
    if not _finite_number(value) or not 0.0 <= float(value) <= 100.0:
        return None
    return float(value)


def _context(row: Mapping[str, Any]) -> str | None:
    values = []
    for key in ("context_title", "context_title_candidate"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    if not values or len(set(values)) != 1:
        return None
    return values[0]


def _neural_lines(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ocr = row.get("ocr")
    if not isinstance(ocr, Mapping) or not isinstance(ocr.get("neural"), list):
        return []
    return [line for line in ocr["neural"] if isinstance(line, Mapping)]


def _header_candidates(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for line in _neural_lines(row):
        text = line.get("text")
        confidence = _confidence(line)
        box = _box(line.get("box"))
        if (
            not isinstance(text, str)
            or confidence is None
            or confidence < _MIN_HEADER_CONFIDENCE
            or box is None
            or not _HEADER_Y_RANGE[0] <= _center_y(box) <= _HEADER_Y_RANGE[1]
            or not _HINT_HEADER_RE.match(text)
        ):
            continue
        result.append({"text": text.strip(), "confidence": confidence, "box": box})
    return result


def _card_candidates(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    headers = _header_candidates(row)
    if len(headers) != 1:
        return []
    header = headers[0]
    result = []
    for line in _neural_lines(row):
        text = line.get("text")
        confidence = _confidence(line)
        box = _box(line.get("box"))
        if (
            not isinstance(text, str)
            or not text.strip()
            or confidence is None
            or confidence < _MIN_CARD_CONFIDENCE
            or box is None
            or not _CARD_Y_RANGE[0] <= _center_y(box) <= _CARD_Y_RANGE[1]
            or _center_y(box) <= _center_y(header["box"]) + 12.0
            or line.get("overlay_occluded") is True
            or bool(line.get("overlay_boxes"))
            or line.get("recipient_name_occluded") is True
            or text.strip().lower().startswith("hint")
            or "gained" in text.lower()
            or " went " in text.lower()
        ):
            continue
        result.append(
            {
                "text": text.strip(),
                "confidence": confidence,
                "box": box,
                "header": deepcopy(header),
            }
        )
    return result


def _parse_hint_text(text: str) -> tuple[int, str] | None:
    match = _GAINED_HINT_RE.fullmatch(text.strip()) or _HINT_UP_RE.fullmatch(text.strip())
    if not match:
        return None
    amount = int(match.group("amount"))
    name = match.group("name").strip()
    if not name:
        return None
    return amount, name


def _wrapped_prefix(text: Any) -> tuple[int, str] | None:
    """Read only the stable amount-bearing prefix of a wrapped receipt.

    The remainder is deliberately opaque.  In particular, this helper does
    not repair OCR glyphs or infer a recipient name.  It exists for lines such
    as ``Gained 2 hint l:ve s.wor Straightaway`` whose fixed boilerplate is
    corrupted while the amount and a visible name fragment remain usable.
    """
    if not isinstance(text, str):
        return None
    match = _WRAPPED_HINT_PREFIX_RE.fullmatch(text.strip())
    if match is None:
        return None
    amount = int(match.group("amount"))
    body = match.group("body").strip()
    if not 0 < amount <= 99 or not body:
        return None
    # A complete strict receipt already has a safer representation.  Keeping
    # it out of this path avoids duplicate candidates for one source row.
    if _parse_hint_text(text) is not None:
        return None
    return amount, body


def _word_tokens(text: str) -> list[str]:
    """Tokenize name words while retaining meaningful internal punctuation.

    Hyphenated (and similarly joined) names must not compare equal to names
    whose words merely happen to be separated by OCR whitespace.  Sentence
    punctuation is handled by the caller for the continuation line.
    """
    return _WORD_TOKEN_RE.findall(text)


def _literal_fragment(text: str) -> str:
    """Normalize only case and whitespace for a source-visible fragment."""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _without_one_terminal_sentence_mark(text: str) -> str:
    """Remove the one punctuation mark added by a terminal receipt line."""
    return re.sub(r"[.!?]\s*$", "", text)


def _wrapped_name_fragment(
    prefix_text: str,
    continuation_text: str,
    card_text: str,
) -> str | None:
    """Match visible receipt fragments to an exact standalone card name."""
    card_text = card_text.strip()
    prefix_text = prefix_text.strip()
    continuation_text = continuation_text.strip()
    card_words = _word_tokens(card_text)
    prefix_words = _word_tokens(prefix_text)
    continuation_words = _word_tokens(continuation_text)
    if len(card_words) < 2 or not prefix_words or not continuation_words:
        return None
    card_spans = list(_WORD_TOKEN_RE.finditer(card_text))
    prefix_spans = list(_WORD_TOKEN_RE.finditer(prefix_text))
    if (
        len(card_spans) != len(card_words)
        or len(prefix_spans) != len(prefix_words)
        or card_spans[0].start() != 0
    ):
        # A leading symbol that is absent from both receipt fragments is an
        # unsupported identity character.  Dropping it would make two
        # distinct cards compare equal.
        return None
    card_lower = [word.lower() for word in card_words]
    prefix_lower = [word.lower() for word in prefix_words]
    continuation_lower = [word.lower() for word in continuation_words]
    # The wrapped line must end with a visible prefix of the exact card name,
    # and the continuation must be its exact final suffix.  No edit distance,
    # catalog lookup, or expected name is involved.
    matched_prefix_length = 0
    for length in range(min(len(card_lower), len(prefix_lower)), 0, -1):
        # The two visible fragments must partition the card name exactly.
        # This rejects an apparent ``Alpha ... Gamma`` match for the card
        # ``Alpha Beta Gamma`` and prevents a later line from supplying an
        # arbitrary suffix while silently dropping middle words.
        if (
            prefix_lower[-length:] == card_lower[:length]
            and continuation_lower == card_lower[length:]
            and _literal_fragment(
                prefix_text[prefix_spans[-length].start() :]
            )
            == _literal_fragment(
                card_text[card_spans[0].start() : card_spans[length - 1].end()]
            )
            and (
                _literal_fragment(continuation_text)
                == _literal_fragment(card_text[card_spans[length - 1].end() :])
                or _literal_fragment(_without_one_terminal_sentence_mark(continuation_text))
                == _literal_fragment(card_text[card_spans[length - 1].end() :])
            )
        ):
            matched_prefix_length = length
            break
    if matched_prefix_length == 0:
        return None
    return card_text.strip()


def _wrapped_line_records(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return deduplicated raw/retained OCR lines for wrapped receipts."""
    records: dict[tuple[str, tuple[float, ...]], dict[str, Any]] = {}
    for line in _neural_lines(row):
        text = line.get("text")
        box = _box(line.get("box"))
        confidence = _confidence(line)
        if not isinstance(text, str) or box is None or confidence is None:
            continue
        overlay_boxes = line.get("overlay_boxes")
        if overlay_boxes is not None:
            if not isinstance(overlay_boxes, list):
                continue
            parsed_overlays = []
            for overlay in overlay_boxes:
                parsed_overlay = _box(overlay)
                if parsed_overlay is None:
                    parsed_overlays = None
                    break
                parsed_overlays.append(_box_list(parsed_overlay))
            if parsed_overlays is None:
                continue
        else:
            parsed_overlays = []
        item = {
            "text": text.strip(),
            "confidence": confidence,
            "box": box,
            "source": "neural",
            "overlay_occluded": line.get("overlay_occluded") is True or bool(parsed_overlays),
            "overlay_boxes": parsed_overlays,
            "recipient_name_occluded": line.get("recipient_name_occluded") is True,
        }
        key = (item["text"], tuple(item["box"]))
        prior = records.get(key)
        if prior is None or item["confidence"] > prior["confidence"]:
            records[key] = item
    facts = row.get("facts")
    occluded = facts.get("occluded_receipt_lines") if isinstance(facts, Mapping) else None
    if isinstance(occluded, list):
        for line in occluded:
            if not isinstance(line, Mapping):
                continue
            text = line.get("text")
            box = _box(line.get("box"))
            confidence = _confidence(line)
            if not isinstance(text, str) or box is None or confidence is None:
                continue
            overlay_boxes = line.get("overlay_boxes")
            if overlay_boxes is not None:
                if not isinstance(overlay_boxes, list):
                    continue
                parsed_overlays = []
                for overlay in overlay_boxes:
                    parsed_overlay = _box(overlay)
                    if parsed_overlay is None:
                        parsed_overlays = None
                        break
                    parsed_overlays.append(_box_list(parsed_overlay))
                if parsed_overlays is None:
                    continue
            else:
                parsed_overlays = []
            key = (text.strip(), tuple(box))
            item = {
                "text": text.strip(),
                "confidence": confidence,
                "box": box,
                "source": "occluded_receipt_line",
                "overlay_occluded": line.get("overlay_occluded") is True or bool(parsed_overlays),
                "overlay_boxes": parsed_overlays,
                "recipient_name_occluded": line.get("recipient_name_occluded") is True,
            }
            prior = records.get(key)
            if prior is None or item["confidence"] > prior["confidence"]:
                records[key] = item
    return sorted(records.values(), key=lambda item: (item["box"][1], item["box"][0], item["text"]))


def _wrapped_is_effect_line(line: Mapping[str, Any]) -> bool:
    try:
        from .gameplay import effects_from_lines

        return bool(effects_from_lines([line]))
    except (ImportError, TypeError, ValueError):
        # If the semantic parser is unavailable, do not turn an unknown line
        # into a continuation by guessing from its prose.
        return True


def _wrapped_continuation(
    lines: Sequence[Mapping[str, Any]],
    prefix: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Find one immediate, short, terminal-punctuated continuation line."""
    prefix_box = prefix.get("box")
    if prefix_box is None:
        return None
    prefix_center = _center_y(prefix_box)
    candidates: list[dict[str, Any]] = []
    for line in lines:
        text = line.get("text")
        box = line.get("box")
        confidence = line.get("confidence")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(box, (list, tuple))
            or _box(box) is None
            or not _finite_number(confidence)
            or float(confidence) < _MIN_RECEIPT_CONFIDENCE
            or line.get("overlay_occluded") is True
            or bool(line.get("overlay_boxes"))
            or line.get("recipient_name_occluded") is True
            or box[1] <= prefix_box[1]
            or not 0.0 < _center_y(box) - prefix_center <= _WRAPPED_MAX_CONTINUATION_GAP
            or abs(float(box[0]) - float(prefix_box[0])) > 15.0
            or len(_word_tokens(text)) > 4
            or not re.search(r"[.!?]\s*$", text)
            or not text.lstrip()[0].isupper()
            or _wrapped_is_effect_line(line)
        ):
            continue
        candidates.append(
            {
                "text": text.strip(),
                "confidence": float(confidence),
                "box": _box(box),
                "source": line.get("source", "neural"),
            }
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (_center_y(item["box"]), item["box"][0], item["text"]))
    # A second line at the same transition is ambiguous.  Do not select one
    # merely because it is shorter or has a higher OCR score.
    if len(candidates) > 1 and abs(_center_y(candidates[0]["box"]) - _center_y(candidates[1]["box"])) <= 4.0:
        return None
    return candidates[0]


def _wrapped_receipt_candidates(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    lines = _wrapped_line_records(row)
    result: list[dict[str, Any]] = []
    for prefix in lines:
        parsed = _wrapped_prefix(prefix.get("text"))
        box = prefix.get("box")
        if parsed is None or box is None:
            continue
        if not _RECEIPT_Y_RANGE[0] <= _center_y(box) <= _RECEIPT_Y_RANGE[1]:
            continue
        continuation = _wrapped_continuation(lines, prefix)
        if continuation is None:
            continue
        result.append(
            {
                "text": prefix["text"],
                "confidence": min(prefix["confidence"], continuation["confidence"]),
                "box": box,
                "amount": parsed[0],
                "prefix": dict(prefix),
                "continuation": continuation,
                "source": "wrapped_receipt",
            }
        )
    return result


def _wrapped_context(row: Mapping[str, Any]) -> tuple[bool, str | None]:
    values = []
    for key in ("context_title", "context_title_candidate"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    if values and len(set(values)) != 1:
        return False, None
    # A missing title is allowed for the source HINT transition, but it is
    # represented as unknown and must match another unknown row exactly.
    return True, values[0] if values else None


def _wrapped_contexts_compatible(left: str | None, right: str | None) -> bool:
    return left == right


def _wrapped_line_geometry_compatible(
    first: Sequence[float],
    second: Sequence[float],
) -> bool:
    first_width = first[2] - first[0]
    second_width = second[2] - second[0]
    first_height = first[3] - first[1]
    second_height = second[3] - second[1]
    top_delta = second[1] - first[1]
    return (
        abs(first[0] - second[0]) <= 8.0
        and abs(first_width - second_width) <= 25.0
        and abs(first_height - second_height) <= 8.0
        and -_WRAPPED_MAX_LINE_MOVE <= top_delta <= 4.0
    )


def _wrapped_card_match(item: Mapping[str, Any]) -> bool:
    card = item.get("card")
    receipt = item.get("receipt")
    if not isinstance(card, Mapping) or not isinstance(receipt, Mapping):
        return False
    return _wrapped_name_fragment(
        receipt["prefix"]["text"],
        receipt["continuation"]["text"],
        card["text"],
    ) is not None


def _receipt_candidates(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for line in _neural_lines(row):
        box = _box(line.get("box"))
        confidence = _confidence(line)
        text = line.get("text")
        if (
            box is None
            or confidence is None
            or confidence < _MIN_RECEIPT_CONFIDENCE
            or not isinstance(text, str)
            or line.get("overlay_occluded") is True
            or not _RECEIPT_Y_RANGE[0] <= _center_y(box) <= _RECEIPT_Y_RANGE[1]
        ):
            continue
        parsed = _parse_hint_text(text)
        if parsed is None:
            continue
        amount, name = parsed
        lines.append(
            {
                "text": text.strip(),
                "confidence": confidence,
                "box": box,
                "name": name,
                "amount": amount,
                "source": "neural",
            }
        )

    facts = row.get("facts")
    if isinstance(facts, Mapping):
        occluded = facts.get("occluded_receipt_lines")
        if isinstance(occluded, list):
            for line in occluded:
                if not isinstance(line, Mapping):
                    continue
                box = _box(line.get("box"))
                confidence = _confidence(line)
                text = line.get("text")
                if (
                    box is None
                    or confidence is None
                    or confidence < _MIN_RECEIPT_CONFIDENCE
                    or not isinstance(text, str)
                    or not _RECEIPT_Y_RANGE[0] <= _center_y(box) <= _RECEIPT_Y_RANGE[1]
                ):
                    continue
                parsed = _parse_hint_text(text)
                if parsed is None:
                    continue
                amount, name = parsed
                lines.append(
                    {
                        "text": text.strip(),
                        "confidence": confidence,
                        "box": box,
                        "name": name,
                        "amount": amount,
                        "source": "occluded_receipt_line",
                    }
                )

    # OCR and the retained occlusion line can be the same observation.  They
    # are correlated and must never create two amounts or two timestamps.
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for line in lines:
        key = (line["text"], line["amount"], tuple(line["box"]))
        prior = unique.get(key)
        if prior is None or line["confidence"] > prior["confidence"]:
            unique[key] = line
    return list(unique.values())


def _overlay_boxes(row: Mapping[str, Any]) -> list[tuple[float, float, float, float]]:
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return []
    result: list[tuple[float, float, float, float]] = []
    metadata = facts.get("receipt_overlay_evidence")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("overlay_boxes"), list):
        values = metadata["overlay_boxes"]
        for value in values:
            box = _box(value)
            if box is not None:
                result.append(box)
    occluded = facts.get("occluded_receipt_lines")
    if isinstance(occluded, list):
        for line in occluded:
            if not isinstance(line, Mapping) or not isinstance(line.get("overlay_boxes"), list):
                continue
            for value in line["overlay_boxes"]:
                box = _box(value)
                if box is not None:
                    result.append(box)
    return list(dict.fromkeys(result))


def _intersecting_overlay(
    receipt_box: Sequence[float], overlays: Sequence[Sequence[float]]
) -> tuple[float, float, float, float] | None:
    receipt_center = _center_y(receipt_box)
    candidates = [
        overlay
        for overlay in overlays
        if overlay[0] < receipt_box[2]
        and overlay[2] > receipt_box[0]
        and overlay[1] <= receipt_center <= overlay[3]
    ]
    if not candidates:
        return None
    # The prefix crop ends at the first verified obstruction.  No text width
    # or expected name is used to choose this boundary.
    return min(candidates, key=lambda value: (value[0], value[1], value[2], value[3]))


def _path_for(evidence_root: Path, evidence: str) -> Path | None:
    root = evidence_root.resolve()
    path = (root / evidence).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frame_id(evidence: str) -> str | None:
    name = Path(evidence).name
    if not name.endswith(".png"):
        return None
    value = name[:-4]
    return value if value.startswith("part-") and "-frame-" in value else None


def _load_source_manifest(
    evidence_root: Path,
    *,
    source_sha256: str,
) -> dict[str, Mapping[str, Any]] | None:
    """Load the immutable capture index used to bind gameplay PNGs to source frames.

    A report-level source digest alone does not prove that a PNG belongs to
    that recording.  The normal full-recording cache provides the missing
    chain: capture manifest -> source frame bytes -> neural cache -> gameplay
    PNG bytes.  This is loaded once per recovery call.
    """
    manifest_path = evidence_root / "capture.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, Mapping):
        return None
    source = manifest.get("source")
    frames = manifest.get("frames")
    if not isinstance(source, Mapping) or source.get("sha256") != source_sha256:
        return None
    if not isinstance(frames, list):
        return None
    result: dict[str, Mapping[str, Any]] = {}
    for frame in frames:
        if not isinstance(frame, Mapping):
            return None
        frame_id = frame.get("id")
        timestamp = frame.get("source_timestamp_ms")
        evidence = frame.get("evidence")
        if (
            not isinstance(frame_id, str)
            or not isinstance(evidence, str)
            or type(timestamp) is not int
            or timestamp < 0
            or frame_id in result
        ):
            return None
        result[frame_id] = frame
    return result


def _raw_cache_path(evidence_root: Path, evidence: str) -> Path | None:
    frame_id = _frame_id(evidence)
    if frame_id is None:
        return None
    path = evidence_root / "neural" / f"{frame_id}.json"
    return path if path.is_file() else None


def _load_raw_binding(
    evidence_root: Path,
    row: Mapping[str, Any],
    *,
    source_sha256: str,
    evidence_path: Path,
    image: Any,
    evidence_sha256: str,
    source_manifest: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Validate the cache provenance chain for one parsed reading."""
    evidence = _evidence(row)
    timestamp = _timestamp(row)
    frame_id = _frame_id(evidence) if evidence is not None else None
    raw_path = _raw_cache_path(evidence_root, evidence) if evidence is not None else None
    if evidence is None or timestamp is None or frame_id is None or raw_path is None:
        return None
    frame = source_manifest.get(frame_id)
    if frame is None or frame.get("source_timestamp_ms") != timestamp:
        return None
    if frame.get("evidence") is not None and not isinstance(frame.get("evidence"), str):
        return None
    source_frame_rel = frame.get("evidence")
    if not isinstance(source_frame_rel, str):
        return None
    source_frame_path = _path_for(evidence_root, source_frame_rel)
    if source_frame_path is None:
        return None
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    if (
        raw.get("source_timestamp_ms") != timestamp
        or raw.get("evidence") != evidence
        or not isinstance(raw.get("lines"), list)
        or not isinstance(raw.get("engine_fingerprint"), str)
        or not raw.get("engine_fingerprint")
        or not isinstance(raw.get("model_sha256"), Mapping)
        or not raw.get("model_sha256")
    ):
        return None
    raw_gameplay_sha = raw.get("gameplay_sha256")
    raw_frame_sha = raw.get("source_frame_sha256")
    if (
        not isinstance(raw_gameplay_sha, str)
        or not _SOURCE_SHA_RE.fullmatch(raw_gameplay_sha)
        or not isinstance(raw_frame_sha, str)
        or not _SOURCE_SHA_RE.fullmatch(raw_frame_sha)
    ):
        return None
    try:
        gameplay_sha = hashlib.sha256(image.tobytes()).hexdigest()
    except (AttributeError, TypeError):
        return None
    if gameplay_sha != raw_gameplay_sha:
        return None
    try:
        source_frame_digest = _digest(source_frame_path)
        raw_digest = _digest(raw_path)
        current_evidence_digest = _digest(evidence_path)
    except OSError:
        return None
    if source_frame_digest != raw_frame_sha:
        return None
    if current_evidence_digest != evidence_sha256:
        return None
    if row.get("source_sha256") is not None and row.get("source_sha256") != source_sha256:
        return None
    if row.get("evidence_sha256") is not None and row.get("evidence_sha256") != evidence_sha256:
        return None
    if row.get("gameplay_sha256") is not None and row.get("gameplay_sha256") != gameplay_sha:
        return None
    if row.get("source_frame_sha256") is not None and row.get("source_frame_sha256") != raw_frame_sha:
        return None
    if row.get("source_frame_path") is not None and row.get("source_frame_path") != source_frame_rel:
        return None
    return {
        "raw_path": raw_path,
        "raw_sha256": raw_digest,
        "raw": raw,
        "evidence_sha256": evidence_sha256,
        "gameplay_sha256": gameplay_sha,
        "source_frame_path": source_frame_rel,
        "source_frame_sha256": raw_frame_sha,
        "capture_frame_id": frame_id,
        "capture_frame_evidence": source_frame_rel,
    }


def _raw_has_line(raw: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    """Require the parsed selected line to exist in the bound raw OCR cache."""
    candidate_text = candidate.get("text")
    candidate_box = candidate.get("box")
    if not isinstance(candidate_text, str) or candidate_box is None:
        return False
    for line in raw.get("lines", ()):
        if not isinstance(line, Mapping):
            continue
        if line.get("text") != candidate_text:
            continue
        box = _box(line.get("box"))
        if box is not None and tuple(box) == tuple(candidate_box):
            return True
    return False


def _raw_supports_item(item: Mapping[str, Any], raw: Mapping[str, Any]) -> bool:
    """Bind card/header/receipt facts to the same immutable neural reading."""
    card = item.get("card")
    receipt = item.get("receipt")
    context = item.get("context")
    header = card.get("header") if isinstance(card, Mapping) else None
    if (
        not isinstance(card, Mapping)
        or not isinstance(receipt, Mapping)
        or not isinstance(header, Mapping)
        or not isinstance(context, str)
        or not context
    ):
        return False
    if (
        not _raw_has_line(raw, card)
        or not _raw_has_line(raw, header)
        or not any(
            isinstance(line, Mapping) and line.get("text") == context
            for line in raw.get("lines", ())
        )
    ):
        return False
    # Occlusion annotation may set the parsed neural receipt confidence to
    # zero, but it retains the original text/box in facts.  Both representations
    # must still point to a line in the immutable raw cache.
    return _raw_has_line(raw, receipt)


def _source_overlay_matches(image: Any, expected: Sequence[float]) -> bool:
    """Require the obstruction boundary to be visible in the bound PNG."""
    try:
        from .receipt_occlusion import overlay_boxes

        actual = overlay_boxes(image)
    except (AttributeError, ImportError, IndexError, RuntimeError, TypeError, ValueError):
        return False
    if not isinstance(actual, list):
        return False
    # The parser stores the padded cursor outline while the detector may vary
    # by one pixel at an antialiased edge.  A close match still binds the crop
    # boundary to source pixels; a fabricated facts-only box does not.
    return any(
        isinstance(box, (list, tuple))
        and len(box) == 4
        and all(_finite_number(value) for value in box)
        and all(abs(float(left) - float(right)) <= 1.0 for left, right in zip(box, expected))
        for box in actual
    )


def _validate_provenance(
    row: Mapping[str, Any],
    *,
    source_sha256: str,
    evidence_root: Path,
    evidence_path: Path,
    image: Any,
    evidence_sha256: str,
) -> bool:
    # Optional provenance fields are checked when present.  A caller that
    # supplies a field without its matching source asset fails closed.
    row_source = row.get("source_sha256")
    if row_source is not None and row_source != source_sha256:
        return False
    expected_file = row.get("evidence_sha256")
    if expected_file is not None and expected_file != evidence_sha256:
        return False
    gameplay_sha = row.get("gameplay_sha256")
    if gameplay_sha is not None:
        try:
            actual_gameplay = hashlib.sha256(image.tobytes()).hexdigest()
        except (AttributeError, TypeError):
            return False
        if gameplay_sha != actual_gameplay:
            return False
    source_frame_sha = row.get("source_frame_sha256")
    if source_frame_sha is not None:
        source_frame_path = row.get("source_frame_path")
        if not isinstance(source_frame_path, str):
            return False
        frame_path = _path_for(evidence_root, source_frame_path)
        if frame_path is None:
            return False
        try:
            frame_digest = _digest(frame_path)
        except OSError:
            return False
        if frame_digest != source_frame_sha:
            return False
    timestamp = row.get("source_timestamp_ms")
    return type(timestamp) is int and timestamp >= 0


def _load_image(
    evidence_root: Path,
    row: Mapping[str, Any],
    cache: dict[str, dict[str, Any]],
    *,
    source_sha256: str,
    source_manifest: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    evidence = _evidence(row)
    if evidence is None:
        return None
    cached = cache.get(evidence)
    if cached is not None:
        # Reusing the same PNG at two timestamps would make the timestamps
        # aliases rather than observations.  The caller detects that case.
        return cached
    path = _path_for(evidence_root, evidence)
    if path is None:
        return None
    try:
        from PIL import Image

        with Image.open(path) as opened:
            image = opened.convert("RGB")
    except (OSError, ValueError, TypeError, ImportError):
        return None
    if image.size != (810, 1080):
        return None
    try:
        evidence_sha256 = _digest(path)
    except OSError:
        return None
    if not _validate_provenance(
        row,
        source_sha256=source_sha256,
        evidence_root=evidence_root,
        evidence_path=path,
        image=image,
        evidence_sha256=evidence_sha256,
    ):
        return None
    binding = _load_raw_binding(
        evidence_root,
        row,
        source_sha256=source_sha256,
        evidence_path=path,
        image=image,
        evidence_sha256=evidence_sha256,
        source_manifest=source_manifest,
    )
    if binding is None:
        return None
    cached = {
        "path": path,
        "image": image,
        "evidence_sha256": evidence_sha256,
        "binding": binding,
    }
    cache[evidence] = cached
    return cached


def _prefix_amount_ocr(reader: Any, image: Any, crop_box: Sequence[float]) -> dict[str, Any] | None:
    """Read an amount using a crop ending at a verified obstruction.

    The OCR receives only pixels.  It is not given a target text, a name
    catalog, or a crop width estimated from the expected sentence.
    """
    try:
        import numpy as np

        left, top, right, bottom = (int(round(value)) for value in crop_box)
        crop = image.crop((left - 148, top, right - 148, bottom))
        if crop.width < 32 or crop.height < 8:
            return None
        result = reader.engine.text_rec(
            reader.TextRecInput(img=[np.asarray(crop)[:, :, ::-1]])
        )
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    readings = []
    for text, score in zip(getattr(result, "txts", ()) or (), getattr(result, "scores", ()) or ()):
        if not isinstance(text, str) or not _finite_number(score):
            continue
        parsed = _parse_hint_text(text)
        if parsed is None:
            continue
        amount, name = parsed
        confidence = float(score) * 100.0
        if confidence < _MIN_PREFIX_CONFIDENCE:
            continue
        readings.append((amount, name, text.strip(), confidence))
    if not readings:
        return None
    amounts = {item[0] for item in readings}
    if len(amounts) != 1:
        return None
    best = max(readings, key=lambda item: (item[3], len(item[2]), item[2]))
    return {
        "amount": best[0],
        "raw_name": best[1],
        "recognized_text": best[2],
        "confidence": round(best[3], 4),
        "crop_box": _box_list(crop_box),
        "basis": "independent_prefix_crop_ocr_to_verified_overlay_boundary",
    }


def _static_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if row.get("screen") != "event_outcome":
        return None
    timestamp = _timestamp(row)
    evidence = _evidence(row)
    context = _context(row)
    cards = _card_candidates(row)
    receipts = _receipt_candidates(row)
    if timestamp is None or evidence is None or context is None:
        return None
    if len(cards) != 1 or len(receipts) != 1:
        return None
    overlay = _intersecting_overlay(receipts[0]["box"], _overlay_boxes(row))
    if overlay is None or overlay[0] <= receipts[0]["box"][0] + 16.0:
        return None
    return {
        "row": row,
        "timestamp": timestamp,
        "evidence": evidence,
        "context": context,
        "card": cards[0],
        "receipt": receipts[0],
        "overlay": overlay,
    }


def _wrapped_static_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract one source row whose receipt is split across two OCR lines.

    The card line supplies the identity anchor.  The receipt contributes only
    its stable amount prefix and two exact name fragments; the continuation is
    retained verbatim so later stages can audit the wrapped text.  A missing
    context title is represented as ``None`` rather than fabricated from the
    card or receipt.
    """
    if row.get("screen") != "event_outcome":
        return None
    timestamp = _timestamp(row)
    evidence = _evidence(row)
    context_valid, context = _wrapped_context(row)
    cards = _card_candidates(row)
    receipts = _wrapped_receipt_candidates(row)
    if (
        timestamp is None
        or evidence is None
        or not context_valid
        or len(cards) != 1
        or len(receipts) != 1
    ):
        return None
    receipt = receipts[0]
    matched_name = _wrapped_name_fragment(
        receipt["prefix"]["text"],
        receipt["continuation"]["text"],
        cards[0]["text"],
    )
    if matched_name is None:
        # Multiple wrapped receipts or no card-aligned receipt is ambiguous;
        # never choose by OCR confidence or line order.
        return None
    receipt_box = receipt["box"]
    overlay = _intersecting_overlay(receipt_box, _overlay_boxes(row))
    return {
        "variant": _WRAPPED_HINT_KIND,
        "row": row,
        "timestamp": timestamp,
        "evidence": evidence,
        "context": context,
        "card": cards[0],
        "receipt": {
            "text": f'{receipt["prefix"]["text"]} {receipt["continuation"]["text"]}',
            "name": matched_name,
            "amount": receipt["amount"],
            "confidence": receipt["confidence"],
            "box": receipt_box,
            "source": receipt["source"],
            "prefix": deepcopy(receipt["prefix"]),
            "continuation": deepcopy(receipt["continuation"]),
        },
        "overlay": overlay,
    }


def _wrapped_contiguous_runs(
    rows: Sequence[dict[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Build wrapped runs without skipping any source row or card change."""
    accepted = {
        (item["timestamp"], item["evidence"]): item
        for item in rows
    }
    ordered = sorted(
        source_rows,
        key=lambda row: (_timestamp(row), _evidence(row)),
    )
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in ordered:
        key = (_timestamp(row), _evidence(row))
        item = accepted.get(key)
        if item is None:
            if current:
                runs.append(current)
                current = []
            continue
        if current:
            previous = current[-1]
            if not (
                item["variant"] == previous["variant"] == _WRAPPED_HINT_KIND
                and item["timestamp"] > previous["timestamp"]
                and item["timestamp"] - previous["timestamp"] <= _MAX_ROW_GAP_MS
                and item["context"] == previous["context"]
                and item["card"]["text"] == previous["card"]["text"]
                and item["receipt"]["amount"] == previous["receipt"]["amount"]
                and _geometry_compatible(
                    previous["card"]["box"], item["card"]["box"]
                )
                and _wrapped_line_geometry_compatible(
                    previous["receipt"]["box"], item["receipt"]["box"]
                )
            ):
                runs.append(current)
                current = []
        if current:
            current.append(item)
        else:
            current = [item]
    if current:
        runs.append(current)
    return runs


def _wrapped_run_is_consistent(run: Sequence[dict[str, Any]]) -> bool:
    if len(run) < 2:
        return False
    if len({item.get("timestamp") for item in run}) != len(run):
        return False
    if len({item.get("evidence") for item in run}) != len(run):
        return False
    if any(item.get("variant") != _WRAPPED_HINT_KIND for item in run):
        return False
    cards = [item.get("card") for item in run]
    receipts = [item.get("receipt") for item in run]
    if any(not isinstance(card, Mapping) for card in cards):
        return False
    if any(not isinstance(receipt, Mapping) for receipt in receipts):
        return False
    if len({card["text"] for card in cards}) != 1:
        return False
    if len({receipt["amount"] for receipt in receipts}) != 1:
        return False
    if any(
        not _wrapped_card_match(item)
        for item in run
    ):
        return False
    for previous, current in zip(run, run[1:]):
        if (
            current["timestamp"] <= previous["timestamp"]
            or current["timestamp"] - previous["timestamp"] > _MAX_ROW_GAP_MS
            or not _wrapped_contexts_compatible(
                previous["context"], current["context"]
            )
            or not _geometry_compatible(
                previous["card"]["box"], current["card"]["box"]
            )
            or not _wrapped_line_geometry_compatible(
                previous["receipt"]["box"], current["receipt"]["box"]
            )
        ):
            return False
    return True


def _wrapped_amount_crop_box(
    receipt_box: Sequence[float],
) -> tuple[float, float, float, float] | None:
    """Return the fixed source-space amount-digit box for a wrapped line."""
    box = _box(receipt_box)
    if box is None:
        return None
    # The amount is the stable first field after the 148px pane offset.  The
    # box is derived solely from the observed receipt geometry, never from an
    # expected name or estimated text width.
    return _box((box[0] + 79.0, box[1] - 2.0, box[0] + 99.0, box[3] + 2.0))


def _wrapped_amount_delimiter_crop_box(
    receipt_box: Sequence[float],
) -> tuple[float, float, float, float] | None:
    """Return the source box immediately after the amount field.

    The wrapped receipt has a fixed ``hint`` delimiter after its amount.  A
    separate crop of that delimiter provides a right-boundary witness: an
    amount crop that read only the first digit of ``20`` or ``12`` must not be
    promoted when the next source pixels still contain the missing digit.
    """
    box = _box(receipt_box)
    if box is None:
        return None
    return _box((box[0] + 94.0, box[1] - 2.0, box[0] + 124.0, box[3] + 2.0))


def _wrapped_identity_fragment_crop_box(
    receipt_box: Sequence[float],
    overlay_box: Sequence[float],
) -> tuple[float, float, float, float] | None:
    """Return a source crop after the obstruction and before the line edge."""
    receipt = _box(receipt_box)
    overlay = _box(overlay_box)
    if receipt is None or overlay is None:
        return None
    if not (
        overlay[0] < receipt[2]
        and overlay[2] > receipt[0]
        and overlay[1] <= _center_y(receipt) <= overlay[3]
    ):
        return None
    # Start outside the padded cursor box.  The gap is fixed source geometry,
    # not an estimate based on the expected name's width.
    return _box(
        (
            overlay[2] + _WRAPPED_IDENTITY_FRAGMENT_GAP,
            receipt[1] - 2.0,
            receipt[2],
            receipt[3] + 2.0,
        )
    )


def _wrapped_identity_fragment_from_tail(
    recognized_text: str,
    card_text: str,
) -> str | None:
    """Match an OCR tail to an exact prefix of the standalone card name.

    The crop begins immediately after the obstruction, so OCR may include the
    end of the fixed ``for`` boilerplate.  We may discard that leading tail
    only when the remaining literal text is an exact card prefix.  This keeps
    punctuation and token boundaries visible; no catalog or edit distance is
    involved.
    """
    if not isinstance(recognized_text, str) or not isinstance(card_text, str):
        return None
    recognized_text = recognized_text.strip()
    card_text = card_text.strip()
    if not recognized_text or not card_text:
        return None
    card_spans = list(_WORD_TOKEN_RE.finditer(card_text))
    tail_spans = list(_WORD_TOKEN_RE.finditer(recognized_text))
    if len(card_spans) < 2 or not tail_spans or card_spans[0].start() != 0:
        return None
    card_words = [match.group(0).casefold() for match in card_spans]
    tail_words = [match.group(0).casefold() for match in tail_spans]
    for tail_start in range(len(tail_spans)):
        if tail_start > 0 and not recognized_text[tail_spans[tail_start].start() - 1].isspace():
            continue
        # The crop can retain the tail of the fixed ``for`` boilerplate, but
        # it must not be allowed to discard arbitrary words before a matching
        # card prefix.  Otherwise ``Other Wrapped`` could certify ``Wrapped
        # Skill`` merely because the second word happens to match.
        leading_text = recognized_text[: tail_spans[tail_start].start()].strip().casefold()
        if leading_text not in ("", "for", "or", "r"):
            continue
        for length in range(1, min(len(card_words), len(tail_words) - tail_start) + 1):
            if tail_words[tail_start : tail_start + length] != card_words[:length]:
                continue
            tail_fragment = recognized_text[tail_spans[tail_start].start() :]
            card_fragment = card_text[card_spans[0].start() : card_spans[length - 1].end()]
            if (
                _literal_fragment(tail_fragment) == _literal_fragment(card_fragment)
                or _literal_fragment(_without_one_terminal_sentence_mark(tail_fragment))
                == _literal_fragment(card_fragment)
            ):
                return card_fragment
    return None


def _wrapped_identity_fragment_ocr(
    reader: Any,
    image: Any,
    item: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Read a name fragment from source pixels beyond a cursor obstruction."""
    receipt = item.get("receipt")
    card = item.get("card")
    overlay = item.get("overlay")
    if not isinstance(receipt, Mapping) or not isinstance(card, Mapping):
        return None
    crop_box = _wrapped_identity_fragment_crop_box(receipt.get("box"), overlay)
    if crop_box is None:
        return None
    try:
        import numpy as np

        left, top, right, bottom = (int(round(value)) for value in crop_box)
        crop = image.crop((left - 148, top, right - 148, bottom))
        if crop.width < 32 or crop.height < 8:
            return None
        result = reader.engine.text_rec(
            reader.TextRecInput(img=[np.asarray(crop)[:, :, ::-1]])
        )
        texts = getattr(result, "txts", ()) or ()
        scores = getattr(result, "scores", ()) or ()
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    if len(texts) != 1 or len(scores) != 1:
        return None
    recognized_text, score = texts[0], scores[0]
    if not isinstance(recognized_text, str) or not _finite_number(score):
        return None
    confidence = float(score) * 100.0
    if not 90.0 <= confidence <= 100.0:
        return None
    fragment = _wrapped_identity_fragment_from_tail(
        recognized_text,
        card.get("text"),
    )
    if fragment is None:
        return None
    try:
        pixel_sha256 = hashlib.sha256(crop.convert("RGB").tobytes()).hexdigest()
    except (AttributeError, TypeError, ValueError):
        return None
    return {
        "basis": _WRAPPED_IDENTITY_FRAGMENT_BASIS,
        "recognized_text": recognized_text.strip(),
        "fragment": fragment,
        "confidence": round(confidence, 4),
        "crop_box": _box_list(crop_box),
        "overlay_box": _box_list(overlay),
        "pixel_rgb_sha256": pixel_sha256,
        "model_fingerprint": getattr(reader, "fingerprint", None),
    }


def _wrapped_identity_fragment_proof_matches(
    proof: Mapping[str, Any] | None,
    item: Mapping[str, Any],
    image: Any | None = None,
) -> bool:
    """Validate a persisted source-bound identity-fragment proof."""
    if not isinstance(proof, Mapping):
        return False
    receipt = item.get("receipt")
    card = item.get("card")
    overlay = item.get("overlay")
    if not isinstance(receipt, Mapping) or not isinstance(card, Mapping):
        return False
    recognized_text = proof.get("recognized_text")
    fragment = proof.get("fragment")
    confidence = proof.get("confidence")
    model_fingerprint = proof.get("model_fingerprint")
    pixel_sha256 = proof.get("pixel_rgb_sha256")
    expected_crop = _wrapped_identity_fragment_crop_box(receipt.get("box"), overlay)
    saved_crop = _box(proof.get("crop_box"))
    saved_overlay = _box(proof.get("overlay_box"))
    expected_overlay = _box(overlay)
    prefix = receipt.get("prefix")
    continuation = receipt.get("continuation")
    if not isinstance(prefix, Mapping) or not isinstance(continuation, Mapping):
        # Prepared event observations keep the raw receipt parts beside the
        # compact receipt object.  Accept that representation only as an
        # equivalent view; the parts still have to pass the exact literal
        # partition below.
        parts = item.get("receipt_parts")
        if isinstance(parts, Mapping):
            prefix = parts.get("prefix")
            continuation = parts.get("continuation")
    if (
        proof.get("basis") != _WRAPPED_IDENTITY_FRAGMENT_BASIS
        or not isinstance(recognized_text, str)
        or not recognized_text.strip()
        or not isinstance(fragment, str)
        or not fragment.strip()
        or not _finite_number(confidence)
        or not 90.0 <= float(confidence) <= 100.0
        or not isinstance(model_fingerprint, str)
        or _SOURCE_SHA_RE.fullmatch(model_fingerprint) is None
        or not isinstance(pixel_sha256, str)
        or _SOURCE_SHA_RE.fullmatch(pixel_sha256) is None
        or expected_crop is None
        or saved_crop != expected_crop
        or expected_overlay is None
        or saved_overlay != expected_overlay
        or _wrapped_identity_fragment_from_tail(recognized_text, card.get("text")) != fragment
        # The independently read tail must still belong to a complete,
        # literal partition of the source receipt.  This rejects cards whose
        # standalone punctuation/symbols disappeared between the wrapped
        # prefix and continuation, even when the tail happens to match a
        # token prefix of the card name.
        or not isinstance(prefix, Mapping)
        or not isinstance(continuation, Mapping)
        or _wrapped_name_fragment(
            prefix.get("text"), continuation.get("text"), card.get("text")
        )
        != card.get("text", "").strip()
    ):
        return False
    if image is not None:
        try:
            left, top, right, bottom = (int(round(value)) for value in saved_crop)
            crop = image.crop((left - 148, top, right - 148, bottom))
            actual_sha256 = hashlib.sha256(crop.convert("RGB").tobytes()).hexdigest()
        except (AttributeError, OSError, TypeError, ValueError):
            return False
        if actual_sha256 != pixel_sha256:
            return False
    return True


def _wrapped_amount_ocr(
    reader: Any,
    image: Any,
    receipt: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Read only the amount digit from a wrapped receipt's source pixels."""
    amount = receipt.get("amount")
    crop_box = _wrapped_amount_crop_box(receipt.get("box"))
    if type(amount) is not int or not 0 < amount <= 99 or crop_box is None:
        return None
    try:
        import numpy as np

        left, top, right, bottom = (
            int(round(value)) for value in crop_box
        )
        crop = image.crop((left - 148, top, right - 148, bottom))
        if crop.width < 8 or crop.height < 8:
            return None
        result = reader.engine.text_rec(
            reader.TextRecInput(img=[np.asarray(crop)[:, :, ::-1]])
        )
        texts = getattr(result, "txts", ()) or ()
        scores = getattr(result, "scores", ()) or ()
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    readings: list[tuple[int, str, float]] = []
    for text, score in zip(texts, scores):
        if not isinstance(text, str) or not _finite_number(score):
            continue
        raw_text = text.strip()
        if not re.fullmatch(r"\d{1,2}", raw_text):
            continue
        confidence = float(score) * 100.0
        if not _finite_number(confidence) or not 90.0 <= confidence <= 100.0:
            continue
        value = int(raw_text)
        readings.append((value, raw_text, confidence))
    if not readings:
        return None
    if len({value for value, _, _ in readings}) != 1:
        return None
    value, raw_text, confidence = max(
        readings,
        key=lambda item: (item[2], len(item[1]), item[1]),
    )
    if value != amount:
        return None
    try:
        pixel_sha256 = hashlib.sha256(crop.convert("RGB").tobytes()).hexdigest()
    except (AttributeError, TypeError, ValueError):
        return None
    delimiter_box = _wrapped_amount_delimiter_crop_box(receipt.get("box"))
    if delimiter_box is None:
        return None
    try:
        delimiter_left, delimiter_top, delimiter_right, delimiter_bottom = (
            int(round(value)) for value in delimiter_box
        )
        delimiter_crop = image.crop(
            (delimiter_left - 148, delimiter_top, delimiter_right - 148, delimiter_bottom)
        )
        delimiter_result = reader.engine.text_rec(
            reader.TextRecInput(img=[np.asarray(delimiter_crop)[:, :, ::-1]])
        )
        delimiter_texts = getattr(delimiter_result, "txts", ()) or ()
        delimiter_scores = getattr(delimiter_result, "scores", ()) or ()
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    delimiter_readings = []
    for text, score in zip(delimiter_texts, delimiter_scores):
        if not isinstance(text, str) or not _finite_number(score):
            continue
        raw_delimiter = text.strip()
        confidence = float(score) * 100.0
        if (
            re.fullmatch(r"h\s*in(?:t)?", raw_delimiter, re.IGNORECASE)
            and _finite_number(confidence)
            and 90.0 <= confidence <= 100.0
        ):
            delimiter_readings.append((raw_delimiter, confidence))
    # Any digit or competing OCR result in this fixed boundary crop makes the
    # amount field incomplete or ambiguous.  Do not select the best-looking
    # delimiter from correlated alternatives.
    if len(delimiter_readings) != 1 or len(delimiter_texts) != 1:
        return None
    delimiter_text, delimiter_confidence = delimiter_readings[0]
    try:
        delimiter_sha256 = hashlib.sha256(
            delimiter_crop.convert("RGB").tobytes()
        ).hexdigest()
    except (AttributeError, TypeError, ValueError):
        return None
    model_fingerprint = getattr(reader, "fingerprint", None)
    return {
        "amount": value,
        "recognized_text": raw_text,
        "confidence": round(confidence, 4),
        "crop_box": _box_list(crop_box),
        "pixel_rgb_sha256": pixel_sha256,
        "basis": _WRAPPED_AMOUNT_BASIS,
        "model_fingerprint": model_fingerprint,
        "delimiter_proof": {
            "recognized_text": delimiter_text,
            "confidence": round(delimiter_confidence, 4),
            "crop_box": _box_list(delimiter_box),
            "pixel_rgb_sha256": delimiter_sha256,
            "basis": _WRAPPED_AMOUNT_DELIMITER_BASIS,
            "model_fingerprint": model_fingerprint,
        },
    }


def _raw_supports_wrapped_item(
    item: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> bool:
    """Bind both wrapped receipt lines and the card to raw neural OCR."""
    card = item.get("card")
    receipt = item.get("receipt")
    header = card.get("header") if isinstance(card, Mapping) else None
    if (
        not isinstance(card, Mapping)
        or not isinstance(receipt, Mapping)
        or not isinstance(header, Mapping)
        or not _raw_has_line(raw, card)
        or not _raw_has_line(raw, header)
    ):
        return False
    prefix = receipt.get("prefix")
    continuation = receipt.get("continuation")
    return (
        isinstance(prefix, Mapping)
        and isinstance(continuation, Mapping)
        and _raw_has_line(raw, prefix)
        and _raw_has_line(raw, continuation)
    )


def _contiguous_runs(
    rows: Sequence[dict[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Build runs only across rows that all passed the source-frame gates.

    Looking only at accepted rows would let a missing card, screen change, or
    ambiguous OCR row disappear from the timeline.  Every input row between
    two accepted observations must itself be accepted and context-compatible.
    """
    accepted = {
        (item["timestamp"], item["evidence"]): item
        for item in rows
    }
    ordered = sorted(
        source_rows,
        key=lambda row: (_timestamp(row), _evidence(row)),
    )
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in ordered:
        key = (_timestamp(row), _evidence(row))
        item = accepted.get(key)
        if item is None:
            if current:
                runs.append(current)
                current = []
            continue
        if current:
            previous = current[-1]
            if not (
                item["context"] == previous["context"]
                and item["timestamp"] > previous["timestamp"]
                and item["timestamp"] - previous["timestamp"] <= _MAX_ROW_GAP_MS
            ):
                runs.append(current)
                current = []
        if current:
            current.append(item)
        else:
            current = [item]
    if current:
        runs.append(current)
    return runs


def _run_is_consistent(run: Sequence[dict[str, Any]]) -> bool:
    if len(run) < 2:
        return False
    if len({item["timestamp"] for item in run}) != len(run):
        return False
    if len({item["evidence"] for item in run}) != len(run):
        return False
    cards = [item["card"] for item in run]
    receipts = [item["receipt"] for item in run]
    if len({card["text"] for card in cards}) != 1:
        return False
    if len({receipt["amount"] for receipt in receipts}) != 1:
        return False
    if any(not _geometry_compatible(cards[0]["box"], card["box"]) for card in cards[1:]):
        return False
    if any(not _geometry_compatible(receipts[0]["box"], receipt["box"]) for receipt in receipts[1:]):
        return False
    if any(
        not _geometry_compatible(
            cards[0]["header"]["box"],
            card["header"]["box"],
            height_tolerance=12.0,
        )
        for card in cards[1:]
    ):
        return False
    return True


def _candidate_from_run(
    run: Sequence[dict[str, Any]],
    *,
    evidence_root: Path,
    source_sha256: str,
    image_cache: dict[str, dict[str, Any]],
    source_manifest: Mapping[str, Mapping[str, Any]],
    capture_manifest_sha256: str,
    reader: Any,
) -> dict[str, Any] | None:
    if not _run_is_consistent(run):
        return None
    try:
        from .inventory_suffix import detect as detect_suffix
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None

    observations: list[dict[str, Any]] = []
    suffixes: list[str] = []
    prefix_cache: dict[tuple[str, tuple[float, ...]], dict[str, Any] | None] = {}
    for item in run:
        loaded = _load_image(
            evidence_root,
            item["row"],
            image_cache,
            source_sha256=source_sha256,
            source_manifest=source_manifest,
        )
        if loaded is None:
            return None
        if not _raw_supports_item(item, loaded["binding"]["raw"]):
            return None
        if not _source_overlay_matches(loaded["image"], item["overlay"]):
            return None
        image = loaded["image"]
        try:
            suffix = detect_suffix(image, item["card"]["box"])
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            return None
        if suffix is not None:
            suffixes.append(suffix)
        crop_box = (
            item["receipt"]["box"][0],
            item["receipt"]["box"][1],
            item["overlay"][0],
            item["receipt"]["box"][3],
        )
        crop_key = (item["evidence"], tuple(crop_box))
        if crop_key not in prefix_cache:
            prefix_cache[crop_key] = _prefix_amount_ocr(reader, image, crop_box)
        prefix = prefix_cache[crop_key]
        if prefix is not None and prefix["amount"] != item["receipt"]["amount"]:
            return None
        observations.append(
            {
                "timestamp_ms": item["timestamp"],
                "evidence": item["evidence"],
                    "evidence_sha256": loaded["evidence_sha256"],
                    "raw_sha256": loaded["binding"]["raw_sha256"],
                    "gameplay_sha256": loaded["binding"]["gameplay_sha256"],
                    "source_frame_sha256": loaded["binding"]["source_frame_sha256"],
                    "source_frame_path": loaded["binding"]["source_frame_path"],
                    "capture_frame_id": loaded["binding"]["capture_frame_id"],
                "card": {
                    "text": item["card"]["text"],
                    "confidence": item["card"]["confidence"],
                    "box": _box_list(item["card"]["box"]),
                    "header": {
                        "text": item["card"]["header"]["text"],
                        "confidence": item["card"]["header"]["confidence"],
                        "box": _box_list(item["card"]["header"]["box"]),
                    },
                    "suffix": suffix,
                },
                "receipt": {
                    "text": item["receipt"]["text"],
                    "raw_name": item["receipt"]["name"],
                    "amount": item["receipt"]["amount"],
                    "confidence": item["receipt"]["confidence"],
                    "box": _box_list(item["receipt"]["box"]),
                    "source": item["receipt"]["source"],
                },
                "overlay_box": _box_list(item["overlay"]),
                "prefix_amount_proof": prefix,
            }
        )

    # At least two source timestamps must have both a pixel suffix and an
    # independent amount crop.  The same PNG, a second crop, or a second OCR
    # view cannot satisfy this requirement.
    suffix_timestamps = {
        item["timestamp_ms"] for item in observations if item["card"]["suffix"] is not None
    }
    evidence_hashes = [item["evidence_sha256"] for item in observations]
    if len(set(evidence_hashes)) != len(evidence_hashes):
        return None
    prefix_observations = [item for item in observations if item["prefix_amount_proof"] is not None]
    prefix_timestamps = {item["timestamp_ms"] for item in prefix_observations}
    if len(suffix_timestamps) < 2 or len(prefix_timestamps) < 2:
        return None
    if len({item["card"]["suffix"] for item in observations if item["card"]["suffix"] is not None}) != 1:
        return None
    if any(item["prefix_amount_proof"]["amount"] != observations[0]["receipt"]["amount"] for item in prefix_observations):
        return None
    names = sorted({item["receipt"]["raw_name"] for item in observations})
    card_name = observations[0]["card"]["text"]
    amount = observations[0]["receipt"]["amount"]
    return {
        "kind": "skill_hint_change",
        "name": card_name,
        "amount": amount,
        "source_sha256": source_sha256,
        "source_timestamps_ms": [item["timestamp_ms"] for item in observations],
        "raw_receipt_name_candidates": names,
        "identity_proof": {
            "basis": "standalone_hint_card_with_pixel_suffix_and_independent_prefix_amount_ocr",
            "card_text": card_name,
            "suffix": next(item["card"]["suffix"] for item in observations if item["card"]["suffix"] is not None),
            "distinct_timestamp_count": len({item["timestamp_ms"] for item in observations}),
            "same_context": run[0]["context"],
            "geometry_stable": True,
        },
        "provenance": {
            "source_evidence_type": "decoded_gameplay_png",
            "capture_manifest_sha256": capture_manifest_sha256,
            "source_frame_chain": "capture.json -> source frame -> neural cache -> gameplay PNG",
            "independent_observations": False,
            "multi_crop_not_counted_as_timestamp": True,
            "prefix_ocr_model_fingerprint": getattr(reader, "fingerprint", None),
            "prefix_crop_count": len(prefix_observations),
        },
        "observations": observations,
    }


def _wrapped_amount_proof_matches(
    proof: Mapping[str, Any] | None,
    item: Mapping[str, Any],
    image: Any,
) -> bool:
    """Validate an amount proof and bind its crop hash to source pixels."""
    if not isinstance(proof, Mapping):
        return False
    receipt = item.get("receipt")
    if not isinstance(receipt, Mapping):
        return False
    amount = receipt.get("amount")
    recognized = proof.get("recognized_text")
    confidence = proof.get("confidence")
    crop_box = proof.get("crop_box")
    pixel_sha256 = proof.get("pixel_rgb_sha256")
    model_fingerprint = proof.get("model_fingerprint")
    if (
        type(amount) is not int
        or proof.get("amount") != amount
        or not isinstance(recognized, str)
        or not re.fullmatch(r"\d{1,2}", recognized.strip())
        or int(recognized.strip()) != amount
        or not _finite_number(confidence)
        or not 90.0 <= float(confidence) <= 100.0
        or proof.get("basis") != _WRAPPED_AMOUNT_BASIS
        or not isinstance(pixel_sha256, str)
        or _SOURCE_SHA_RE.fullmatch(pixel_sha256) is None
        or not isinstance(model_fingerprint, str)
        or _SOURCE_SHA_RE.fullmatch(model_fingerprint) is None
    ):
        return False
    expected_crop = _wrapped_amount_crop_box(receipt.get("box"))
    saved_crop = _box(crop_box)
    if expected_crop is None or saved_crop is None or saved_crop != expected_crop:
        return False
    try:
        left, top, right, bottom = (
            int(round(value)) for value in saved_crop
        )
        crop = image.crop((left - 148, top, right - 148, bottom))
        actual_sha256 = hashlib.sha256(crop.convert("RGB").tobytes()).hexdigest()
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    if actual_sha256 != pixel_sha256:
        return False
    delimiter = proof.get("delimiter_proof")
    if not isinstance(delimiter, Mapping):
        return False
    delimiter_text = delimiter.get("recognized_text")
    delimiter_confidence = delimiter.get("confidence")
    delimiter_sha256 = delimiter.get("pixel_rgb_sha256")
    delimiter_model = delimiter.get("model_fingerprint")
    if (
        not isinstance(delimiter_text, str)
        or re.fullmatch(r"h\s*in(?:t)?", delimiter_text.strip(), re.IGNORECASE)
        is None
        or not _finite_number(delimiter_confidence)
        or not 90.0 <= float(delimiter_confidence) <= 100.0
        or delimiter.get("basis") != _WRAPPED_AMOUNT_DELIMITER_BASIS
        or not isinstance(delimiter_model, str)
        or _SOURCE_SHA_RE.fullmatch(delimiter_model) is None
        or delimiter_model != model_fingerprint
        or not isinstance(delimiter_sha256, str)
        or _SOURCE_SHA_RE.fullmatch(delimiter_sha256) is None
    ):
        return False
    expected_delimiter = _wrapped_amount_delimiter_crop_box(receipt.get("box"))
    saved_delimiter = _box(delimiter.get("crop_box"))
    if expected_delimiter is None or saved_delimiter is None or saved_delimiter != expected_delimiter:
        return False
    try:
        left, top, right, bottom = (
            int(round(value)) for value in saved_delimiter
        )
        delimiter_crop = image.crop((left - 148, top, right - 148, bottom))
        actual_delimiter_sha256 = hashlib.sha256(
            delimiter_crop.convert("RGB").tobytes()
        ).hexdigest()
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return actual_delimiter_sha256 == delimiter_sha256


def _candidate_from_wrapped_run(
    run: Sequence[dict[str, Any]],
    *,
    evidence_root: Path,
    source_sha256: str,
    image_cache: dict[str, dict[str, Any]],
    source_manifest: Mapping[str, Mapping[str, Any]],
    capture_manifest_sha256: str,
    reader: Any,
) -> dict[str, Any] | None:
    """Build a wrapped receipt candidate from independently proven rows."""
    if not _wrapped_run_is_consistent(run):
        return None
    try:
        from .inventory_suffix import detect as detect_suffix
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    reader_fingerprint = getattr(reader, "fingerprint", None)
    if not isinstance(reader_fingerprint, str) or _SOURCE_SHA_RE.fullmatch(reader_fingerprint) is None:
        return None

    observations: list[dict[str, Any]] = []
    suffix_values: list[str] = []
    for item in run:
        loaded = _load_image(
            evidence_root,
            item["row"],
            image_cache,
            source_sha256=source_sha256,
            source_manifest=source_manifest,
        )
        if loaded is None:
            return None
        raw = loaded["binding"]["raw"]
        if not _raw_supports_wrapped_item(item, raw):
            return None
        if item["overlay"] is not None and not _source_overlay_matches(
            loaded["image"], item["overlay"]
        ):
            return None
        try:
            suffix = detect_suffix(loaded["image"], item["card"]["box"])
        except (AttributeError, ImportError, IndexError, RuntimeError, TypeError, ValueError):
            return None
        if suffix not in (None, "single_circle", "double_circle"):
            return None
        if suffix is not None:
            suffix_values.append(suffix)
        prefix_line = item["receipt"]["prefix"]
        identity_fragment_proof = None
        prefix_occluded = (
            item["overlay"] is not None
            or prefix_line.get("overlay_occluded") is True
            or bool(prefix_line.get("overlay_boxes"))
            or prefix_line.get("recipient_name_occluded") is True
        )
        if prefix_occluded:
            # A cursor-covered prefix needs an independently source-bound
            # tail fragment.  The standalone card and a hidden OCR line do
            # not, by themselves, prove the receipt identity.
            if item["overlay"] is None:
                return None
            identity_fragment_proof = _wrapped_identity_fragment_ocr(
                reader, loaded["image"], item
            )
            if not _wrapped_identity_fragment_proof_matches(
                identity_fragment_proof, item, loaded["image"]
            ):
                return None
        proof = _wrapped_amount_ocr(reader, loaded["image"], item["receipt"])
        if (
            not _wrapped_amount_proof_matches(proof, item, loaded["image"])
            or proof.get("model_fingerprint") != reader_fingerprint
        ):
            return None
        suffix_state = "present" if suffix is not None else "undetermined"
        observations.append(
            {
                "timestamp_ms": item["timestamp"],
                "evidence": item["evidence"],
                "evidence_sha256": loaded["evidence_sha256"],
                "raw_sha256": loaded["binding"]["raw_sha256"],
                "gameplay_sha256": loaded["binding"]["gameplay_sha256"],
                "source_frame_sha256": loaded["binding"]["source_frame_sha256"],
                "source_frame_path": loaded["binding"]["source_frame_path"],
                "capture_frame_id": loaded["binding"]["capture_frame_id"],
                "card": {
                    "text": item["card"]["text"],
                    "confidence": item["card"]["confidence"],
                    "box": _box_list(item["card"]["box"]),
                    "header": {
                        "text": item["card"]["header"]["text"],
                        "confidence": item["card"]["header"]["confidence"],
                        "box": _box_list(item["card"]["header"]["box"]),
                    },
                    "suffix": suffix,
                    "suffix_state": suffix_state,
                },
                "receipt": {
                    "text": item["receipt"]["text"],
                    "raw_name": item["receipt"]["name"],
                    "amount": item["receipt"]["amount"],
                    "confidence": item["receipt"]["confidence"],
                    "box": _box_list(item["receipt"]["box"]),
                    "source": item["receipt"]["source"],
                },
                "receipt_parts": {
                    "prefix": deepcopy(item["receipt"]["prefix"]),
                    "continuation": deepcopy(item["receipt"]["continuation"]),
                },
                "overlay_box": (
                    _box_list(item["overlay"]) if item["overlay"] is not None else None
                ),
                "identity_fragment_proof": identity_fragment_proof,
                "prefix_amount_proof": proof,
            }
        )

    if len(observations) < 2:
        return None
    evidence_hashes = [observation["evidence_sha256"] for observation in observations]
    gameplay_hashes = [observation["gameplay_sha256"] for observation in observations]
    source_frame_hashes = [
        observation["source_frame_sha256"] for observation in observations
    ]
    if (
        len(set(evidence_hashes)) != len(evidence_hashes)
        or len(set(gameplay_hashes)) != len(gameplay_hashes)
        or len(set(source_frame_hashes)) != len(source_frame_hashes)
    ):
        return None
    if len(suffix_values) > 1 and len(set(suffix_values)) != 1:
        return None
    rank_state = (
        "present"
        if len(suffix_values) == len(observations) and suffix_values
        else "undetermined"
    )
    suffix = suffix_values[0] if rank_state == "present" else None
    card_name = observations[0]["card"]["text"]
    amount = observations[0]["receipt"]["amount"]
    raw_names = sorted({observation["receipt"]["raw_name"] for observation in observations})
    return {
        "observation_kind": _WRAPPED_HINT_KIND,
        "kind": "skill_hint_change",
        "name": card_name,
        "amount": amount,
        "source_sha256": source_sha256,
        "source_timestamps_ms": [observation["timestamp_ms"] for observation in observations],
        "raw_receipt_name_candidates": raw_names,
        "identity_proof": {
            "basis": "standalone_hint_card_with_wrapped_receipt_and_per_row_amount_ocr",
            "card_text": card_name,
            "suffix": suffix,
            "rank_state": rank_state,
            "distinct_timestamp_count": len(observations),
            "same_context": run[0]["context"],
            "context_observed": run[0]["context"] is not None,
            "geometry_stable": True,
        },
        "provenance": {
            "source_evidence_type": "decoded_gameplay_png",
            "capture_manifest_sha256": capture_manifest_sha256,
            "source_frame_chain": "capture.json -> source frame -> neural cache -> gameplay PNG",
            "independent_observations": False,
            "multi_crop_not_counted_as_timestamp": True,
            "prefix_ocr_model_fingerprint": reader_fingerprint,
            "prefix_crop_count": len(observations),
        },
        "observations": observations,
    }


def recover(
    rows: Sequence[Mapping[str, Any]],
    evidence_root: str | Path,
    *,
    source_sha256: str,
) -> list[dict[str, Any]]:
    """Return source-backed card identity candidates without mutating rows.

    ``source_sha256`` is required to keep a candidate attached to one
    recording.  Malformed identities, duplicate timestamps/evidence, context
    boundaries, stale cards, conflicting amounts, and any missing pixel/OCR
    proof return no candidate.  Raw receipt names remain in each candidate's
    ``raw_receipt_name_candidates`` and in its observations.
    """
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    if not isinstance(source_sha256, str) or not _SOURCE_SHA_RE.fullmatch(source_sha256):
        return []
    root = Path(evidence_root)
    if not root.is_dir():
        return []
    source_manifest = _load_source_manifest(root, source_sha256=source_sha256)
    if source_manifest is None:
        return []
    try:
        capture_manifest_sha256 = _digest(root / "capture.json")
    except OSError:
        return []

    source_rows: list[Mapping[str, Any]] = []
    static = []
    wrapped_static = []
    all_timestamp_evidence: dict[int, str] = {}
    all_evidence_timestamp: dict[str, int] = {}
    timestamp_evidence: dict[int, str] = {}
    evidence_timestamp: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            return []
        # A malformed row cannot be safely ordered relative to a candidate;
        # fail closed instead of silently removing a possible episode
        # boundary.
        if _timestamp(row) is None or _evidence(row) is None:
            return []
        source_rows.append(row)
        source_timestamp = _timestamp(row)
        source_evidence = _evidence(row)
        prior_evidence = all_timestamp_evidence.get(source_timestamp)
        prior_timestamp = all_evidence_timestamp.get(source_evidence)
        if (
            prior_evidence is not None
            and prior_evidence != source_evidence
        ) or (
            prior_timestamp is not None
            and prior_timestamp != source_timestamp
        ):
            return []
        all_timestamp_evidence[source_timestamp] = source_evidence
        all_evidence_timestamp[source_evidence] = source_timestamp
        item = _static_row(row)
        if item is not None:
            timestamp = item["timestamp"]
            evidence = item["evidence"]
            prior_evidence = timestamp_evidence.get(timestamp)
            prior_timestamp = evidence_timestamp.get(evidence)
            if prior_evidence is not None and prior_evidence != evidence:
                return []
            if prior_timestamp is not None and prior_timestamp != timestamp:
                return []
            timestamp_evidence[timestamp] = evidence
            evidence_timestamp[evidence] = timestamp
            static.append(item)
        wrapped_item = _wrapped_static_row(row)
        if wrapped_item is not None:
            wrapped_static.append(wrapped_item)

    image_cache: dict[str, dict[str, Any]] = {}
    reader: Any | None = None
    candidates = []

    def get_reader() -> Any | None:
        nonlocal reader
        if reader is not None:
            return reader
        try:
            from .vision import NeuralReader

            reader = NeuralReader()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return None
        return reader

    for run in _contiguous_runs(static, source_rows):
        if len(run) < 2:
            continue
        active_reader = get_reader()
        if active_reader is None:
            return []
        candidate = _candidate_from_run(
            run,
            evidence_root=root,
            source_sha256=source_sha256,
            image_cache=image_cache,
            source_manifest=source_manifest,
            capture_manifest_sha256=capture_manifest_sha256,
            reader=active_reader,
        )
        if candidate is not None:
            candidates.append(candidate)
    for run in _wrapped_contiguous_runs(wrapped_static, source_rows):
        if len(run) < 2:
            continue
        active_reader = get_reader()
        if active_reader is None:
            return []
        candidate = _candidate_from_wrapped_run(
            run,
            evidence_root=root,
            source_sha256=source_sha256,
            image_cache=image_cache,
            source_manifest=source_manifest,
            capture_manifest_sha256=capture_manifest_sha256,
            reader=active_reader,
        )
        if candidate is not None:
            candidates.append(candidate)

    # Two overlapping candidates from one episode mean the source does not
    # identify one card unambiguously.  Preserve both only as ambiguity by
    # returning no promotion candidate.
    if len(candidates) > 1:
        spans = [set(candidate["source_timestamps_ms"]) for candidate in candidates]
        if any(left & right for index, left in enumerate(spans) for right in spans[index + 1 :]):
            return []
    return candidates


def recover_hint_card_identities(
    rows: Sequence[Mapping[str, Any]],
    evidence_root: str | Path,
    *,
    source_sha256: str,
) -> list[dict[str, Any]]:
    """Descriptive alias for callers integrating the recovery stage."""
    return recover(rows, evidence_root, source_sha256=source_sha256)


__all__ = ["recover", "recover_hint_card_identities"]
