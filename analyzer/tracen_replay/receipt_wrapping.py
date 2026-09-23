"""Source-bound recovery for a friendship receipt whose amount wraps.

The game can render a long friendship receipt on two baselines.  The first
baseline ends at ``went up by`` and the amount plus sentence terminator starts
at the receipt's left edge on the next baseline.  The ordinary detector often
keeps the first line and misses the small continuation, especially while the
green pointer is over that edge.

This module reads only a geometry-derived continuation crop.  It never sends
the recipient, an expected amount, or a neighboring receipt to OCR.  A proof
is accepted only when the crop returns one terminated numeric token and its
source pixels, model, frame, and gameplay pane are recorded.  The parser can
then consume the proof from a cached raw observation without rerunning OCR.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
from typing import Any, Mapping, Sequence

from .gameplay import receipt_rows
from .layout import ORIGIN_X, pane_box, pane_size
from .ocr_confidence import confidence_percent


VERSION = "friendship-wrapped-receipt-v1"
METHOD = "source_bound_wrapped_friendship_receipt"
_MIN_PREFIX_CONFIDENCE = 95.0
_MIN_AMOUNT_CONFIDENCE = 90.0
_MIN_COLOR_AMOUNT_CONFIDENCE = 90.0
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PREFIX_RE = re.compile(
    r"^(?P<keyword>[A-Za-z]+)\s+with\s+(?P<name>[^.!?\n]+?)\s+went\s+up\s+by$",
    re.IGNORECASE,
)
_STATUS_PREFIX_RE = re.compile(
    r"^(?P<keyword>[A-Za-z]+)\s+with\s+(?P<name>[^.!?\n]+?)\s+is\s+maxed$",
    re.IGNORECASE,
)
_STATUS_TAIL_RE = re.compile(r"^out[.!]$", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"^(?P<amount>[0-9]{1,3})(?P<stop>[.!])$")


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(_finite_number(item) for item in value):
        return None
    left, top, right, bottom = (float(item) for item in value)
    if not left < right or not top < bottom:
        return None
    pane = pane_box()
    if left < pane[0] or top < pane[1] or right > pane[2] or bottom > pane[3]:
        return None
    return left, top, right, bottom


def _in_receipt_box(top: float, bottom: float) -> bool:
    band_top, band_bottom = receipt_rows()
    return band_top <= top < bottom <= band_bottom


def _box_list(value: Sequence[float]) -> list[float | int]:
    result = []
    for item in value:
        numeric = float(item)
        result.append(int(numeric) if numeric.is_integer() else numeric)
    return result


def _fixed_keyword(value: str) -> bool:
    """Allow a bounded OCR error in the fixed ``Friendship`` word.

    The fixed UI keyword can be partially covered by the pointer.  A single
    dropped glyph was the original repair bound, but source OCR also produces
    ``Fendship`` when the pointer covers two adjacent glyphs.  Use a small
    edit-distance bound for the fixed keyword only; the recipient, suffix,
    geometry, confidence, and wrapped continuation are still validated by the
    surrounding receipt grammar.  This keeps the repair general without
    guessing a name or an effect value.
    """

    observed = value.strip().casefold()
    expected = "friendship"
    if not observed or abs(len(observed) - len(expected)) > 2:
        return False
    # Bounded Levenshtein distance.  The matrix is tiny (the expected token is
    # a fixed ten-character UI label), and an early row cutoff keeps malformed
    # detector output from becoming a broad fuzzy match.
    previous = list(range(len(expected) + 1))
    for row, character in enumerate(observed, 1):
        current = [row]
        for column, expected_character in enumerate(expected, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (character != expected_character),
            ))
        if min(current) > 2:
            return False
        previous = current
    return previous[-1] <= 2


def prefix(line: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a validated incomplete friendship prefix and its geometry."""

    if not isinstance(line, Mapping):
        return None
    confidence = confidence_percent(line.get("confidence"))
    box = _box(line.get("box"))
    text = line.get("text")
    if (confidence is None or confidence < _MIN_PREFIX_CONFIDENCE
            or box is None or not isinstance(text, str)):
        return None
    if not _in_receipt_box(box[1], box[3]):
        return None
    normalized = " ".join(text.split())
    match = _PREFIX_RE.fullmatch(normalized)
    if not match or not _fixed_keyword(match["keyword"]):
        return None
    name = match["name"].strip()
    # Keep the name literal.  This grammar shape rejects a second sentence or
    # an OCR line that merely contains the fixed suffix after unrelated text.
    if (not name or not re.fullmatch(
            r"[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*)*", name)):
        return None
    canonical = f"Friendship with {name} went up by"
    return dict(
        text=text,
        normalized_text=normalized,
        canonical_text=canonical,
        keyword=match["keyword"],
        name=name,
        confidence=confidence,
        box=_box_list(box),
    )


