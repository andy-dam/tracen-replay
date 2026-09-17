"""Source-bound recovery for translucent training-preview rows.

The fast detector is intentionally conservative around Grand Live training
menus: the orange training row and the pink Concert Bonuses row can overlap
in one detector box, and a transition frame can lose the top ``Training``
heading.  This module asks the recognizer for a small, fixed set of gameplay
crop regions when that geometry is present.  It keeps the two row roles in
separate channels and requires agreement from distinct preprocessing views of
the same crop.

The module does not know a recording, a case, a balance, or an expected
amount.  ``recover``/``generate`` publish an auditable sidecar; the normal
``NeuralReader.read`` path can use ``recover_in_memory`` to attach the same
source-bound result to a fresh raw observation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


VERSION = 1
SCHEMA = "tracen-replay/preview-recovery-v1"
STAGE = "preview_recovery"
PANE_BOUNDS = (148, 0, 958, 1080)
PANE_OFFSET = 148
DEFAULT_MAX_REQUESTS = 16
DEFAULT_MIN_CONSENSUS = 2

STAT_FIELDS = ("speed", "stamina", "power", "guts", "wit", "skill_points")

# These are global source-frame coordinates.  The decoded gameplay crop starts
# at x=148.  Each crop is deliberately wider than one glyph so a translucent
# leading plus sign can be recognized, but narrow enough to exclude the next
# stat column.
FIELD_X_BOUNDS = {
    "speed": (270, 390),
    "stamina": (370, 480),
    "power": (465, 580),
    "guts": (560, 680),
    "wit": (655, 755),
    "skill_points": (745, 850),
}
MAIN_ROW_BOXES = {
    # The amount glyphs occupy the 665..705 band.  Ending before the stat-card
    # label reduces accidental reads of the persistent current values while
    # retaining enough pixels for the translucent plus sign.
    field: (left, 665, right, 705)
    for field, (left, right) in FIELD_X_BOUNDS.items()
}
MODIFIER_ROW_BOXES = {
    field: (left, 622, right, 668)
    for field, (left, right) in FIELD_X_BOUNDS.items()
}

# The selected card can be in any of the four lower card columns depending on
# the transition frame.  These boxes are all within the gameplay pane and are
# used only to find the source-visible Failure badge.
FAILURE_BOXES = (
    (250, 750, 400, 830),
    (380, 750, 550, 830),
    (530, 750, 700, 830),
    (680, 750, 850, 830),
)
CONTROL_BOUNDS = (205, 150, 420, 240)
STAT_LABEL_BOUNDS = (260, 680, 850, 735)
CONCERT_BOUNDS = (135, 520, 360, 680)

_SIGNED_RE = re.compile(r"^\+\s*(\d{1,3})\s*[.,]?$")
_FAILURE_RE = re.compile(r"^failure[.!r]*$", re.I)
_OPTION_RE = re.compile(r"^(Speed|Stamina|Power|Guts|Wit)\s+Lv[lI1]\s*\d{1,2}$", re.I)
_OPTION_PREFIX_RE = re.compile(r"^(Speed|Stamina|Power|Guts|Wit)\s+Lv[lI1]?$", re.I)
_CONTROL_NAMES = frozenset({
    "turf", "dirt", "incline", "bunny-hop", "bunny hop", "breaststroke",
})
_LABELS = {
    "speed": frozenset({"speed"}),
    "stamina": frozenset({"stamina"}),
    "power": frozenset({"power"}),
    "guts": frozenset({"guts"}),
    "wit": frozenset({"wit"}),
    "skill_points": frozenset({"skill pts", "skill points", "skillpts", "sil pts", "sill pts", "skilll pts"}),
}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gameplay_fingerprint(path: str | Path) -> str:
    from .frame_cache import rgb_digest

    digest, size = rgb_digest(path)
    if size != (810, 1080):
        raise ValueError("Preview recovery evidence is not an 810x1080 gameplay pane.")
    return digest


def _confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value))


def _box(value: Any, *, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"Preview recovery {name} box is invalid.")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Preview recovery {name} box is invalid.") from exc
    if not result[0] < result[2] or not result[1] < result[3]:
        raise ValueError(f"Preview recovery {name} box is empty.")
    left, top, right, bottom = result
    if not (PANE_BOUNDS[0] <= left < right <= PANE_BOUNDS[2]
            and PANE_BOUNDS[1] <= top < bottom <= PANE_BOUNDS[3]):
        raise ValueError(f"Preview recovery {name} box leaves gameplay bounds.")
    return result


def _line_box(line: Mapping[str, Any]) -> list[float] | None:
    try:
        return _box(line.get("box"), name="source line")
    except ValueError:
        return None


def _center(box: Sequence[float]) -> tuple[float, float]:
    return ((float(box[0]) + float(box[2])) / 2,
            (float(box[1]) + float(box[3])) / 2)


def _source_lines(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    lines = raw.get("lines")
    return [line for line in lines if isinstance(line, dict)] if isinstance(lines, list) else []


def _within(line: Mapping[str, Any], bounds: Sequence[float]) -> bool:
    box = _line_box(line)
    if box is None:
        return False
    x, y = _center(box)
    return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def _header(raw: Mapping[str, Any]) -> str:
    return _text(raw.get("header")).casefold()


def _has_result_marker(raw: Mapping[str, Any]) -> bool:
    if raw.get("result_grid") is True or raw.get("success_visible") is True:
        return True
    if raw.get("result_marker_visible") is True:
        return True
    for line in _source_lines(raw):
        if _confidence(line.get("confidence")) < 90:
            continue
        text = _text(line.get("text")).casefold()
        box = _line_box(line)
        if box is None:
            continue
        _x, y = _center(box)
        if 600 <= y <= 850 and re.fullmatch(r"(?:success|suocess|failure)[.!r]*", text):
            # Failure belongs to the selected menu card.  SUCCESS is the
            # committed result marker and always wins over a stale grid.
            if text.startswith("success") or text.startswith("suocess"):
                return True
    return False


def _has_failure_line(raw: Mapping[str, Any]) -> bool:
    for line in _source_lines(raw):
        box = _line_box(line)
        if (_confidence(line.get("confidence")) >= 90 and box is not None
                and _within(line, (240, 735, 850, 850))
                and _FAILURE_RE.fullmatch(_text(line.get("text")))):
            return True
    return False


def _control_lines(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for line in _source_lines(raw):
        if _confidence(line.get("confidence")) < 80 or not _within(line, CONTROL_BOUNDS):
            continue
        normalized = _text(line.get("text")).casefold()
        compact = normalized.replace(" ", "")
        if (_OPTION_RE.fullmatch(_text(line.get("text")))
                or _OPTION_PREFIX_RE.fullmatch(_text(line.get("text")))
                or normalized in _CONTROL_NAMES
                or compact in {item.replace(" ", "") for item in _CONTROL_NAMES}):
            result.append(line)
    return result


def _canonical_number(value: Any) -> int | float:
    """Return a stable JSON number for source-line proof fields."""

    number = float(value)
    return int(number) if number.is_integer() else round(number, 4)


def _canonical_line(line: Mapping[str, Any], *, normalized: str | None = None) -> dict[str, Any] | None:
    """Project one OCR line to the immutable geometry/text proof we use.

    Raw OCR records may grow auxiliary fields over time.  Recovery validation
    binds the fields that identify the visible source line and deliberately
    ignores those unrelated fields.
    """

    box = _line_box(line)
    if box is None:
        return None
    result: dict[str, Any] = {
        "text": _text(line.get("text")),
        "confidence": _canonical_number(_confidence(line.get("confidence"))),
        "box": [_canonical_number(item) for item in box],
    }
    if normalized is not None:
        result["normalized"] = normalized
    return result


def _canonical_control_proof(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return sorted source proof for the training control/option lines."""

    result = []
    for line in _control_lines(raw):
        canonical = _canonical_line(line)
        if canonical is not None:
            result.append(canonical)
    return sorted(result, key=lambda item: (
        tuple(item["box"]), item["text"].casefold(), item["confidence"],
    ))


