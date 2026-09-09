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
* ``inventory_suffix.detect`` to report the same visible suffix at two or
  more distinct timestamps; and
* an OCR pass over a prefix crop whose right edge is the detected cursor
  boundary to read the amount.  The crop is created from source geometry and
  does not receive the expected name or amount.

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
        if item is None:
            continue
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

    image_cache: dict[str, dict[str, Any]] = {}
    reader: Any | None = None
    candidates = []
    for run in _contiguous_runs(static, source_rows):
        if len(run) < 2:
            continue
        if reader is None:
            try:
                from .vision import NeuralReader

                reader = NeuralReader()
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                return []
        candidate = _candidate_from_run(
            run,
            evidence_root=root,
            source_sha256=source_sha256,
            image_cache=image_cache,
            source_manifest=source_manifest,
            capture_manifest_sha256=capture_manifest_sha256,
            reader=reader,
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
