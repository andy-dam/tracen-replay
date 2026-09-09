"""Provenance-bound refinement for a low-confidence Concert Info level slot.

The ordinary detector can read the first recording's visible ``Lvl 0`` as
``Lvl O`` below its acceptance threshold. This module re-reads only that
known support-chain slot from distinct base-sampled source frames. It never
maps the letter ``O`` to zero: an accepted value must be a numeric OCR
reading, have a stable panel anchor, and agree across a short contiguous
source interval whose decoded PTS and evidence hashes are retained.
"""

import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path

from .refine_contrast import fingerprint


FIELD = "support_chain_event_frequency"
SLOT_BOX = (650, 375, 840, 430)
SLOT_BAND = (640, 365, 850, 445)
PANEL_HEADER_BOX = (430, 0, 700, 100)
PANEL_CONTEXT_BOX = (390, 75, 720, 175)
BONUS_HEADER_BOX = (430, 220, 680, 320)
DEFAULT_MIN_CONFIDENCE = 90.0
DEFAULT_MIN_FRAMES = 3
DEFAULT_MIN_SPAN_MS = 250
DEFAULT_MAX_GAP_MS = 1000
DEFAULT_MAX_BASE_DISTANCE_MS = 1000

_ANCHORS = (
    ("panel_header", "Concert Info", PANEL_HEADER_BOX),
    ("bonus_header", "Concert Bonus Changes", BONUS_HEADER_BOX),
)
_PROOF_FIELDS = (
    "evidence",
    "evidence_sha256",
    "source_frame_evidence",
    "source_frame_sha256",
    "source_pts",
    "time_base",
    "source_manifest_evidence",
    "source_manifest_sha256",
    "source_manifest_row_id",
    "panel_raw_evidence",
    "panel_raw_sha256",
    "panel_anchors",
    "panel_context",
)


def _center(box):
    return ((float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2)


def _within(line, box):
    try:
        x, y = _center(line["box"])
    except (KeyError, TypeError, ValueError):
        return False
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _lines(raw):
    return [line for line in raw.get("lines", []) if isinstance(line, dict)]


def _anchor_metadata_valid(anchors):
    """Check the compact anchor claims stored beside each slot crop."""

    if not isinstance(anchors, list):
        return False
    found = {}
    for anchor in anchors:
        if not isinstance(anchor, dict) or anchor.get("name") in found:
            return False
        name = anchor.get("name")
        expected = next((item for item in _ANCHORS if item[0] == name), None)
        if expected is None:
            return False
        _, text, box = expected
        try:
            confidence = float(anchor["confidence"])
            actual_box = tuple(anchor["box"])
        except (KeyError, TypeError, ValueError):
            return False
        if anchor.get("text", "").strip().lower() != text.lower():
            return False
        if confidence < DEFAULT_MIN_CONFIDENCE or len(actual_box) != 4:
            return False
        if not _within({"box": actual_box}, box):
            return False
        found[name] = anchor
    return set(found) == {item[0] for item in _ANCHORS}


def is_concert_info_panel(raw):
    """Require two independent layout anchors before accepting a slot value."""

    lines = _lines(raw)
    return all(
        any(
            line.get("confidence", 0) >= DEFAULT_MIN_CONFIDENCE
            and line.get("text", "").strip().lower() == text.lower()
            and _within(line, box)
            for line in lines
        )
        for _, text, box in _ANCHORS
    )


def _extract_anchor_metadata(raw):
    """Return the best source OCR line for each panel anchor."""

    result = []
    for name, text, box in _ANCHORS:
        candidates = [
            line
            for line in _lines(raw)
            if line.get("text", "").strip().lower() == text.lower()
            and _within(line, box)
        ]
        if not candidates:
            return None
        line = max(candidates, key=lambda item: float(item.get("confidence", 0)))
        result.append(
            dict(
                name=name,
                text=line.get("text", ""),
                confidence=line.get("confidence", 0),
                box=list(line.get("box", [])),
            )
        )
    return result


def _extract_panel_context(raw):
    """Return the visible concert title when the panel provides one."""

    candidates = [
        line
        for line in _lines(raw)
        if line.get("confidence", 0) >= DEFAULT_MIN_CONFIDENCE
        and _within(line, PANEL_CONTEXT_BOX)
        and line.get("text", "").strip().lower() != "concert info"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("confidence", 0))).get("text", "").strip()


