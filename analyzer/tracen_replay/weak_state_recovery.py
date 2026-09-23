"""Recover weak same-frame state fields with bounded source crops.

The neural cache is the immutable observation.  This module creates a small,
separately validated reread when a field in that observation is missing or
below the normal semantic confidence floor.  Every selected value comes from
the same gameplay image and a geometry request derived from that image's
layout.  The module never consults another frame, a balance residual, a
recording-specific expectation, or a later state.

Two entry points are intentionally separate:

``discover`` / ``recover``
    Build and process a bounded set of automatic crop candidates for a fresh
    source image.

``load`` / ``cached_replay``
    Validate a previously written sidecar and apply its source observations
    without constructing an OCR reader or reparsing the source image.

The sidecar is external to ``neural/*.json``.  Applying one returns a copy of
the raw observation and keeps all original detector lines intact.  Selected
regions are placed beside those lines so existing parsers can consume them
only after the caller explicitly opts into this source-bound supplement.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Sequence

from .layout import ORIGIN_X, pane_box, pane_size, place, place_y
from .reconcile import FIELDS
from .stats import BOXES as STAT_BOXES


SCHEMA = "tracen-replay/weak-state-recovery-v1"
STAGE = "weak_state_recovery"
VERSION = 1

# Coordinates are in the reader's coordinate system: the gameplay pane's
# pixels shifted right by 148. Fixed boxes are the PC pane's and are placed
# on the recording being read; the result banner's centre lies in this band,
# pinned to the centre of the clear area.
_BANNER_CENTRES = (250, 635, 850, 815)
STATE_CONFIDENCE_FLOOR = 97.0
PANEL_COMPONENT_CONFIDENCE_FLOOR = 97.0
PANEL_LOCALIZED_CONFIDENCE_FLOOR = 90.0
PANEL_ANCHOR_CONFIDENCE_FLOOR = 80.0
DEFAULT_MAX_REQUESTS = 16
DEFAULT_MIN_CONSENSUS = 2

PANEL_ANCHOR_ALIASES = frozenset({"points", "point", "poin"})
PANEL_HEADER_ALIASES = frozenset({"performance", "perfoornce", "perfornce"})
TRAINING_RESULT_VALUES = frozenset({"SUCCESS", "FAILURE"})
PANEL_FIELDS = ("dance", "passion", "vocal", "visual", "composure")
CURRENT_STAT_MAX = 2000
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_PLAIN_NUMBER = re.compile(r"^\d{1,4}$")
_PANEL_NUMBER = re.compile(r"^\d{1,3}$")
_PANEL_PROJECTED = re.compile(r"^\+?(\d{1,3})$")
_PANEL_CAP = re.compile(r"^/\s*\d{1,4}$")


def fingerprint(raw: Mapping[str, Any]) -> str:
    """Hash one exact JSON observation value for sidecar binding."""

    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def file_fingerprint(path: str | Path) -> str:
    """Hash the exact bytes of a source/evidence file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _safe_relative_path(value: Any, *, name: str) -> str | None:
    """Normalize a declared evidence path and reject namespace escapes."""

    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"Weak-state {name} path is invalid.")
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or ".." in path.parts:
        raise ValueError(f"Weak-state {name} path must be relative.")
    return path.as_posix().casefold()


def _declared_parent(value: str | None) -> str | None:
    if value is None:
        return None
    parts = PureWindowsPath(value).parts
    return parts[-2].casefold() if len(parts) > 1 else None


def _validate_path_namespace(sidecar: Mapping[str, Any], *, evidence_path: Path,
                             source_frame_path: Path | None) -> None:
    """Bind caller paths to the declared evidence namespace.

    Hashes prove bytes, while the declared relative paths identify which
    source namespace those bytes belong to.  For single-file declarations the
    evidence and source files must share their caller directory; nested
    declarations additionally require the immediate declared directory name.
    """

    declared_evidence = _safe_relative_path(sidecar.get("evidence"), name="evidence")
    declared_source = _safe_relative_path(
        sidecar.get("source_frame_evidence"), name="source-frame evidence")
    actual_evidence = evidence_path.resolve()
    actual_source = source_frame_path.resolve() if source_frame_path is not None else None
    expected_evidence_parent = _declared_parent(declared_evidence)
    if expected_evidence_parent is not None and (
            actual_evidence.parent.name.casefold() != expected_evidence_parent):
        raise ValueError("Weak-state gameplay evidence namespace changed.")
    if actual_source is not None:
        expected_source_parent = _declared_parent(declared_source)
        if expected_source_parent is not None and (
                actual_source.parent.name.casefold() != expected_source_parent):
            raise ValueError("Weak-state source-frame namespace changed.")
        if expected_evidence_parent is None and expected_source_parent is None:
            if actual_evidence.parent != actual_source.parent:
                raise ValueError("Weak-state evidence files are from different namespaces.")


def gameplay_fingerprint(path: str | Path) -> str:
    """Hash decoded gameplay pixels, independently of image encoding."""

    from .frame_cache import rgb_digest

    digest, size = rgb_digest(path)
    if size != pane_size():
        raise ValueError("Weak-state gameplay evidence must be the gameplay pane.")
    return digest


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite.")
    return result


def _box(value: Any, *, name: str, bounds: Sequence[float] | None = None) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{name} box is invalid.")
    result = [_number(item, name=f"{name} box coordinate") for item in value]
    left, top, right, bottom = result
    if not left < right or not top < bottom:
        raise ValueError(f"{name} box is empty.")
    bounds = pane_box() if bounds is None else bounds
    if not (bounds[0] <= left < right <= bounds[2]
            and bounds[1] <= top < bottom <= bounds[3]):
        raise ValueError(f"{name} box is outside the gameplay source pane.")
    return result


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", str(value).strip())


def _letters(value: Any) -> str:
    """Return OCR text's letters in a stable form for banner hints."""

    return re.sub(r"[^A-Za-z]", "", str(value)).upper()


