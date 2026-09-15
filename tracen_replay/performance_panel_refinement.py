"""Source-bound component rereads for the current/projected side panel.

The regular neural cache keeps the detector's merged panel line.  During an
animation that line can be low confidence even though two localized crops are
readable.  This module records those component reads in a separate sidecar,
bound to the exact raw observation, gameplay image, source frame and parser
identity.  It never rewrites a ``neural`` JSON file and it never chooses a
value from a balance or an interval residual.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from pathlib import Path

from .vision import _PERFORMANCE_PANEL_ROWS, _performance_panel_line_eligible, within


REFINEMENT_DIR = "performance-panel-refinement"
STAGE = "performance_panel_refinement"
VERSION = 1
PANEL_FIELDS = tuple(row[0] for row in _PERFORMANCE_PANEL_ROWS)
PANEL_ROWS = {row[0]: row for row in _PERFORMANCE_PANEL_ROWS}
_MERGED_RE = re.compile(r"^(\d{1,3})\+(\d{1,3})$")
_CURRENT_RE = re.compile(r"^(\d{1,3})$")
_PROJECTED_RE = re.compile(r"^\+?(\d{1,3})$")


def fingerprint(raw):
    """Hash the exact immutable neural JSON value used by the sidecar."""

    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def file_fingerprint(path):
    """Hash the exact bytes of a source or gameplay evidence file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gameplay_fingerprint(path):
    """Hash decoded RGB gameplay pixels, independently of PNG encoding."""

    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.size != (810, 1080):
            raise ValueError("Performance panel evidence is not an 810x1080 gameplay pane.")
        return hashlib.sha256(image.tobytes()).hexdigest()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"Refusing to overwrite performance panel sidecar: {path}")
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


def _number(value, *, name):
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"Performance panel {name} must be numeric.")
    return float(value)


def _box(value, *, name):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"Performance panel {name} box is invalid.")
    result = [_number(item, name=f"{name} box coordinate") for item in value]
    if not result[0] < result[2] or not result[1] < result[3]:
        raise ValueError(f"Performance panel {name} box is empty.")
    return result


def _box_equal(left, right):
    try:
        return [float(item) for item in left] == [float(item) for item in right]
    except (TypeError, ValueError):
        return False


def _normalized_text(value):
    return re.sub(r"\s+", "", str(value).strip())


def _record(observation):
    """Retain source geometry and all component provenance fields."""

    return copy.deepcopy(observation)


def _validate_component(observation, field, component):
    if not isinstance(observation, dict):
        raise ValueError(f"Performance panel {field} {component} component is invalid.")
    text = _normalized_text(observation.get("text", ""))
    if component == "current":
        match = _CURRENT_RE.fullmatch(text)
    else:
        match = _PROJECTED_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"Performance panel {field} {component} component is not numeric.")
    confidence = _number(observation.get("confidence"), name=f"{field} {component} confidence")
    if confidence < 97:
        raise ValueError(f"Performance panel {field} {component} component confidence is too low.")
    box = _box(observation.get("box"), name=f"{field} {component}")
    if observation.get("input_eligible") is not True:
        raise ValueError(f"Performance panel {field} {component} component is not input eligible.")
    if observation.get("component") != component:
        raise ValueError(f"Performance panel {field} {component} component role is missing.")
    if observation.get("role") != f"panel_{component}_component":
        raise ValueError(f"Performance panel {field} {component} component role is invalid.")
    if observation.get("geometry_basis") != "same_row_merged_panel_line":
        raise ValueError(f"Performance panel {field} {component} geometry basis is invalid.")
    return int(match[1] if component == "projected" else match[0]), box


def _validate_localized_current(observation, field):
    """Validate one fixed-row current value from a bounded reread."""
    if not isinstance(observation, dict):
        raise ValueError(f"Performance panel {field} localized current is invalid.")
    text = _normalized_text(observation.get("text", ""))
    match = _CURRENT_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"Performance panel {field} localized current is not numeric.")
    confidence = _number(observation.get("confidence"), name=f"{field} localized current confidence")
    if confidence < 90:
        raise ValueError(f"Performance panel {field} localized current confidence is too low.")
    box = _box(observation.get("box"), name=f"{field} localized current")
    if observation.get("input_eligible") is not True:
        raise ValueError(f"Performance panel {field} localized current is not input eligible.")
    if observation.get("component") != "current":
        raise ValueError(f"Performance panel {field} localized current role is missing.")
    if observation.get("role") != "panel_localized_current":
        raise ValueError(f"Performance panel {field} localized current role is invalid.")
    if observation.get("geometry_basis") != "fixed_row_panel_geometry":
        raise ValueError(f"Performance panel {field} localized current geometry is invalid.")
    row = PANEL_ROWS.get(field)
    if row is None or not within(observation, (160, row[2] - 35, 280, row[2] + 20)):
        raise ValueError(f"Performance panel {field} localized current row geometry is invalid.")
    return int(match[1]), box