def normalize_level(text):
    """Return a numeric level, rejecting letter/digit ambiguities."""

    match = re.fullmatch(r"Lvl\s*(\d{1,2})", str(text or "").strip(), re.IGNORECASE)
    return int(match[1]) if match else None


def _looks_like_level(text):
    return re.match(r"^Lv[lI1]\s*", str(text or "").strip(), re.IGNORECASE) is not None


def _raw_slot_has_single_value(raw):
    """Prove that the base panel displays one unchanged support level.

    A refinement reads the same literal slot from several frames. It may
    only set both current and planned to that value when the full panel OCR
    has one slot token and no transition arrow or second level in the slot
    band. This catches a re-read crop that accidentally hides a planned value
    visible in the source panel.
    """

    candidates = []
    for line in _lines(raw):
        if not _within(line, SLOT_BAND) or not _looks_like_level(line.get("text", "")):
            continue
        text = str(line.get("text", "")).strip()
        if re.search(r"[>▶→]", text):
            return False
        compact = re.sub(r"\s+", "", text)
        if len(re.findall(r"Lvl", compact, re.IGNORECASE)) != 1:
            return False
        if normalize_level(text) is None and not re.fullmatch(r"Lvl\s*[Oo]", text, re.IGNORECASE):
            return False
        candidates.append(line)
    return len(candidates) == 1


def _required_provenance(observation):
    if not isinstance(observation, dict) or any(
        not observation.get(key) for key in _PROOF_FIELDS
    ):
        return False
    try:
        int(observation["source_pts"])
        Fraction(str(observation["time_base"]))
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    row_id = observation.get("source_manifest_row_id")
    if not isinstance(row_id, (str, int)) or not str(row_id):
        return False
    return _anchor_metadata_valid(observation["panel_anchors"])


def _normalized_path(root, value):
    try:
        path = (root / value).resolve()
    except (TypeError, ValueError):
        return None
    if not path.is_relative_to(root):
        return None
    return path


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_row(manifest, row_id):
    rows = manifest if isinstance(manifest, list) else manifest.get("frames", [])
    matches = [row for row in rows if isinstance(row, dict) and row.get("id") == row_id]
    return matches[0] if len(matches) == 1 else None