def _edit_distance(left: str, right: str, *, limit: int = 2) -> int:
    """Compute a small bounded Levenshtein distance for OCR candidate hints."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        row_min = current[0]
        for right_index, right_char in enumerate(right, start=1):
            value = min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _training_result_hint(value: Any) -> bool:
    """Recognize only near-matches to a training result banner label.

    This is a candidate gate, rather than semantic parsing.  The crop reader
    still requires an exact ``SUCCESS`` or ``FAILURE`` result before a region
    can be selected, so a noisy detector label can never promote itself.
    """

    token = _letters(value)
    return bool(token) and any(
        _edit_distance(token, target, limit=2) <= 2
        for target in TRAINING_RESULT_VALUES
    )


def _confidence(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _eligible_line(line: Any) -> bool:
    if not isinstance(line, dict) or line.get("input_eligible") is False:
        return False
    role = str(line.get("role", "")).strip().casefold()
    return role not in {
        "source_context_line",
        "merged_current_projection_observation",
        "noisy_amount_candidate_excluded",
    } and not role.endswith("_excluded")


def _center_in(line: Mapping[str, Any], box: Sequence[float]) -> bool:
    value = line.get("box")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    try:
        left, top, right, bottom = [float(part) for part in value]
    except (TypeError, ValueError):
        return False
    return (box[0] <= (left + right) / 2 <= box[2]
            and box[1] <= (top + bottom) / 2 <= box[3])


def _expand_line_box(line: Mapping[str, Any], *, left: float = 5,
                     top: float = 3, right: float = 5,
                     bottom: float = 3) -> list[int] | None:
    """Expand an observed detector box while staying in the source pane."""

    raw = line.get("box")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        source = [float(part) for part in raw]
    except (TypeError, ValueError):
        return None
    pane = pane_box()
    candidate = [
        max(pane[0], source[0] - left),
        max(pane[1], source[1] - top),
        min(pane[2], source[2] + right),
        min(pane[3], source[3] + bottom),
    ]
    try:
        return [int(round(part)) for part in _box(candidate, name="source crop")]
    except ValueError:
        return None


def _request(*, request_id: str, region: str, channel: str,
             field: str | None, box: Sequence[float], kind: str,
             pattern: str, minimum_confidence: float,
             priority: int, basis: str, source_observation: Any = None,
             metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    checked = _box(box, name=request_id)
    value: dict[str, Any] = {
        "id": request_id,
        "region": region,
        "channel": channel,
        "field": field,
        "kind": kind,
        "box": [int(round(part)) for part in checked],
        "pattern": pattern,
        "minimum_confidence": float(minimum_confidence),
        "minimum_consensus": DEFAULT_MIN_CONSENSUS,
        "priority": priority,
        "geometry_basis": basis,
    }
    if isinstance(source_observation, dict):
        value["source_observation"] = copy.deepcopy(source_observation)
    if isinstance(metadata, Mapping):
        for key, item in metadata.items():
            if key not in value:
                value[key] = copy.deepcopy(item)
    return value


def _panel_rows() -> tuple[tuple[str, str, int, int], ...]:
    # Importing the layout keeps this module aligned with the existing parser
    # while avoiding a second, divergent coordinate table.
    from .vision import _performance_panel_rows

    return tuple(_performance_panel_rows())


def _component_candidates(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return []
    from .vision import _performance_panel_component_requests

    result = []
    for name, box, metadata in _performance_panel_component_requests(lines):
        component = metadata.get("component") if isinstance(metadata, dict) else None
        field = name.rsplit(".", 1)[-1]
        if component not in ("current", "projected"):
            continue
        result.append(_request(
            request_id=f"panel-component:{component}:{field}",
            region=name,
            channel="performance",
            field=field,
            box=box,
            kind="panel_component",
            pattern="panel_current" if component == "current" else "panel_projected",
            minimum_confidence=PANEL_COMPONENT_CONFIDENCE_FLOOR,
            priority=30,
            basis="same_row_merged_panel_line",
            metadata=metadata,
        ))
    return result


def _localized_candidates(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return []
    from .vision import _performance_panel_localized_requests

    result = []
    for name, box, metadata in _performance_panel_localized_requests(lines):
        field = name.rsplit(".", 1)[-1]
        result.append(_request(
            request_id=("panel-slot:" if name.startswith('performance_panel_localized_slot.')
                        else "panel-localized:") + field,
            region=name,
            channel="performance",
            field=field,
            box=box,
            kind="panel_localized_current",
            pattern="panel_current",
            minimum_confidence=PANEL_LOCALIZED_CONFIDENCE_FLOOR,
            priority=25,
            # The request states which geometry it came from; a crop widened
            # to the row's slot is not the fixed-row crop and must not be
            # relabelled as one on its way through recovery.
            basis=str(metadata.get("geometry_basis") or "fixed_row_panel_geometry"),
            metadata=metadata,
        ))
    return result


def _weak_panel_row_candidates(raw: Mapping[str, Any], existing: set[str]) -> list[dict[str, Any]]:
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return []
    result = []
    for field, _label, label_y, _cap_y in _panel_rows():
        name = f"performance_panel_localized_current.{field}"
        if name in existing:
            continue
        band = (160, label_y - 27, 275, label_y + 5)
        candidates = []
        for line in lines:
            if not _eligible_line(line) or not _center_in(line, band):
                continue
            if _PANEL_NUMBER.fullmatch(_normalized(line.get("text", ""))):
                candidates.append(line)
        if len(candidates) != 1:
            continue
        source = candidates[0]
        # The existing semantic path uses a 97 confidence floor.  A weak
        # source line is worth a fixed-row reread; a strong line is already
        # accepted and does not need a duplicate crop.
        if _confidence(source.get("confidence")) >= STATE_CONFIDENCE_FLOOR:
            continue
        # Keep the value glyphs tight while leaving a little horizontal room
        # for a clipped leading/trailing stroke.  Extra vertical background
        # tends to introduce the adjacent cap into this crop.
        box = _expand_line_box(source, left=5, top=0, right=5, bottom=2)
        if box is None:
            continue
        result.append(_request(
            request_id=f"panel-weak-row:{field}",
            region=name,
            channel="performance",
            field=field,
            box=box,
            kind="panel_localized_current",
            pattern="panel_current",
            minimum_confidence=PANEL_LOCALIZED_CONFIDENCE_FLOOR,
            priority=20,
            # The output region remains compatible with the parser's
            # fixed-row localized source contract.  The request's
            # ``source_observation`` records why this particular row needed
            # recovery, so no case-specific semantic tag is required here.
            basis="fixed_row_panel_geometry",
            source_observation=source,
            metadata={
                "role": "panel_localized_current",
                "input_eligible": True,
                "component": "current",
                "geometry_basis": "fixed_row_panel_geometry",
                "source_row_geometry": {
                    "field": field,
                    "label_y": label_y,
                    "box": list(box),
                },
                "preprocess": "panel_grayscale_autocontrast",
            },
        ))
    return result


def _weak_panel_anchor_candidates(raw: Mapping[str, Any], existing: set[str]) -> list[dict[str, Any]]:
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return []
    result = []
    rows = _panel_rows()
    for line in lines:
        if not _eligible_line(line):
            continue
        text = _normalized(line.get("text", "")).casefold()
        confidence = _confidence(line.get("confidence"))
        if text in PANEL_ANCHOR_ALIASES and confidence < 90:
            region = "weak_state_recovery.performance_panel_anchor.points"
            if region not in existing:
                box = _expand_line_box(line, left=0, top=2, right=5, bottom=2)
                if box is not None:
                    result.append(_request(
                        request_id="panel-anchor:points",
                        region=region,
                        channel="performance",
                        field=None,
                        box=box,
                        kind="panel_anchor_points",
                        pattern="points_header",
                        minimum_confidence=PANEL_ANCHOR_CONFIDENCE_FLOOR,
                        priority=10,
                        basis="same_frame_panel_heading",
                        source_observation=line,
                    ))
        elif text in PANEL_HEADER_ALIASES and (confidence < 90 or text not in {"performance"}):
            region = "weak_state_recovery.performance_panel_anchor.header"
            if region not in existing:
                box = _expand_line_box(line, left=0, top=2, right=5, bottom=2)
                if box is not None:
                    result.append(_request(
                        request_id="panel-anchor:header",
                        region=region,
                        channel="performance",
                        field=None,
                        box=box,
                        kind="panel_anchor_header",
                        pattern="panel_header",
                        minimum_confidence=PANEL_ANCHOR_CONFIDENCE_FLOOR,
                        priority=10,
                        basis="same_frame_panel_heading",
                        source_observation=line,
                    ))
        elif _PANEL_CAP.fullmatch(_normalized(line.get("text", ""))) and confidence < 90:
            box_value = line.get("box")
            if not isinstance(box_value, (list, tuple)) or len(box_value) != 4:
                continue
            try:
                center_y = (float(box_value[1]) + float(box_value[3])) / 2
            except (TypeError, ValueError):
                continue
            row = min(rows, key=lambda item: abs(item[3] - center_y))
            field, _label, _label_y, cap_y = row
            if abs(cap_y - center_y) > 28:
                continue
            region = f"weak_state_recovery.performance_panel_anchor.cap.{field}"
            if region in existing:
                continue
            # Keep the right edge of the slash-cap glyph.  A cap's trailing
            # digit is close to the detector box edge on the translucent
            # panel, and a crop that clips it can turn ``/400`` into noise.
            box = _expand_line_box(line, left=7, top=3, right=8, bottom=3)
            if box is None:
                continue
            result.append(_request(
                request_id=f"panel-anchor:cap:{field}",
                region=region,
                channel="performance",
                field=field,
                box=box,
                kind="panel_anchor_cap",
                pattern="panel_cap",
                minimum_confidence=PANEL_ANCHOR_CONFIDENCE_FLOOR,
                priority=10,
                basis="same_frame_panel_cap_geometry",
                source_observation=line,
            ))
    return result


def _weak_stat_candidates(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    if raw.get("current_grid") is not True:
        return []
    regions = raw.get("regions") if isinstance(raw.get("regions"), dict) else {}
    result = []
    for field, box in zip(FIELDS, STAT_BOXES):
        observation = regions.get(f"current.{field}")
        text = _normalized(observation.get("text", "")) if isinstance(observation, dict) else ""
        if _PLAIN_NUMBER.fullmatch(text) and _confidence(observation.get("confidence")) >= STATE_CONFIDENCE_FLOOR:
            continue
        result.append(_request(
            request_id=f"stat-current:{field}",
            region=f"current.{field}",
            channel="stats",
            field=field,
            box=place(box, "bc"),
            kind="current_stat",
            pattern="current_stat",
            minimum_confidence=90,
            priority=20,
            basis="fixed_main_stat_geometry",
            source_observation=observation,
            metadata={
                "role": "weak_state_current_crop",
                "input_eligible": True,
                "component": "current",
                "geometry_basis": "fixed_main_stat_geometry",
            },
        ))
    return result


def _training_result_candidates(raw: Mapping[str, Any], existing: set[str]) -> list[dict[str, Any]]:
    """Find a weak result banner on a visible training result screen.

    The result grid is the geometry gate.  Training preview screens can also
    contain a ``Failure`` percentage, but that indicator is attached to the
    selectable training option and is deliberately excluded by requiring the
    result grid plus the centered result-banner band.
    """

    if raw.get("result_grid") is not True:
        return []
    header = _normalized(raw.get("header", "")).casefold()
    if header != "training":
        return []
    lines = raw.get("lines")
    if not isinstance(lines, list):
        return []
    region = "weak_state_recovery.training_result_banner"
    if region in existing:
        return []
    # The result banner is centered below the result grid.  Keep this band
    # broad enough for scaled UI variants, while deriving the final crop from
    # the same-frame detector geometry.
    banner_band = place(_BANNER_CENTRES, "mc")
    candidates = []
    for line in lines:
        if not _eligible_line(line) or not _center_in(line, banner_band):
            continue
        if not _training_result_hint(line.get("text", "")):
            continue
        candidates.append(line)
    if len(candidates) != 1:
        return []
    source = candidates[0]
    # The detector's banner box already encloses the stylized label.  Adding
    # surrounding artwork can change the recognition result (the same source
    # frame can alternate between ``SUCCESS`` and ``SUOCESS``), so preserve
    # that source geometry and validate it against the pane bounds.
    try:
        box = [int(round(part)) for part in _box(source.get("box"), name=region)]
    except ValueError:
        return []
    return [_request(
        request_id="training-result-banner",
        region=region,
        channel="training_result",
        field="outcome",
        box=box,
        kind="training_result_banner",
        pattern="training_result_banner",
        minimum_confidence=90,
        priority=40,
        basis="same_frame_training_result_banner_geometry",
        source_observation=source,
        metadata={
            "role": "training_result_banner",
            "input_eligible": True,
            "geometry_basis": "same_frame_training_result_banner_geometry",
            # The result label is retained as an observation.  A caller must
            # apply its normal outcome corroboration before promoting it to a
            # semantic training outcome.
            "semantic_promotion": "none",
        },
    )]


def _deduplicate(requests: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    found: dict[tuple[str, str, tuple[int, ...]], dict[str, Any]] = {}
    for item in requests:
        key = (str(item.get("kind")), str(item.get("region")),
               tuple(item.get("box", ())))
        if key not in found:
            found[key] = copy.deepcopy(dict(item))
    return sorted(found.values(), key=lambda item: (
        -int(item.get("priority", 0)),
        str(item.get("channel", "")),
        str(item.get("field", "")),
        str(item.get("id", "")),
    ))


def candidate_requests(raw: Mapping[str, Any], *, max_requests: int | None = None) -> list[dict[str, Any]]:
    """Return bounded same-frame crop requests for weak fields.

    Candidate discovery only inspects the supplied raw observation.  It does
    not know a recording ID, timestamp, expected amount, or endpoint balance.
    """

    if not isinstance(raw, Mapping):
        raise TypeError("raw observation must be a mapping")
    existing = set((raw.get("regions") or {}).keys()) if isinstance(raw.get("regions"), dict) else set()
    requests = _training_result_candidates(raw, existing)
    requests += _weak_stat_candidates(raw)
    requests += _component_candidates(raw)
    requests += _localized_candidates(raw)
    requests += _weak_panel_row_candidates(raw, {item["region"] for item in requests})
    requests += _weak_panel_anchor_candidates(raw, {item["region"] for item in requests} | existing)
    requests = _deduplicate(requests)
    if max_requests is not None and (type(max_requests) is not int or max_requests < 0):
        raise ValueError("max_requests must be a non-negative integer")
    return requests if max_requests is None else requests[:max_requests]


def discover(raw: Mapping[str, Any], *, max_requests: int = DEFAULT_MAX_REQUESTS) -> dict[str, Any]:
    """Describe selected and deferred automatic candidates without OCR."""

    if type(max_requests) is not int or max_requests < 0:
        raise ValueError("max_requests must be a non-negative integer")
    all_requests = candidate_requests(raw, max_requests=None)
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


def _request_value(request: Mapping[str, Any], text: Any) -> Any:
    normalized = _normalized(text)
    kind = request.get("kind")
    if kind == "training_result_banner":
        # Candidate discovery tolerates a bounded OCR typo (for example
        # ``SUOCESS!``), but selection and replay accept only an exact banner
        # label from the fresh crop read.
        value = _letters(normalized)
        return value if value in TRAINING_RESULT_VALUES else None
    if kind in {"current_stat", "panel_localized_current", "panel_component"}:
        if kind == "panel_component" and request.get("pattern") == "panel_projected":
            match = _PANEL_PROJECTED.fullmatch(normalized)
            return int(match[1]) if match else None
        pattern = _PLAIN_NUMBER if kind == "current_stat" else _PANEL_NUMBER
        match = pattern.fullmatch(normalized)
        if not match:
            return None
        value = int(match[0])
        if kind == "current_stat" and value > CURRENT_STAT_MAX:
            return None
        return value
    if kind == "panel_anchor_cap":
        match = _PANEL_CAP.fullmatch(normalized)
        return int(re.sub(r"\D", "", match[0])) if match else None
    if kind == "panel_anchor_points":
        return "Points" if normalized.casefold() in PANEL_ANCHOR_ALIASES else None
    if kind == "panel_anchor_header":
        return "Performance" if normalized.casefold() in PANEL_HEADER_ALIASES else None
    return None


def _eligible_view_groups(request: Mapping[str, Any], views: Sequence[Mapping[str, Any]], *,
                          minimum: float) -> tuple[dict[str, list[dict[str, Any]]], bool, bool]:
    """Group eligible reads by parsed value and unique preprocessing variant.

    A repeated result carrying the same variant name is one OCR view, not a
    second witness.  A conflicting reuse of one variant is rejected as a
    malformed sidecar rather than silently choosing one of its values.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    seen_variants: dict[str, str] = {}
    duplicate_variant = False
    duplicate_conflict = False
    for view in views:
        value = _request_value(request, view.get("text"))
        if value is None or _confidence(view.get("confidence")) < minimum:
            continue
        variant = view.get("variant")
        if not isinstance(variant, str) or not variant.strip():
            continue
        variant = variant.strip()
        key = json.dumps(value, sort_keys=True)
        previous = seen_variants.get(variant)
        if previous is not None:
            duplicate_variant = True
            if previous != key:
                duplicate_conflict = True
            continue
        seen_variants[variant] = key
        grouped.setdefault(key, []).append(dict(view, parsed_value=value))
    return grouped, duplicate_conflict, duplicate_variant