def _validate_parent(observation, field):
    if not isinstance(observation, dict):
        raise ValueError(f"Performance panel {field} parent observation is invalid.")
    text = _normalized_text(observation.get("text", ""))
    match = _MERGED_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"Performance panel {field} parent observation is not merged numeric text.")
    box = _box(observation.get("box"), name=f"{field} parent")
    confidence = observation.get("confidence")
    if confidence is not None:
        _number(confidence, name=f"{field} parent confidence")
    return (int(match[1]), int(match[2])), box


def _find_parent_line(original, field_spec, field):
    lines = original.get("lines")
    if not isinstance(lines, list):
        raise ValueError("Performance panel source observation has no lines.")
    parent = field_spec.get("parent_observation")
    parent_values, parent_box = _validate_parent(parent, field)
    index = field_spec.get("parent_line_index")
    if type(index) is int:
        if index < 0 or index >= len(lines):
            raise ValueError(f"Performance panel {field} parent line index is outside the source.")
        candidates = [(index, lines[index])]
    else:
        candidates = list(enumerate(lines))
    matches = []
    for candidate_index, line in candidates:
        if not isinstance(line, dict):
            continue
        if _normalized_text(line.get("text", "")) != _normalized_text(parent.get("text", "")):
            continue
        if not _box_equal(line.get("box"), parent_box):
            continue
        matches.append((candidate_index, line))
    if len(matches) != 1:
        raise ValueError(f"Performance panel {field} parent observation does not bind uniquely to source lines.")
    return matches[0]


def _validate_field(original, field, field_spec):
    if field not in PANEL_FIELDS:
        raise ValueError(f"Unsupported performance panel field: {field!r}")
    if not isinstance(field_spec, dict):
        raise ValueError(f"Performance panel {field} field is invalid.")
    parent_values, parent_box = _validate_parent(field_spec.get("parent_observation"), field)
    line_index, source_line = _find_parent_line(original, field_spec, field)
    current_value, current_box = _validate_component(field_spec.get("current"), field, "current")
    projected_value, projected_box = _validate_component(field_spec.get("projected"), field, "projected")
    if (current_value, projected_value) != parent_values:
        raise ValueError(f"Performance panel {field} component disagreement with merged source line.")
    if not (parent_box[0] <= current_box[0] <= current_box[2] <= parent_box[2]
            and parent_box[1] <= current_box[1] <= current_box[3] <= parent_box[3]
            and parent_box[0] <= projected_box[0] <= projected_box[2] <= parent_box[2]
            and parent_box[1] <= projected_box[1] <= projected_box[3] <= parent_box[3]):
        raise ValueError(f"Performance panel {field} component crop escapes its parent line.")
    if current_box[0] > projected_box[0]:
        raise ValueError(f"Performance panel {field} component crops are out of order.")
    return {
        "field": field,
        "parent_line_index": line_index,
        "source_line": source_line,
        "parent_observation": copy.deepcopy(field_spec["parent_observation"]),
        "current": copy.deepcopy(field_spec["current"]),
        "projected": copy.deepcopy(field_spec["projected"]),
        "current_value": current_value,
        "projected_value": projected_value,
    }