def _validate_observation_proofs(observations, observation_root):
    """Validate crop, decoded-frame, raw-panel and PTS-manifest linkage."""

    root = Path(observation_root).resolve()
    for observation in observations:
        if not _required_provenance(observation):
            raise ValueError("Refinement observation provenance is incomplete.")

        paths = {}
        for field in (
            "evidence",
            "source_frame_evidence",
            "source_manifest_evidence",
            "panel_raw_evidence",
        ):
            path = _normalized_path(root, observation[field])
            if path is None or not path.is_file():
                raise ValueError("Refinement observation evidence is missing or escapes the run directory.")
            paths[field] = path
        for field, path in paths.items():
            digest_field = {
                "evidence": "evidence_sha256",
                "source_frame_evidence": "source_frame_sha256",
                "source_manifest_evidence": "source_manifest_sha256",
                "panel_raw_evidence": "panel_raw_sha256",
            }[field]
            if _sha256(path) != observation.get(digest_field):
                raise ValueError("Refinement observation evidence changed.")

        try:
            panel_raw = json.loads(paths["panel_raw_evidence"].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Refinement panel OCR proof is unreadable.")
        if (
            not is_concert_info_panel(panel_raw)
            or panel_raw.get("source_timestamp_ms") != observation["timestamp_ms"]
            or panel_raw.get("source_frame_sha256") != observation["source_frame_sha256"]
            or _extract_panel_context(panel_raw) != observation["panel_context"]
        ):
            raise ValueError("Refinement panel anchor or source identity changed.")
        actual_anchors = _extract_anchor_metadata(panel_raw)
        if actual_anchors != observation["panel_anchors"]:
            raise ValueError("Refinement panel anchors changed.")

        try:
            manifest = json.loads(paths["source_manifest_evidence"].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Refinement PTS manifest is unreadable.")
        row = _manifest_row(manifest, observation["source_manifest_row_id"])
        if row is None or any(
            (
                row.get("source_timestamp_ms") != observation.get("timestamp_ms")
                if key == "source_timestamp_ms"
                else row.get(key) != observation.get(key)
            )
            for key in ("source_timestamp_ms", "source_pts", "time_base")
        ):
            raise ValueError("Refinement PTS manifest linkage changed.")
        # The source-frame proof must be the decoded image named by the
        # manifest row. Relative run roots can differ, so compare suffixes.
        expected_frame = str(row.get("evidence", "")).replace("\\", "/").lstrip("/")
        actual_frame = str(observation["source_frame_evidence"]).replace("\\", "/").lstrip("/")
        if not expected_frame or not actual_frame.endswith(expected_frame):
            raise ValueError("Refinement source-frame linkage changed.")


def consensus(
    observations,
    *,
    min_confidence=DEFAULT_MIN_CONFIDENCE,
    min_frames=DEFAULT_MIN_FRAMES,
    min_span_ms=DEFAULT_MIN_SPAN_MS,
    max_gap_ms=DEFAULT_MAX_GAP_MS,
    base_timestamp_ms=None,
    max_base_distance_ms=DEFAULT_MAX_BASE_DISTANCE_MS,
):
    """Find one conservative numeric value across a bounded source interval.

    Multiple preprocessing views of one timestamp are correlated. They may
    corroborate a timestamp, but never count as separate frames. Every
    accepted row carries decoded PTS/proof linkage, two panel anchors and
    matching panel context. Callers may bind the sequence to a base panel
    timestamp with ``base_timestamp_ms``. Ambiguous ``Lvl O``/``LvIO`` text
    is never normalized.
    """

    by_timestamp = {}
    for observation in observations or ():
        if not _required_provenance(observation):
            continue
        try:
            timestamp = int(observation["timestamp_ms"])
            confidence = float(observation.get("confidence", 0))
            source_pts = int(observation["source_pts"])
            Fraction(str(observation["time_base"]))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        text = str(observation.get("text", "")).strip()
        value = normalize_level(text)
        if observation.get("box") is not None and tuple(observation["box"]) != SLOT_BOX:
            continue
        if confidence < min_confidence:
            continue
        bucket = by_timestamp.setdefault(timestamp, {"numeric": [], "ambiguous": [], "pts": []})
        bucket["pts"].append(source_pts)
        if value is not None:
            bucket["numeric"].append((value, confidence, observation))
        elif _looks_like_level(text):
            bucket["ambiguous"].append(observation)

    # If a source timestamp produced two numeric values, or a high-confidence
    # ambiguous value alongside a numeric one, do not choose a winner.
    clean = []
    for timestamp, bucket in sorted(by_timestamp.items()):
        candidates = bucket["numeric"]
        if not candidates:
            continue
        values = {value for value, _, _ in candidates}
        if len(values) != 1 or bucket["ambiguous"] or len(set(bucket["pts"])) != 1:
            return None
        value, confidence, observation = max(candidates, key=lambda item: item[1])
        clean.append((timestamp, value, confidence, observation))

    if len(clean) < min_frames:
        return None
    values = {value for _, value, _, _ in clean}
    if len(values) != 1:
        return None
    contexts = {observation["panel_context"] for _, _, _, observation in clean}
    if len(contexts) != 1:
        return None
    gaps = [right[0] - left[0] for left, right in zip(clean, clean[1:])]
    if any(gap <= 0 or gap > max_gap_ms for gap in gaps):
        return None
    if base_timestamp_ms is not None and any(
        abs(timestamp - int(base_timestamp_ms)) > max_base_distance_ms
        for timestamp, _, _, _ in clean
    ):
        return None
    if clean[-1][0] - clean[0][0] < min_span_ms:
        return None

    accepted = [observation for _, _, _, observation in clean]
    evidence_paths = [observation.get("evidence") for observation in accepted]
    evidence_hashes = [observation.get("evidence_sha256") for observation in accepted]
    source_paths = [observation.get("source_frame_evidence") for observation in accepted]
    source_hashes = [observation.get("source_frame_sha256") for observation in accepted]
    panel_paths = [observation.get("panel_raw_evidence") for observation in accepted]
    if (
        any(not path for path in evidence_paths)
        or len(set(evidence_paths)) != len(evidence_paths)
        or any(not digest for digest in evidence_hashes)
        or any(not path for path in source_paths)
        or len(set(source_paths)) != len(source_paths)
        or any(not digest for digest in source_hashes)
        or len(set(source_hashes)) != len(source_hashes)
        or any(not path for path in panel_paths)
        or len(set(panel_paths)) != len(panel_paths)
    ):
        return None

    return {
        "value": clean[0][1],
        "observations": accepted,
        "independent_frame_count": len(clean),
        "first_timestamp_ms": clean[0][0],
        "last_timestamp_ms": clean[-1][0],
        "minimum_confidence": round(min(confidence for _, _, confidence, _ in clean), 4),
        "timestamp_span_ms": clean[-1][0] - clean[0][0],
        "maximum_gap_ms": max(gaps, default=0),
        "panel_context": clean[0][3]["panel_context"],
    }


def build(raw, observations, evidence_path, *, model_sha256=None, observation_root=None):
    """Create a refinement artifact without mutating ``raw``.

    ``observations`` must contain source-relative crop, decoded-frame, panel
    OCR and PTS-manifest proofs. When ``observation_root`` is supplied, all
    proof hashes and cross-file identities are checked before the artifact is
    returned.
    """

    if not is_concert_info_panel(raw):
        raise ValueError("Concert Info panel anchors are not proven.")
    if not _raw_slot_has_single_value(raw):
        raise ValueError("Support level is not a single unchanged panel value.")
    base_timestamp = raw.get("source_timestamp_ms")
    base_context = _extract_panel_context(raw)
    if not isinstance(base_timestamp, int) or not base_context:
        raise ValueError("Concert Info base timestamp or context is missing.")
    if any(observation.get("panel_context") != base_context for observation in observations or ()):
        raise ValueError("Concert Info support context does not match the base panel.")
    result = consensus(observations, base_timestamp_ms=base_timestamp)
    if result is None:
        raise ValueError("No bounded, provenance-backed numeric level consensus.")
    if observation_root is not None:
        _validate_observation_proofs(result["observations"], observation_root)
    evidence_path = Path(evidence_path)
    if not evidence_path.is_file():
        raise ValueError("Base panel evidence is missing.")
    artifact = {
        "version": 2,
        "source_timestamp_ms": raw.get("source_timestamp_ms"),
        "evidence": raw.get("evidence"),
        "raw_sha256": fingerprint(raw),
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "source_frame_sha256": raw.get("source_frame_sha256"),
        "field": FIELD,
        "box": list(SLOT_BOX),
        "value": result["value"],
        "current": result["value"],
        "planned": result["value"],
        "method": "concert_support_level_gray_contrast_temporal_consensus_v2",
        "minimum_confidence": result["minimum_confidence"],
        "independent_frame_count": result["independent_frame_count"],
        "timestamp_span_ms": result["timestamp_span_ms"],
        "maximum_gap_ms": result["maximum_gap_ms"],
        "maximum_base_distance_ms": DEFAULT_MAX_BASE_DISTANCE_MS,
        "panel_context": result["panel_context"],
        "observations": result["observations"],
    }
    if model_sha256:
        artifact["model_sha256"] = model_sha256
    return artifact


def apply(raw, refinement, evidence_path, *, observation_root=None, original=None):
    """Apply a validated level refinement to a copy of an OCR observation.

    The original detector lines remain in the result. A separate marked line
    carries the accepted numeric reading so downstream parsing can expose the
    field while retaining the raw ``Lvl O`` observation and its uncertainty.
    """

    source = original or raw
    required = (
        "raw_sha256",
        "evidence_sha256",
        "source_timestamp_ms",
        "evidence",
        "field",
        "box",
        "value",
        "observations",
    )
    if any(key not in refinement for key in required):
        raise ValueError("Incomplete Concert Info refinement.")
    if not is_concert_info_panel(source):
        raise ValueError("Concert Info panel anchors are not proven.")
    if not _raw_slot_has_single_value(source):
        raise ValueError("Support level is not a single unchanged panel value.")
    base_timestamp = source.get("source_timestamp_ms")
    base_context = _extract_panel_context(source)
    if not isinstance(base_timestamp, int) or not base_context:
        raise ValueError("Concert Info base timestamp or context is missing.")
    if refinement.get("panel_context") != base_context:
        raise ValueError("Concert Info refinement panel context changed.")
    if refinement["raw_sha256"] != fingerprint(source):
        raise ValueError("Concert Info refinement source mismatch.")
    evidence_path = Path(evidence_path)
    if not evidence_path.is_file() or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != refinement["evidence_sha256"]:
        raise ValueError("Concert Info panel evidence changed.")
    if refinement["source_timestamp_ms"] != source.get("source_timestamp_ms") or refinement["evidence"] != source.get("evidence"):
        raise ValueError("Concert Info panel identity mismatch.")
    if refinement["field"] != FIELD or tuple(refinement["box"]) != SLOT_BOX:
        raise ValueError("Unsupported Concert Info refinement field or box.")
    if type(refinement["value"]) is not int or refinement["value"] < 0:
        raise ValueError("Concert Info level must be a nonnegative integer.")
    if observation_root is None:
        raise ValueError("Observation evidence root is required for provenance validation.")
    if any(item.get("panel_context") != base_context for item in refinement["observations"]):
        raise ValueError("Concert Info support context does not match the base panel.")
    _validate_observation_proofs(refinement["observations"], observation_root)
    result = consensus(refinement["observations"], base_timestamp_ms=base_timestamp)
    if result is None or result["value"] != refinement["value"]:
        raise ValueError("Concert Info refinement consensus changed.")

    existing = []
    for line in _lines(source):
        if line.get("confidence", 0) < DEFAULT_MIN_CONFIDENCE or not _within(line, SLOT_BOX):
            continue
        value = normalize_level(line.get("text"))
        if value is not None:
            existing.append(value)
    if existing:
        if set(existing) == {refinement["value"]}:
            return raw
        raise ValueError("Concert Info slot already contains a conflicting accepted value.")

    lines = list(raw.get("lines", []))
    marked = dict(
        text=f"Lvl {refinement['value']}",
        confidence=result["minimum_confidence"],
        box=list(SLOT_BOX),
        refinement="concert_support_level",
        refinement_evidence=[item["evidence"] for item in result["observations"]],
        refinement_evidence_sha256=[item["evidence_sha256"] for item in result["observations"]],
        refinement_source_frames=[item["source_frame_evidence"] for item in result["observations"]],
        refinement_source_pts=[item["source_pts"] for item in result["observations"]],
        refinement_source_timestamps_ms=[item["timestamp_ms"] for item in result["observations"]],
    )
    lines.append(marked)
    extra = dict(refinement)
    extra["consensus"] = {
        key: result[key]
        for key in ("independent_frame_count", "timestamp_span_ms", "maximum_gap_ms", "minimum_confidence")
    }
    return dict(raw, lines=lines, concert_bonus_refinement=extra)
