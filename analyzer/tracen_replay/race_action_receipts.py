"""Source-bound assembly of completed race action receipts.

Race result rows and action receipts are produced by different parts of the
reader.  This module joins them only when the receipt carries an explicit
race id and its source time/evidence identifies exactly one result row.  A
matching label, a nearby timestamp, or an expected evaluation label is never
used to make the join.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re


_BASIS = "explicit_race_id_source_interval_evidence"
_VALID_PHASES = frozenset({"preview", "committed", "applied", "observed", "context"})
_INVALID_PHASE = object()
_RACE_CONFLICT_FIELDS = frozenset({
    "race_name", "name", "placing", "fans", "fans_gained", "course",
    "course.condition", "race_grade", "grade",
})
_RACE_GRADE_RE = re.compile(r"^(?:DEBUT|G[123]|OP|PRE[- ]?OP|EX)$", re.I)
_INTERVAL_VALUE_KEYS = (
    "race_source_interval_ms",
    "source_interval_ms",
    "result_interval_ms",
)
_INTERVAL_START_END_KEYS = (
    ("race_source_start_ms", "race_source_end_ms"),
    ("source_start_ms", "source_end_ms"),
    ("result_first_seen_ms", "result_last_seen_ms"),
    ("completion_first_seen_ms", "completion_last_seen_ms"),
    ("first_seen_ms", "last_seen_ms"),
)


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(value):
    return type(value) is int and value >= 0


def _normalized_race_grade(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value.strip()).upper()
    if value == "PRE OP":
        value = "PRE-OP"
    return value if _RACE_GRADE_RE.fullmatch(value) else None


def _paths(value):
    """Normalize evidence paths without collapsing them to basenames."""

    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result = []
    for item in value:
        if isinstance(item, (list, tuple, set)):
            result.extend(_paths(item))
        elif isinstance(item, str) and item.strip():
            result.append(item.replace("\\", "/").strip())
    return list(dict.fromkeys(result))


def _phase(action):
    values = []
    for key in ("phase", "observation_phase", "action_phase"):
        if key not in action:
            continue
        value = action.get(key)
        if not isinstance(value, str) or value not in _VALID_PHASES:
            return _INVALID_PHASE
        values.append(value)
    if not values:
        return None
    if len(set(values)) != 1:
        return _INVALID_PHASE
    return values[0]


def _coerce_interval(value):
    if isinstance(value, Mapping):
        for start_key, end_key in (
            ("start_ms", "end_ms"),
            ("first_seen_ms", "last_seen_ms"),
        ):
            if start_key in value or end_key in value:
                value = (value.get(start_key), value.get(end_key))
                break
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    start, end = value
    if not _integer(start) or not _integer(end) or start > end:
        return None
    return start, end


def _explicit_interval(row):
    """Return an explicitly declared interval, or a timestamp point.

    Presence of a malformed interval is a hard failure.  Falling back to a
    timestamp in that case would turn bad source metadata into a successful
    cross-race association.
    """

    if not isinstance(row, Mapping):
        return None, "invalid_row"
    for key in _INTERVAL_VALUE_KEYS:
        if key in row:
            interval = _coerce_interval(row.get(key))
            return interval, key if interval is not None else "invalid_" + key
    for start_key, end_key in _INTERVAL_START_END_KEYS:
        if start_key in row or end_key in row:
            start, end = row.get(start_key), row.get(end_key)
            if not _integer(start) or not _integer(end) or start > end:
                return None, "invalid_" + start_key
            return (start, end), start_key
    timestamp = row.get("source_timestamp_ms")
    if _integer(timestamp):
        return (timestamp, timestamp), "source_timestamp_ms"
    return None, "missing_source_interval"


def _record_interval(record):
    """Get the canonical result interval, including an observed completion."""

    # Prefer the result-panel interval when it is present.  The generic
    # action interval order includes completion fields, but using that order
    # here would accidentally discard the later result panel when both spans
    # are available.
    detail = None
    if "first_seen_ms" in record or "last_seen_ms" in record:
        detail = _coerce_interval((record.get("first_seen_ms"), record.get("last_seen_ms")))
        if detail is None:
            return None, "invalid_first_seen_ms"
        detail_key = "first_seen_ms"
    else:
        detail, detail_key = _explicit_interval(record)
        if detail is None:
            return None, detail_key

    # A completion animation can precede the first visible result panel.  It
    # is part of the same canonical record only when the record explicitly
    # carries both completion endpoints; the union is still source-bounded.
    completion = None
    start, end = record.get("completion_first_seen_ms"), record.get("completion_last_seen_ms")
    if start is not None or end is not None:
        if not _integer(start) or not _integer(end) or start > end:
            return None, "invalid_completion_interval"
        completion = (start, end)
    if completion is None:
        return detail, detail_key
    return (min(detail[0], completion[0]), max(detail[1], completion[1])), detail_key


def _record_id(record):
    ids = {_text(record.get(key)) for key in ("id", "race_id") if _text(record.get(key)) is not None}
    if len(ids) != 1:
        return None
    return next(iter(ids))


def _record_evidence(record):
    evidence = _paths(record.get("evidence"))
    evidence.extend(_paths(record.get("completion_evidence")))
    provenance = record.get("completion_provenance")
    if isinstance(provenance, Mapping):
        for observation in provenance.get("observations", ()):
            if isinstance(observation, Mapping):
                evidence.extend(_paths(observation.get("evidence")))
    return list(dict.fromkeys(evidence))


def _record_grade_conflicted(record):
    """Treat malformed or unscoped conflict metadata as grade-unknown."""

    if "conflicting_readings" not in record:
        return False
    conflicts = record.get("conflicting_readings")
    if not isinstance(conflicts, Mapping):
        return True
    if any(key not in _RACE_CONFLICT_FIELDS for key in conflicts):
        return True
    return any(key in conflicts for key in ("race_grade", "grade"))


def _record_metadata(record):
    """Return independently readable fields, leaving conflicts unknown."""

    conflicts = record.get("conflicting_readings")
    conflicts = conflicts if isinstance(conflicts, Mapping) else {}
    metadata = {}
    statuses = {}

    name_values = []
    for key in ("race_name", "name"):
        value = _text(record.get(key))
        if value is not None:
            name_values.append(value)
    if "race_name" in conflicts or "name" in conflicts:
        statuses["race_name"] = "unknown_conflicting_readings"
    elif len(set(name_values)) > 1:
        statuses["race_name"] = "unknown_alias_conflict"
    elif name_values:
        metadata["race_name"] = name_values[0]
        statuses["race_name"] = "observed"
    else:
        statuses["race_name"] = "unknown_missing"

    placing = record.get("placing")
    if "placing" in conflicts:
        statuses["placing"] = "unknown_conflicting_readings"
    elif type(placing) is int and placing >= 1:
        metadata["placing"] = placing
        statuses["placing"] = "observed"
    else:
        statuses["placing"] = "unknown_missing"

    grade_values = []
    malformed_grades = []
    for key in ("race_grade", "grade"):
        value = record.get(key)
        if value is None:
            continue
        normalized = _normalized_race_grade(value)
        if normalized is None:
            malformed_grades.append(value)
        else:
            grade_values.append(normalized)
    if "race_grade" in conflicts or "grade" in conflicts:
        statuses["race_grade"] = "unknown_conflicting_readings"
    elif _record_grade_conflicted(record):
        statuses["race_grade"] = "unknown_conflicting_readings"
    elif malformed_grades or len(set(grade_values)) > 1:
        statuses["race_grade"] = "unknown_alias_conflict"
    elif grade_values:
        metadata["race_grade"] = grade_values[0]
        statuses["race_grade"] = "observed"
    else:
        statuses["race_grade"] = "unknown_missing"
    return metadata, statuses


def _action_metadata_conflicts(action, metadata):
    """Detect source fields already present on an action that disagree."""

    conflicts = []
    raw_names = [action.get(key) for key in ("race_name", "name")]
    malformed_names = [value for value in raw_names if value is not None and _text(value) is None]
    values = [_text(value) for value in raw_names]
    values = [value for value in values if value is not None]
    if malformed_names or len(set(values)) > 1:
        conflicts.append("race_name")
    elif values and "race_name" in metadata and values[0] != metadata["race_name"]:
        conflicts.append("race_name")
    placing = action.get("placing")
    if placing is not None and (
        type(placing) is not int or placing < 1
    ):
        conflicts.append("placing")
    elif type(placing) is int and "placing" in metadata:
        if placing != metadata["placing"]:
            conflicts.append("placing")

    raw_grades = [action.get(key) for key in ("race_grade", "grade")]
    malformed_grades = [value for value in raw_grades
                        if value is not None and _normalized_race_grade(value) is None]
    grade_values = [_normalized_race_grade(value) for value in raw_grades if value is not None]
    if (malformed_grades or len(set(grade_values)) > 1
            or (grade_values and "race_grade" in metadata
                and grade_values[0] != metadata["race_grade"])):
        conflicts.append("race_grade")
    return conflicts


def race_action_binding(action, race_records):
    """Resolve one race action to one source result record.

    The return value is diagnostic and intentionally keeps metadata partial:
    a readable placing may be returned while a conflicting name remains
    unknown.  ``status`` is ``bound`` only for one matching id, interval and
    evidence record.  Every other outcome is ``unknown``.
    """

    binding = {"status": "unknown", "basis": _BASIS}
    if not isinstance(action, Mapping) or action.get("kind") != "race":
        binding["reason"] = "not_race_action"
        return binding
    binding["race_id"] = _text(action.get("race_id"))
    phase = _phase(action)
    if phase is _INVALID_PHASE:
        binding["reason"] = "invalid_phase"
        return binding
    if phase == "preview" or action.get("is_preview") is True or action.get("preview") is True:
        binding["reason"] = "preview_is_not_completed_action"
        return binding
    race_id = binding["race_id"]
    if race_id is None:
        binding["reason"] = "missing_explicit_race_id"
        return binding

    records = [record for record in (race_records or ()) if isinstance(record, Mapping)]
    identified = [record for record in records if _record_id(record) == race_id]
    binding["candidate_count"] = len(identified)
    if not identified:
        binding["reason"] = "no_matching_race_id"
        return binding

    action_interval, action_interval_key = _explicit_interval(action)
    binding["action_interval_key"] = action_interval_key
    if action_interval is None:
        binding["reason"] = action_interval_key
        return binding
    timestamp = action.get("source_timestamp_ms")
    if timestamp is not None and (
        not _integer(timestamp)
        or not action_interval[0] <= timestamp <= action_interval[1]
    ):
        binding["reason"] = "timestamp_outside_action_interval"
        return binding
    action_evidence = _paths(action.get("evidence"))
    if not action_evidence:
        binding["reason"] = "missing_action_evidence"
        return binding

    matches = []
    rejected = []
    for record in identified:
        record_interval, record_interval_key = _record_interval(record)
        record_evidence = _record_evidence(record)
        overlap = sorted(set(action_evidence).intersection(record_evidence))
        in_interval = (
            record_interval is not None
            and action_interval[0] <= record_interval[1]
            and record_interval[0] <= action_interval[1]
        )
        if in_interval and overlap:
            matches.append((record, record_interval, overlap))
        else:
            rejected.append({
                "record_interval_key": record_interval_key,
                "interval_match": in_interval,
                "evidence_overlap": overlap,
            })
    binding["rejected_candidates"] = rejected
    if len(matches) != 1:
        binding["reason"] = "no_unique_source_match" if not matches else "ambiguous_source_match"
        return binding

    record, record_interval, overlap = matches[0]
    metadata, metadata_status = _record_metadata(record)
    action_conflicts = _action_metadata_conflicts(action, metadata)
    for field in action_conflicts:
        metadata.pop(field, None)
        metadata_status[field] = "unknown_action_conflict"
    binding.update(
        status="bound",
        record_id=_record_id(record),
        source_interval_ms=list(record_interval),
        action_interval_ms=list(action_interval),
        evidence_overlap=overlap,
        metadata=metadata,
        metadata_status=metadata_status,
    )
    return binding


def bind_race_action_metadata(action, race_records):
    """Copy only source-bound race name/grade/placing fields onto one action."""

    result = deepcopy(action)
    binding = race_action_binding(result, race_records)
    if binding.get("status") != "bound":
        return result
    metadata = binding.get("metadata", {})
    if "race_name" in metadata:
        result["race_name"] = metadata["race_name"]
    if "placing" in metadata:
        result["placing"] = metadata["placing"]
    if "race_grade" in metadata:
        result["race_grade"] = metadata["race_grade"]
    for field in binding.get("metadata_status", {}):
        status = binding["metadata_status"][field]
        if status == "unknown_conflicting_readings" and field == "race_grade":
            result.pop("race_grade", None)
            result.pop("grade", None)
        if status != "unknown_action_conflict":
            continue
        if field == "race_name":
            result.pop("race_name", None)
            result.pop("name", None)
        elif field == "placing":
            result.pop("placing", None)
        elif field == "race_grade":
            result.pop("race_grade", None)
            result.pop("grade", None)
    return result


def assemble_race_action_receipts(race_records):
    """Build one source-bound action receipt for every completed race record."""

    records = [record for record in (race_records or ()) if isinstance(record, Mapping)]
    actions = []
    for record in records:
        race_id = _record_id(record)
        timestamp = record.get("completion_first_seen_ms", record.get("first_seen_ms"))
        if race_id is None or not _integer(timestamp):
            # A record without an explicit id or source timestamp cannot
            # become a committed action.  The race result remains available
            # to callers as an unresolved record.
            continue
        action = dict(
            kind="race",
            source_timestamp_ms=timestamp,
            evidence=list(dict.fromkeys(
                _paths(record.get("completion_evidence"))
                + _paths(record.get("evidence"))
                + _record_evidence(record)
            )),
            race_id=race_id,
            click_timestamp_ms=None,
        )
        actions.append(bind_race_action_metadata(action, records))
    return actions


__all__ = [
    "assemble_race_action_receipts",
    "bind_race_action_metadata",
    "race_action_binding",
]