def _validate_localized_field(original, field, field_spec):
    """Bind a localized current read to the immutable source observation."""
    if field not in PANEL_FIELDS:
        raise ValueError(f"Unsupported performance panel field: {field!r}")
    if not isinstance(field_spec, dict):
        raise ValueError(f"Performance panel {field} localized field is invalid.")
    current = field_spec.get("current")
    value, box = _validate_localized_current(current, field)
    lines = original.get("lines") if isinstance(original, dict) else None
    if not isinstance(lines, list):
        raise ValueError("Performance panel source observation has no immutable lines.")
    row = PANEL_ROWS[field]
    source_values = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        if line.get("input_eligible") is False:
            continue
        try:
            if not within(line, (160, row[2] - 27, 280, row[2] + 5)):
                continue
        except (TypeError, ValueError):
            continue
        text = _normalized_text(line.get("text", ""))
        try:
            confidence = float(line.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if _CURRENT_RE.fullmatch(text) and confidence >= 90:
            source_values.append(int(text))
    if source_values and any(item != value for item in source_values):
        raise ValueError(f"Performance panel {field} localized current disagrees with source line.")

    # A fixed-row crop is meaningful only after the source frame establishes
    # the panel's geometry independently of that crop.  Require three
    # unambiguous neighbouring current rows and one slash-cap anchor.  This
    # prevents a readable number in an arbitrary screen region from becoming
    # a panel fact merely because the sidecar supplied a plausible box.
    source_rows = []
    source_caps = []
    for source_field, _label, source_label_y, source_cap_y in _PERFORMANCE_PANEL_ROWS:
        row_values = []
        row_band = (160, source_label_y - 27, 280, source_label_y + 5)
        cap_band = (185, source_cap_y - 25, 280, source_cap_y + 25)
        for line in lines:
            if not isinstance(line, dict) or not _performance_panel_line_eligible(line):
                continue
            try:
                confidence = float(line.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if confidence < 90:
                continue
            text = _normalized_text(line.get("text", ""))
            try:
                in_row = within(line, row_band)
                in_cap = within(line, cap_band)
            except (TypeError, ValueError):
                continue
            if in_row and _CURRENT_RE.fullmatch(text):
                row_values.append(int(text))
            if in_cap and re.fullmatch(r"/\s*(\d{1,4})", text):
                source_caps.append(source_field)
        # Duplicate equal detector lines do not establish an unambiguous
        # source row.  Keep the requirement conservative and auditable.
        if len(set(row_values)) == 1:
            source_rows.append(source_field)
    source_rows = sorted(set(source_rows))
    source_caps = sorted(set(source_caps))
    if len(source_rows) < 3 or not source_caps:
        raise ValueError(
            f"Performance panel {field} localized current lacks source-bound panel geometry."
        )
    return {
        "field": field,
        "current": copy.deepcopy(current),
        "current_value": value,
        "current_box": box,
        "source_values": source_values,
        "source_geometry": {
            "unambiguous_current_rows": source_rows,
            "slash_cap_rows": source_caps,
            "basis": "fixed_row_panel_geometry_with_neighbour_rows_and_cap",
        },
    }


def _required_provenance(raw, refinement, original, evidence_path, source_frame_path,
                        source_frame_id=None, source_frame_evidence=None):
    if not isinstance(refinement, dict) or refinement.get("version") != VERSION:
        raise ValueError("Performance panel refinement version mismatch.")
    if refinement.get("stage") != STAGE:
        raise ValueError("Performance panel refinement stage mismatch.")
    if not isinstance(original, dict) or not isinstance(original.get("lines"), list):
        raise ValueError("Performance panel source observation has no immutable lines.")
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        raise ValueError("Performance panel target observation has no lines.")
    if refinement.get("raw_sha256") != fingerprint(original):
        raise ValueError("Performance panel refinement source JSON changed.")
    required = (
        "source_frame_id", "source_timestamp_ms", "evidence", "evidence_sha256",
        "source_frame_evidence", "source_frame_sha256", "gameplay_sha256",
        "source_model_sha256", "source_engine_fingerprint",
        "refinement_model_sha256", "refinement_engine_fingerprint",
        "independent_observations",
    )
    for key in required:
        if key not in refinement:
            raise ValueError(f"Performance panel refinement missing {key} provenance.")
    if source_frame_id is not None and refinement["source_frame_id"] != source_frame_id:
        raise ValueError("Performance panel refinement frame identity changed.")
    if (source_frame_evidence is not None
            and refinement["source_frame_evidence"] != source_frame_evidence):
        raise ValueError("Performance panel source-frame evidence path mismatch.")
    if refinement["source_timestamp_ms"] != original.get("source_timestamp_ms"):
        raise ValueError("Performance panel refinement timestamp mismatch.")
    if refinement["evidence"] != original.get("evidence"):
        raise ValueError("Performance panel refinement gameplay path mismatch.")
    if refinement["source_frame_sha256"] != original.get("source_frame_sha256"):
        raise ValueError("Performance panel refinement source-frame hash mismatch.")
    if refinement["gameplay_sha256"] != original.get("gameplay_sha256"):
        raise ValueError("Performance panel refinement gameplay hash mismatch.")
    if refinement["source_model_sha256"] != original.get("model_sha256"):
        raise ValueError("Performance panel refinement source model mismatch.")
    if refinement["source_engine_fingerprint"] != original.get("engine_fingerprint"):
        raise ValueError("Performance panel refinement source engine mismatch.")
    if (original.get("source_sha256") is not None
            and refinement.get("source_sha256") != original.get("source_sha256")):
        raise ValueError("Performance panel refinement recording source mismatch.")
    if refinement["independent_observations"] is not False:
        raise ValueError("Performance panel refinement cannot claim independent observations.")
    if refinement["refinement_model_sha256"] is None or refinement["refinement_engine_fingerprint"] is None:
        raise ValueError("Performance panel refinement model identity is missing.")
    if evidence_path is None or not Path(evidence_path).is_file():
        raise ValueError("Performance panel gameplay evidence is missing.")
    if file_fingerprint(evidence_path) != refinement["evidence_sha256"]:
        raise ValueError("Performance panel gameplay evidence changed.")
    if gameplay_fingerprint(evidence_path) != refinement["gameplay_sha256"]:
        raise ValueError("Performance panel gameplay pixels changed.")
    if source_frame_path is None or not Path(source_frame_path).is_file():
        raise ValueError("Performance panel source frame evidence is missing.")
    if file_fingerprint(source_frame_path) != refinement["source_frame_sha256"]:
        raise ValueError("Performance panel source frame changed.")
    source_frame_evidence = refinement["source_frame_evidence"]
    if not isinstance(source_frame_evidence, str) or not source_frame_evidence:
        raise ValueError("Performance panel source-frame evidence path is missing.")


def apply(raw, refinement, *, original=None, evidence_path=None,
          source_frame_path=None, source_frame_id=None, source_frame_evidence=None):
    """Apply a validated source-bound panel component sidecar.

    Existing detector lines are retained as diagnostics.  Component regions
    are accepted only when both localized reads agree with the same source
    merged line and with one another; a disagreement raises instead of being
    resolved through a balance constraint.
    """

    original = raw if original is None else original
    _required_provenance(raw, refinement, original, evidence_path, source_frame_path,
                         source_frame_id=source_frame_id,
                         source_frame_evidence=source_frame_evidence)
    fields = refinement.get("fields", {})
    localized_fields = refinement.get("localized_fields", {})
    if not isinstance(fields, dict) or not isinstance(localized_fields, dict):
        raise ValueError("Performance panel refinement fields are invalid.")
    if not fields and not localized_fields:
        raise ValueError("Performance panel refinement has no source fields.")
    result = dict(raw)
    regions = dict(raw.get("regions", {})) if isinstance(raw.get("regions", {}), dict) else {}
    applied = {}
    for field, field_spec in fields.items():
        checked = _validate_field(original, field, field_spec)
        for component in ("current", "projected"):
            name = f"performance_panel_{component}.{field}"
            observation = copy.deepcopy(checked[component])
            existing = regions.get(name)
            if existing is not None and existing != observation:
                raise ValueError(f"Performance panel {name} disagrees with an existing region.")
            regions[name] = observation
        applied[field] = dict(
            parent_line_index=checked["parent_line_index"],
            parent_observation=copy.deepcopy(checked["parent_observation"]),
            current=copy.deepcopy(checked["current"]),
            projected=copy.deepcopy(checked["projected"]),
            status="resolved_source_bound_components",
            basis="same_row_component_crops",
        )
    applied_localized = {}
    for field, field_spec in localized_fields.items():
        checked = _validate_localized_field(original, field, field_spec)
        name = f"performance_panel_localized_current.{field}"
        observation = copy.deepcopy(checked["current"])
        existing = regions.get(name)
        if existing is not None and existing != observation:
            raise ValueError(f"Performance panel {name} disagrees with an existing region.")
        regions[name] = observation
        applied_localized[field] = dict(
            current=copy.deepcopy(checked["current"]),
            current_value=checked["current_value"],
            source_values=checked["source_values"],
            source_geometry=copy.deepcopy(checked["source_geometry"]),
            status="resolved_source_bound_localized_current",
            basis="fixed_row_panel_geometry",
        )
    result["regions"] = regions
    prior = result.get("performance_panel_refinement")
    metadata = copy.deepcopy(prior) if isinstance(prior, dict) else {}
    metadata.update(
        version=VERSION,
        stage=STAGE,
        source_frame_id=refinement["source_frame_id"],
        source_timestamp_ms=refinement["source_timestamp_ms"],
        evidence=refinement["evidence"],
        evidence_sha256=refinement["evidence_sha256"],
        source_frame_evidence=refinement["source_frame_evidence"],
        source_frame_sha256=refinement["source_frame_sha256"],
        gameplay_sha256=refinement["gameplay_sha256"],
        raw_sha256=refinement["raw_sha256"],
        source_model_sha256=refinement["source_model_sha256"],
        source_engine_fingerprint=refinement["source_engine_fingerprint"],
        refinement_model_sha256=refinement["refinement_model_sha256"],
        refinement_engine_fingerprint=refinement["refinement_engine_fingerprint"],
        independent_observations=False,
        fields=applied,
        localized_fields=applied_localized,
        applied_fields=sorted(applied),
        applied_localized_fields=sorted(applied_localized),
    )
    result["performance_panel_refinement"] = metadata
    return result


def load(raw, path, *, original=None, evidence_path=None,
         source_frame_path=None, source_frame_id=None, source_frame_evidence=None):
    """Load and apply a panel sidecar, returning ``raw`` when it is absent."""

    path = Path(path)
    if not path.exists():
        return raw
    try:
        refinement = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid performance panel refinement artifact.") from exc
    return apply(raw, refinement, original=original, evidence_path=evidence_path,
                 source_frame_path=source_frame_path, source_frame_id=source_frame_id,
                 source_frame_evidence=source_frame_evidence)


def candidate_fields(raw):
    """Describe bounded panel rereads suggested by one raw observation.

    The worker can use this read-only discovery result to schedule a sidecar
    without supplying recording-specific frame IDs, timestamps, or expected
    amounts to the parser.  A merged row requests the two component crops;
    rows absent from an otherwise geometrically established panel request the
    fixed current crop.
    """
    lines = raw.get("lines", []) if isinstance(raw, dict) else []
    if not isinstance(lines, list):
        return []
    requests = []
    observed = {}
    merged_fields = set()
    for field, _label, label_y, _cap_y in _PERFORMANCE_PANEL_ROWS:
        band = (160, label_y - 27, 280, label_y + 5)
        plain = []
        merged = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            text = _normalized_text(line.get("text", ""))
            if not isinstance(line.get("box"), (list, tuple)) or len(line["box"]) != 4:
                continue
            try:
                eligible = line.get("input_eligible") is not False and within(line, band)
                confidence = float(line.get("confidence", 0))
            except (TypeError, ValueError):
                eligible, confidence = False, 0
            if not eligible:
                continue
            if _MERGED_RE.fullmatch(text):
                merged.append(line)
            elif _CURRENT_RE.fullmatch(text):
                plain.append(line)
        if merged:
            merged_fields.add(field)
            if len(merged) == 1:
                requests.append(dict(field=field, kind="component_split",
                                     parent_observation=copy.deepcopy(merged[0]),
                                     confidence=merged[0].get("confidence")))
        if len(plain) == 1:
            observed[field] = plain[0]
    if len(observed) >= 3:
        lefts = sorted(float(line["box"][0]) for line in observed.values())
        rights = sorted(float(line["box"][2]) for line in observed.values())
        middle = len(lefts) // 2
        left = max(160, round(lefts[middle] - 8))
        right = min(275, round(rights[middle] + 14))
        if right - left >= 35:
            for field, _label, label_y, _cap_y in _PERFORMANCE_PANEL_ROWS:
                source = observed.get(field)
                try:
                    strong = source is not None and float(source.get("confidence", 0)) >= 90
                except (TypeError, ValueError):
                    strong = False
                if strong or field in merged_fields:
                    continue
                requests.append(dict(field=field, kind="localized_current",
                                     crop_box=[left, label_y - 31, right, label_y + 14],
                                     geometry_basis="fixed_row_panel_geometry"))
    return requests


candidates = candidate_fields


def _selected_frames(root, start_ms, end_ms, frame_ids):
    root = Path(root)
    wanted = set(frame_ids or ())
    report_path = root / "report.json"
    selected = []
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for frame in report.get("frames", []):
            frame_id, timestamp = frame.get("id"), frame.get("source_timestamp_ms")
            if not isinstance(frame_id, str) or type(timestamp) is not int:
                continue
            if not start_ms <= timestamp < end_ms or (wanted and frame_id not in wanted):
                continue
            selected.append((frame_id, timestamp, root / "neural" / f"{frame_id}.json",
                             frame.get("evidence")))
    else:
        # Fresh full-recording capture writes its immutable frame manifest to
        # capture.json before report.json exists.  Accept that envelope while
        # retaining the older standalone frames.json format for inspection
        # directories.
        frame_manifest = root / "frames.json"
        if not frame_manifest.is_file():
            frame_manifest = root / "capture.json"
        manifest_frames = {}
        if frame_manifest.is_file():
            payload = json.loads(frame_manifest.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload = payload.get("frames")
            if isinstance(payload, list):
                manifest_frames = {
                    item.get("id"): item.get("evidence")
                    for item in payload
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                }
        for path in sorted((root / "neural").glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            timestamp = raw.get("source_timestamp_ms")
            frame_id = path.stem
            if type(timestamp) is int and start_ms <= timestamp < end_ms and (not wanted or frame_id in wanted):
                source_frame_evidence = raw.get("source_frame_evidence") or manifest_frames.get(frame_id)
                selected.append((frame_id, timestamp, path, source_frame_evidence))
    selected.sort(key=lambda item: (item[1], item[0]))
    if wanted:
        found = {frame_id for frame_id, *_ in selected}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError("Requested frame is outside the bounded selection: " + ", ".join(missing))
    return selected


def _field_from_reader(raw, reread, field):
    """Build one sidecar field from reader-provided component regions."""

    regions = reread.get("regions", {})
    current = regions.get(f"performance_panel_current.{field}")
    projected = regions.get(f"performance_panel_projected.{field}")
    if not isinstance(current, dict) or not isinstance(projected, dict):
        return None
    parent = current.get("parent_observation")
    if parent != projected.get("parent_observation"):
        return None
    try:
        line_index, source_line = _find_parent_line(raw, {"parent_observation": parent}, field)
    except ValueError:
        return None
    return dict(parent_line_index=line_index, parent_observation=copy.deepcopy(parent),
                current=copy.deepcopy(current), projected=copy.deepcopy(projected))


def _localized_field_from_reader(raw, reread, field):
    """Build a sidecar field from a fixed same-frame current crop."""
    regions = reread.get("regions", {})
    current = regions.get(f"performance_panel_localized_current.{field}")
    if not isinstance(current, dict):
        return None
    # Validation here keeps unresolved/weak crop output out of an artifact;
    # apply() repeats it when the sidecar is loaded from disk.
    try:
        _validate_localized_current(current, field)
    except ValueError:
        return None
    return dict(current=copy.deepcopy(current))


def generate(root, *, start_ms, end_ms, frame_ids=None, fields=None,
             reader=None, model_dir=".local/models/rapidocr", output_dir=None):
    """Run OCR for an explicitly bounded set of frames and write new sidecars.

    ``frame_ids`` is the preferred bound for a targeted reread.  The function
    refuses to overwrite an existing sidecar and performs the same validation
    that cache loading will perform before writing it.
    """

    root = Path(root)
    if type(start_ms) is not int or type(end_ms) is not int or end_ms <= start_ms:
        raise ValueError("Performance panel generation requires a non-empty time range.")
    selected = _selected_frames(root, start_ms, end_ms, frame_ids)
    wanted_fields = tuple(fields or PANEL_FIELDS)
    if not wanted_fields or any(field not in PANEL_FIELDS for field in wanted_fields):
        raise ValueError("Performance panel generation received an unsupported field.")
    destination = root / REFINEMENT_DIR if output_dir is None else Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    summary = dict(stage=STAGE, start_ms=start_ms, end_ms=end_ms,
                   selected_frames=len(selected), written=0, skipped_existing=0,
                   fields=list(wanted_fields), unresolved_fields=[])
    if reader is None:
        from .vision import NeuralReader
        reader = NeuralReader(model_dir)
    from PIL import Image

    for frame_id, timestamp, raw_path, source_frame_evidence in selected:
        if not raw_path.is_file():
            raise ValueError(f"Missing neural observation: {frame_id}")
        target = destination / raw_path.name
        if target.exists():
            summary["skipped_existing"] += 1
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        evidence_path = root / raw.get("evidence", "")
        source_frame_path = root / (source_frame_evidence or "")
        if raw.get("source_timestamp_ms") != timestamp:
            raise ValueError("Performance panel source timestamp mismatch.")
        if not evidence_path.is_file() or not source_frame_path.is_file():
            raise ValueError("Performance panel source evidence is missing.")
        if file_fingerprint(source_frame_path) != raw.get("source_frame_sha256"):
            raise ValueError("Performance panel source frame hash mismatch.")
        if gameplay_fingerprint(evidence_path) != raw.get("gameplay_sha256"):
            raise ValueError("Performance panel gameplay pixels changed.")
        with Image.open(evidence_path) as opened:
            pane = opened.convert("RGB")
            reread = reader.read(pane)
        selected_fields = {
            field: _field_from_reader(raw, reread, field)
            for field in wanted_fields
        }
        selected_fields = {field: value for field, value in selected_fields.items() if value is not None}
        selected_localized_fields = {
            field: _localized_field_from_reader(raw, reread, field)
            for field in wanted_fields
        }
        selected_localized_fields = {
            field: value for field, value in selected_localized_fields.items()
            if value is not None
        }
        # A direct high-confidence source line needs no sidecar.  Report only
        # rows that were neither directly visible nor resolved by a bounded
        # localized reread.
        direct_fields=set()
        for field,_label,label_y,_cap_y in _PERFORMANCE_PANEL_ROWS:
            band=(160,label_y-27,280,label_y+5)
            direct=[line for line in raw.get('lines',[])
                    if line.get('confidence',0)>=90 and within(line,band)
                    and _CURRENT_RE.fullmatch(_normalized_text(line.get('text','')))]
            if len(direct)==1:
                direct_fields.add(field)
        missing_fields = sorted(set(wanted_fields) - set(selected_fields)
                                - set(selected_localized_fields) - direct_fields)
        if missing_fields:
            summary["unresolved_fields"].append(
                dict(frame_id=frame_id, source_timestamp_ms=timestamp, fields=missing_fields,
                     reason="localized_component_source_not_resolved")
            )
        if not selected_fields and not selected_localized_fields:
            continue
        sidecar = dict(
            version=VERSION,
            stage=STAGE,
            source_frame_id=frame_id,
            source_timestamp_ms=timestamp,
            evidence=raw["evidence"],
            evidence_sha256=file_fingerprint(evidence_path),
            source_frame_evidence=source_frame_evidence,
            source_frame_sha256=raw["source_frame_sha256"],
            gameplay_sha256=raw["gameplay_sha256"],
            raw_sha256=fingerprint(raw),
            source_model_sha256=raw["model_sha256"],
            source_engine_fingerprint=raw["engine_fingerprint"],
            refinement_model_sha256=getattr(reader, "models", {}),
            refinement_engine_fingerprint=getattr(reader, "fingerprint", None),
            model_sha256=getattr(reader, "models", {}),
            engine_fingerprint=getattr(reader, "fingerprint", None),
            independent_observations=False,
            selection=dict(start_ms=start_ms, end_ms=end_ms, frame_id=frame_id,
                           fields=list(wanted_fields),
                           localized_fields=sorted(selected_localized_fields)),
            fields=selected_fields,
            localized_fields=selected_localized_fields,
        )
        if raw.get("source_sha256") is not None:
            sidecar["source_sha256"] = raw["source_sha256"]
        # Validate all source/image/hash/geometry relations before publishing.
        apply(raw, sidecar, original=raw, evidence_path=evidence_path,
              source_frame_path=source_frame_path, source_frame_id=frame_id,
              source_frame_evidence=source_frame_evidence)
        _write_json(target, sidecar)
        summary["written"] += 1
    return summary


refine = generate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="recording analysis directory")
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--frame-id", action="append", dest="frame_ids", default=[])
    parser.add_argument("--field", action="append", dest="fields", default=[])
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    summary = generate(args.output, start_ms=args.start_ms, end_ms=args.end_ms,
                       frame_ids=args.frame_ids, fields=args.fields or None,
                       model_dir=args.model_dir, output_dir=args.output_dir)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