def _concert_marker_lines(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only canonical Concert/Bonuses marker lines in marker geometry."""

    result = []
    for line in _source_lines(raw):
        if _confidence(line.get("confidence")) < 80 or not _within(line, CONCERT_BOUNDS):
            continue
        normalized = re.sub(r"[^a-z]+", " ", _text(line.get("text")).casefold()).strip()
        if normalized not in {"concert", "bonuses", "bbonuses", "concert bonuses"}:
            continue
        canonical = _canonical_line(line, normalized=normalized)
        if canonical is not None:
            result.append(canonical)
    return sorted(result, key=lambda item: (
        tuple(item["box"]), item["normalized"], item["confidence"],
    ))


def _has_training_control(raw: Mapping[str, Any]) -> bool:
    header = _header(raw)
    return header.startswith(("training", "career")) or bool(_control_lines(raw))


def _field_for_x(x: float) -> str | None:
    matches = [field for field, (left, right) in FIELD_X_BOUNDS.items() if left <= x <= right]
    return matches[0] if len(matches) == 1 else None


def _label_fields(raw: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for line in _source_lines(raw):
        if _confidence(line.get("confidence")) < 80 or not _within(line, STAT_LABEL_BOUNDS):
            continue
        text = _compact(line.get("text")).casefold()
        for field, aliases in _LABELS.items():
            if text in {item.replace(" ", "") for item in aliases}:
                result.add(field)
    return result


def _has_stat_layout(raw: Mapping[str, Any]) -> bool:
    return len(_label_fields(raw)) >= 3


def _training_option(raw: Mapping[str, Any]) -> str | None:
    full = []
    prefixes = []
    lines = _control_lines(raw)
    for line in lines:
        text = _text(line.get("text"))
        match = _OPTION_RE.fullmatch(text)
        if match:
            full.append(match[1].casefold())
        prefix = _OPTION_PREFIX_RE.fullmatch(text)
        if prefix:
            prefixes.append(prefix[1].casefold())
    if full and len(set(full)) == 1:
        return full[0]
    if prefixes and len(set(prefixes)) == 1:
        return prefixes[0]
    panel = raw.get("preview_panel")
    if isinstance(panel, dict):
        value = panel.get("option", panel.get("option_label"))
        if isinstance(value, str) and value.casefold() in STAT_FIELDS:
            return value.casefold()
    return None


def _has_concert_marker(raw: Mapping[str, Any]) -> bool:
    values = {item["normalized"] for item in _concert_marker_lines(raw)}
    return ("concert bonuses" in values
            or {"concert", "bonuses"}.issubset(values)
            or {"concert", "bbonuses"}.issubset(values))


def _row_lines(raw: Mapping[str, Any], *, modifier: bool = False) -> list[dict[str, Any]]:
    top, bottom = (MODIFIER_ROW_BOXES["speed"][1], MODIFIER_ROW_BOXES["speed"][3]) if modifier else (MAIN_ROW_BOXES["speed"][1], MAIN_ROW_BOXES["speed"][3])
    result = []
    for line in _source_lines(raw):
        box = _line_box(line)
        if box is None or _confidence(line.get("confidence")) < 80:
            continue
        _x, y = _center(box)
        if top <= y <= bottom:
            result.append(line)
    return result


def _signed_by_field(raw: Mapping[str, Any], *, modifier: bool = False) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    result: dict[str, list[tuple[int, dict[str, Any]]]] = {field: [] for field in STAT_FIELDS}
    for line in _row_lines(raw, modifier=modifier):
        box = _line_box(line)
        if box is None:
            continue
        field = _field_for_x(_center(box)[0])
        match = _SIGNED_RE.fullmatch(_text(line.get("text")))
        if field and match:
            result[field].append((int(match[1]), line))
    return result


def _needs_recovery(raw: Mapping[str, Any]) -> bool:
    lines = _row_lines(raw)
    signed = _signed_by_field(raw)
    stacked_fields = {
        field for field, values in signed.items()
        if len({amount for amount, _line in values}) > 1
    }
    malformed = any(
        "+" in _text(line.get("text")) and not _SIGNED_RE.fullmatch(_text(line.get("text")))
        for line in lines
    )
    # A missing menu heading, missing Failure OCR, split option heading,
    # merged row, or multi-column stack is enough to request fixed rereads.
    if not _header(raw).startswith(("training", "career")):
        return True
    if not _has_failure_line(raw) or _training_option(raw) is None:
        return True
    if malformed or len(stacked_fields) >= 2:
        return True
    # If at least two orange rows are visible but one labeled column is absent,
    # schedule the bounded per-column pass.  A normal zero-gain column is not
    # considered a failure when the fast path has no other trigger.
    visible = {field for field, values in signed.items() if values}
    if len(visible) >= 2 and len(visible) < 4:
        return True
    if _has_concert_marker(raw):
        modifier = _signed_by_field(raw, modifier=True)
        if any(modifier[field] and not signed[field] for field in STAT_FIELDS):
            return True
        # A Concert marker makes the row stack explicit.  When at least one
        # orange row is visible but a labeled stat column has no fast-detector
        # row, schedule the fixed pass so a translucent main glyph cannot be
        # mistaken for a genuine zero.  The fixed crop may still resolve to
        # no observation; no amount is supplied by this condition.
        labels = _label_fields(raw)
        if visible and len(visible) < len(labels):
            return True
    return False


def _phase_candidate(raw: Mapping[str, Any]) -> bool:
    if not isinstance(raw, Mapping):
        return False
    if raw.get("result_grid") is True or _has_result_marker(raw):
        return False
    if raw.get("current_grid") is True:
        return _has_training_control(raw) and _has_stat_layout(raw)
    # The fast blue/colour probe can miss the selectable stat cards when a
    # friendship card is pink/red or a transition overlay is translucent.
    # Permit the bounded recovery pass when the source itself still proves a
    # training menu: a Training header, one selected option/control, and the
    # fixed stat-label band.  The Failure badge is useful corroboration, but
    # it can be hidden behind the trainee on a genuine menu.  This is a
    # layout proof only; signed rows and amounts are still read from the
    # fixed source crops below.  The selected option and the stat-label band
    # keep result cards, dialogue, and unrelated panels outside this fallback.
    return (
        _header(raw).startswith("training")
        and _training_option(raw) is not None
        and _has_stat_layout(raw)
    )


def _request(*, request_id: str, region: str, field: str, kind: str,
             role: str, box: Sequence[int], priority: int,
             pattern: str) -> dict[str, Any]:
    _box(box, name=request_id)
    return {
        "id": request_id,
        "region": region,
        "channel": "preview",
        "field": field,
        "kind": kind,
        "box": list(box),
        "pattern": pattern,
        # The fixed preview glyphs are translucent.  Distinct-view agreement
        # is the primary safeguard; 80 keeps source-readable rows whose
        # general detector confidence is low while still requiring an exact
        # signed value in at least two independent preprocessing variants.
        "minimum_confidence": 80.0 if kind == "preview_stat" else 90.0,
        "minimum_consensus": DEFAULT_MIN_CONSENSUS,
        "priority": priority,
        "geometry_basis": (
            "fixed_preview_main_row_geometry" if role == "main"
            else "fixed_preview_modifier_row_geometry" if role == "modifier"
            else "fixed_preview_failure_badge_geometry"
        ),
        "role": role,
        "input_eligible": True,
    }


def candidate_requests(raw: Mapping[str, Any], *, max_requests: int | None = None) -> list[dict[str, Any]]:
    """Return bounded source-geometry requests for a candidate preview frame."""

    if not isinstance(raw, Mapping):
        raise TypeError("Preview recovery raw observation must be a mapping.")
    if not _phase_candidate(raw) or not _needs_recovery(raw):
        requests: list[dict[str, Any]] = []
    else:
        requests = []
        for index, box in enumerate(FAILURE_BOXES):
            requests.append(_request(
                request_id=f"preview-failure:{index}",
                region=f"preview.failure.{index}", field="failure",
                kind="preview_failure", role="phase", box=box,
                priority=100 - index, pattern="failure_badge"))
        for field in STAT_FIELDS:
            requests.append(_request(
                request_id=f"preview-main:{field}",
                region=f"preview.main.{field}", field=field,
                kind="preview_stat", role="main", box=MAIN_ROW_BOXES[field],
                priority=70, pattern="signed_preview_amount"))
        if _has_concert_marker(raw):
            for field in STAT_FIELDS:
                requests.append(_request(
                    request_id=f"preview-modifier:{field}",
                    region=f"preview.modifier.{field}", field=field,
                    kind="preview_stat", role="modifier",
                    box=MODIFIER_ROW_BOXES[field], priority=60,
                    pattern="signed_preview_amount"))
    if max_requests is not None:
        if type(max_requests) is not int or max_requests < 0:
            raise ValueError("Preview recovery max_requests must be a non-negative integer.")
        requests = requests[:max_requests]
    return requests


def discover(raw: Mapping[str, Any], *, max_requests: int = DEFAULT_MAX_REQUESTS) -> dict[str, Any]:
    if type(max_requests) is not int or max_requests < 0:
        raise ValueError("Preview recovery max_requests must be a non-negative integer.")
    all_requests = candidate_requests(raw)
    selected = all_requests[:max_requests]
    deferred = [dict(item, deferred_reason="source_crop_budget")
                for item in all_requests[max_requests:]]
    return {
        "schema_version": SCHEMA,
        "stage": STAGE,
        "max_requests": max_requests,
        "candidate_count": len(all_requests),
        "selected_count": len(selected),
        "deferred_count": len(deferred),
        "requests": selected,
        "deferred": deferred,
    }


def _request_value(request: Mapping[str, Any], value: Any) -> Any:
    text = _text(value)
    if request.get("kind") == "preview_failure":
        return "Failure" if _FAILURE_RE.fullmatch(text) else None
    match = _SIGNED_RE.fullmatch(text)
    if match is None:
        return None
    amount = int(match[1])
    return amount if 0 <= amount <= 999 else None


def _variant_images(pane: Any, box: Sequence[int]) -> list[tuple[str, Any]]:
    from PIL import Image, ImageOps
    import numpy as np

    left, top, right, bottom = [int(value) for value in box]
    _box(box, name="source crop")
    array = np.asarray(pane.convert("RGB"))[top:bottom,
                                              left - PANE_OFFSET:right - PANE_OFFSET]
    if array.size == 0:
        raise ValueError("Preview recovery source crop is empty.")
    image = Image.fromarray(array, mode="RGB")
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    gray_up = gray.resize((gray.width * 3, gray.height * 3))
    return [
        ("raw", np.asarray(image)[:, :, ::-1]),
        ("gray_autocontrast", np.repeat(np.asarray(gray)[:, :, None], 3, axis=2)[:, :, ::-1]),
        ("gray_autocontrast_3x", np.repeat(np.asarray(gray_up)[:, :, None], 3, axis=2)[:, :, ::-1]),
    ]


def _engine_read(reader: Any, pane: Any,
                 requests: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    if hasattr(reader, "recognize_crops"):
        result = reader.recognize_crops(pane, requests)
        if not isinstance(result, list) or len(result) != len(requests):
            raise ValueError("Preview recovery reader returned the wrong crop count.")
        return result
    engine = getattr(reader, "engine", None)
    input_type = getattr(reader, "TextRecInput", None)
    if engine is None or input_type is None:
        raise TypeError("Preview recovery reader must expose engine and TextRecInput.")
    images = []
    labels = []
    for request in requests:
        for variant, image in _variant_images(pane, request["box"]):
            images.append(image)
            labels.append((request["id"], variant))
    if not images:
        return [[] for _ in requests]
    result = engine.text_rec(input_type(img=images))
    grouped: dict[str, list[dict[str, Any]]] = {str(item["id"]): [] for item in requests}
    for (request_id, variant), text, score in zip(labels, result.txts, result.scores):
        grouped[request_id].append({
            "variant": variant,
            "text": str(text),
            "confidence": round(float(score) * 100, 4),
        })
    result = [grouped[str(item["id"])] for item in requests]
    if len(result) != len(requests):
        raise ValueError("Preview recovery engine returned the wrong crop count.")
    return result


def _resolve_request(request: Mapping[str, Any], views: Sequence[Mapping[str, Any]], *, min_consensus: int) -> dict[str, Any]:
    minimum = _confidence(request.get("minimum_confidence"))
    rows = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    variants: dict[str, str] = {}
    duplicate = False
    for view in views:
        value = _request_value(request, view.get("text"))
        row = dict(view, parsed_value=value,
                   eligible=value is not None and _confidence(view.get("confidence")) >= minimum)
        rows.append(row)
        if not row["eligible"]:
            continue
        variant = view.get("variant")
        if not isinstance(variant, str) or not variant.strip():
            continue
        variant = variant.strip()
        key = json.dumps(value, sort_keys=True)
        if variant in variants:
            duplicate = True
            continue
        variants[variant] = key
        grouped.setdefault(key, []).append(row)
    selected = None
    status = "unresolved_no_eligible_crop_read"
    if duplicate:
        status = "unresolved_duplicate_crop_variant"
    elif len(grouped) > 1:
        status = "unresolved_conflicting_crop_reads"
    elif len(grouped) == 1:
        alternatives = next(iter(grouped.values()))
        if len(alternatives) >= min_consensus:
            selected = max(alternatives, key=lambda item: _confidence(item.get("confidence")))
            status = "resolved_same_frame_crop_consensus"
        else:
            status = "unresolved_insufficient_crop_consensus"
    result: dict[str, Any] = {
        "request_id": request["id"],
        "region": request["region"],
        "box": list(request["box"]),
        "role": request.get("role"),
        "field": request.get("field"),
        "views": rows,
        "status": status,
    }
    if selected is not None:
        result["selected"] = dict(selected)
    return result


def _selected_observation(request: Mapping[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    selected = resolved.get("selected")
    if not isinstance(selected, Mapping) or selected.get("parsed_value") is None:
        return None
    return {
        "text": selected.get("text", ""),
        "confidence": selected.get("confidence", 0),
        "box": list(request["box"]),
        "role": request.get("role"),
        "field": request.get("field"),
        "input_eligible": True,
        "geometry_basis": request.get("geometry_basis"),
        "source_request_id": request["id"],
        "source_variant": selected.get("variant"),
        "parsed_value": selected.get("parsed_value"),
    }


def _raw_phase(raw: Mapping[str, Any], regions: Mapping[str, Any]) -> dict[str, Any]:
    failures = [name for name, observation in regions.items()
                if name.startswith("preview.failure.") and observation.get("parsed_value") == "Failure"]
    header = _header(raw)
    control = [_text(line.get("text")) for line in _control_lines(raw)]
    raw_failure = _has_failure_line(raw)
    base = _phase_candidate(raw)
    # Existing detector Failure is already same-frame gameplay evidence.  The
    # fixed badge reread is supplemental and can be occluded by the character
    # model on these transition frames.
    source_fallback = (
        raw.get("current_grid") is not True
        and base
        and _header(raw).startswith("training")
    )
    if len(failures) == 1 and base:
        phase_proven = True
        basis = (
            "source_training_menu_fixed_failure_and_labeled_stat_layout"
            if source_fallback
            else "current_grid_and_fixed_failure_and_training_control"
        )
        failure_regions = failures
    elif raw_failure and base:
        phase_proven = True
        basis = (
            "source_training_menu_failure_and_labeled_stat_layout"
            if source_fallback
            else "current_grid_and_source_failure_and_training_control"
        )
        failure_regions = ["raw_line:failure"]
    elif source_fallback:
        # The selected-card badge may be occluded, while the option heading
        # and lower stat-label row remain intact.  Keep the proof source-only
        # and leave the failure region empty; no amount is accepted here.
        phase_proven = True
        basis = "source_training_menu_header_option_and_labeled_stat_layout"
        failure_regions = []
    elif (header.startswith("training") and _training_option(raw)
          and _has_training_control(raw) and base):
        # When the header and typed option are intact, the source menu layout
        # itself proves browse phase even if the rounded Failure glyph is
        # hidden behind the trainee.  This branch is never available to a
        # header-faded frame.
        phase_proven = True
        basis = "current_grid_and_training_header_and_option"
        failure_regions = []
    else:
        phase_proven = False
        basis = "current_grid_and_fixed_failure_and_training_control"
        failure_regions = failures
    return {
        "status": "resolved" if phase_proven else "unresolved",
        "menu_proven": phase_proven,
        "result_proven": False,
        "basis": basis,
        "header": raw.get("header"),
        "option": _training_option(raw),
        "control_lines": control,
        # These canonical line projections bind the phase proof to the
        # source gameplay crop.  They are recomputed during cached-load
        # validation; callers cannot replace a control or Concert marker by
        # editing the attached preview regions.
        "control_proof": _canonical_control_proof(raw),
        "modifier_marker_proof": _concert_marker_lines(raw),
        "failure_regions": failure_regions,
    }


def _build_recovery(raw: Mapping[str, Any], requests: Sequence[Mapping[str, Any]],
                    resolved: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    regions: dict[str, dict[str, Any]] = {}
    by_request = {item.get("request_id"): item for item in resolved}
    for request in requests:
        observation = _selected_observation(request, by_request.get(request["id"], {}))
        if observation is not None:
            regions[request["region"]] = observation
    phase = _raw_phase(raw, regions)
    main_effects = []
    modifier_effects = []
    # Existing exact orange detector rows are valid same-frame row anchors for
    # a pink modifier even when the fixed reread selected no duplicate.  The
    # amount still comes only from the fixed role-specific crop below.
    main_fields = {field for field, values in _signed_by_field(raw).items() if values}
    for name, observation in regions.items():
        if not name.startswith("preview.main."):
            continue
        field = observation.get("field")
        amount = observation.get("parsed_value")
        if field in STAT_FIELDS and isinstance(amount, int):
            main_fields.add(field)
            main_effects.append({
                "kind": "stat_change", "field": field, "amount": amount,
                "phase": "preview", "preview": True, "awarded": False,
                "source_region": name, "geometry_basis": observation.get("geometry_basis"),
            })
    if phase["menu_proven"] and _has_concert_marker(raw):
        for name, observation in regions.items():
            if not name.startswith("preview.modifier."):
                continue
            field = observation.get("field")
            amount = observation.get("parsed_value")
            # A modifier row is meaningful only when the same frame also has
            # the lower main row for that field.  This prevents an upper pink
            # glyph from becoming a main effect when the orange row is absent.
            if field in main_fields and isinstance(amount, int):
                modifier_effects.append({
                    "kind": "song_modifier_change", "field": field, "amount": amount,
                    "phase": "preview", "preview": True, "awarded": False,
                    "modifier": "song", "source_region": name,
                    "geometry_basis": observation.get("geometry_basis"),
                })
    return {
        "version": VERSION,
        "schema_version": SCHEMA,
        "stage": STAGE,
        "status": "resolved" if phase["menu_proven"] else "unresolved",
        "phase": phase,
        "option": phase.get("option"),
        "regions": regions,
        "observations": [copy.deepcopy(item) for item in resolved],
        "effects": main_effects if phase["menu_proven"] else [],
        "modifier_effects": modifier_effects if phase["menu_proven"] else [],
        "requests": [copy.deepcopy(dict(item)) for item in requests],
        "source_timestamp_ms": raw.get("source_timestamp_ms"),
        "evidence": raw.get("evidence"),
        "source_frame_sha256": raw.get("source_frame_sha256"),
        "gameplay_sha256": raw.get("gameplay_sha256"),
        "raw_sha256": fingerprint(raw),
        "independent_observations": False,
    }


def recover_in_memory(raw: Mapping[str, Any], pane: Any, *, reader: Any,
                      max_requests: int = DEFAULT_MAX_REQUESTS,
                      min_consensus: int = DEFAULT_MIN_CONSENSUS) -> dict[str, Any]:
    """Attach a fresh same-frame preview recovery to a normal OCR result."""

    if not isinstance(raw, Mapping):
        raise TypeError("Preview recovery raw observation must be a mapping.")
    if type(min_consensus) is not int or min_consensus < 1:
        raise ValueError("Preview recovery min_consensus must be positive.")
    if getattr(pane, "size", None) != (810, 1080):
        raise ValueError("Preview recovery accepts only the gameplay crop.")
    requests = candidate_requests(raw, max_requests=max_requests)
    if not requests:
        return dict(raw)
    for request in requests:
        request["minimum_consensus"] = min_consensus
    views = _engine_read(reader, pane, requests)
    resolved = [
        _resolve_request(request, item, min_consensus=min_consensus)
        for request, item in zip(requests, views)
    ]
    return attach(raw, _build_recovery(raw, requests, resolved))


def attach(raw: Mapping[str, Any], recovery: Mapping[str, Any]) -> dict[str, Any]:
    """Attach a validated in-memory result without changing source lines."""

    if not isinstance(raw, Mapping) or not isinstance(recovery, Mapping):
        raise TypeError("Preview recovery attach requires mappings.")
    if recovery.get("schema_version") != SCHEMA or recovery.get("stage") != STAGE:
        raise ValueError("Preview recovery schema mismatch.")
    result = copy.deepcopy(dict(raw))
    existing_recovery = result.get("preview_recovery")
    if existing_recovery is not None and existing_recovery != recovery:
        raise ValueError("Preview recovery disagrees with an existing recovery.")
    result["preview_recovery"] = copy.deepcopy(dict(recovery))
    regions = dict(result.get("regions") or {})
    for name, observation in (recovery.get("regions") or {}).items():
        if name in regions and regions[name] != observation:
            raise ValueError(f"Preview recovery region disagrees with existing region: {name}")
        regions[name] = copy.deepcopy(observation)
    result["regions"] = regions
    return result


def _safe_relative(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"Preview recovery {name} path is invalid.")
    from pathlib import PurePosixPath, PureWindowsPath

    normalized = value.replace("\\", "/")
    if PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).is_absolute() \
            or PureWindowsPath(value).drive or PureWindowsPath(value).root:
        raise ValueError(f"Preview recovery {name} path must be relative.")
    if any(part in {"", ".", ".."} for part in PurePosixPath(normalized).parts):
        raise ValueError(f"Preview recovery {name} path is unsafe.")
    return PurePosixPath(normalized).as_posix()


def _source_verification(raw: Mapping[str, Any], source_frame_path: str | Path | None,
                         source_frame_evidence: str | None) -> dict[str, Any]:
    expected = raw.get("source_frame_sha256")
    if expected is None:
        return {"status": "unavailable", "reason": "raw_source_frame_hash_missing"}
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError("Preview recovery source-frame hash is invalid.")
    path_value = _safe_relative(source_frame_evidence, name="source-frame evidence")
    if source_frame_path is None:
        result = {"status": "unavailable", "expected_sha256": expected}
        if path_value:
            result["path"] = path_value
        return result
    path = Path(source_frame_path)
    if not path.is_file():
        raise ValueError("Preview recovery source-frame evidence is missing.")
    actual = file_fingerprint(path)
    if actual != expected:
        raise ValueError("Preview recovery source-frame hash mismatch.")
    result = {"status": "verified", "sha256": actual}
    if path_value:
        result["path"] = path_value
    return result


def _build_sidecar(raw: Mapping[str, Any], evidence_path: Path,
                   recovery: Mapping[str, Any], requests: Sequence[Mapping[str, Any]],
                   resolved: Sequence[Mapping[str, Any]], *, reader: Any,
                   source_frame_path: str | Path | None,
                   source_frame_evidence: str | None,
                   source_frame_id: str | None) -> dict[str, Any]:
    models = copy.deepcopy(getattr(reader, "models", {}))
    engine = getattr(reader, "fingerprint", None)
    if not models or not engine:
        raise ValueError("Preview recovery reader identity is missing.")
    sidecar = copy.deepcopy(dict(recovery))
    sidecar.update({
        "source_frame_id": source_frame_id,
        "evidence_sha256": file_fingerprint(evidence_path),
        "gameplay_sha256": gameplay_fingerprint(evidence_path),
        "source_frame_evidence": _safe_relative(source_frame_evidence, name="source-frame evidence"),
        "source_frame_verification": _source_verification(raw, source_frame_path, source_frame_evidence),
        "source_model_sha256": copy.deepcopy(raw.get("model_sha256")),
        "source_engine_fingerprint": raw.get("engine_fingerprint"),
        "recovery_model_sha256": models,
        "recovery_engine_fingerprint": engine,
        "selection": {
            "max_requests": len(requests),
            "request_ids": [item["id"] for item in requests],
            "geometry_only": True,
        },
    })
    # ``raw_sha256`` must describe the immutable input, not the sidecar.
    sidecar["raw_sha256"] = fingerprint(raw)
    return sidecar


def recover(raw: Mapping[str, Any], evidence_path: str | Path, *, reader: Any = None,
            model_dir: str | Path = ".local/models/rapidocr",
            max_requests: int = DEFAULT_MAX_REQUESTS,
            min_consensus: int = DEFAULT_MIN_CONSENSUS,
            source_frame_path: str | Path | None = None,
            source_frame_evidence: str | None = None,
            source_frame_id: str | None = None) -> dict[str, Any]:
    """Run bounded OCR on the exact gameplay evidence and return a sidecar."""

    if not isinstance(raw, Mapping):
        raise TypeError("Preview recovery raw observation must be a mapping.")
    evidence_path = Path(evidence_path)
    if not evidence_path.is_file():
        raise ValueError("Preview recovery gameplay evidence is missing.")
    expected_pixels = raw.get("gameplay_sha256")
    actual_pixels = gameplay_fingerprint(evidence_path)
    if expected_pixels and actual_pixels != expected_pixels:
        raise ValueError("Preview recovery gameplay pixels changed.")
    if reader is None:
        from .vision import NeuralReader

        reader = NeuralReader(model_dir)
    requests = candidate_requests(raw, max_requests=max_requests)
    for request in requests:
        request["minimum_consensus"] = min_consensus
    from PIL import Image

    with Image.open(evidence_path) as opened:
        pane = opened.convert("RGB")
        if requests:
            views = _engine_read(reader, pane, requests)
        else:
            views = [[] for _ in requests]
    resolved = [
        _resolve_request(request, item, min_consensus=min_consensus)
        for request, item in zip(requests, views)
    ]
    recovery = _build_recovery(raw, requests, resolved)
    sidecar = _build_sidecar(
        raw, evidence_path, recovery, requests, resolved, reader=reader,
        source_frame_path=source_frame_path,
        source_frame_evidence=source_frame_evidence,
        source_frame_id=source_frame_id,
    )
    validate(raw, sidecar, evidence_path=evidence_path,
             source_frame_path=source_frame_path,
             source_frame_id=source_frame_id,
             source_frame_evidence=source_frame_evidence)
    return sidecar


fresh = recover


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"Refusing to overwrite preview recovery sidecar: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def generate(raw_path: str | Path, evidence_path: str | Path, output_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    raw_path = Path(raw_path)
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Preview recovery raw observation is not readable.") from exc
    if not isinstance(raw, dict):
        raise ValueError("Preview recovery raw observation must be a JSON object.")
    sidecar = recover(raw, evidence_path, **kwargs)
    _write_json(output_path, sidecar)
    return sidecar


def _validate_sidecar_request(request: Mapping[str, Any]) -> None:
    required = ("id", "region", "channel", "field", "kind", "box",
                "pattern", "minimum_confidence", "minimum_consensus",
                "priority", "geometry_basis", "role")
    for key in required:
        if key not in request:
            raise ValueError(f"Preview recovery request missing {key}.")
    _box(request["box"], name=str(request.get("id")))
    if request.get("channel") != "preview" or request.get("input_eligible") is not True:
        raise ValueError("Preview recovery request channel is invalid.")
    if request.get("kind") == "preview_stat":
        if request.get("field") not in STAT_FIELDS or request.get("role") not in {"main", "modifier"}:
            raise ValueError("Preview recovery stat request field or role is invalid.")
        field = request["field"]
        role = request["role"]
        expected = MAIN_ROW_BOXES[field] if role == "main" else MODIFIER_ROW_BOXES[field]
        if list(request["box"]) != list(expected):
            raise ValueError("Preview recovery stat request geometry is invalid.")
        expected_values = {
            "id": f"preview-{role}:{field}",
            "region": f"preview.{role}.{field}",
            "pattern": "signed_preview_amount",
            "geometry_basis": (
                "fixed_preview_main_row_geometry" if role == "main"
                else "fixed_preview_modifier_row_geometry"
            ),
            "priority": 70 if role == "main" else 60,
            "minimum_confidence": 80.0,
        }
        for key, value in expected_values.items():
            if request.get(key) != value:
                raise ValueError(f"Preview recovery stat request {key} is invalid.")
    elif request.get("kind") == "preview_failure":
        if request.get("field") != "failure" or request.get("role") != "phase":
            raise ValueError("Preview recovery failure request is invalid.")
        if list(request["box"]) not in [list(item) for item in FAILURE_BOXES]:
            raise ValueError("Preview recovery failure request geometry is invalid.")
        index = next(index for index, box in enumerate(FAILURE_BOXES)
                     if list(box) == list(request["box"]))
        expected_values = {
            "id": f"preview-failure:{index}",
            "region": f"preview.failure.{index}",
            "pattern": "failure_badge",
            "geometry_basis": "fixed_preview_failure_badge_geometry",
            "priority": 100 - index,
            "minimum_confidence": 90.0,
        }
        for key, value in expected_values.items():
            if request.get(key) != value:
                raise ValueError(f"Preview recovery failure request {key} is invalid.")
    else:
        raise ValueError("Preview recovery request kind is invalid.")
    if (type(request.get("minimum_consensus")) is not int
            or not 1 <= request["minimum_consensus"] <= 5):
        raise ValueError("Preview recovery request consensus is invalid.")
    if "expected" in request or "amount" in request:
        raise ValueError("Preview recovery request contains a forbidden value.")


def validate(raw: Mapping[str, Any], sidecar: Mapping[str, Any], *, evidence_path: str | Path | None = None,
             source_frame_path: str | Path | None = None, source_frame_id: str | None = None,
             source_frame_evidence: str | None = None) -> None:
    """Validate immutable source, request, and selected-crop provenance."""

    if not isinstance(raw, Mapping) or not isinstance(sidecar, Mapping):
        raise ValueError("Preview recovery validation requires mappings.")
    if sidecar.get("version") != VERSION or sidecar.get("schema_version") != SCHEMA \
            or sidecar.get("stage") != STAGE:
        raise ValueError("Preview recovery sidecar schema mismatch.")
    if sidecar.get("raw_sha256") != fingerprint(raw):
        raise ValueError("Preview recovery raw source JSON changed.")
    for key in ("source_timestamp_ms", "evidence", "gameplay_sha256"):
        if sidecar.get(key) != raw.get(key):
            raise ValueError(f"Preview recovery {key} changed.")
    if sidecar.get("source_frame_sha256") != raw.get("source_frame_sha256"):
        raise ValueError("Preview recovery source-frame hash changed.")
    if sidecar.get("source_model_sha256") != raw.get("model_sha256"):
        raise ValueError("Preview recovery source model identity changed.")
    if sidecar.get("source_engine_fingerprint") != raw.get("engine_fingerprint"):
        raise ValueError("Preview recovery source engine identity changed.")
    if sidecar.get("independent_observations") is not False:
        raise ValueError("Preview recovery sidecar cannot claim independent observations.")
    if source_frame_id is not None and sidecar.get("source_frame_id") != source_frame_id:
        raise ValueError("Preview recovery source frame identity changed.")
    if source_frame_evidence is not None and sidecar.get("source_frame_evidence") != _safe_relative(source_frame_evidence, name="source-frame evidence"):
        raise ValueError("Preview recovery source-frame evidence path changed.")
    requests = sidecar.get("requests")
    if not isinstance(requests, list):
        raise ValueError("Preview recovery requests are missing.")
    request_ids = []
    for request in requests:
        if not isinstance(request, Mapping):
            raise ValueError("Preview recovery request is invalid.")
        _validate_sidecar_request(request)
        request_ids.append(request["id"])
    if len(set(request_ids)) != len(request_ids):
        raise ValueError("Preview recovery request IDs are duplicated.")
    observations = sidecar.get("observations")
    if not isinstance(observations, list) or len(observations) != len(requests):
        raise ValueError("Preview recovery observations are incomplete.")
    observation_ids = [item.get("request_id") for item in observations
                       if isinstance(item, Mapping)]
    if observation_ids != request_ids:
        raise ValueError("Preview recovery observation request order changed.")
    for request, observation in zip(requests, observations):
        if not isinstance(observation, Mapping):
            raise ValueError("Preview recovery observation is invalid.")
        expected_observation = _resolve_request(
            request, observation.get("views", ()),
            min_consensus=request["minimum_consensus"])
        if dict(observation) != expected_observation:
            raise ValueError("Preview recovery crop consensus changed.")
    regions = sidecar.get("regions")
    if not isinstance(regions, Mapping):
        raise ValueError("Preview recovery regions are missing.")
    for name, observation in regions.items():
        if not isinstance(name, str) or not isinstance(observation, Mapping):
            raise ValueError("Preview recovery selected region is invalid.")
        request = next((item for item in requests if item.get("region") == name), None)
        if request is None:
            raise ValueError("Preview recovery selected region has no request.")
        if list(observation.get("box", ())) != list(request["box"]):
            raise ValueError("Preview recovery selected crop geometry changed.")
        if _request_value(request, observation.get("text")) != observation.get("parsed_value"):
            raise ValueError("Preview recovery selected crop value changed.")
    expected = _build_recovery(raw, requests, observations)
    for key in ("status", "phase", "option", "regions", "effects", "modifier_effects"):
        if sidecar.get(key) != expected.get(key):
            raise ValueError(f"Preview recovery {key} changed.")
    if evidence_path is not None:
        evidence = Path(evidence_path)
        if not evidence.is_file():
            raise ValueError("Preview recovery evidence is missing.")
        if sidecar.get("evidence_sha256") != file_fingerprint(evidence):
            raise ValueError("Preview recovery evidence changed.")
        if sidecar.get("gameplay_sha256") != gameplay_fingerprint(evidence):
            raise ValueError("Preview recovery gameplay pixels changed.")
    if source_frame_path is not None:
        source = Path(source_frame_path)
        if not source.is_file() or sidecar.get("source_frame_sha256") != file_fingerprint(source):
            raise ValueError("Preview recovery source frame changed.")
        verification = sidecar.get("source_frame_verification")
        if (not isinstance(verification, Mapping)
                or verification.get("status") != "verified"
                or verification.get("sha256") != sidecar.get("source_frame_sha256")):
            raise ValueError("Preview recovery source-frame verification is invalid.")
    else:
        verification = sidecar.get("source_frame_verification")
        if not isinstance(verification, Mapping) or verification.get("status") != "unavailable":
            raise ValueError("Preview recovery source-frame verification is invalid.")


_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH_RE.fullmatch(value) is not None


def _strip_attached_regions(raw: Mapping[str, Any], recovery: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct possible immutable raw inputs from an attached recovery.

    ``attach`` always materializes ``regions`` even when the input mapping did
    not have that key.  The second candidate preserves the omission so both
    normal raw observations and sidecar-applied observations can be checked
    without trusting an attached field.
    """

    source = copy.deepcopy(dict(raw))
    source.pop("preview_recovery", None)
    attached = recovery.get("regions")
    if not isinstance(attached, Mapping):
        raise ValueError("Preview recovery attached regions are invalid.")
    regions = source.get("regions", {})
    if regions is None:
        regions = {}
    if not isinstance(regions, Mapping):
        raise ValueError("Preview recovery source regions are invalid.")
    remaining = copy.deepcopy(dict(regions))
    for name, observation in attached.items():
        if not isinstance(name, str) or not isinstance(observation, Mapping):
            raise ValueError("Preview recovery attached region is invalid.")
        if name not in remaining or remaining[name] != observation:
            raise ValueError("Preview recovery attached region is not source-bound.")
        del remaining[name]
    # A preview-named region that was not published by this recovery is
    # ambiguous: it could be a forged second overlay, so fail closed.
    if any(isinstance(name, str) and name.startswith("preview.") for name in remaining):
        raise ValueError("Preview recovery contains an unbound preview region.")
    source["regions"] = remaining
    candidates = [source]
    if not remaining and "regions" in source:
        without_regions = copy.deepcopy(source)
        del without_regions["regions"]
        candidates.append(without_regions)
    return candidates


def _validate_source_verification_metadata(raw: Mapping[str, Any], recovery: Mapping[str, Any]) -> None:
    """Validate sidecar verification metadata without touching the filesystem."""

    expected = raw.get("source_frame_sha256")
    verification = recovery.get("source_frame_verification")
    if verification is None:
        return  # Normal in-memory attachments do not carry sidecar metadata.
    if not isinstance(verification, Mapping):
        raise ValueError("Preview recovery source-frame verification is invalid.")
    status = verification.get("status")
    if status == "verified":
        if not _valid_hash(expected) or verification.get("sha256") != expected:
            raise ValueError("Preview recovery verified source hash is invalid.")
        if "expected_sha256" in verification:
            raise ValueError("Preview recovery verified source hash has an expected hash.")
    elif status == "unavailable":
        if expected is None:
            if verification != {"status": "unavailable", "reason": "raw_source_frame_hash_missing"}:
                raise ValueError("Preview recovery missing source hash proof is invalid.")
        else:
            if not _valid_hash(expected) or verification.get("expected_sha256") != expected:
                raise ValueError("Preview recovery unavailable source hash is invalid.")
            if "sha256" in verification:
                raise ValueError("Preview recovery unavailable source hash is invalid.")
    else:
        raise ValueError("Preview recovery source-frame verification status is invalid.")
    if "path" in verification:
        _safe_relative(verification.get("path"), name="source-frame verification")

    if "source_frame_evidence" in recovery:
        value = recovery.get("source_frame_evidence")
        if value is not None:
            _safe_relative(value, name="source-frame evidence")
    if "source_frame_id" in recovery:
        value = recovery.get("source_frame_id")
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError("Preview recovery source frame identity is invalid.")


def _validate_sidecar_metadata(raw: Mapping[str, Any], recovery: Mapping[str, Any],
                               requests: Sequence[Mapping[str, Any]]) -> None:
    """Validate optional fields that exist on loaded sidecars."""

    for key in ("evidence_sha256",):
        if key in recovery and not _valid_hash(recovery.get(key)):
            raise ValueError(f"Preview recovery {key} is invalid.")
    for key in ("recovery_engine_fingerprint",):
        if key in recovery and (not isinstance(recovery.get(key), str) or not recovery.get(key)):
            raise ValueError(f"Preview recovery {key} is invalid.")
    if "recovery_model_sha256" in recovery:
        models = recovery.get("recovery_model_sha256")
        if not isinstance(models, Mapping) or any(
                not isinstance(name, str) or not _valid_hash(value)
                for name, value in models.items()):
            raise ValueError("Preview recovery recovery model identity is invalid.")
    selection = recovery.get("selection")
    if selection is not None:
        if not isinstance(selection, Mapping) or selection.get("geometry_only") is not True:
            raise ValueError("Preview recovery selection metadata is invalid.")
        if selection.get("max_requests") != len(requests):
            raise ValueError("Preview recovery selection request count changed.")
        if selection.get("request_ids") != [item.get("id") for item in requests]:
            raise ValueError("Preview recovery selection request IDs changed.")


def validated_recovery(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a source-bound attached recovery, or ``None`` when invalid.

    This is deliberately a no-OCR path.  It strips the attached crop regions,
    binds the recovery to the remaining raw observation by ``raw_sha256``,
    regenerates requests/consensus/effects/phase, and checks every published
    value against that deterministic reconstruction.  It is used for both
    normal in-memory attachments and sidecars after :func:`apply`/``load``.
    """

    try:
        if not isinstance(raw, Mapping):
            return None
        recovery = raw.get("preview_recovery")
        if not isinstance(recovery, Mapping):
            return None
        if (recovery.get("version") != VERSION
                or recovery.get("schema_version") != SCHEMA
                or recovery.get("stage") != STAGE):
            return None
        candidates = _strip_attached_regions(raw, recovery)
        matching = [candidate for candidate in candidates
                    if recovery.get("raw_sha256") == fingerprint(candidate)]
        if len(matching) != 1:
            return None
        source = matching[0]

        for key in ("source_timestamp_ms", "evidence", "source_frame_sha256", "gameplay_sha256"):
            if recovery.get(key) != source.get(key):
                return None
        if "source_model_sha256" in recovery and recovery.get("source_model_sha256") != source.get("model_sha256"):
            return None
        if "source_engine_fingerprint" in recovery and recovery.get("source_engine_fingerprint") != source.get("engine_fingerprint"):
            return None
        if recovery.get("independent_observations") is not False:
            return None
        _validate_source_verification_metadata(source, recovery)

        requests = recovery.get("requests")
        if not isinstance(requests, list):
            return None
        for request in requests:
            if not isinstance(request, Mapping):
                return None
            _validate_sidecar_request(request)
        request_ids = [request.get("id") for request in requests]
        if len(set(request_ids)) != len(request_ids):
            return None
        _validate_sidecar_metadata(source, recovery, requests)

        # Rebuild the bounded request list from source geometry.  The selected
        # consensus threshold is caller configuration, so copy only that one
        # validated parameter before comparing the canonical request objects.
        expected_requests = candidate_requests(source, max_requests=len(requests))
        if len(expected_requests) != len(requests):
            return None
        for expected_request, request in zip(expected_requests, requests):
            minimum_consensus = request.get("minimum_consensus")
            expected_request["minimum_consensus"] = minimum_consensus
        if expected_requests != requests:
            return None

        observations = recovery.get("observations")
        if not isinstance(observations, list) or len(observations) != len(requests):
            return None
        if [item.get("request_id") if isinstance(item, Mapping) else None for item in observations] != request_ids:
            return None
        resolved = []
        for request, observation in zip(requests, observations):
            if not isinstance(observation, Mapping) or not isinstance(observation.get("views"), list):
                return None
            if any(not isinstance(view, Mapping) for view in observation["views"]):
                return None
            checked = _resolve_request(
                request, observation["views"],
                min_consensus=request["minimum_consensus"])
            if dict(observation) != checked:
                return None
            resolved.append(checked)

        expected = _build_recovery(source, expected_requests, resolved)
        # These are all source-derived or consensus-derived fields.  Keeping
        # the comparison exact prevents a caller from changing a selected
        # amount, role, geometry, phase basis, or typed effect in place.
        for key in ("status", "phase", "option", "regions", "effects", "modifier_effects",
                    "requests", "observations", "source_timestamp_ms", "evidence",
                    "source_frame_sha256", "gameplay_sha256", "raw_sha256",
                    "independent_observations"):
            if recovery.get(key) != expected.get(key):
                return None

        phase = recovery.get("phase")
        if not isinstance(phase, Mapping):
            return None
        if recovery.get("modifier_effects"):
            # Modifier values are accepted only with both canonical controls
            # and the same-frame Concert marker proof.  The proof is source
            # geometry, not an attached region or a prediction.
            if not phase.get("control_proof") or not phase.get("modifier_marker_proof"):
                return None
            if not _has_training_control(source) or not _has_concert_marker(source):
                return None
            if phase.get("control_proof") != _canonical_control_proof(source):
                return None
            if phase.get("modifier_marker_proof") != _concert_marker_lines(source):
                return None
        return copy.deepcopy(dict(recovery))
    except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError):
        return None


def apply(raw: Mapping[str, Any], sidecar: Mapping[str, Any], *, original: Mapping[str, Any] | None = None,
          evidence_path: str | Path | None = None, source_frame_path: str | Path | None = None,
          source_frame_id: str | None = None, source_frame_evidence: str | None = None) -> dict[str, Any]:
    original = raw if original is None else original
    validate(original, sidecar, evidence_path=evidence_path,
             source_frame_path=source_frame_path, source_frame_id=source_frame_id,
             source_frame_evidence=source_frame_evidence)
    return attach(raw, sidecar)


def load(raw: Mapping[str, Any], path: str | Path, *, original: Mapping[str, Any] | None = None,
         evidence_path: str | Path | None = None, source_frame_path: str | Path | None = None,
         source_frame_id: str | None = None, source_frame_evidence: str | None = None,
         allow_ocr: bool = False) -> dict[str, Any]:
    if allow_ocr:
        raise ValueError("Preview recovery cached load does not run OCR.")
    try:
        sidecar = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Preview recovery sidecar is not readable.") from exc
    if not isinstance(sidecar, dict):
        raise ValueError("Preview recovery sidecar must be a JSON object.")
    return apply(raw, sidecar, original=original, evidence_path=evidence_path,
                 source_frame_path=source_frame_path, source_frame_id=source_frame_id,
                 source_frame_evidence=source_frame_evidence)


__all__ = [
    "SCHEMA", "STAGE", "VERSION", "PANE_BOUNDS", "MAIN_ROW_BOXES",
    "MODIFIER_ROW_BOXES", "FAILURE_BOXES", "candidate_requests", "discover",
    "recover_in_memory", "recover", "fresh", "generate", "validate",
    "validated_recovery", "apply", "load", "attach",
]