def _variant_images(pane: Any, box: Sequence[int]) -> list[tuple[str, Any]]:
    """Create bounded OCR views from one exact source crop."""

    from PIL import Image, ImageOps
    import numpy as np

    left, top, right, bottom = [int(value) for value in box]
    array = np.asarray(pane.convert("RGB"))[top:bottom, left - ORIGIN_X:right - ORIGIN_X]
    if array.size == 0:
        raise ValueError("Weak-state source crop is empty.")
    raw = Image.fromarray(array, mode="RGB")
    gray = ImageOps.autocontrast(ImageOps.grayscale(raw))
    gray_up = gray.resize((gray.width * 3, gray.height * 3))
    views = [
        ("raw", np.asarray(raw)[:, :, ::-1]),
        ("gray_autocontrast", np.repeat(np.asarray(gray)[:, :, None], 3, axis=2)[:, :, ::-1]),
        ("gray_autocontrast_3x", np.repeat(np.asarray(gray_up)[:, :, None], 3, axis=2)[:, :, ::-1]),
    ]
    return views


def _engine_read(reader: Any, pane: Any, requests: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    """Read all bounded variants in one recognizer batch."""

    if hasattr(reader, "recognize_crops"):
        return reader.recognize_crops(pane, requests)
    engine = getattr(reader, "engine", None)
    input_type = getattr(reader, "TextRecInput", None)
    if engine is None or input_type is None:
        raise TypeError("weak-state reader must expose engine and TextRecInput")
    images = []
    labels = []
    for request in requests:
        for variant, image in _variant_images(pane, request["box"]):
            images.append(image)
            labels.append((request["id"], variant))
    if not images:
        return [[] for _ in requests]
    result = engine.text_rec(input_type(img=images))
    per_request: dict[str, list[dict[str, Any]]] = {str(item["id"]): [] for item in requests}
    for (request_id, variant), text, score in zip(labels, result.txts, result.scores):
        per_request[request_id].append({
            "variant": variant,
            "text": str(text),
            "confidence": round(float(score) * 100, 4),
        })
    return [per_request[str(item["id"])] for item in requests]


def _resolve_request(request: Mapping[str, Any], views: Sequence[Mapping[str, Any]], *, min_consensus: int) -> dict[str, Any]:
    minimum = _confidence(request.get("minimum_confidence"))
    rows = []
    for view in views:
        value = _request_value(request, view.get("text"))
        row = dict(view, parsed_value=value,
                   eligible=value is not None and _confidence(view.get("confidence")) >= minimum)
        rows.append(row)
    grouped, duplicate_conflict, duplicate_variant = _eligible_view_groups(
        request, rows, minimum=minimum)
    if duplicate_variant:
        raise ValueError("Weak-state crop views reuse an OCR variant.")
    selected = None
    status = "unresolved_no_eligible_crop_read"
    reason = status
    if duplicate_conflict:
        status = "unresolved_conflicting_crop_reads"
        reason = status
    elif len(grouped) == 1:
        alternatives = next(iter(grouped.values()))
        if len(alternatives) >= min_consensus:
            selected = max(alternatives, key=lambda item: _confidence(item.get("confidence")))
            status = "resolved_same_frame_crop_consensus"
            reason = None
        else:
            status = "unresolved_insufficient_crop_consensus"
            reason = status
    elif len(grouped) > 1:
        status = "unresolved_conflicting_crop_reads"
        reason = status
    result = {
        "request_id": request["id"],
        "region": request["region"],
        "box": list(request["box"]),
        "views": [dict(item) for item in rows],
        "status": status,
    }
    if reason is not None:
        result["reason"] = reason
    if selected is not None:
        result["selected"] = dict(selected)
    return result


def _selected_observation(request: Mapping[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    selected = resolved.get("selected")
    if not isinstance(selected, dict):
        return None
    value = selected.get("parsed_value")
    if value is None:
        return None
    observation: dict[str, Any] = {
        "text": selected.get("text", ""),
        "confidence": selected.get("confidence", 0),
        "box": list(request["box"]),
        "role": request.get("role", "weak_state_crop"),
        "input_eligible": True,
        "geometry_basis": request.get("geometry_basis"),
        "source_request_id": request["id"],
        "source_variant": selected.get("variant"),
        "parsed_value": value,
    }
    for key in ("component", "parent_observation", "source_row_geometry", "preprocess"):
        if key in request:
            observation[key] = copy.deepcopy(request[key])
    return observation


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"Refusing to overwrite weak-state sidecar: {path}")
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


def _source_verification(raw: Mapping[str, Any], source_frame_path: str | Path | None,
                         source_frame_evidence: str | None) -> dict[str, Any]:
    expected = raw.get("source_frame_sha256")
    if expected is None:
        return {"status": "unavailable", "reason": "raw_source_frame_hash_missing"}
    if not _valid_sha256(expected):
        raise ValueError("Weak-state source-frame hash is invalid.")
    _safe_relative_path(source_frame_evidence, name="source-frame evidence")
    if source_frame_path is None:
        result = {"status": "unavailable", "expected_sha256": expected}
        if source_frame_evidence:
            result["path"] = source_frame_evidence
        return result
    path = Path(source_frame_path)
    if not path.is_file():
        raise ValueError("Weak-state source-frame evidence is missing.")
    actual = file_fingerprint(path)
    if actual != expected:
        raise ValueError("Weak-state source-frame hash mismatch.")
    result = {"status": "verified", "sha256": actual}
    if source_frame_evidence:
        result["path"] = source_frame_evidence
    return result


def _build_sidecar(raw: Mapping[str, Any], evidence_path: Path,
                   requests: Sequence[Mapping[str, Any]],
                   resolved: Sequence[Mapping[str, Any]], *,
                   source_frame_path: str | Path | None,
                   source_frame_evidence: str | None,
                   source_frame_id: str | None,
                   reader: Any) -> dict[str, Any]:
    regions: dict[str, dict[str, Any]] = {}
    indexed = {item["request_id"]: item for item in resolved}
    for request in requests:
        selected = _selected_observation(request, indexed.get(request["id"], {}))
        if selected is not None:
            regions[request["region"]] = selected
    source_check = _source_verification(raw, source_frame_path, source_frame_evidence)
    recovery_models = copy.deepcopy(getattr(reader, "models", {}))
    recovery_engine = getattr(reader, "fingerprint", None)
    if not recovery_models or not recovery_engine:
        raise ValueError("Weak-state recovery reader identity is missing.")
    sidecar: dict[str, Any] = {
        "version": VERSION,
        "schema_version": SCHEMA,
        "stage": STAGE,
        "source_frame_id": source_frame_id,
        "source_timestamp_ms": raw.get("source_timestamp_ms"),
        "evidence": raw.get("evidence"),
        "evidence_sha256": file_fingerprint(evidence_path),
        "gameplay_sha256": gameplay_fingerprint(evidence_path),
        "source_frame_evidence": source_frame_evidence,
        "source_frame_sha256": raw.get("source_frame_sha256"),
        "source_frame_verification": source_check,
        "raw_sha256": fingerprint(raw),
        "source_model_sha256": copy.deepcopy(raw.get("model_sha256")),
        "source_engine_fingerprint": raw.get("engine_fingerprint"),
        "recovery_model_sha256": recovery_models,
        "recovery_engine_fingerprint": recovery_engine,
        "independent_observations": False,
        "selection": {
            "max_requests": len(requests),
            "request_ids": [item["id"] for item in requests],
            "geometry_only": True,
        },
        "requests": [copy.deepcopy(dict(item)) for item in requests],
        "observations": [copy.deepcopy(dict(item)) for item in resolved],
        "regions": regions,
    }
    source_sha = raw.get("source_sha256")
    if source_sha is not None:
        sidecar["source_sha256"] = source_sha
    # Result-card occlusion is derived from the exact gameplay pixels, not
    # from a missing/weak OCR field.  Keep the proof in the sidecar so fresh
    # generation and cached replay expose the same typed metadata.
    if raw.get("gameplay_sha256"):
        from PIL import Image
        from .stat_state_details import read_result_card_occlusion
        with Image.open(evidence_path) as pane:
            occlusion = read_result_card_occlusion(raw, pane)
        if occlusion:
            sidecar["result_card_occlusion"] = occlusion
    return sidecar


def recover(raw: Mapping[str, Any], evidence_path: str | Path, *,
            reader: Any = None, model_dir: str | Path = ".local/models/rapidocr",
            max_requests: int = DEFAULT_MAX_REQUESTS,
            min_consensus: int = DEFAULT_MIN_CONSENSUS,
            source_frame_path: str | Path | None = None,
            source_frame_evidence: str | None = None,
            source_frame_id: str | None = None) -> dict[str, Any]:
    """Run the bounded fresh-source crop pipeline and return a sidecar value."""

    if not isinstance(raw, Mapping):
        raise TypeError("raw observation must be a mapping")
    if type(min_consensus) is not int or min_consensus < 1:
        raise ValueError("min_consensus must be a positive integer")
    requests = candidate_requests(raw, max_requests=max_requests)
    for request in requests:
        request["minimum_consensus"] = min_consensus
    evidence_path = Path(evidence_path)
    if not evidence_path.is_file():
        raise ValueError("Weak-state gameplay evidence is missing.")
    expected_pixels = raw.get("gameplay_sha256")
    actual_pixels = gameplay_fingerprint(evidence_path)
    if expected_pixels and actual_pixels != expected_pixels:
        raise ValueError("Weak-state gameplay pixels changed.")
    if reader is None:
        from .vision import NeuralReader

        reader = NeuralReader(model_dir)
    if requests:
        from .frame_cache import open_rgb

        views = _engine_read(reader, open_rgb(evidence_path), requests)
    else:
        views = [[] for _ in requests]
    resolved = [
        _resolve_request(request, item, min_consensus=min_consensus)
        for request, item in zip(requests, views)
    ]
    sidecar = _build_sidecar(
        raw, evidence_path, requests, resolved,
        source_frame_path=source_frame_path,
        source_frame_evidence=source_frame_evidence,
        source_frame_id=source_frame_id,
        reader=reader,
    )
    validate(
        raw, sidecar, evidence_path=evidence_path,
        source_frame_path=source_frame_path,
        source_frame_id=source_frame_id,
        source_frame_evidence=source_frame_evidence,
    )
    return sidecar


def generate(raw_path: str | Path, evidence_path: str | Path, output_path: str | Path, *,
             reader: Any = None, model_dir: str | Path = ".local/models/rapidocr",
             max_requests: int = DEFAULT_MAX_REQUESTS,
             min_consensus: int = DEFAULT_MIN_CONSENSUS,
             source_frame_path: str | Path | None = None,
             source_frame_evidence: str | None = None,
             source_frame_id: str | None = None) -> dict[str, Any]:
    """Generate and atomically publish one new weak-state sidecar."""

    raw_path = Path(raw_path)
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Weak-state raw observation is not readable.") from exc
    if not isinstance(raw, dict):
        raise ValueError("Weak-state raw observation must be a JSON object.")
    sidecar = recover(
        raw, evidence_path, reader=reader, model_dir=model_dir,
        max_requests=max_requests, min_consensus=min_consensus,
        source_frame_path=source_frame_path,
        source_frame_evidence=source_frame_evidence,
        source_frame_id=source_frame_id,
    )
    _write_json(output_path, sidecar)
    return sidecar


def _validate_request(request: Mapping[str, Any]) -> None:
    required = ("id", "region", "channel", "field", "kind", "box",
                "pattern", "minimum_confidence", "minimum_consensus",
                "priority", "geometry_basis")
    for key in required:
        if key not in request:
            raise ValueError(f"Weak-state request missing {key}.")
    if not isinstance(request["id"], str) or not request["id"]:
        raise ValueError("Weak-state request ID is invalid.")
    if not isinstance(request["region"], str) or not request["region"]:
        raise ValueError("Weak-state request region is invalid.")
    _box(request["box"], name=f"weak-state request {request['id']}")
    if _confidence(request["minimum_confidence"]) <= 0:
        raise ValueError("Weak-state request confidence floor is invalid.")
    if type(request["minimum_consensus"]) is not int or not 1 <= request["minimum_consensus"] <= 5:
        raise ValueError("Weak-state request consensus floor is invalid.")
    if type(request["priority"]) is not int or not 0 <= request["priority"] <= 100:
        raise ValueError("Weak-state request priority is invalid.")
    _validate_request_contract(request)


def _validate_request_contract(request: Mapping[str, Any]) -> None:
    """Reject request aliases and crops outside the shared UI geometry.

    Sidecars are data, so the validator must not accept a caller-renamed field
    or an arbitrary crop simply because its OCR text happens to parse.  These
    contracts describe the stable parser schema and layout, not a recording,
    timestamp, or expected value.
    """

    kind = request.get("kind")
    field = request.get("field")
    contract: dict[str, Any]
    if kind == "current_stat":
        if field not in FIELDS:
            raise ValueError("Weak-state current-stat field alias is invalid.")
        index = FIELDS.index(field)
        contract = {
            "id": f"stat-current:{field}",
            "region": f"current.{field}",
            "channel": "stats",
            "pattern": "current_stat",
            "geometry_basis": "fixed_main_stat_geometry",
            "minimum_confidence": 90.0,
            "priority": 20,
            "component": "current",
        }
        if list(request.get("box", ())) != list(place(STAT_BOXES[index], "bc")):
            raise ValueError("Weak-state current-stat crop geometry is invalid.")
    elif kind == "panel_component":
        component = request.get("component")
        if component not in {"current", "projected"} or field not in PANEL_FIELDS:
            raise ValueError("Weak-state panel-component alias is invalid.")
        contract = {
            "id": f"panel-component:{component}:{field}",
            "region": f"performance_panel_{component}.{field}",
            "channel": "performance",
            "pattern": "panel_current" if component == "current" else "panel_projected",
            "geometry_basis": "same_row_merged_panel_line",
            "minimum_confidence": PANEL_COMPONENT_CONFIDENCE_FLOOR,
            "priority": 30,
            "component": component,
        }
        _validate_panel_crop_geometry(request, field, component=component)
    elif kind == "panel_localized_current":
        if field not in PANEL_FIELDS:
            raise ValueError("Weak-state localized panel field alias is invalid.")
        request_id = request.get("id")
        if request_id not in {f"panel-localized:{field}", f"panel-weak-row:{field}",
                              f"panel-slot:{field}"}:
            raise ValueError("Weak-state localized panel request alias is invalid.")
        # A slot crop is one row's own detector line widened across the value
        # slot, not the fixed-row crop, and it carries its own region and
        # geometry so the two can never be read as each other.
        slot = request_id.startswith("panel-slot:")
        contract = {
            "id": request_id,
            "region": (f"performance_panel_localized_slot.{field}" if slot
                       else f"performance_panel_localized_current.{field}"),
            "channel": "performance",
            "pattern": "panel_current",
            "geometry_basis": ("slot_widened_value_geometry" if slot
                               else "fixed_row_panel_geometry"),
            "minimum_confidence": PANEL_LOCALIZED_CONFIDENCE_FLOOR,
            "priority": 20 if request_id.startswith("panel-weak-row:") else 25,
            "component": "current",
        }
        _validate_panel_crop_geometry(request, field, component="current")
    elif kind == "panel_anchor_cap":
        if field not in PANEL_FIELDS:
            raise ValueError("Weak-state panel-cap field alias is invalid.")
        contract = {
            "id": f"panel-anchor:cap:{field}",
            "region": f"weak_state_recovery.performance_panel_anchor.cap.{field}",
            "channel": "performance",
            "pattern": "panel_cap",
            "geometry_basis": "same_frame_panel_cap_geometry",
            "minimum_confidence": PANEL_ANCHOR_CONFIDENCE_FLOOR,
            "priority": 10,
        }
        _validate_panel_cap_geometry(request, field)
    elif kind == "panel_anchor_points":
        contract = {
            "id": "panel-anchor:points",
            "region": "weak_state_recovery.performance_panel_anchor.points",
            "channel": "performance",
            "pattern": "points_header",
            "geometry_basis": "same_frame_panel_heading",
            "minimum_confidence": PANEL_ANCHOR_CONFIDENCE_FLOOR,
            "priority": 10,
        }
        _validate_panel_heading_geometry(request)
    elif kind == "panel_anchor_header":
        contract = {
            "id": "panel-anchor:header",
            "region": "weak_state_recovery.performance_panel_anchor.header",
            "channel": "performance",
            "pattern": "panel_header",
            "geometry_basis": "same_frame_panel_heading",
            "minimum_confidence": PANEL_ANCHOR_CONFIDENCE_FLOOR,
            "priority": 10,
        }
        _validate_panel_heading_geometry(request)
    elif kind == "training_result_banner":
        if field != "outcome":
            raise ValueError("Weak-state training-result field alias is invalid.")
        contract = {
            "id": "training-result-banner",
            "region": "weak_state_recovery.training_result_banner",
            "channel": "training_result",
            "pattern": "training_result_banner",
            "geometry_basis": "same_frame_training_result_banner_geometry",
            "minimum_confidence": 90.0,
            "priority": 40,
            "semantic_promotion": "none",
        }
        left, top, right, bottom = [float(value) for value in request["box"]]
        width, height = right - left, bottom - top
        band = place(_BANNER_CENTRES, "mc")
        if not (band[0] <= (left + right) / 2 <= band[2]
                and band[1] <= (top + bottom) / 2 <= band[3]
                and 40 <= width <= 600 and 20 <= height <= 180):
            raise ValueError("Weak-state training-result banner geometry is invalid.")
    else:
        raise ValueError("Weak-state request kind is invalid.")

    for key, expected in contract.items():
        if request.get(key) != expected:
            raise ValueError(f"Weak-state request {key} alias or range is invalid.")


def _validate_panel_crop_geometry(request: Mapping[str, Any], field: str, *, component: str) -> None:
    row = next(item for item in _panel_rows() if item[0] == field)
    _field, _label, label_y, _cap_y = row
    left, top, right, bottom = [float(value) for value in request["box"]]
    width, height = right - left, bottom - top
    if not (160 <= left < right <= 335 and 20 <= width <= 180
            and 10 <= height <= 80
            and label_y - 35 <= (top + bottom) / 2 <= label_y + 12):
        raise ValueError("Weak-state panel crop geometry is invalid.")


def _validate_panel_cap_geometry(request: Mapping[str, Any], field: str) -> None:
    row = next(item for item in _panel_rows() if item[0] == field)
    _field, _label, _label_y, cap_y = row
    left, top, right, bottom = [float(value) for value in request["box"]]
    width, height = right - left, bottom - top
    if not (160 <= left < right <= 335 and 20 <= width <= 140
            and 10 <= height <= 70
            and cap_y - 35 <= (top + bottom) / 2 <= cap_y + 35):
        raise ValueError("Weak-state panel-cap geometry is invalid.")


def _validate_panel_heading_geometry(request: Mapping[str, Any]) -> None:
    left, top, right, bottom = [float(value) for value in request["box"]]
    width, height = right - left, bottom - top
    if not (148 <= left < right <= 360 and 10 <= width <= 220
            and 10 <= height <= 70 and place_y(235, "t") <= (top + bottom) / 2 <= place_y(325, "t")):
        raise ValueError("Weak-state panel-heading geometry is invalid.")


def _validate_resolved(request: Mapping[str, Any], resolved: Mapping[str, Any], regions: Mapping[str, Any]) -> None:
    if resolved.get("request_id") != request.get("id"):
        raise ValueError("Weak-state observation request identity changed.")
    if list(resolved.get("box", [])) != list(request.get("box", [])):
        raise ValueError("Weak-state observation crop geometry changed.")
    views = resolved.get("views")
    if not isinstance(views, list):
        raise ValueError("Weak-state observation views are invalid.")
    minimum = _confidence(request.get("minimum_confidence"))
    grouped, duplicate_conflict, duplicate_variant = _eligible_view_groups(
        request, views, minimum=minimum)
    if duplicate_variant:
        raise ValueError("Weak-state crop views reuse an OCR variant.")
    minimum_consensus = request.get("minimum_consensus", DEFAULT_MIN_CONSENSUS)
    if type(minimum_consensus) is not int or minimum_consensus < 1:
        raise ValueError("Weak-state request consensus floor is invalid.")
    selected = resolved.get("selected")
    region = request["region"]
    if selected is None:
        if (not duplicate_conflict and len(grouped) == 1
                and len(next(iter(grouped.values()))) >= minimum_consensus):
            raise ValueError("Weak-state sidecar omitted a resolvable crop consensus.")
        if region in regions:
            raise ValueError("Weak-state unresolved request has a selected region.")
        return
    if not isinstance(selected, dict):
        raise ValueError("Weak-state selected observation is invalid.")
    parsed = _request_value(request, selected.get("text"))
    if parsed is None or selected.get("parsed_value") != parsed:
        raise ValueError("Weak-state selected value is not source-readable.")
    key = json.dumps(parsed, sort_keys=True)
    if (duplicate_conflict or len(grouped) != 1 or key not in grouped
            or len(grouped[key]) < minimum_consensus):
        raise ValueError("Weak-state selected value lacks same-frame crop consensus.")
    if _confidence(selected.get("confidence")) < _confidence(request.get("minimum_confidence")):
        raise ValueError("Weak-state selected observation is below its confidence floor.")
    matching = [item for item in views
                if item.get("variant") == selected.get("variant")
                and item.get("text") == selected.get("text")
                and item.get("parsed_value") == parsed]
    if not matching:
        raise ValueError("Weak-state selected observation is absent from its views.")
    if region not in regions:
        raise ValueError("Weak-state selected region is missing.")
    selected_region = regions[region]
    if not isinstance(selected_region, dict):
        raise ValueError("Weak-state selected region is invalid.")
    if _request_value(request, selected_region.get("text")) != parsed:
        raise ValueError("Weak-state selected region value changed.")
    if list(selected_region.get("box", [])) != list(request.get("box", [])):
        raise ValueError("Weak-state selected region geometry changed.")


def _validate_source_verification_metadata(sidecar: Mapping[str, Any],
                                           source_status: Mapping[str, Any]) -> None:
    """Validate every nested source-verification field, not just its status."""

    expected = sidecar.get("source_frame_sha256")
    if not _valid_sha256(expected):
        raise ValueError("Weak-state source-frame hash is invalid.")
    status = source_status.get("status")
    declared_path = sidecar.get("source_frame_evidence")
    allowed = {"status", "expected_sha256", "sha256", "path"}
    if set(source_status) - allowed:
        raise ValueError("Weak-state source-frame verification metadata is unrecognized.")
    nested_path = source_status.get("path")
    if nested_path is not None:
        normalized = _safe_relative_path(nested_path, name="source-frame verification")
        declared = _safe_relative_path(declared_path, name="source-frame evidence")
        if declared is None or normalized != declared:
            raise ValueError("Weak-state source-frame verification path changed.")
    if status == "verified":
        if source_status.get("sha256") != expected:
            raise ValueError("Weak-state source-frame verification hash changed.")
        if "expected_sha256" in source_status:
            raise ValueError("Weak-state verified source-frame metadata is malformed.")
    elif status == "unavailable":
        if source_status.get("expected_sha256") != expected:
            raise ValueError("Weak-state unavailable source-frame hash changed.")
        if "sha256" in source_status:
            raise ValueError("Weak-state unavailable source-frame metadata is malformed.")
    else:
        raise ValueError("Weak-state source-frame verification status is invalid.")


def validate(raw: Mapping[str, Any], sidecar: Mapping[str, Any], *,
             evidence_path: str | Path | None = None,
             source_frame_path: str | Path | None = None,
             source_frame_id: str | None = None,
             source_frame_evidence: str | None = None) -> None:
    """Validate source, model, image, request, and crop provenance."""

    if not isinstance(raw, Mapping) or not isinstance(sidecar, Mapping):
        raise ValueError("Weak-state validation requires mapping inputs.")
    if sidecar.get("version") != VERSION or sidecar.get("schema_version") != SCHEMA:
        raise ValueError("Weak-state sidecar version mismatch.")
    if sidecar.get("stage") != STAGE:
        raise ValueError("Weak-state sidecar stage mismatch.")
    for key in (
        "source_timestamp_ms", "evidence", "evidence_sha256", "gameplay_sha256",
        "source_frame_sha256", "raw_sha256", "source_model_sha256",
        "source_engine_fingerprint", "recovery_model_sha256",
        "recovery_engine_fingerprint", "independent_observations",
        "requests", "observations", "regions", "source_frame_verification",
    ):
        if key not in sidecar:
            raise ValueError(f"Weak-state sidecar missing {key} provenance.")
    if sidecar.get("raw_sha256") != fingerprint(raw):
        raise ValueError("Weak-state raw source JSON changed.")
    for key in ("source_timestamp_ms", "evidence", "source_frame_sha256", "gameplay_sha256"):
        raw_key = key
        if sidecar.get(key) != raw.get(raw_key):
            raise ValueError(f"Weak-state {key} changed.")
    if sidecar.get("source_model_sha256") != raw.get("model_sha256"):
        raise ValueError("Weak-state source model identity changed.")
    if sidecar.get("source_engine_fingerprint") != raw.get("engine_fingerprint"):
        raise ValueError("Weak-state source engine identity changed.")
    if sidecar.get("independent_observations") is not False:
        raise ValueError("Weak-state sidecar cannot claim independent observations.")
    if not sidecar.get("source_engine_fingerprint") or not sidecar.get("recovery_engine_fingerprint"):
        raise ValueError("Weak-state engine identity is missing.")
    if not sidecar.get("source_model_sha256") or not sidecar.get("recovery_model_sha256"):
        raise ValueError("Weak-state model identity is missing.")
    declared_frame_id = sidecar.get("source_frame_id")
    if declared_frame_id is not None:
        if (not isinstance(declared_frame_id, str) or not declared_frame_id
                or source_frame_id is None or declared_frame_id != source_frame_id):
            raise ValueError("Weak-state frame identity is not bound to this replay.")
    elif source_frame_id is not None:
        raise ValueError("Weak-state frame identity changed.")
    declared_source_evidence = sidecar.get("source_frame_evidence")
    if declared_source_evidence is not None:
        _safe_relative_path(declared_source_evidence, name="source-frame evidence")
        if source_frame_evidence is None or declared_source_evidence != source_frame_evidence:
            raise ValueError("Weak-state source-frame evidence path is not bound to this replay.")
    elif source_frame_evidence is not None:
        raise ValueError("Weak-state source-frame evidence path changed.")
    source_status = sidecar.get("source_frame_verification")
    if not isinstance(source_status, dict):
        raise ValueError("Weak-state source-frame verification status is invalid.")
    _validate_source_verification_metadata(sidecar, source_status)
    if source_status.get("status") == "verified" and source_frame_path is None:
        raise ValueError("Weak-state verified source-frame replay requires its source-frame path.")
    if evidence_path is None:
        raise ValueError("Weak-state validation requires gameplay evidence.")
    evidence_path = Path(evidence_path)
    if not evidence_path.is_file():
        raise ValueError("Weak-state gameplay evidence is missing.")
    _validate_path_namespace(
        sidecar, evidence_path=evidence_path,
        source_frame_path=Path(source_frame_path) if source_frame_path is not None else None,
    )
    if source_frame_path is not None:
        path = Path(source_frame_path)
        if not path.is_file():
            raise ValueError("Weak-state source-frame evidence is missing.")
        expected = sidecar.get("source_frame_sha256")
        if file_fingerprint(path) != expected:
            raise ValueError("Weak-state source-frame hash mismatch.")
        if source_status.get("status") != "verified":
            raise ValueError("Weak-state source-frame verification is incomplete.")
    if evidence_path is not None:
        path = Path(evidence_path)
        if file_fingerprint(path) != sidecar.get("evidence_sha256"):
            raise ValueError("Weak-state gameplay evidence changed.")
        if gameplay_fingerprint(path) != sidecar.get("gameplay_sha256"):
            raise ValueError("Weak-state gameplay pixels changed.")
    requests = sidecar.get("requests")
    observations = sidecar.get("observations")
    regions = sidecar.get("regions")
    if not isinstance(requests, list) or not isinstance(observations, list) or not isinstance(regions, dict):
        raise ValueError("Weak-state request or observation collection is invalid.")
    request_by_id = {}
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("Weak-state request is invalid.")
        _validate_request(request)
        if request["id"] in request_by_id:
            raise ValueError("Weak-state request IDs are duplicated.")
        request_by_id[request["id"]] = request
    observation_by_id = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("Weak-state crop observation is invalid.")
        request_id = observation.get("request_id")
        if request_id not in request_by_id or request_id in observation_by_id:
            raise ValueError("Weak-state crop observation request identity is invalid.")
        observation_by_id[request_id] = observation
    if set(observation_by_id) != set(request_by_id):
        raise ValueError("Weak-state request and observation sets differ.")
    request_regions = set()
    for request in requests:
        request_regions.add(request["region"])
        _validate_resolved(request, observation_by_id[request["id"]], regions)
    if not set(regions).issubset(request_regions):
        raise ValueError("Weak-state sidecar contains an undeclared region.")


def apply(raw: Mapping[str, Any], sidecar: Mapping[str, Any], *,
          original: Mapping[str, Any] | None = None,
          evidence_path: str | Path | None = None,
          source_frame_path: str | Path | None = None,
          source_frame_id: str | None = None,
          source_frame_evidence: str | None = None) -> dict[str, Any]:
    """Apply a validated sidecar to a copy without changing detector lines."""

    original = raw if original is None else original
    if evidence_path is None:
        raise ValueError("Weak-state replay requires gameplay evidence for validation.")
    validate(
        original, sidecar, evidence_path=evidence_path,
        source_frame_path=source_frame_path, source_frame_id=source_frame_id,
        source_frame_evidence=source_frame_evidence,
    )
    result = copy.deepcopy(dict(raw))
    # Recompute this small pixel proof against the validated gameplay evidence
    # so an old sidecar can gain the metadata without rerunning OCR.  The
    # derived result is source-bound to the same immutable image checked above;
    # sidecar text never becomes an occlusion assertion by itself.
    # Clear any raw/cached assertion first.  A readable replacement frame must
    # remove stale occlusion metadata instead of carrying it forward.
    result.pop("result_card_occlusion", None)
    if original.get("gameplay_sha256"):
        from PIL import Image
        from .stat_state_details import read_result_card_occlusion
        with Image.open(evidence_path) as pane:
            occlusion = read_result_card_occlusion(original, pane)
        if occlusion:
            result["result_card_occlusion"] = occlusion
    regions = copy.deepcopy(raw.get("regions", {})) if isinstance(raw.get("regions"), dict) else {}
    applied = []
    retained = []
    unresolved = []
    conflicting: list[dict[str, Any]] = []
    source_bound_observations: dict[str, dict[str, Any]] = {}
    for request, observation in zip(sidecar["requests"], sidecar["observations"]):
        selected = observation.get("selected")
        if not isinstance(selected, dict):
            unresolved.append({"request_id": request["id"], "region": request["region"],
                               "reason": observation.get("reason", observation.get("status"))})
            continue
        parsed = _request_value(request, selected.get("text"))
        if observation.get("status") == "resolved_same_frame_crop_consensus":
            views = observation.get("views")
            if isinstance(views, list):
                source_bound_observations[request["region"]] = {
                    "request_id": request["id"],
                    "region": request["region"],
                    "kind": request.get("kind"),
                    "field": request.get("field"),
                    "box": copy.deepcopy(request.get("box")),
                    "minimum_confidence": request.get("minimum_confidence"),
                    "minimum_consensus": request.get("minimum_consensus"),
                    "basis": "source_bound_same_frame_crop_consensus",
                    "selected": copy.deepcopy(selected),
                    "views": copy.deepcopy(views),
                    "source_frame_id": sidecar.get("source_frame_id"),
                    "source_timestamp_ms": sidecar.get("source_timestamp_ms"),
                    "evidence": sidecar.get("evidence"),
                    "source_frame_evidence": sidecar.get("source_frame_evidence"),
                    "source_frame_sha256": sidecar.get("source_frame_sha256"),
                    "gameplay_sha256": sidecar.get("gameplay_sha256"),
                    "raw_sha256": sidecar.get("raw_sha256"),
                    "source_frame_verification": copy.deepcopy(
                        sidecar.get("source_frame_verification")),
                }
        existing = regions.get(request["region"])
        if isinstance(existing, dict):
            existing_value = _request_value(request, existing.get("text"))
            if existing_value is not None and existing_value != parsed:
                # A region attached by an earlier weak-state replay must agree
                # with this sidecar: two weak-state envelopes for one frame
                # that disagree are inconsistent inputs, not evidence.
                if isinstance(existing.get("source_request_id"), str):
                    raise ValueError(f"Weak-state {request['region']} disagrees with an existing region.")
                # Another refinement stage read the same crop from one view
                # (for example the merged performance-panel line split).  The
                # OCR engine is not bit-identical across passes, so the two
                # source-bound reads can differ.  Keep the read with the
                # stronger evidence and record the other; never abort the job.
                existing_confidence = _confidence(existing.get("confidence"))
                selected_confidence = _confidence(selected.get("confidence"))
                record = {
                    "region": request["region"], "request_id": request["id"],
                    "existing_text": existing.get("text"), "existing_confidence": existing_confidence,
                    "existing_role": existing.get("role"), "sidecar_text": selected.get("text"),
                    "sidecar_confidence": selected_confidence,
                }
                if selected_confidence < existing_confidence:
                    record["kept"] = "existing"
                    conflicting.append(record)
                    retained.append(request["region"])
                    continue
                record["kept"] = "sidecar"
                conflicting.append(record)
        # A weak detector line can already carry the same panel value.  Adding
        # a second localized line to ``regions`` would make the parser see two
        # observations for one row and (correctly) abstain.  Keep the raw line
        # authoritative in that case and expose the stronger crop only in the
        # recovery metadata.  Missing rows still receive a parser-compatible
        # localized region below.
        if request.get("kind") == "panel_localized_current":
            field = request.get("field")
            row = next((item for item in _panel_rows() if item[0] == field), None)
            if row is not None:
                _field, _label, label_y, _cap_y = row
                band = (160, label_y - 27, 275, label_y + 5)
                same_source = [
                    line for line in (raw.get("lines") or [])
                    if _eligible_line(line)
                    and _center_in(line, band)
                    and _PANEL_NUMBER.fullmatch(_normalized(line.get("text", "")))
                    and _request_value(request, line.get("text")) == parsed
                ]
                if same_source:
                    retained.append(request["region"])
                    continue
        region = _selected_observation(request, observation)
        if region is None:
            continue
        regions[request["region"]] = region
        applied.append(request["region"])
    result["regions"] = regions
    result["weak_state_recovery"] = {
        "version": VERSION,
        "schema_version": SCHEMA,
        "stage": STAGE,
        "source_frame_id": sidecar.get("source_frame_id"),
        "source_timestamp_ms": sidecar.get("source_timestamp_ms"),
        "evidence": sidecar.get("evidence"),
        "source_frame_sha256": sidecar.get("source_frame_sha256"),
        "gameplay_sha256": sidecar.get("gameplay_sha256"),
        "raw_sha256": sidecar.get("raw_sha256"),
        "source_model_sha256": copy.deepcopy(sidecar.get("source_model_sha256")),
        "source_engine_fingerprint": sidecar.get("source_engine_fingerprint"),
        "recovery_model_sha256": copy.deepcopy(sidecar.get("recovery_model_sha256")),
        "recovery_engine_fingerprint": sidecar.get("recovery_engine_fingerprint"),
        "independent_observations": False,
        "applied_regions": sorted(applied),
        "retained_source_regions": sorted(retained),
        "resolved_regions": sorted({
            item["region"] for item in sidecar["observations"]
            if isinstance(item, dict) and isinstance(item.get("selected"), dict)
        }),
        "source_bound_observations": source_bound_observations,
        "unresolved_requests": unresolved,
        "conflicting_source_regions": conflicting,
        "source_frame_verification": copy.deepcopy(sidecar.get("source_frame_verification")),
    }
    if result.get("result_card_occlusion"):
        result["weak_state_recovery"]["result_card_occlusion"] = copy.deepcopy(
            result["result_card_occlusion"])
    return result


def load(raw: Mapping[str, Any], path: str | Path, *,
         original: Mapping[str, Any] | None = None,
         evidence_path: str | Path | None = None,
         source_frame_path: str | Path | None = None,
         source_frame_id: str | None = None,
         source_frame_evidence: str | None = None,
         allow_ocr: bool = False) -> Mapping[str, Any]:
    """Load and apply one cached sidecar; cached replay never runs OCR."""

    if allow_ocr:
        raise ValueError("Weak-state cached replay does not run OCR; use recover for fresh OCR.")
    path = Path(path)
    if not path.exists():
        return raw
    if evidence_path is None:
        raise ValueError("Weak-state cached replay requires gameplay evidence for validation.")
    try:
        sidecar = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid weak-state recovery sidecar.") from exc
    return apply(
        raw, sidecar, original=original, evidence_path=evidence_path,
        source_frame_path=source_frame_path, source_frame_id=source_frame_id,
        source_frame_evidence=source_frame_evidence,
    )