def status_prefix(line: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a validated wrapped friendship-status prefix."""

    if not isinstance(line, Mapping):
        return None
    confidence = confidence_percent(line.get("confidence"))
    box = _box(line.get("box"))
    text = line.get("text")
    if (confidence is None or confidence < _MIN_PREFIX_CONFIDENCE
            or box is None or not isinstance(text, str)):
        return None
    if not _in_receipt_box(box[1], box[3]):
        return None
    normalized = " ".join(text.split())
    match = _STATUS_PREFIX_RE.fullmatch(normalized)
    if not match or not _fixed_keyword(match["keyword"]):
        return None
    name = match["name"].strip()
    if (not name or not re.fullmatch(
            r"[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*)*", name)):
        return None
    return dict(
        text=text,
        normalized_text=normalized,
        canonical_text=f"Friendship with {name} is maxed",
        keyword=match["keyword"],
        name=name,
        confidence=confidence,
        box=_box_list(box),
    )


def _status_tail(line: Mapping[str, Any] | None,
                 prefix_info: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(line, Mapping):
        return None
    confidence = confidence_percent(line.get("confidence"))
    text = line.get("text")
    box = _box(line.get("box"))
    if (confidence is None or confidence < _MIN_AMOUNT_CONFIDENCE
            or box is None or not isinstance(text, str)):
        return None
    if not _STATUS_TAIL_RE.fullmatch("".join(text.split())):
        return None
    prefix_box = _box(prefix_info.get("box"))
    if prefix_box is None:
        return None
    # The tail starts on the next baseline at the same receipt left edge.
    if (not 0 < box[1] - prefix_box[1] <= 45
            or abs(box[0] - prefix_box[0]) > 15
            or box[1] < prefix_box[3] - 3):
        return None
    return dict(
        text=text,
        recognized_text="".join(text.split()),
        confidence=confidence,
        box=_box_list(box),
    )


def _status_tail_like(line: Mapping[str, Any] | None,
                      prefix_info: Mapping[str, Any]) -> bool:
    """Recognize a nearby tail-shaped line for ambiguity accounting.

    ``_status_tail`` intentionally uses a tight baseline distance.  A second
    animated duplicate can be a little farther down while still being close
    enough to make the first tail unsafe.  Count that shape before accepting
    a pair so a duplicate cannot be silently ignored by the tight validator.
    """

    if not isinstance(line, Mapping):
        return False
    confidence = confidence_percent(line.get("confidence"))
    text = line.get("text")
    box = _box(line.get("box"))
    prefix_box = _box(prefix_info.get("box"))
    if (confidence is None or confidence < _MIN_AMOUNT_CONFIDENCE
            or box is None or prefix_box is None
            or not isinstance(text, str)
            or not _STATUS_TAIL_RE.fullmatch("".join(text.split()))):
        return False
    return (0 < box[1] - prefix_box[1] <= 60
            and abs(box[0] - prefix_box[0]) <= 15
            and box[1] >= prefix_box[3] - 3)


def _merge_status_line(prefix_info: Mapping[str, Any],
                       tail_info: Mapping[str, Any]) -> dict[str, Any]:
    original = f"{prefix_info['text']} {tail_info['recognized_text']}"
    merged = dict(
        text=f"{prefix_info['canonical_text']} {tail_info['recognized_text']}",
        confidence=min(float(prefix_info["confidence"]), float(tail_info["confidence"])),
        box=list(prefix_info["box"]),
        original_text=original,
        text_normalization="source_wrapped_friendship_status",
        wrapped_receipt_kind="friendship_status",
        wrapped_receipt_parts=[
            dict(text=prefix_info["text"], confidence=prefix_info["confidence"],
                 box=list(prefix_info["box"])),
            dict(text=tail_info["recognized_text"], confidence=tail_info["confidence"],
                 box=list(tail_info["box"])),
        ],
    )
    return merged


def _join_status_lines(lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Join only one same-receipt ``is maxed`` + ``out.`` pair."""

    copied = [dict(line) for line in lines if isinstance(line, Mapping)]
    consumed: set[int] = set()
    result: list[dict[str, Any]] = []
    for index, line in enumerate(copied):
        if index in consumed:
            continue
        info = status_prefix(line)
        if info is None:
            result.append(line)
            continue
        candidates = []
        tail_like_count = 0
        for tail_index in range(index + 1, len(copied)):
            if tail_index in consumed:
                continue
            if _status_tail_like(copied[tail_index], info):
                tail_like_count += 1
            # The status continuation is rendered on the immediately next
            # baseline.  Do not bridge an intervening receipt or narrative
            # line merely because a later ``out.`` happens to share x/y.
            if tail_index != index + 1:
                continue
            tail = _status_tail(copied[tail_index], info)
            if tail is not None:
                candidates.append((tail_index, tail))
        # Multiple possible tails do not establish identity.  Leave both
        # source lines visible for the ordinary parser to abstain.
        if len(candidates) != 1 or tail_like_count != 1:
            result.append(line)
            continue
        tail_index, tail_info = candidates[0]
        consumed.add(tail_index)
        result.append(_merge_status_line(info, tail_info))
    return result


def continuation_crop_box(prefix_box: Sequence[float]) -> list[float | int] | None:
    """Return the fixed source-space crop for the wrapped amount.

    The continuation is rendered at the receipt's left edge, below the first
    baseline.  The offsets are UI geometry, derived from that observed line
    box; they do not depend on recipient length or a guessed amount.
    """

    box = _box(prefix_box)
    if box is None:
        return None
    left, _top, _right, bottom = box
    candidate = (left + 7.0, bottom - 8.0, left + 55.0, bottom + 18.0)
    if _box(candidate) is None or candidate[3] > receipt_rows()[1]:
        return None
    return _box_list(candidate)


def _fallback_crop_box(prefix_box: Sequence[float],
                        lines: Sequence[Mapping[str, Any]] | None = None
                        ) -> list[float | int] | None:
    """Return a lower-band crop that stops before a known following receipt.

    A pointer can cover the first pixels of the wrapped amount.  The amount
    is still visible in the lower part of that line, where color-separated
    source views are useful.  The lower edge is clipped at the first later
    line that overlaps this receipt band; this prevents the next friendship
    row from becoming part of the proof crop.
    """

    box = _box(prefix_box)
    if box is None:
        return None
    left, _top, _right, bottom = box
    x1, x2 = left - 3.0, left + 35.0
    y1 = bottom
    y2 = bottom + 18.0
    if isinstance(lines, Sequence):
        later_tops = []
        band_top, band_bottom = receipt_rows()
        for line in lines:
            if not isinstance(line, Mapping):
                continue
            other = _box(line.get("box"))
            if other is None or not band_top <= other[1] <= band_bottom:
                continue
            if other[1] < bottom + 10:
                continue
            if other[0] >= x2 + 8 or other[2] <= x1 - 8:
                continue
            later_tops.append(other[1])
        if later_tops:
            y2 = min(y2, min(later_tops))
    if y2 - y1 < 14.0:
        return None
    candidate = (x1, y1, x2, y2)
    return _box_list(candidate) if _box(candidate) is not None else None


def _same_continuation_box(line_box: Sequence[float], prefix_box: Sequence[float]) -> bool:
    observed = _box(line_box)
    expected = continuation_crop_box(prefix_box)
    if observed is None or expected is None:
        return False
    # A detector box can be narrower than the source crop, but its center must
    # remain in the same left-edge continuation band and before the next row.
    center_x = (observed[0] + observed[2]) / 2.0
    center_y = (observed[1] + observed[3]) / 2.0
    return (expected[0] - 4 <= center_x <= expected[2] + 4
            and expected[1] - 3 <= center_y <= expected[3] + 3)


def amount_line(line: Mapping[str, Any] | None,
                prefix_info: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read one complete numeric continuation already exposed by detection."""

    if not isinstance(line, Mapping):
        return None
    confidence = confidence_percent(line.get("confidence"))
    text = line.get("text")
    if (confidence is None or confidence < _MIN_AMOUNT_CONFIDENCE
            or not isinstance(text, str)):
        return None
    match = _AMOUNT_RE.fullmatch("".join(text.split()))
    if not match or not _same_continuation_box(line.get("box"), prefix_info["box"]):
        return None
    return dict(
        text=text,
        recognized_text="".join(text.split()),
        amount=int(match["amount"]),
        confidence=confidence,
        box=_box_list(_box(line["box"])),
    )


def _score_percent(score: Any) -> float | None:
    if not _finite_number(score):
        return None
    value = float(score)
    if 0 <= value <= 1:
        value *= 100.0
    return value if 0 <= value <= 100 else None


def _recognize_amount(reader: Any, pane: Any, crop_box: Sequence[float]) -> dict[str, Any] | None:
    """OCR the source crop and return one terminated numeric witness."""

    try:
        import numpy as np

        if getattr(pane, "size", None) != pane_size():
            return None
        left, top, right, bottom = (int(round(item)) for item in crop_box)
        crop = pane.crop((left - ORIGIN_X, top, right - ORIGIN_X, bottom)).convert("RGB")
        if crop.width < 32 or crop.height < 8:
            return None
        result = reader.engine.text_rec(
            reader.TextRecInput(img=[np.asarray(crop)[:, :, ::-1]])
        )
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    readings = []
    for text, score in zip(getattr(result, "txts", ()) or (),
                           getattr(result, "scores", ()) or ()):
        if not isinstance(text, str):
            continue
        confidence = _score_percent(score)
        match = _AMOUNT_RE.fullmatch("".join(text.split()))
        if match is None or confidence is None or confidence < _MIN_AMOUNT_CONFIDENCE:
            continue
        readings.append(dict(
            recognized_text="".join(text.split()),
            amount=int(match["amount"]),
            confidence=round(confidence, 4),
        ))
    if not readings:
        return None
    amounts = {item["amount"] for item in readings}
    if len(amounts) != 1:
        return None
    best = max(readings, key=lambda item: (item["confidence"], item["recognized_text"]))
    return dict(
        recognized_text=best["recognized_text"],
        amount=best["amount"],
        confidence=best["confidence"],
        crop_box=_box_list(crop_box),
        pixel_rgb_sha256=hashlib.sha256(crop.tobytes()).hexdigest(),
        model_fingerprint=getattr(reader, "fingerprint", None),
        basis="source_bound_wrapped_friendship_amount_crop_ocr",
    )


def _color_text_view(crop: Any, kind: str) -> Any:
    """Keep receipt-colored source pixels while whitening pointer/background.

    The friendship receipt uses warm brown/orange glyphs on a pale bubble.
    The pointer is green with a dark outline.  These predicates are only an
    image view of the supplied crop; they do not encode a recipient or an
    expected amount.
    """

    import numpy as np
    from PIL import Image

    array = np.asarray(crop.convert("RGB"))
    red, green, blue = (array[:, :, index].astype("int16") for index in range(3))
    if kind == "warm":
        keep = ((red >= green + 4) & (green >= blue - 2)
                & (red < 220) & (green < 200))
    elif kind == "brown":
        keep = ((red > green) & (green >= blue)
                & (red < 220) & (green < 190))
    else:
        raise ValueError(f"Unknown receipt color view: {kind}")
    return Image.fromarray(np.where(keep[:, :, None], array, 255).astype("uint8"))


def _recognize_crop_image(reader: Any, crop: Any) -> dict[str, Any] | None:
    """Recognize one transformed crop without changing its source geometry."""

    try:
        import numpy as np

        result = reader.engine.text_rec(
            reader.TextRecInput(img=[np.asarray(crop.convert("RGB"))[:, :, ::-1]])
        )
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    readings = []
    for text, score in zip(getattr(result, "txts", ()) or (),
                           getattr(result, "scores", ()) or ()):
        if not isinstance(text, str):
            continue
        confidence = _score_percent(score)
        match = _AMOUNT_RE.fullmatch("".join(text.split()))
        if match is None or confidence is None or confidence < _MIN_COLOR_AMOUNT_CONFIDENCE:
            continue
        readings.append(dict(
            recognized_text="".join(text.split()),
            amount=int(match["amount"]),
            confidence=round(confidence, 4),
        ))
    if not readings or len({item["amount"] for item in readings}) != 1:
        return None
    return max(readings, key=lambda item: (item["confidence"], item["recognized_text"]))


def _recognize_color_consensus(reader: Any, pane: Any,
                               crop_box: Sequence[float]) -> dict[str, Any] | None:
    """Require two independent color views to read one wrapped amount."""

    try:
        import hashlib

        import numpy as np

        if getattr(pane, "size", None) != pane_size():
            return None
        left, top, right, bottom = (int(round(item)) for item in crop_box)
        crop = pane.crop((left - ORIGIN_X, top, right - ORIGIN_X, bottom)).convert("RGB")
        if crop.width < 30 or crop.height < 14:
            return None
        source_pixels = hashlib.sha256(crop.tobytes()).hexdigest()
        views = []
        for kind in ("warm", "brown"):
            transformed = _color_text_view(crop, kind)
            reading = _recognize_crop_image(reader, transformed)
            if reading is None:
                return None
            views.append(dict(
                view=kind,
                recognized_text=reading["recognized_text"],
                amount=reading["amount"],
                confidence=reading["confidence"],
                crop_box=_box_list(crop_box),
                source_pixel_rgb_sha256=source_pixels,
                pixel_rgb_sha256=hashlib.sha256(transformed.tobytes()).hexdigest(),
                predicate="warm_receipt_glyph_pixels" if kind == "warm"
                else "brown_receipt_glyph_pixels",
            ))
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    if len({view["amount"] for view in views}) != 1:
        return None
    if len({view["recognized_text"] for view in views}) != 1:
        return None
    confidence = min(view["confidence"] for view in views)
    return dict(
        recognized_text=views[0]["recognized_text"],
        amount=views[0]["amount"],
        confidence=confidence,
        crop_box=_box_list(crop_box),
        source_pixel_rgb_sha256=source_pixels,
        pixel_rgb_sha256=views[0]["pixel_rgb_sha256"],
        views=views,
        model_fingerprint=getattr(reader, "fingerprint", None),
        basis="source_bound_wrapped_friendship_amount_color_consensus",
    )


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH_RE.fullmatch(value) is not None


def enrich(raw: Mapping[str, Any], pane: Any, reader: Any) -> dict[str, Any]:
    """Attach a source-pixel wrapped receipt observation when it is readable."""

    if not isinstance(raw, Mapping):
        return dict(raw)
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return dict(raw)
    prefixes = [item for item in (prefix(line) for line in lines) if item is not None]
    if len(prefixes) != 1:
        return dict(raw)
    prefix_info = prefixes[0]
    # A complete standalone continuation is still checked against the same
    # crop geometry.  Any conflicting complete continuation makes the frame
    # abstain rather than choosing a line by confidence or position.
    visible = [item for item in (amount_line(line, prefix_info) for line in lines)
               if item is not None]
    if len({item["amount"] for item in visible}) > 1:
        return dict(raw)
    crop_box = continuation_crop_box(prefix_info["box"])
    if crop_box is None:
        return dict(raw)
    proof = _recognize_amount(reader, pane, crop_box)
    # If the ordinary continuation crop is obscured by the green pointer,
    # reread only the lower source band before the next detected receipt row.
    # Two independently filtered views must agree on a terminated amount;
    # this keeps an isolated OCR guess from becoming a friendship effect.
    color_crop = _fallback_crop_box(prefix_info["box"], lines)
    color_proof = None
    if proof is None and color_crop is not None:
        color_proof = _recognize_color_consensus(reader, pane, color_crop)
    if proof is None and color_proof is not None:
        proof = color_proof
        crop_box = color_crop
    if proof is None:
        return dict(raw)
    if visible and any(item["amount"] != proof["amount"] for item in visible):
        return dict(raw)
    gameplay_sha256 = raw.get("gameplay_sha256")
    source_sha256 = raw.get("source_sha256")
    engine_fingerprint = raw.get("engine_fingerprint")
    model_sha256 = raw.get("model_sha256")
    reader_fingerprint = getattr(reader, "fingerprint", None)
    if (not _valid_hash(gameplay_sha256)
            or not _valid_hash(source_sha256)
            or not _valid_hash(raw.get("source_frame_sha256"))
            or type(raw.get("source_timestamp_ms")) is not int
            or not isinstance(raw.get("evidence"), str)
            or not raw.get("evidence")
            or not isinstance(engine_fingerprint, str)
            or not engine_fingerprint
            or not isinstance(model_sha256, Mapping)
            or not model_sha256
            or not isinstance(reader_fingerprint, str)
            or reader_fingerprint != engine_fingerprint):
        # A crop without the pane hash cannot be replay-bound, even if OCR was
        # able to read it in memory.  Model identity is part of the persisted
        # proof contract as well; a proof produced by another reader must not
        # be attached to this raw observation.
        return dict(raw)
    observation = dict(
        version=VERSION,
        method=METHOD,
        source_pixel_verified=True,
        source_timestamp_ms=raw.get("source_timestamp_ms"),
        evidence=raw.get("evidence"),
        source_frame_sha256=raw.get("source_frame_sha256"),
        gameplay_sha256=gameplay_sha256,
        engine_fingerprint=engine_fingerprint,
        model_sha256=copy.deepcopy(model_sha256),
        prefix=copy.deepcopy(prefix_info),
        continuation_box=copy.deepcopy(crop_box),
        amount=proof["amount"],
        amount_proof=proof,
        complete_grammar=True,
        multi_crop_not_counted_as_timestamp=True,
    )
    observation["source_sha256"] = source_sha256
    result = dict(raw)
    result["wrapped_receipt_observations"] = [observation]
    return result


def _observation_matches_raw(observation: Mapping[str, Any], raw: Mapping[str, Any],
                             lines: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if observation.get("version") != VERSION or observation.get("method") != METHOD:
        return None
    if observation.get("source_pixel_verified") is not True or observation.get("complete_grammar") is not True:
        return None
    for key in ("source_timestamp_ms", "evidence", "source_sha256",
                "source_frame_sha256", "gameplay_sha256"):
        declared = observation.get(key)
        actual = raw.get(key)
        if declared is not None and actual is not None and declared != actual:
            return None
    source_sha256 = raw.get("source_sha256")
    if (not _valid_hash(source_sha256)
            or observation.get("source_sha256") != source_sha256):
        return None
    engine_fingerprint = raw.get("engine_fingerprint")
    model_sha256 = raw.get("model_sha256")
    if (not isinstance(engine_fingerprint, str) or not engine_fingerprint
            or not isinstance(model_sha256, Mapping) or not model_sha256):
        return None
    if (observation.get("engine_fingerprint") != engine_fingerprint
            or observation.get("model_sha256") != model_sha256):
        return None
    gameplay_sha256 = observation.get("gameplay_sha256")
    if not _valid_hash(gameplay_sha256) or gameplay_sha256 != raw.get("gameplay_sha256"):
        return None
    if (not _valid_hash(observation.get("source_frame_sha256"))
            or type(observation.get("source_timestamp_ms")) is not int
            or not isinstance(observation.get("evidence"), str)
            or not observation.get("evidence")
            or observation.get("source_frame_sha256") != raw.get("source_frame_sha256")
            or observation.get("source_timestamp_ms") != raw.get("source_timestamp_ms")
            or observation.get("evidence") != raw.get("evidence")):
        return None
    raw_prefix = observation.get("prefix")
    prefix_info = prefix(raw_prefix)
    if (prefix_info is None or not isinstance(raw_prefix, Mapping)
            or prefix_info["box"] != raw_prefix.get("box")):
        return None
    matching_prefixes = []
    for line in lines:
        if not isinstance(line, Mapping) or line.get("text") != prefix_info["text"]:
            continue
        if list(line.get("box", ())) != prefix_info["box"]:
            continue
        confidence = confidence_percent(line.get("confidence"))
        if confidence is None or confidence != prefix_info["confidence"]:
            continue
        matching_prefixes.append(line)
    if len(matching_prefixes) != 1:
        return None
    expected_crop = continuation_crop_box(prefix_info["box"])
    source_lines = raw.get("lines") if isinstance(raw.get("lines"), list) else lines
    fallback_crop = _fallback_crop_box(prefix_info["box"], source_lines)
    declared_crop = observation.get("continuation_box")
    if (expected_crop is None
            or declared_crop not in (expected_crop, fallback_crop)):
        return None
    proof = observation.get("amount_proof")
    if not isinstance(proof, Mapping):
        return None
    recognized = proof.get("recognized_text")
    match = _AMOUNT_RE.fullmatch(str(recognized)) if isinstance(recognized, str) else None
    if match is None or type(proof.get("amount")) is not int:
        return None
    if int(match["amount"]) != proof["amount"] or proof["amount"] != observation.get("amount"):
        return None
    confidence = confidence_percent(proof.get("confidence"))
    basis = proof.get("basis")
    if (confidence is None
            or proof.get("crop_box") != declared_crop
            or not _valid_hash(proof.get("pixel_rgb_sha256"))
            or not isinstance(basis, str)):
        return None
    model = proof.get("model_fingerprint")
    if not isinstance(model, str) or not model or model != engine_fingerprint:
        return None
    if basis == "source_bound_wrapped_friendship_amount_crop_ocr":
        if confidence < _MIN_AMOUNT_CONFIDENCE:
            return None
    elif basis == "source_bound_wrapped_friendship_amount_color_consensus":
        views = proof.get("views")
        if (not isinstance(views, list) or len(views) != 2
                or not _valid_hash(proof.get("source_pixel_rgb_sha256"))):
            return None
        view_names = set()
        view_texts = set()
        view_amounts = set()
        for view in views:
            if not isinstance(view, Mapping):
                return None
            if view.get("view") in view_names:
                return None
            view_names.add(view.get("view"))
            recognized_view = view.get("recognized_text")
            view_match = (_AMOUNT_RE.fullmatch(recognized_view)
                          if isinstance(recognized_view, str) else None)
            if (view_match is None or type(view.get("amount")) is not int
                    or int(view_match["amount"]) != view.get("amount")
                    or view.get("amount") != proof.get("amount")
                    or view.get("crop_box") != declared_crop
                    or not _valid_hash(view.get("source_pixel_rgb_sha256"))
                    or view.get("source_pixel_rgb_sha256") != proof.get("source_pixel_rgb_sha256")
                    or not _valid_hash(view.get("pixel_rgb_sha256"))
                    or not isinstance(view.get("predicate"), str)
                    or confidence_percent(view.get("confidence")) is None
                    or confidence_percent(view.get("confidence")) < _MIN_COLOR_AMOUNT_CONFIDENCE):
                return None
            view_texts.add(recognized_view)
            view_amounts.add(view.get("amount"))
        if view_names != {"warm", "brown"} or len(view_texts) != 1 or view_amounts != {proof.get("amount")}:
            return None
        view_confidences = [confidence_percent(view.get("confidence")) for view in views]
        if (any(value is None for value in view_confidences)
                or abs(confidence - min(value for value in view_confidences if value is not None)) > 1e-4):
            return None
    else:
        return None
    return prefix_info, dict(
        text=recognized,
        recognized_text=recognized,
        amount=proof["amount"],
        confidence=confidence,
        box=list(declared_crop),
        source_proof=copy.deepcopy(dict(observation)),
    )


def _merge_line(prefix_info: Mapping[str, Any], amount_info: Mapping[str, Any],
               *, proof: Mapping[str, Any] | None = None) -> dict[str, Any]:
    original = f"{prefix_info['text']} {amount_info['recognized_text']}"
    merged = dict(
        text=f"{prefix_info['canonical_text']} {amount_info['recognized_text']}",
        confidence=min(float(prefix_info["confidence"]), float(amount_info["confidence"])),
        box=list(prefix_info["box"]),
        original_text=original,
        wrapped_receipt_parts=[
            dict(text=prefix_info["text"], confidence=prefix_info["confidence"], box=list(prefix_info["box"])),
            dict(text=amount_info["recognized_text"], confidence=amount_info["confidence"],
                 box=list(amount_info["box"])),
        ],
    )
    if proof is not None:
        merged["wrapped_receipt_proof"] = copy.deepcopy(dict(proof))
    return merged


def join(outcome_lines: Sequence[Mapping[str, Any]],
         observations: Any = None, raw: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Join one complete, same-frame wrapped friendship receipt.

    ``observations`` may contain a source-crop proof from :func:`enrich`.  A
    detector-visible standalone amount can be used directly when it satisfies
    the same strict continuation geometry.  In all cases conflicting amount
    candidates abstain.
    """

    lines = _join_status_lines(outcome_lines)
    by_prefix = [item for item in (prefix(line) for line in lines) if item is not None]
    if not by_prefix:
        return lines
    metadata = observations if isinstance(observations, list) else []
    validated = []
    for item in metadata:
        if not isinstance(item, Mapping) or not isinstance(raw, Mapping):
            continue
        proof = _observation_matches_raw(item, raw, lines)
        if proof is not None:
            validated.append(proof)
    consumed: set[int] = set()
    result: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        info = prefix(line)
        if info is None:
            result.append(line)
            continue
        candidates = []
        for candidate_index, candidate in enumerate(lines):
            if candidate_index == index or candidate_index in consumed:
                continue
            amount_info = amount_line(candidate, info)
            if amount_info is not None:
                candidates.append((candidate_index, amount_info))
        matching_proof = [(proof_prefix, amount_info) for proof_prefix, amount_info in validated
                          if proof_prefix["text"] == info["text"]
                          and proof_prefix["box"] == info["box"]]
        if matching_proof:
            amount_values = {amount_info["amount"] for _prefix_info, amount_info in matching_proof}
            if len(amount_values) != 1:
                result.append(line)
                continue
            proof_prefix, amount_info = matching_proof[0]
            proof = amount_info["source_proof"]
            if candidates and {item[1]["amount"] for item in candidates} != {amount_info["amount"]}:
                result.append(line)
                continue
            amount_info = dict(
                text=amount_info["recognized_text"], recognized_text=amount_info["recognized_text"],
                amount=amount_info["amount"], confidence=amount_info["confidence"], box=amount_info["box"],
            )
            merged = _merge_line(info, amount_info, proof=proof)
            consumed.update(candidate_index for candidate_index, _amount in candidates)
            result.append(merged)
            continue
        if len({item[1]["amount"] for item in candidates}) == 1 and candidates:
            candidate_index, amount_info = candidates[0]
            consumed.add(candidate_index)
            result.append(_merge_line(info, amount_info))
            continue
        result.append(line)
    return result


__all__ = [
    "METHOD",
    "VERSION",
    "amount_line",
    "continuation_crop_box",
    "enrich",
    "join",
    "prefix",
    "status_prefix",
]
