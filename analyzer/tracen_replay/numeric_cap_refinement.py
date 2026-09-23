"""Source-bound rereads for visible caps and clipped result ratios.

The ordinary detector deliberately keeps weak numeric crops as diagnostics.
This module provides a small, auditable sidecar for a bounded reread of the
same gameplay frame.  A sidecar can add a cap observation or a complete
``current/cap`` result ratio only after it is bound to the original raw line
geometry and to the exact source/evidence hashes.  It never computes a value
from a state delta, a balance, or a neighbouring frame.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

from .layout import pane_size, place, place_y


VERSION = 1
STAGE = "numeric_cap_refinement"
REFINEMENT_DIR = "numeric-cap-refinement"
STAT_FIELDS = ("speed", "stamina", "power", "guts", "wit")
PERFORMANCE_FIELDS = ("dance", "passion", "vocal", "visual", "composure")
RESULT_FIELDS = STAT_FIELDS

_CAP_RE = re.compile(r"^/\s*(\d{1,4})$")
_CAP_VARIANT_RE = re.compile(r"^[VYlI]\s*(\d{1,4})$", re.IGNORECASE)
_RATIO_RE = re.compile(r"^(\d{1,4})\s*/\s*(\d{3,4})$")


def fingerprint(value):
    """Hash one JSON value without changing the source file."""

    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gameplay_fingerprint(path):
    """Hash decoded gameplay pixels, independent of PNG/JPEG encoding."""

    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.size != pane_size():
            raise ValueError("Numeric refinement evidence is not a gameplay pane.")
        return hashlib.sha256(image.tobytes()).hexdigest()


def _number(value, *, name):
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"Numeric refinement {name} must be numeric.")
    return float(value)


def _box(value, *, name):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"Numeric refinement {name} box is invalid.")
    result = [_number(item, name=f"{name} box coordinate") for item in value]
    if not result[0] < result[2] or not result[1] < result[3]:
        raise ValueError(f"Numeric refinement {name} box is empty.")
    return result


def _box_equal(left, right):
    try:
        return [float(item) for item in left] == [float(item) for item in right]
    except (TypeError, ValueError):
        return False


def _center(box):
    return ((float(box[0]) + float(box[2])) / 2,
            (float(box[1]) + float(box[3])) / 2)


def _within(observation, box):
    item = observation.get("box") if isinstance(observation, dict) else None
    if not isinstance(item, (list, tuple)) or len(item) != 4:
        return False
    x, y = _center(item)
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _overlap_ratio(left, right):
    try:
        l0, t0, l1, b0 = [float(item) for item in left]
        r0, u0, r1, v0 = [float(item) for item in right]
    except (TypeError, ValueError):
        return 0.0
    width = max(0.0, min(l1, r1) - max(l0, r0))
    height = max(0.0, min(b0, v0) - max(t0, u0))
    area = max(0.0, (l1 - l0) * (b0 - t0))
    return width * height / area if area else 0.0


def _text(value):
    return re.sub(r"\s+", "", str(value or "").strip())


def parse_cap(text, *, allow_ocr_slash_variant=True):
    """Return one cap amount and its normalization, or ``None``.

    ``V1500``/``l1500`` are retained as a diagnostic OCR slash variant.  The
    caller still has to prove the field's label and row geometry before the
    value is accepted.
    """

    normalized = _text(text)
    match = _CAP_RE.fullmatch(normalized)
    if match:
        return int(match[1]), None
    if allow_ocr_slash_variant:
        match = _CAP_VARIANT_RE.fullmatch(normalized)
        if match:
            return int(match[1]), "ocr_slash_variant"
    return None


def parse_ratio(text):
    normalized = _text(text)
    match = _RATIO_RE.fullmatch(normalized)
    if match is None:
        return None
    current, cap = int(match[1]), int(match[2])
    if cap <= 0 or current > cap:
        return None
    return current, cap


def _performance_rows():
    from .vision import _performance_panel_rows

    return _performance_panel_rows()


def _stat_columns():
    from .stats import BOXES

    # The stat bar is pinned to the bottom of the clear area.
    return [place(box, "bc") for box in BOXES[:5]]


def _result_boxes():
    # These are the fixed result-card columns used by NeuralReader, on the PC
    # pane; the cards are pinned to the centre of the clear area.  They are
    # UI geometry, not values or recording-specific frame identifiers.
    return {
        field: place(box, "mc") for field, box in zip(
            RESULT_FIELDS,
            ((322, 834, 448, 876), (518, 834, 644, 876),
             (714, 834, 840, 876), (322, 952, 448, 994),
             (518, 952, 644, 994)),
        )
    }


def _source_lines(raw):
    lines = raw.get("lines") if isinstance(raw, dict) else None
    return lines if isinstance(lines, list) else []


def _line_matches_anchor(line, anchor):
    if not isinstance(line, dict) or not isinstance(anchor, dict):
        return False
    if "box" in anchor and not _box_equal(line.get("box"), anchor.get("box")):
        return False
    if "text" in anchor and _text(line.get("text")) != _text(anchor.get("text")):
        return False
    return True


def _unique_anchor(raw, anchor):
    if not isinstance(anchor, dict):
        return None
    matches = [(index, line) for index, line in enumerate(_source_lines(raw))
               if _line_matches_anchor(line, anchor)]
    if len(matches) != 1:
        return None
    return matches[0]


def _panel_source_geometry(raw):
    """Return fields whose source frame establishes the sidebar row geometry."""

    lines = _source_lines(raw)
    result = set()
    for field, label, label_y, cap_y in _performance_rows():
        band = (140, label_y - 30, 335, label_y + 18)
        values = []
        for line in lines:
            if not isinstance(line, dict) or line.get("input_eligible") is False:
                continue
            try:
                confidence = float(line.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if confidence < 90 or not _within(line, band):
                continue
            if re.fullmatch(r"\d{1,3}", _text(line.get("text"))):
                values.append(int(_text(line.get("text"))))
        if len(set(values)) == 1 and values:
            result.add(field)
    return result


def _panel_source_caps(raw):
    """Return performance rows with a source cap in the fixed sidebar band.

    This is deliberately independent of any reported value.  A cap line is
    useful as a panel anchor only when it is in the row's gameplay-pane
    geometry and has a slash-shaped cap grammar.  The helper is used by
    candidate discovery, where a false positive would schedule OCR over
    unrelated menu, story, or lobby text.
    """

    lines = _source_lines(raw)
    result = set()
    for field, _label, _label_y, cap_y in _performance_rows():
        for line in lines:
            if not isinstance(line, dict) or line.get("input_eligible") is False:
                continue
            try:
                confidence = float(line.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if (confidence >= 80
                    and _within(line, (170, cap_y - 28, 285, cap_y + 28))
                    and parse_cap(line.get("text")) is not None):
                result.add(field)
                break
    return result


def performance_panel_geometry(raw):
    """Return conservative source geometry for performance-cap discovery.

    A complete labeled panel uses the two fixed headings and at least three
    row labels.  Some transition frames lose those labels while retaining
    the fixed numeric stack; those frames are accepted only when three
    unambiguous current rows and one source cap still anchor the same panel.
    The returned proof contains geometry only and never an amount to copy.
    """

    if not isinstance(raw, dict):
        return None
    lines = _source_lines(raw)

    def _matches(text, box, minimum_confidence):
        return [line for line in lines
                if isinstance(line, dict)
                and line.get("input_eligible") is not False
                and _text(line.get("text")).casefold() == text.casefold()
                and _within(line, box)
                and _number_or_zero(line.get("confidence")) >= minimum_confidence]

    # These anchors match the production sidebar geometry.  Requiring both
    # words avoids treating an incidental "Performance" label as this pane.
    performance_headers = _matches("Performance", place((145, 245, 280, 290), "tl"), 90)
    points_headers = []
    for alias in ("Points", "Poin", "Point"):
        points_headers.extend(_matches(alias, place((155, 265, 280, 315), "tl"), 80))
    # Deduplicate an OCR line that happened to match more than one alias.
    points_headers = {id(line): line for line in points_headers}.values()
    points_headers = list(points_headers)
    labels = {}
    for field, label, label_y, _cap_y in _performance_rows():
        found = _matches(label, (140, label_y - 25, 205, label_y + 25), 80)
        if len(found) == 1:
            labels[field] = found[0]

    source_rows = _panel_source_geometry(raw)
    source_caps = _panel_source_caps(raw)

    if (len(performance_headers) == 1 and len(points_headers) == 1
            and len(labels) >= 3
            and (len(source_rows) >= 3 or source_caps)):
        return {
            "basis": "labeled_performance_panel_geometry",
            "performance_header": copy.deepcopy(performance_headers[0]),
            "points_header": copy.deepcopy(points_headers[0]),
            "labels": sorted(labels),
            "source_rows": sorted(source_rows),
            "source_caps": sorted(source_caps),
        }

    # Headerless transition frames are recoverable only from the stable
    # stack: three source current rows plus at least one cap in its own row.
    # This admits the reviewed clipped-header frame while rejecting isolated
    # numbers in unrelated screens.
    if len(source_rows) >= 3 and source_caps:
        return {
            "basis": "fixed_row_performance_panel_geometry",
            "performance_header": None,
            "points_header": None,
            "labels": sorted(labels),
            "source_rows": sorted(source_rows),
            "source_caps": sorted(source_caps),
        }
    return None


def _number_or_zero(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def read_performance_caps(lines, regions=None):
    """Read explicit performance caps with row geometry and provenance.

    The return shape mirrors ``read_main_stat_caps``: values are separate from
    proof, and a disagreement leaves that field absent.
    """

    lines = lines if isinstance(lines, list) else []
    regions = regions if isinstance(regions, dict) else {}
    values, proof = {}, {}
    source_rows = _panel_source_geometry({"lines": lines})
    # A strict panel heading is ideal, but transition frames can retain the
    # fixed row geometry after its header has been occluded. Three source rows
    # are the minimum independent anchor for that case.
    for field, label, label_y, cap_y in _performance_rows():
        label_lines = [line for line in lines
                       if isinstance(line, dict)
                       and line.get("confidence", 0) >= 80
                       and _text(line.get("text")) == label
                       and _within(line, (140, label_y - 30, 205, label_y + 30))]
        candidates = []
        for line in lines:
            if not isinstance(line, dict) or line.get("input_eligible") is False:
                continue
            try:
                confidence = float(line.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if confidence < 80 or not _within(line, (170, cap_y - 28, 285, cap_y + 28)):
                continue
            parsed = parse_cap(line.get("text"))
            if parsed is not None:
                candidates.append((parsed, line))
        # A numeric-cap sidecar stores the localized row in a namespaced
        # region.  It is accepted only after the source frame establishes at
        # least three neighbouring current rows and the fixed cap band.
        refined = []
        for name, observation in regions.items():
            if name not in {
                f"numeric_cap.performance.{field}",
                f"performance_panel_cap.{field}",
            }:
                continue
            if not isinstance(observation, dict):
                continue
            if observation.get("input_eligible") is False:
                continue
            try:
                confidence = float(observation.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            parsed = parse_cap(observation.get("text"))
            if (parsed is not None and confidence >= 80
                    and _within(observation, (170, cap_y - 32, 285, cap_y + 32))):
                refined.append((parsed, observation))
        all_candidates = candidates + refined
        candidate_values = {item[0][0] for item in all_candidates}
        # A cap is accepted from a localized sidecar when the original frame
        # has three rows. A direct slash cap remains valid with its own row
        # label, preserving the historical strict-panel behavior.
        if len(candidate_values) == 1 and all_candidates and (
                len(label_lines) == 1 or len(source_rows) >= 3):
            value, observation = all_candidates[0]
            values[field] = value[0]
            proof[field] = {
                "reading": copy.deepcopy(observation),
                "label": copy.deepcopy(label_lines[0]) if len(label_lines) == 1 else None,
                "source_rows": sorted(source_rows),
                "basis": "labeled_performance_panel_cap",
                "slash_normalization": value[1],
            }
    return values, proof


def _find_stat_header_and_rows(raw, field):
    lines = [line for line in _source_lines(raw)
             if isinstance(line, dict) and line.get("confidence", 0) >= 90
             and len(line.get("box", [])) == 4]
    field_index = STAT_FIELDS.index(field)
    left, _, right, _ = _stat_columns()[field_index]
    headers = [line for line in lines
               if _text(line.get("text")).casefold() == field
               and _within(line, (left - 24, place_y(650, "b"), right + 24, place_y(785, "b")))]
    if len(headers) != 1:
        return None
    header = headers[0]
    header_y = _center(header["box"])[1]
    current = [line for line in lines
               if re.fullmatch(r"\d{1,4}", _text(line.get("text")))
               and _within(line, (left - 12, header_y + 10, right + 12, header_y + 70))]
    caps = []
    for line in lines:
        if not _within(line, (left - 12, header_y + 20, right + 12, header_y + 100)):
            continue
        parsed = parse_cap(line.get("text"))
        if parsed is not None:
            caps.append((parsed, line))
    return header, current, caps


def read_stat_caps(lines, regions=None, *, result_grid=False, current_grid=False):
    """Read main-stat caps through the shared source-bound cap vocabulary."""

    if result_grid is True:
        return {}, {}
    raw = {"lines": lines if isinstance(lines, list) else []}
    regions = regions if isinstance(regions, dict) else {}
    found_by_field = {
        field: _find_stat_header_and_rows(raw, field)
        for field in STAT_FIELDS
    }
    # A false grid probe is only recoverable when the source frame contains
    # the complete labeled current stat bar.  This prevents a slash-shaped
    # number in a menu or an isolated cap crop from becoming a stat fact.
    if current_grid is not True:
        current_fields = {
            field for field, found in found_by_field.items()
            if found is not None and len({
                _text(item.get("text")) for item in found[1]
            }) == 1 and found[1]
        }
        if len(current_fields) != len(STAT_FIELDS):
            return {}, {}
    values, proof = {}, {}
    for field in STAT_FIELDS:
        found = found_by_field[field]
        if found is None:
            continue
        header, current, caps = found
        field_index = STAT_FIELDS.index(field)
        left, _, right, _ = _stat_columns()[field_index]
        refined = []
        for region_name in (f"numeric_cap.stats.{field}", f"stats_cap.{field}"):
            observation = regions.get(region_name)
            if not isinstance(observation, dict):
                continue
            parsed = parse_cap(observation.get("text"))
            if parsed is not None and _within(
                    observation, (left - 12, _center(header["box"])[1] + 20,
                                  right + 12, _center(header["box"])[1] + 100)):
                refined.append((parsed, observation))
        all_candidates = caps + refined
        candidate_values = {item[0][0] for item in all_candidates}
        if len(candidate_values) == 1 and all_candidates:
            value, reading = all_candidates[0]
            values[field] = value[0]
            proof[field] = {
                "header": copy.deepcopy(header),
                "current": copy.deepcopy(current[0]) if len({
                    _text(item.get("text")) for item in current
                }) == 1 and current else None,
                "readings": [copy.deepcopy(item[1]) for item in all_candidates],
                "basis": "labeled_main_stat_bar_cap",
                "slash_normalization": value[1],
            }
    return values, proof


def _result_source_line(raw, field, anchor):
    found = _unique_anchor(raw, anchor)
    if found is not None:
        return found
    # A caller may omit the anchor only when the source line is uniquely
    # located in the fixed result-card row. This keeps generated artifacts
    # compact while retaining source geometry as the binding proof.
    expected = _result_boxes().get(field)
    if expected is None:
        return None
    matches = []
    for index, line in enumerate(_source_lines(raw)):
        text = _text(line.get("text")) if isinstance(line, dict) else ""
        if not parse_ratio(text) and not re.fullmatch(r"\d{1,4}/\d{1,3}", text):
            continue
        if _overlap_ratio(line.get("box"), expected) >= 0.55:
            matches.append((index, line))
    return matches[0] if len(matches) == 1 else None


def _validate_result_field(raw, field, spec):
    if field not in RESULT_FIELDS or not isinstance(spec, dict):
        raise ValueError(f"Numeric result field is invalid: {field!r}")
    observation = spec.get("observation")
    if not isinstance(observation, dict):
        raise ValueError(f"Numeric result {field} observation is missing.")
    parsed = parse_ratio(observation.get("text"))
    if parsed is None:
        raise ValueError(f"Numeric result {field} is not a complete ratio.")
    confidence = _number(observation.get("confidence"), name=f"{field} result confidence")
    if confidence < 90:
        raise ValueError(f"Numeric result {field} confidence is too low.")
    box = _box(observation.get("box"), name=f"{field} result")
    expected = _result_boxes()[field]
    if _overlap_ratio(box, expected) < 0.55:
        raise ValueError(f"Numeric result {field} crop is outside its labeled row.")
    source = _result_source_line(raw, field, spec.get("source_anchor"))
    if source is None:
        raise ValueError(f"Numeric result {field} does not bind to a source row.")
    source_index, source_line = source
    source_box = _box(source_line.get("box"), name=f"{field} source result")
    if _overlap_ratio(box, source_box) < 0.55:
        raise ValueError(f"Numeric result {field} reread escapes the source row.")
    source_text = _text(source_line.get("text"))
    source_parsed = parse_ratio(source_text)
    if source_parsed is not None and source_parsed != parsed:
        raise ValueError(f"Numeric result {field} disagrees with a complete source ratio.")
    return {
        "field": field,
        "value": parsed[0],
        "cap": parsed[1],
        "observation": copy.deepcopy(observation),
        "source_line_index": source_index,
        "source_line": copy.deepcopy(source_line),
        "source_anchor": copy.deepcopy(spec.get("source_anchor")),
    }


def _validate_cap_field(raw, field, kind, spec):
    fields = PERFORMANCE_FIELDS if kind == "performance_cap" else STAT_FIELDS
    if field not in fields or not isinstance(spec, dict):
        raise ValueError(f"Numeric {kind} field is invalid: {field!r}")
    observation = spec.get("observation")
    if not isinstance(observation, dict):
        raise ValueError(f"Numeric cap {field} observation is missing.")
    parsed = parse_cap(observation.get("text"))
    if parsed is None:
        raise ValueError(f"Numeric cap {field} is not a slash amount.")
    confidence = _number(observation.get("confidence"), name=f"{field} cap confidence")
    if confidence < 80:
        raise ValueError(f"Numeric cap {field} confidence is too low.")
    box = _box(observation.get("box"), name=f"{field} cap")
    lines = _source_lines(raw)
    direct_candidates = []
    if kind == "performance_cap":
        row = next(item for item in _performance_rows() if item[0] == field)
        _name, label, label_y, cap_y = row
        expected = (170, cap_y - 32, 285, cap_y + 32)
        if _overlap_ratio(box, (170, cap_y - 28, 285, cap_y + 28)) < 0.45:
            raise ValueError(f"Numeric cap {field} crop is outside its panel row.")
        for line in lines:
            if not isinstance(line, dict) or line.get("input_eligible") is False:
                continue
            if not _within(line, expected):
                continue
            candidate = parse_cap(line.get("text"))
            if candidate is not None:
                direct_candidates.append(candidate[0])
        source_rows = _panel_source_geometry(raw)
        label_lines = [line for line in lines
                       if isinstance(line, dict) and _text(line.get("text")) == label
                       and _within(line, (140, label_y - 30, 205, label_y + 30))]
        if len(label_lines) != 1 and len(source_rows) < 3:
            raise ValueError(f"Numeric cap {field} lacks source panel geometry.")
    else:
        found = _find_stat_header_and_rows(raw, field)
        if found is None:
            raise ValueError(f"Numeric cap {field} lacks source stat geometry.")
        header, _current, caps = found
        header_y = _center(header["box"])[1]
        left, _, right, _ = _stat_columns()[STAT_FIELDS.index(field)]
        if _overlap_ratio(box, (left - 12, header_y + 20, right + 12, header_y + 100)) < 0.45:
            raise ValueError(f"Numeric cap {field} crop is outside its stat row.")
        direct_candidates = [candidate[0][0] for candidate in caps]
    if direct_candidates and any(value != parsed[0] for value in direct_candidates):
        raise ValueError(f"Numeric cap {field} disagrees with a complete source cap.")
    return {
        "field": field,
        "value": parsed[0],
        "observation": copy.deepcopy(observation),
        "normalization": parsed[1],
    }


def _required_provenance(raw, refinement, evidence_path, source_frame_path,
                         source_frame_id=None, source_frame_evidence=None,
                         original=None):
    """Validate a sidecar against its immutable source observation.

    ``raw`` may already contain another validated supplement.  Callers can
    pass that working observation while ``original`` remains the untouched
    neural record named by ``raw_sha256`` in the sidecar.
    """

    original = raw if original is None else original
    if not isinstance(refinement, dict) or refinement.get("version") != VERSION:
        raise ValueError("Numeric refinement version mismatch.")
    if refinement.get("stage") != STAGE:
        raise ValueError("Numeric refinement stage mismatch.")
    if not isinstance(original, dict) or not isinstance(original.get("lines"), list):
        raise ValueError("Numeric refinement target observation has no lines.")
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        raise ValueError("Numeric refinement working observation has no lines.")
    if refinement.get("raw_sha256") != fingerprint(original):
        raise ValueError("Numeric refinement source JSON changed.")
    if raw.get("source_timestamp_ms") != original.get("source_timestamp_ms"):
        raise ValueError("Numeric refinement working observation timestamp changed.")
    required = (
        "source_frame_id", "source_timestamp_ms", "evidence", "evidence_sha256",
        "source_frame_evidence", "source_frame_sha256", "gameplay_sha256",
        "source_model_sha256", "source_engine_fingerprint",
        "refinement_model_sha256", "refinement_engine_fingerprint",
        "independent_observations",
    )
    for key in required:
        if key not in refinement:
            raise ValueError(f"Numeric refinement missing {key} provenance.")
    if source_frame_id is not None and refinement["source_frame_id"] != source_frame_id:
        raise ValueError("Numeric refinement frame identity changed.")
    if (source_frame_evidence is not None
            and refinement["source_frame_evidence"] != source_frame_evidence):
        raise ValueError("Numeric refinement source-frame evidence path mismatch.")
    if refinement["source_timestamp_ms"] != original.get("source_timestamp_ms"):
        raise ValueError("Numeric refinement timestamp mismatch.")
    if refinement["evidence"] != original.get("evidence"):
        raise ValueError("Numeric refinement gameplay path mismatch.")
    if refinement["source_frame_sha256"] != original.get("source_frame_sha256"):
        raise ValueError("Numeric refinement source-frame hash mismatch.")
    if refinement["gameplay_sha256"] != original.get("gameplay_sha256"):
        raise ValueError("Numeric refinement gameplay hash mismatch.")
    if refinement["source_model_sha256"] != original.get("model_sha256"):
        raise ValueError("Numeric refinement source model mismatch.")
    if refinement["source_engine_fingerprint"] != original.get("engine_fingerprint"):
        raise ValueError("Numeric refinement source engine mismatch.")
    if refinement["independent_observations"] is not False:
        raise ValueError("Numeric refinement cannot claim independent observations.")
    if evidence_path is None or not Path(evidence_path).is_file():
        raise ValueError("Numeric refinement gameplay evidence is missing.")
    if file_fingerprint(evidence_path) != refinement["evidence_sha256"]:
        raise ValueError("Numeric refinement gameplay evidence changed.")
    if gameplay_fingerprint(evidence_path) != refinement["gameplay_sha256"]:
        raise ValueError("Numeric refinement gameplay pixels changed.")
    if source_frame_path is None or not Path(source_frame_path).is_file():
        raise ValueError("Numeric refinement source frame evidence is missing.")
    if file_fingerprint(source_frame_path) != refinement["source_frame_sha256"]:
        raise ValueError("Numeric refinement source frame changed.")


def apply(raw, refinement, *, original=None, evidence_path=None,
          source_frame_path=None, source_frame_id=None, source_frame_evidence=None):
    """Apply a validated cap/result sidecar while retaining raw diagnostics."""

    original = raw if original is None else original
    _required_provenance(raw, refinement, evidence_path, source_frame_path,
                         source_frame_id=source_frame_id,
                         source_frame_evidence=source_frame_evidence,
                         original=original)
    fields = refinement.get("fields", {})
    if not isinstance(fields, dict) or not fields:
        raise ValueError("Numeric refinement has no source fields.")
    regions = (copy.deepcopy(raw.get("regions", {}))
               if isinstance(raw.get("regions"), dict) else {})
    applied = {}
    for key, spec in fields.items():
        if not isinstance(key, str) or "." not in key:
            raise ValueError(f"Numeric refinement field key is invalid: {key!r}")
        kind, field = key.split(".", 1)
        if kind in {"performance_cap", "stats_cap"}:
            checked = _validate_cap_field(
                original, field,
                "performance_cap" if kind == "performance_cap" else "stats_cap", spec)
            region_name = f"numeric_cap.{'performance' if kind == 'performance_cap' else 'stats'}.{field}"
            observation = copy.deepcopy(checked["observation"])
        elif kind == "result_total":
            checked = _validate_result_field(original, field, spec)
            region_name = f"numeric_result.{field}"
            observation = copy.deepcopy(checked["observation"])
        else:
            raise ValueError(f"Numeric refinement field kind is unsupported: {kind!r}")
        existing = regions.get(region_name)
        if existing is not None:
            existing_value = ((parse_ratio(existing.get("text"))
                               if kind == "result_total" else
                               parse_cap(existing.get("text")))
                              if isinstance(existing, dict) else None)
            new_value = ((checked["value"], checked["cap"]) if kind == "result_total"
                         else checked["value"])
            if existing_value != new_value:
                raise ValueError(f"Numeric refinement {region_name} disagrees with an existing region.")
            # Keep the already accepted observation.  A later sidecar may
            # compose with this one, but it cannot overwrite its evidence.
            observation = existing
        else:
            regions[region_name] = observation
        applied[key] = checked
    result = dict(raw, regions=regions)
    prior = raw.get("numeric_cap_refinement")
    metadata = copy.deepcopy(prior) if isinstance(prior, dict) else {}
    prior_fields = {}
    if (isinstance(prior, dict)
            and prior.get("stage") == STAGE
            and prior.get("raw_sha256") == fingerprint(original)
            and isinstance(prior.get("fields"), dict)):
        prior_fields = copy.deepcopy(prior["fields"])
    merged_fields = dict(prior_fields)
    merged_fields.update(copy.deepcopy(applied))
    metadata.update({
        "version": VERSION,
        "stage": STAGE,
        "source_frame_id": refinement["source_frame_id"],
        "source_timestamp_ms": refinement["source_timestamp_ms"],
        "evidence": refinement["evidence"],
        "evidence_sha256": refinement["evidence_sha256"],
        "source_frame_evidence": refinement["source_frame_evidence"],
        "source_frame_sha256": refinement["source_frame_sha256"],
        "gameplay_sha256": refinement["gameplay_sha256"],
        "raw_sha256": refinement["raw_sha256"],
        "source_model_sha256": refinement["source_model_sha256"],
        "source_engine_fingerprint": refinement["source_engine_fingerprint"],
        "refinement_model_sha256": refinement["refinement_model_sha256"],
        "refinement_engine_fingerprint": refinement["refinement_engine_fingerprint"],
        "independent_observations": False,
        "fields": merged_fields,
        "applied_fields": sorted(merged_fields),
    })
    result["numeric_cap_refinement"] = metadata
    return result


def load(raw, path, *, original=None, evidence_path=None,
         source_frame_path=None, source_frame_id=None,
         source_frame_evidence=None):
    """Load a cached sidecar and apply it against an immutable original.

    ``original`` is optional for compatibility with the first-pass API.  A
    worker composing multiple supplements should pass the same untouched raw
    observation on every call while ``raw`` is its accumulating working copy.
    Cached loading never starts OCR.
    """

    path = Path(path)
    if not path.exists():
        return raw
    try:
        refinement = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid numeric refinement artifact.") from exc
    return apply(raw, refinement, original=original,
                 evidence_path=evidence_path,
                 source_frame_path=source_frame_path,
                 source_frame_id=source_frame_id,
                 source_frame_evidence=source_frame_evidence)


def candidate_fields(raw):
    """Return bounded reread requests without values or expected amounts."""

    if not isinstance(raw, dict):
        return []
    lines = _source_lines(raw)
    regions = raw.get("regions") if isinstance(raw.get("regions"), dict) else {}
    requests = []
    # Do not schedule five sidebar cap rereads on every raw frame.  The
    # performance pane must first be established from its fixed headings and
    # row geometry, or from the conservative headerless transition fallback.
    panel_geometry = performance_panel_geometry(raw)
    if panel_geometry is not None:
        for field, _label, _label_y, cap_y in _performance_rows():
            cap_lines = [line for line in lines
                         if isinstance(line, dict)
                         and _number_or_zero(line.get("confidence")) >= 90
                         and _within(line, (170, cap_y - 28, 285, cap_y + 28))
                         and parse_cap(line.get("text")) is not None]
            region_lines = []
            for region_name in (
                    f"numeric_cap.performance.{field}",
                    f"performance_panel_cap.{field}"):
                observation = regions.get(region_name)
                if (isinstance(observation, dict)
                        and _number_or_zero(observation.get("confidence")) >= 80
                        and _within(observation, (170, cap_y - 32, 285, cap_y + 32))
                        and parse_cap(observation.get("text")) is not None):
                    region_lines.append(observation)
            all_cap_lines = cap_lines + region_lines
            if len({parse_cap(line.get("text"))[0] for line in all_cap_lines}) != 1:
                requests.append({
                    "field": field,
                    "kind": "performance_cap",
                    "crop_box": [170, cap_y - 28, 285, cap_y + 28],
                    "geometry_basis": panel_geometry["basis"],
                })
    found_by_field = {
        field: _find_stat_header_and_rows(raw, field)
        for field in STAT_FIELDS
    }
    allow_stat_requests = raw.get("result_grid") is not True
    if raw.get("current_grid") is not True:
        current_fields = {
            field for field, found in found_by_field.items()
            if found is not None and len({
                _text(item.get("text")) for item in found[1]
            }) == 1 and found[1]
        }
        allow_stat_requests = len(current_fields) == len(STAT_FIELDS)
    if allow_stat_requests:
        for field in STAT_FIELDS:
            found = found_by_field[field]
            if found is None:
                continue
            _header, _current, caps = found
            region_observation = regions.get(f"stats_cap.{field}")
            if not isinstance(region_observation, dict):
                region_observation = regions.get(f"numeric_cap.stats.{field}")
            region_value = parse_cap(region_observation.get("text")) \
                if isinstance(region_observation, dict) else None
            candidate_values = {item[0][0] for item in caps}
            if region_value is not None:
                candidate_values.add(region_value[0])
            if len(candidate_values) != 1:
                header_y = _center(found[0]["box"])[1]
                left, _, right, _ = _stat_columns()[STAT_FIELDS.index(field)]
                requests.append({
                    "field": field,
                    "kind": "stats_cap",
                    "crop_box": [left - 12, header_y + 20, right + 12, header_y + 100],
                    "geometry_basis": "labeled_main_stat_bar_cap",
                })
    if raw.get("result_grid") is True:
        for field, box in _result_boxes().items():
            observation = regions.get(f"result.{field}")
            ratio = (parse_ratio(observation.get("text"))
                     if isinstance(observation, dict) else None)
            if ratio is None:
                # ``apply`` stores accepted rereads in the namespaced region;
                # keep discovery idempotent after a worker composes a cached
                # numeric sidecar onto the working observation.
                observation = regions.get(f"numeric_result.{field}")
                ratio = (parse_ratio(observation.get("text"))
                         if isinstance(observation, dict) else None)
            if ratio is None:
                requests.append({
                    "field": field,
                    "kind": "result_total",
                    "crop_box": list(box),
                    "geometry_basis": "fixed_result_card_row_geometry",
                })
    return requests


candidates = candidate_fields


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"Refusing to overwrite numeric refinement: {path}")
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


def _selected_frames(root, start_ms, end_ms, frame_ids):
    from .performance_panel_refinement import _selected_frames as select

    return select(root, start_ms, end_ms, frame_ids)


def generate(root, *, start_ms, end_ms, frame_ids=None, fields=None,
             reader=None, model_dir=".local/models/rapidocr", output_dir=None):
    """Run bounded OCR and publish only complete, source-bound fields.

    The function accepts no expected values.  It requests fixed geometry from
    the reader and leaves a field unresolved when recognition is incomplete or
    conflicts with the immutable source lines.
    """

    root = Path(root)
    selected = _selected_frames(root, start_ms, end_ms, frame_ids)
    destination = root / REFINEMENT_DIR if output_dir is None else Path(output_dir)
    if reader is None:
        from .vision import NeuralReader

        reader = NeuralReader(model_dir)
    if fields:
        requested = {str(field) for field in fields}
    else:
        requested = None
    summary = {"stage": STAGE, "selected_frames": len(selected), "written": 0,
               "skipped_existing": 0, "unresolved": []}
    from PIL import Image

    for frame_id, timestamp, raw_path, source_frame_evidence in selected:
        target = destination / raw_path.name
        if target.exists():
            summary["skipped_existing"] += 1
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        evidence_path = root / raw.get("evidence", "")
        source_frame_path = root / (source_frame_evidence or "")
        with Image.open(evidence_path) as image:
            reread = reader.read(image.convert("RGB"))
        reread_regions = reread.get("regions", {}) if isinstance(reread, dict) else {}
        proposed = {}
        for item in candidate_fields(raw):
            field, kind = item["field"], item["kind"]
            if requested is not None and field not in requested:
                continue
            if kind == "performance_cap":
                observation = reread_regions.get(f"performance_panel_cap.{field}")
                if isinstance(observation, dict) and parse_cap(observation.get("text")):
                    proposed[f"performance_cap.{field}"] = {"observation": copy.deepcopy(observation)}
            elif kind == "stats_cap":
                observation = reread_regions.get(f"stats_cap.{field}")
                if isinstance(observation, dict) and parse_cap(observation.get("text")):
                    proposed[f"stats_cap.{field}"] = {"observation": copy.deepcopy(observation)}
            elif kind == "result_total":
                observation = reread_regions.get(f"result.{field}")
                if isinstance(observation, dict) and parse_ratio(observation.get("text")):
                    proposed[f"result_total.{field}"] = {
                        "observation": copy.deepcopy(observation),
                        "source_anchor": next((
                            {"text": line.get("text"), "box": list(line.get("box", []))}
                            for line in _source_lines(raw)
                            if _overlap_ratio(line.get("box"), item["crop_box"]) >= 0.55
                            and (parse_ratio(line.get("text")) is not None
                                 or re.fullmatch(r"\d{1,4}/\d{1,3}", _text(line.get("text"))))
                        ), None),
                    }
        if not proposed:
            summary["unresolved"].append({"frame_id": frame_id,
                                          "source_timestamp_ms": timestamp,
                                          "fields": sorted(requested or {
                                              item["field"] for item in candidate_fields(raw)
                                          })})
            continue
        sidecar = {
            "version": VERSION,
            "stage": STAGE,
            "source_frame_id": frame_id,
            "source_timestamp_ms": timestamp,
            "evidence": raw["evidence"],
            "evidence_sha256": file_fingerprint(evidence_path),
            "source_frame_evidence": source_frame_evidence,
            "source_frame_sha256": raw["source_frame_sha256"],
            "gameplay_sha256": raw["gameplay_sha256"],
            "raw_sha256": fingerprint(raw),
            "source_model_sha256": raw["model_sha256"],
            "source_engine_fingerprint": raw["engine_fingerprint"],
            "refinement_model_sha256": getattr(reader, "models", {}),
            "refinement_engine_fingerprint": getattr(reader, "fingerprint", None),
            "independent_observations": False,
            "fields": proposed,
        }
        try:
            apply(raw, sidecar, evidence_path=evidence_path,
                  source_frame_path=source_frame_path, source_frame_id=frame_id,
                  source_frame_evidence=source_frame_evidence)
        except ValueError as exc:
            summary["unresolved"].append({"frame_id": frame_id,
                                          "source_timestamp_ms": timestamp,
                                          "reason": str(exc)})
            continue
        _write_json(target, sidecar)
        summary["written"] += 1
    return summary
