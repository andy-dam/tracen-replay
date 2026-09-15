"""Grade the frozen final numeric cases against a gameplay report.

This evaluator is deliberately narrower than the full-report evaluator.  It
joins a frozen numeric case to report-owned training observations by field and
source evidence/time.  The frozen amount and event id are used only after the
join has been made: event ids may be renumbered and an expected amount must
never select a prediction.  State-derived rows remain state-derived even when
their amount happens to agree with the source label.

The command accepts either one report or a directory containing the three
``*-report.json`` files.  It emits JSON with per-case verdicts, candidate
provenance, report/reference hashes, and aggregate counts.  No OCR or video
processing is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping


SCHEMA = "tracen-replay/final-numeric-evaluation-v1"
CAUSES_SCHEMA = "tracen-replay/final-numeric-causes-v1"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
# These are the production causal-accounting vocabularies.  Keep this an
# exact-token check: substring matching would incorrectly classify a basis
# such as ``indirect`` as direct evidence.
CANONICAL_DIRECT_BASES = {
    "observed_receipt",
    "observed_training_gain",
    "committed_skill_debit",
}
CANONICAL_DERIVED_BASES = {
    "state_derived",
    "state_constrained",
    "summary_only",
    "projected_debit",
    "unresolved",
}
CANONICAL_UNRESOLVED_STATUSES = {
    "ambiguous",
    "conflict",
    "conflicting",
    "unresolved",
    "unresolved_attribution",
    "missing_field_timing",
    "unobserved_amount",
}
VERDICT_ORDER = (
    "correct_direct",
    "correct_derived",
    "wrong_amount",
    "wrong_turn",
    "ambiguous_attribution",
    "unobservable",
    "missing",
    "duplicate",
    "cancellation",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = Path(path).read_text(encoding="utf-8")
    document = json.loads(value)
    if not isinstance(document, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return document


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _unique_text(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _looks_like_evidence(value: str) -> bool:
    lower = re.split(r"[?#]", value.replace("\\", "/").lower(), 1)[0]
    return lower.endswith(IMAGE_SUFFIXES)


def _evidence_recording(value: str) -> str | None:
    """Return an explicit recording component from a cache path.

    Reports normally store paths relative to the selected recording cache, so
    a relative ``training-inspection/...`` path has no recording component and
    remains valid.  Paths that explicitly name ``.local/full-recording/<run>``
    do carry identity, however, and must not be compared by suffix alone.
    """
    if not isinstance(value, str):
        return None
    path = re.sub(r"/+", "/", value.replace("\\", "/").strip().lower())
    match = re.search(r"(?:^|/)\\.local/full-recording/([^/]+)/", path)
    if match:
        return match.group(1)
    # Preserve the same guard for an absolute path whose caller omitted the
    # leading .local component while still referring to a recording cache.
    match = re.search(r"(?:^|/)full-recording/([^/]+)/", path)
    return match.group(1) if match else None


def _foreign_evidence_paths(value: Any, recording: str | None) -> set[str]:
    """Find explicitly cache-qualified evidence belonging to another run."""
    if not recording:
        return set()
    expected = recording.strip("/").lower()
    return {
        path
        for path in _collect_evidence(value)
        if (source_run := _evidence_recording(path)) is not None
        and source_run != expected
    }


def _evidence_path_allowed(value: str, recording: str | None) -> bool:
    source_run = _evidence_recording(value)
    return not recording or source_run is None or source_run == recording.strip("/").lower()


def _collect_evidence(value: Any) -> list[str]:
    """Collect image paths without treating OCR prose as evidence."""
    result: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, str):
            if _looks_like_evidence(item.strip()):
                result.append(item.strip())
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)

    walk(value)
    return _unique_text(result)


def _normalise_evidence(value: str, recording: str | None = None) -> str:
    """Normalize absolute and relative evidence paths to comparable suffixes."""
    path = value.replace("\\", "/").strip().lower()
    path = re.sub(r"^file://", "", path)
    path = re.split(r"[?#]", path, 1)[0]
    path = re.sub(r"/+", "/", path)
    # The frozen file records .local/full-recording/<run>/..., whereas reports
    # normally record the path relative to that run's cache root.
    marker = "/.local/full-recording/"
    if marker in path:
        suffix = path.split(marker, 1)[1]
        parts = suffix.split("/", 1)
        path = parts[1] if len(parts) == 2 else suffix
    elif path.startswith(".local/full-recording/"):
        suffix = path[len(".local/full-recording/"):]
        parts = suffix.split("/", 1)
        path = parts[1] if len(parts) == 2 else suffix
    # A caller may give the cache root explicitly in a synthetic report.
    if recording:
        prefix = f"{recording.lower().strip('/')}/"
        if path.startswith(prefix):
            path = path[len(prefix):]
    return path.lstrip("./")


def _source_paths(value: Any, recording: str | None = None) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                paths.update(_source_paths(row, recording))
            elif isinstance(row, list):
                paths.update(_source_paths(row, recording))
            elif isinstance(row, str) and _looks_like_evidence(row):
                if _evidence_path_allowed(row, recording):
                    paths.add(_normalise_evidence(row, recording))
    elif isinstance(value, dict):
        path = value.get("path") or value.get("evidence")
        if isinstance(path, str) and _looks_like_evidence(path):
            if _evidence_path_allowed(path, recording):
                paths.add(_normalise_evidence(path, recording))
        elif isinstance(path, (dict, list)):
            paths.update(_source_paths(path, recording))
    elif isinstance(value, str) and _looks_like_evidence(value):
        if _evidence_path_allowed(value, recording):
            paths.add(_normalise_evidence(value, recording))
    return paths


def _path_matches(left: str, right: str) -> bool:
    if left == right:
        return True
    return left.endswith("/" + right) or right.endswith("/" + left)


def _evidence_matches(left: set[str], right: set[str]) -> set[str]:
    return {a for a in left for b in right if _path_matches(a, b)}


def _interval(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = _int(value[0]), _int(value[1])
        if start is not None and end is not None:
            if end < start:
                start, end = end, start
            return start, end
    return None


def _row_interval(row: Mapping[str, Any]) -> tuple[int, int] | None:
    for key in ("observation_window_ms", "interval_ms", "scope_ms"):
        interval = _interval(row.get(key))
        if interval is not None:
            return interval
    start = None
    end = None
    for key in ("first_seen_ms", "source_timestamp_ms", "observed_at_ms", "start_ms",
                "observation_start_ms", "timestamp_ms"):
        start = _int(row.get(key))
        if start is not None:
            break
    for key in ("last_seen_ms", "end_ms", "observation_end_ms"):
        end = _int(row.get(key))
        if end is not None:
            break
    if start is None:
        return None
    return start, end if end is not None else start


def _interval_overlap(left: tuple[int, int] | None, right: tuple[int, int] | None) -> int:
    if left is None or right is None:
        return 0
    # Source timestamps include point observations, so use inclusive overlap.
    return max(0, min(left[1], right[1]) - max(left[0], right[0]) + 1)


def _pointer(value: Any, path: str | None) -> Any:
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    current = value
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            return None
    return current


def _nested(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _field_map(row: Mapping[str, Any], key: str, field_name: str) -> Any:
    value = row.get(key)
    if isinstance(value, Mapping):
        return value.get(field_name)
    return None


def _normalise_basis(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _basis_values(row: Mapping[str, Any], field_name: str) -> list[str]:
    values: list[Any] = []
    for key in ("basis", "direct_evidence_basis", "evidence_basis", "observation_basis"):
        values.append(row.get(key))
    for key in ("field_basis", "field_evidence_basis", "field_status"):
        values.append(_field_map(row, key, field_name))
    values.append(_nested(row.get("facts"), "basis", "observation_basis"))
    result: list[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(
                normalised for item in value
                if (normalised := _normalise_basis(item)) is not None
            )
        elif isinstance(value, str):
            normalised = _normalise_basis(value)
            if normalised is not None:
                result.append(normalised)
    return _unique_text(result)


def _basis_info(bases: Iterable[str], evidence: set[str], row: Mapping[str, Any],
                field_name: str) -> tuple[bool, str]:
    """Classify basis metadata without treating source presence as proof.

    This compatibility helper intentionally returns ``False`` for every
    source-linked basis.  Direct credit additionally needs the canonical
    contribution's independent-effect flag, a resolved field proof, and no
    unresolved conflict; those checks live in :func:`_candidate_direct`.
    """
    values = {_normalise_basis(value) for value in bases}
    values.discard(None)
    derived = values & CANONICAL_DERIVED_BASES
    if field_name in _as_list(row.get("result_state_derived_fields")):
        derived.add("state_derived")
    if derived:
        return False, "state_derived" if "state_derived" in derived else sorted(derived)[0]
    if values & CANONICAL_DIRECT_BASES:
        return False, "independent_effect_unverified"
    return False, "unobservable"


def _conflict_values(row: Mapping[str, Any], field_name: str) -> list[Any]:
    result: list[Any] = []
    for key in ("conflicting_readings", "gain_reading_disagreements", "animated_candidates"):
        value = row.get(key)
        if isinstance(value, Mapping):
            value = value.get(field_name)
            if isinstance(value, Mapping):
                value = _nested(value, "animated_candidates", "candidates", "values")
        if isinstance(value, list):
            result.extend(value)
    # A field-level disagreement can be embedded under the field evidence
    # object in synthetic reports.
    disagreement = _field_map(row, "disagreements", field_name)
    if isinstance(disagreement, list):
        result.extend(disagreement)
    return [value for value in result if _number(value) is not None]


def _owner_values(row: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    candidates: set[str] = set()
    for key in ("turn_id", "actual_turn_id", "owner_turn_id", "report_turn_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            exact.add(value)
    for key in ("candidate_turn_ids", "turn_ids", "owner_turn_ids"):
        value = row.get(key)
        for item in _as_list(value):
            if isinstance(item, str) and item:
                candidates.add(item)
    for nested_key in ("ownership", "turn_ownership", "boundary_state_recovery",
                       "training_gain_recovery"):
        nested = row.get(nested_key)
        rows = nested if isinstance(nested, list) else [nested]
        for item in rows:
            if isinstance(item, Mapping):
                item_exact, item_candidates = _owner_values(item)
                exact.update(item_exact)
                candidates.update(item_candidates)
    return exact, candidates


def _event_parent_ref(ref: Any) -> str | None:
    if not isinstance(ref, str):
        return None
    match = re.match(r"^(/(?:gameplay_tracking/)?events/\d+)(?:/|$)", ref)
    return match.group(1) if match else None


def _boolean_field(row: Mapping[str, Any], key: str, field_name: str) -> list[bool]:
    """Read the canonical independent-proof flag at row or field scope."""
    value = row.get(key)
    if isinstance(value, Mapping):
        value = value.get(field_name)
    return [value] if isinstance(value, bool) else []


def _proof_shape(value: Any) -> tuple[str, ...] | None:
    """Normalize a serialized gain-shape proof without accepting free text."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if any(not isinstance(item, str) or not item for item in value):
        return None
    normalized = tuple(sorted(set(value)))
    return normalized if len(normalized) == len(value) else None


def _numeric_list(value: Any, *, minimum: int = 1) -> list[int | float] | None:
    """Validate a non-empty numeric list while rejecting scalar coercion."""
    if not isinstance(value, list) or len(value) < minimum:
        return None
    result: list[int | float] = []
    for item in value:
        number = _number(item)
        if number is None:
            return None
        result.append(number)
    if len(set(result)) != len(result):
        return None
    return result


def _proof_evidence(value: Any, recording: str | None,
                    seen: set[str]) -> str | None:
    """Require one unique, image-backed proof frame per observation."""
    if (not isinstance(value, str) or not value.strip()
            or not _looks_like_evidence(value.strip())
            or not _evidence_path_allowed(value.strip(), recording)):
        return None
    normalized = _normalise_evidence(value.strip(), recording)
    if not normalized or normalized in seen:
        return None
    seen.add(normalized)
    return value.strip()


def _phase_proof_observations(value: Any, *, recording: str | None = None,
                              field_name: str | None = None,
                              seen_evidence: set[str] | None = None,
                              minimum: int = 2) -> list[dict[str, Any]] | None:
    """Validate repeated typed observations in a phase-resolution proof.

    A phase claim is source evidence, not an assertion emitted by the
    producer.  Every observation therefore needs a unique physical image,
    an integer timestamp, a numeric value, and a valid shape.  Reusing one
    frame or supplying an arbitrary/non-image evidence value must not create
    independent support.
    """
    if not isinstance(value, list) or len(value) < minimum:
        return None
    result: list[dict[str, Any]] = []
    timestamps: set[int] = set()
    evidence_seen = seen_evidence if seen_evidence is not None else set()
    for item in value:
        if not isinstance(item, Mapping):
            return None
        timestamp = item.get("source_timestamp_ms")
        observed = _number(item.get("value"))
        shape = _proof_shape(item.get("shape"))
        evidence = _proof_evidence(item.get("evidence"), recording, evidence_seen)
        if field_name is not None and (shape is None or field_name not in shape):
            return None
        if "observed_amounts" in item:
            nested = _numeric_list(item.get("observed_amounts"), minimum=1)
            if nested is None or set(nested) != {observed}:
                return None
        if (type(timestamp) is not int or observed is None or shape is None
                or evidence is None or timestamp in timestamps):
            return None
        timestamps.add(timestamp)
        result.append({
            "source_timestamp_ms": timestamp,
            "value": observed,
            "shape": shape,
            "evidence": evidence,
        })
    if [row["source_timestamp_ms"] for row in result] != sorted(timestamps):
        return None
    return result


def _source_temporal_crop_proof(value: Any, observations: list[dict[str, Any]],
                                *, accepted: int | float,
                                field_name: str,
                                require_canonical: bool) -> int | None:
    """Validate the crop metadata retained by ``source_temporal`` proofs.

    ``source_temporal`` is a producer-side exception to the generic shape
    superset rule.  Its amount decision is therefore admissible only when the
    serialized rows retain the source crop resolver's canonical provenance.
    Empty crop metadata is allowed for ordinary component observations, but a
    partially populated or contradictory crop envelope is rejected.  The
    returned count is the number of rows with complete canonical crop proof.
    """
    if not isinstance(value, list) or len(value) != len(observations):
        return None
    canonical_count = 0
    for raw, observation in zip(value, observations):
        if not isinstance(raw, Mapping):
            return None
        basis = raw.get("crop_basis")
        conflict_state = raw.get("crop_conflict_state")
        region = raw.get("crop_region")
        candidates = raw.get("crop_candidate_amounts")
        observed = observation["value"]
        # ``_source_phase_proof`` emits these keys for every row.  A fully
        # empty envelope means that the source reader did not supply crop
        # provenance; it is acceptable only for component rows.
        empty = (basis is None and conflict_state is None and region is None
                 and candidates == [])
        if empty:
            if require_canonical:
                return None
            continue
        # A component frame can be retained by the source resolver as an
        # explicitly unresolved crop diagnostic.  It is never counted as a
        # canonical crop proof, and it cannot stand in for the accepted full
        # amount.  Accept only this closed vocabulary with a well-formed
        # candidate list; arbitrary partial metadata remains invalid.
        unresolved_component = (
            not require_canonical
            and observed != accepted
            and basis == "unresolved_source_crop_evidence"
            and conflict_state == "unresolved_nonprefix_or_equal_geometry_conflict"
            and region is None
        )
        if unresolved_component:
            candidate_amounts = _numeric_list(candidates, minimum=1)
            if candidate_amounts is None or observed not in set(candidate_amounts):
                return None
            continue
        if (basis not in {
                    "source_crop_family_geometry",
                    "source_crop_family_geometry_prefix_resolution",
                    "same_family_scaled_crop_prefix_consensus",
                    # The localized badge reader is a separate source-bound
                    # crop family.  Its canonical amount is established by
                    # same-frame pixel localization plus the tight training
                    # badge, so it must remain admissible when serialized as
                    # a full phase observation.
                    "source_crop_pixel_localized_training_badge_agreement",
                }
                or conflict_state not in {
                    "resolved_same_amount_across_source_crops",
                    "resolved_broader_prefix",
                    "resolved_same_family_prefix",
                    "resolved_source_pixel_localized",
                }
                or not isinstance(region, str) or not region.strip()):
            return None
        if not region.strip().endswith(f".{field_name}"):
            return None
        candidate_amounts = _numeric_list(candidates, minimum=1)
        if (candidate_amounts is None
                or observed not in set(candidate_amounts)
                or accepted not in set(candidate_amounts)
                and observed == accepted):
            return None
        canonical_count += 1
    return canonical_count


def _source_temporal_diagnostic_proof(value: Any, *, recording: str | None,
                                      seen_evidence: set[str],
                                      field_name: str,
                                      allowed_values: set[int | float]) -> tuple[list[dict[str, Any]], set[int | float]] | None:
    """Validate an optional unproven source-temporal recovery diagnostic."""
    if not isinstance(value, list):
        return None
    result: list[dict[str, Any]] = []
    values: set[int | float] = set()
    timestamps: set[int] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return None
        timestamp = item.get("source_timestamp_ms")
        observed = _number(item.get("value"))
        shape = _proof_shape(item.get("shape"))
        evidence = _proof_evidence(item.get("evidence"), recording, seen_evidence)
        if (type(timestamp) is not int or observed is None or shape is None
                or evidence is None or timestamp in timestamps
                or observed in allowed_values):
            return None
        # The diagnostic is allowed to come from a frame where this field is
        # not readable; the value is retained only to explain why the source
        # window had an extra alternative.  It is never part of the accepted
        # phase proof.
        # A diagnostic row must remain explicitly unproven.  A crop-backed
        # third amount is a competing source observation and must not be
        # hidden behind ``ignored_unproven_values``.
        if (item.get("crop_basis") is not None
                or item.get("crop_conflict_state") is not None
                or item.get("crop_region") is not None
                or item.get("crop_candidate_amounts") not in (None, [])):
            return None
        timestamps.add(timestamp)
        values.add(observed)
        result.append({
            "source_timestamp_ms": timestamp,
            "value": observed,
            "shape": shape,
            "evidence": evidence,
        })
    if [row["source_timestamp_ms"] for row in result] != sorted(timestamps):
        return None
    return result, values


def _explicit_turn_ids(value: Any) -> set[str]:
    """Return only turn ids explicitly carried by one serialized row."""
    if not isinstance(value, Mapping):
        return set()
    result: set[str] = set()
    for key in ("turn_id", "actual_turn_id", "owner_turn_id", "report_turn_id"):
        item = value.get(key)
        if isinstance(item, str) and item:
            result.add(item)
    return result


def _result_reading_index(report: Mapping[str, Any] | None,
                          recording: str | None) -> dict[tuple[int, str], list[tuple[str, Mapping[str, Any]]]]:
    """Index report-owned readings by their physical timestamp and evidence path."""
    index: dict[tuple[int, str], list[tuple[str, Mapping[str, Any]]]] = {}
    if not isinstance(report, Mapping):
        return index
    for ref, actual in _readings(report):
        timestamp = actual.get("source_timestamp_ms")
        if type(timestamp) is not int:
            continue
        for path in _source_paths(actual.get("evidence"), recording):
            index.setdefault((timestamp, path), []).append((ref, actual))
    return index


def _result_group_proof(row: Mapping[str, Any], proof_observations: list[dict[str, Any]],
                        recording: str | None,
                        report: Mapping[str, Any] | None = None) -> set[str] | None:
    """Validate phase frames against the producer's result-reading group.

    ``action_identity_evidence`` answers which frames identify the selected
    training option.  It is intentionally smaller than the result animation
    and cannot be used as the membership set for every component frame.  The
    transaction producer therefore carries the bounded result-reading group
    separately.  This check binds every nested proof to one member of that
    group by its normalized physical path and source timestamp, while also
    checking the event option and group boundaries.

    The helper returns ``None`` for an absent or malformed envelope.  Callers
    distinguish those cases so legacy reports can retain their older,
    stricter action-identity fallback without allowing a malformed new
    envelope to be ignored.
    """
    group = row.get("result_group")
    if not isinstance(group, Mapping):
        return None
    event_interval = _row_interval(row)
    interval_value = group.get("interval_ms")
    if (event_interval is None or not isinstance(interval_value, (list, tuple))
            or len(interval_value) != 2 or any(type(value) is not int for value in interval_value)
            or interval_value[0] > interval_value[1]
            or tuple(interval_value) != event_interval):
        return None
    event_option = row.get("training_option")
    group_option = group.get("training_option")
    if (not isinstance(event_option, str) or not event_option.strip()
            or not isinstance(group_option, str) or group_option != event_option):
        return None
    members = group.get("observations")
    if not isinstance(members, list) or not members:
        return None
    actual_readings = _result_reading_index(report, recording)
    if not actual_readings:
        return None
    event_turn_ids = _explicit_turn_ids(row)
    group_turn_ids = _explicit_turn_ids(group)
    if event_turn_ids and group_turn_ids and event_turn_ids != group_turn_ids:
        return None
    expected_turn_ids = event_turn_ids | group_turn_ids
    by_identity: dict[tuple[int, str], Mapping[str, Any]] = {}
    for member in members:
        if not isinstance(member, Mapping):
            return None
        timestamp = member.get("source_timestamp_ms")
        evidence = member.get("evidence")
        if (type(timestamp) is not int or timestamp < interval_value[0]
                or timestamp > interval_value[1]
                or not isinstance(evidence, str) or not _looks_like_evidence(evidence)
                or not _evidence_path_allowed(evidence, recording)):
            return None
        member_option = member.get("training_option")
        if (member_option is not None
                and (not isinstance(member_option, str) or member_option != group_option)):
            return None
        if (member.get("screen") is not None
                and member.get("screen") != "training_result"):
            return None
        normalized = _normalise_evidence(evidence, recording)
        identity = (timestamp, normalized)
        if identity in by_identity:
            return None
        actual_matches = actual_readings.get(identity, [])
        # The group is only an index over report-owned readings.  A matching
        # suffix is insufficient: a fabricated same-recording path, a copied
        # timestamp, or two actual rows for the same physical frame leaves the
        # source identity ambiguous.
        if len(actual_matches) != 1:
            return None
        actual_ref, actual = actual_matches[0]
        if actual.get("screen") != "training_result":
            return None
        actual_option = actual.get("training_option")
        if (actual_option is not None
                and (not isinstance(actual_option, str)
                     or actual_option != group_option)):
            return None
        member_turn_ids = _explicit_turn_ids(member)
        actual_turn_ids = _explicit_turn_ids(actual)
        if member_turn_ids and expected_turn_ids and member_turn_ids != expected_turn_ids:
            return None
        if actual_turn_ids and expected_turn_ids and actual_turn_ids != expected_turn_ids:
            return None
        if member_turn_ids and actual_turn_ids and member_turn_ids != actual_turn_ids:
            return None
        by_identity[identity] = member

    matched: set[str] = set()
    for observation in proof_observations:
        timestamp = observation.get("source_timestamp_ms")
        evidence = observation.get("evidence")
        if (type(timestamp) is not int or not isinstance(evidence, str)
                or not _looks_like_evidence(evidence)
                or not _evidence_path_allowed(evidence, recording)):
            return None
        normalized = _normalise_evidence(evidence, recording)
        if (timestamp, normalized) not in by_identity:
            return None
        matched.add(normalized)
    return matched


def _phase_resolution_amount(row: Mapping[str, Any], field_name: str,
                             recording: str | None = None,
                             report: Mapping[str, Any] | None = None) -> tuple[str, int | float] | None:
    """Return a phase proof only when its complete/component evidence is coherent.

    Production emits ``gain_phase_candidates[field].source_resolution`` after
    ``resolve_full_component_phase`` has selected a complete badge before a
    smaller component phase (or a documented transient prefix followed by a
    stable suffix).  The evaluator rechecks that serialized proof so the
    producer's ``accepted`` flag alone cannot clear a conflict.
    """
    candidates = row.get("gain_phase_candidates")
    item = candidates.get(field_name) if isinstance(candidates, Mapping) else None
    if not isinstance(item, Mapping) or item.get("accepted") is not True:
        return None
    accepted = _number(item.get("value"))
    resolution = item.get("source_resolution")
    if accepted is None or not isinstance(resolution, Mapping):
        return None
    source_temporal = (
        resolution.get("basis")
        == "source_temporal_full_before_component_phase"
    )
    if source_temporal and resolution.get("source_field") != field_name:
        return None
    resolved_amount = _number(resolution.get("accepted_amount"))
    if resolved_amount != accepted:
        return None
    observed_values_list = _numeric_list(item.get("observed_values"), minimum=2)
    resolved_values_list = _numeric_list(resolution.get("observed_amounts"), minimum=2)
    if observed_values_list is None or resolved_values_list is None:
        return None
    observed_values = set(observed_values_list)
    resolved_values = set(resolved_values_list)
    if observed_values != resolved_values or accepted not in observed_values:
        return None
    full_shape = _proof_shape(resolution.get("full_shape"))
    outer_shape = _proof_shape(item.get("shape"))
    component_shapes = resolution.get("component_shapes")
    if (full_shape is None or outer_shape != full_shape
            or not isinstance(component_shapes, list) or not component_shapes
            or field_name not in full_shape):
        return None
    normalized_component_shapes = []
    for shape in component_shapes:
        normalized = _proof_shape(shape)
        if (normalized is None or field_name not in normalized
                or normalized in normalized_component_shapes):
            return None
        normalized_component_shapes.append(normalized)
    outer_component_shapes = item.get("component_shapes")
    if not isinstance(outer_component_shapes, Mapping):
        return None
    serialized_component_shapes = []
    component_shape_by_value: dict[int | float, set[tuple[str, ...]]] = {}
    for raw_value, values in outer_component_shapes.items():
        value = _number(raw_value)
        if value is None:
            value = _int(raw_value)
        if value is None or not isinstance(values, list) or not values:
            return None
        value_shapes: set[tuple[str, ...]] = set()
        for shape in values:
            normalized = _proof_shape(shape)
            if (normalized is None or field_name not in normalized
                    or normalized in value_shapes):
                return None
            value_shapes.add(normalized)
            if normalized not in serialized_component_shapes:
                serialized_component_shapes.append(normalized)
        component_shape_by_value[value] = value_shapes
    if set(serialized_component_shapes) != set(normalized_component_shapes):
        return None
    # If the outer diagnostic carries an evidence list, validate it too.  It
    # is kept separate from the nested proof paths because production repeats
    # those paths in the diagnostic envelope.
    if "evidence" in item:
        outer_evidence = item.get("evidence")
        if (not isinstance(outer_evidence, list) or not outer_evidence
                or any(not isinstance(path, str)
                       or not _looks_like_evidence(path)
                       or not _evidence_path_allowed(path, recording)
                       for path in outer_evidence)
                or len({_normalise_evidence(path, recording)
                        for path in outer_evidence}) != len(outer_evidence)):
            return None
    proof_evidence: set[str] = set()
    # The source resolver admits a single transient prefix frame when the
    # later complete value has a repeated stable suffix.  Source-temporal
    # equal-shape proofs retain their separate repeated-support requirement
    # below; ordinary full/component phases still require two frames per
    # phase.  Mirror those producer contracts instead of rejecting a valid
    # prefix solely because its component phase is brief.
    phase_order = resolution.get("phase_order")
    if source_temporal:
        full_minimum, component_minimum = 1, 1
    elif phase_order == "component_prefix_before_stable_full_suffix":
        full_minimum, component_minimum = 3, 1
    else:
        full_minimum, component_minimum = 2, 2
    full_observations = _phase_proof_observations(
        resolution.get("full_observations"), recording=recording,
        field_name=field_name, seen_evidence=proof_evidence,
        minimum=full_minimum,
    )
    component_observations = _phase_proof_observations(
        resolution.get("component_observations"), recording=recording,
        field_name=field_name, seen_evidence=proof_evidence,
        minimum=component_minimum,
    )
    if not full_observations or not component_observations:
        return None
    if source_temporal:
        source_full_count = resolution.get("source_full_observation_count")
        if (type(source_full_count) is not int or source_full_count < 1
                or source_full_count > len(full_observations)):
            return None
        # The producer's source-temporal rule requires repeated physical
        # support across the two phases.  A single full and a single
        # component frame cannot clear a conflict, even if all flags agree.
        if len(full_observations) < 2 and len(component_observations) < 2:
            return None
        full_crop_count = _source_temporal_crop_proof(
            resolution.get("full_observations"), full_observations,
            accepted=accepted, field_name=field_name, require_canonical=False,
        )
        component_crop_count = _source_temporal_crop_proof(
            resolution.get("component_observations"), component_observations,
            accepted=accepted, field_name=field_name, require_canonical=False,
        )
        if full_crop_count != source_full_count or component_crop_count is None:
            return None
        source_rule = resolution.get("source_phase_rule")
        if source_rule not in {
            "canonical_source_crop_then_later_component_only",
            "canonical_source_pair_with_single_unproven_outlier_diagnostic",
        }:
            return None
        if source_rule == "canonical_source_pair_with_single_unproven_outlier_diagnostic":
            if source_full_count < 1:
                return None
        elif resolution.get("ignored_unproven_values", []) not in ([], None):
            return None
    else:
        source_full_count = None
    # A nested proof must be tied to the event's own report evidence.  Merely
    # naming image-looking files in a resolution object would otherwise let a
    # producer manufacture an apparently source-backed phase from an unrelated
    # relative path.  The source unit performs the final frozen-image join;
    # this anchor check keeps the proof attached to the report row first.
    direct_evidence: set[str] = set()
    direct_evidence.update(_source_paths(row.get("evidence"), recording))
    field_evidence = row.get("field_evidence")
    if isinstance(field_evidence, Mapping):
        direct_evidence.update(_source_paths(field_evidence.get(field_name), recording))
    proof_paths = {
        _normalise_evidence(observation["evidence"], recording)
        for observation in full_observations + component_observations
    }
    if not _evidence_matches(proof_paths, direct_evidence):
        return None
    # ``action_identity_evidence`` identifies the selected option; it does
    # not necessarily contain every physical frame in the result animation.
    # New producers carry that bounded reading membership separately.  Bind
    # nested proofs to the group when present, while retaining the legacy
    # action-identity fallback for older reports.
    result_group_present = "result_group" in row
    result_group_paths = None
    if result_group_present:
        result_group_paths = _result_group_proof(
            row, full_observations + component_observations, recording, report
        )
        if result_group_paths is None:
            return None
    if source_temporal:
        if result_group_paths is None:
            # Older reports have no result-reading envelope.  Their identity
            # list was the only bounded membership signal, so preserve the
            # stricter fallback rather than accepting an unbound proof.
            action_identity = row.get("action_identity_evidence")
            if not isinstance(action_identity, list) or not action_identity:
                return None
            action_paths = _source_paths(action_identity, recording)
            if not action_paths or not proof_paths <= action_paths:
                return None
    if any(observation["value"] != accepted for observation in full_observations):
        return None
    component_values = {observation["value"] for observation in component_observations}
    if (len(component_values) != 1 or accepted in component_values
            or not component_values <= observed_values
            or any(observation["shape"] not in component_shape_by_value.get(observation["value"], set())
                   for observation in component_observations)):
        return None
    # Every serialized amount must be witnessed by a nested observation.  A
    # fabricated component value (for example 99) cannot be accepted merely
    # because it is absent from the outer observed_values list.
    if {accepted} | component_values != observed_values:
        if not source_temporal:
            return None
    ignored_values: set[int | float] = set()
    ignored_observations: list[dict[str, Any]] = []
    if source_temporal:
        raw_ignored_values = resolution.get("ignored_unproven_values", [])
        if not isinstance(raw_ignored_values, list):
            return None
        if raw_ignored_values:
            ignored_values_list = _numeric_list(raw_ignored_values, minimum=1)
            if ignored_values_list is None:
                return None
            ignored_values = set(ignored_values_list)
        raw_ignored = resolution.get("ignored_unproven_observations", [])
        diagnostic = _source_temporal_diagnostic_proof(
            raw_ignored, recording=recording, seen_evidence=proof_evidence,
            field_name=field_name,
            allowed_values={accepted} | component_values,
        )
        if diagnostic is None:
            return None
        ignored_observations, diagnostic_values = diagnostic
        if ignored_values != diagnostic_values:
            return None
        if not ignored_values <= observed_values:
            return None
        if ({accepted} | component_values | ignored_values) != observed_values:
            return None
        if result_group_present:
            # Diagnostic observations are source paths too.  A broad or
            # malformed group must not hide a known readable duplicate.
            if _result_group_proof(
                row, full_observations + component_observations + ignored_observations,
                recording, report,
            ) is None:
                return None
    if source_temporal:
        # A source-backed full amount must be the unique longer decimal
        # prefix outcome; no amount agreement or balance is used to choose it.
        if (accepted < 0 or any(value < 0 for value in component_values)
                or any(value < 0 for value in ignored_values)
                or any(
                    len(str(accepted)) <= len(str(value))
                    or not str(accepted).startswith(str(value))
                    for value in component_values
                )):
            return None
    full_times = [observation["source_timestamp_ms"] for observation in full_observations]
    component_times = [observation["source_timestamp_ms"] for observation in component_observations]
    if (type(resolution.get("full_last_seen_ms")) is not int
            or type(resolution.get("component_first_seen_ms")) is not int
            or resolution.get("full_last_seen_ms") != max(full_times)
            or resolution.get("component_first_seen_ms") != min(component_times)):
        return None
    phase_order = resolution.get("phase_order")
    if phase_order == "full_before_component":
        if source_temporal:
            # The source-temporal resolver defines the phase boundary by the
            # first complete badge.  A complete badge may recur after the
            # component animation, so requiring *all* complete frames to
            # precede every component frame would reject a valid interleaved
            # source trace.  Keep the conservative boundary and bounded-span
            # checks instead.
            if min(full_times) >= min(component_times):
                return None
            if max(full_times + component_times) - min(full_times + component_times) > 500:
                return None
            if any(observation["shape"] not in normalized_component_shapes
                   for observation in component_observations):
                return None
            if any(not set(observation["shape"]) <= set(full_shape)
                   for observation in full_observations):
                return None
            phase_times = full_times + component_times + [
                observation["source_timestamp_ms"]
                for observation in ignored_observations
            ]
            if max(phase_times) - min(phase_times) > 500:
                return None
        else:
            if any(observation["shape"] not in normalized_component_shapes
                   or not set(observation["shape"]) < set(full_shape)
                   for observation in component_observations):
                return None
            if any(observation["shape"] != full_shape for observation in full_observations):
                return None
    elif phase_order == "component_prefix_before_stable_full_suffix":
        # The producer's prefix resolver retains all source rows after the
        # component boundary.  OCR field visibility can vary across those
        # rows, so the serialized final ``full_shape`` is the last suffix
        # shape rather than an assertion that every suffix frame has identical
        # fields.  Keep every row inside the declared phase shape universe and
        # require the final row to agree with the producer's selected shape.
        allowed_full_fields = set(full_shape)
        for shape in normalized_component_shapes:
            allowed_full_fields.update(shape)
        if any(
            not set(observation["shape"]) <= allowed_full_fields
            for observation in full_observations
        ):
            return None
        component_last = max(component_times)
        suffix = [observation for observation in full_observations
                  if observation["source_timestamp_ms"] > component_last]
        if (len(suffix) < 2
                or suffix[-1]["source_timestamp_ms"] - suffix[0]["source_timestamp_ms"] < 30
                or suffix[-1]["shape"] != full_shape):
            return None
    else:
        return None
    basis = resolution.get("basis")
    outer_basis = item.get("basis")
    compatible_bases = {
        ("repeated_full_gain_shape_strictly_contains_all_component_shapes",
         "repeated_full_gain_before_repeated_component_phase"),
        ("repeated_full_gain_before_repeated_component_phase",
         "repeated_full_gain_before_repeated_component_phase"),
        ("repeated_full_gain_prefix_phase_with_stable_suffix",
         "repeated_full_gain_prefix_phase_with_stable_suffix"),
        ("source_temporal_full_before_component_phase",
         "source_temporal_full_before_component_phase"),
    }
    if (not isinstance(basis, str) or not isinstance(outer_basis, str)
            or (outer_basis, basis) not in compatible_bases):
        return None
    return "gain_phase_candidates", accepted


def _prefix_proof_observations(value: Any, *, accepted: int | float,
                               recording: str | None, seen_evidence: set[str],
                               minimum: int) -> list[dict[str, Any]] | None:
    """Validate repeated complete/prefix observations in a prefix proof."""
    if not isinstance(value, list) or len(value) < minimum:
        return None
    result: list[dict[str, Any]] = []
    timestamps: set[int] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return None
        timestamp = item.get("source_timestamp_ms")
        observed = _number(item.get("value"))
        evidence = _proof_evidence(item.get("evidence"), recording, seen_evidence)
        if (type(timestamp) is not int or observed is None or evidence is None
                or timestamp in timestamps):
            return None
        # Unknown nested amount claims are not harmless metadata.  If one is
        # present, it must describe this exact physical observation.
        if "observed_amounts" in item:
            nested = _numeric_list(item.get("observed_amounts"), minimum=1)
            if nested is None or set(nested) != {observed}:
                return None
        timestamps.add(timestamp)
        result.append({
            "source_timestamp_ms": timestamp,
            "value": observed,
            "evidence": evidence,
        })
    if [row["source_timestamp_ms"] for row in result] != sorted(timestamps):
        return None
    return result


def _prefix_resolution(row: Mapping[str, Any], field_name: str,
                       recording: str | None = None,
                       report: Mapping[str, Any] | None = None) -> tuple[int | float, set[int | float]] | None:
    """Validate a complete-badge/prefix resolution before clearing conflicts."""
    resolutions = row.get("gain_prefix_resolutions")
    item = resolutions.get(field_name) if isinstance(resolutions, Mapping) else None
    if not isinstance(item, Mapping):
        return None
    accepted = _number(item.get("accepted_amount"))
    observed_values = _numeric_list(item.get("observed_amounts"), minimum=2)
    if accepted is None or observed_values is None or accepted not in observed_values:
        return None

    # A nested resolution is not part of the production prefix schema, but
    # reports produced by adapters may retain one.  If present, bind its
    # observed amounts to the outer declaration instead of silently ignoring
    # a contradictory nested value.
    nested_resolution = item.get("source_resolution")
    if nested_resolution is not None:
        if not isinstance(nested_resolution, Mapping):
            return None
        nested_values = _numeric_list(nested_resolution.get("observed_amounts"), minimum=2)
        if nested_values is None or set(nested_values) != set(observed_values):
            return None

    seen_evidence: set[str] = set()
    complete = _prefix_proof_observations(
        item.get("complete_observations"), accepted=accepted,
        recording=recording, seen_evidence=seen_evidence, minimum=2,
    )
    prefix = _prefix_proof_observations(
        item.get("prefix_observations"), accepted=accepted,
        recording=recording, seen_evidence=seen_evidence, minimum=1,
    )
    if not complete or not prefix:
        return None
    direct_evidence: set[str] = set()
    direct_evidence.update(_source_paths(row.get("evidence"), recording))
    field_evidence = row.get("field_evidence")
    if isinstance(field_evidence, Mapping):
        direct_evidence.update(_source_paths(field_evidence.get(field_name), recording))
    proof_paths = {
        _normalise_evidence(observation["evidence"], recording)
        for observation in complete + prefix
    }
    if not _evidence_matches(proof_paths, direct_evidence):
        return None
    # Prefix resolutions use the same bounded result-reading membership as
    # source-temporal phase resolutions.  Keep the event/field anchor above
    # independent so a result-group envelope cannot manufacture a claim that
    # the report did not attach to this event.
    if "result_group" in row and _result_group_proof(
        row, complete + prefix, recording, report
    ) is None:
        return None
    if {observation["value"] for observation in complete} != {accepted}:
        return None
    prefix_values = {observation["value"] for observation in prefix}
    if accepted in prefix_values or not prefix_values <= set(observed_values):
        return None
    if {accepted} | prefix_values != set(observed_values):
        return None
    if accepted < 0 or any(value < 0 for value in prefix_values):
        return None
    if any(
        len(str(accepted)) <= len(str(value))
        or not str(accepted).startswith(str(value))
        for value in prefix_values
    ):
        return None

    basis = item.get("basis")
    if basis != "repeated_complete_badge_with_brief_prefix_observations":
        return None
    complete_times = [observation["source_timestamp_ms"] for observation in complete]
    prefix_times = [observation["source_timestamp_ms"] for observation in prefix]
    all_times = complete_times + prefix_times
    if (max(complete_times) - min(complete_times) < 30
            or max(all_times) - min(all_times) > 250
            or any(not (min(complete_times) < timestamp < max(complete_times))
                   for timestamp in prefix_times)):
        return None

    crosscheck = item.get("counter_crosscheck_evidence")
    if crosscheck is None:
        return None
    if (not isinstance(crosscheck, list) or not crosscheck
            or any(not isinstance(path, str)
                   or not _looks_like_evidence(path)
                   or not _evidence_path_allowed(path, recording)
                   for path in crosscheck)):
        return None
    crosscheck_paths = {_normalise_evidence(path, recording) for path in crosscheck}
    if len(crosscheck_paths) != len(crosscheck):
        return None
    complete_paths = {
        _normalise_evidence(observation["evidence"], recording)
        for observation in complete
    }
    if not crosscheck_paths & complete_paths:
        return None
    return accepted, set(observed_values)


_SOURCE_CLIPPED_GAIN_BASIS = "source_clipped_training_gain"
_SOURCE_CLIPPED_RESOLUTION_BASIS = (
    "cross_frame_source_clipped_gain_overlay_and_broad_agreement"
)


def _source_clipped_resolution(
    row: Mapping[str, Any], field_name: str,
    recording: str | None = None,
    report: Mapping[str, Any] | None = None,
) -> tuple[int, set[int | float]] | None:
    """Rebuild a clipped gain decision from report-owned result readings.

    The short OCR value remains a raw observation.  Only the source resolver's
    rebuilt result can authorize the longer value, and all of its physical
    observations must be members of the event's bounded result group.
    """
    provenance_map = row.get("direct_gain_provenance")
    provenance = (provenance_map.get(field_name)
                  if isinstance(provenance_map, Mapping) else None)
    resolution_map = row.get("source_clipped_gain_resolutions")
    resolution = (provenance.get("source_clipping_resolution")
                  if isinstance(provenance, Mapping) else None)
    if (not isinstance(provenance, Mapping)
            or provenance.get("basis") != _SOURCE_CLIPPED_GAIN_BASIS
            or not isinstance(resolution, Mapping)
            or not isinstance(resolution_map, Mapping)
            or resolution_map.get(field_name) != resolution
            or resolution.get("status") != "accepted"
            or resolution.get("basis") != _SOURCE_CLIPPED_RESOLUTION_BASIS
            or resolution.get("field") != field_name):
        return None

    group = row.get("result_group")
    event_interval = _row_interval(row)
    interval = group.get("interval_ms") if isinstance(group, Mapping) else None
    option = row.get("training_option")
    if (not isinstance(group, Mapping) or event_interval is None
            or not isinstance(interval, (list, tuple)) or len(interval) != 2
            or any(type(value) is not int for value in interval)
            or interval[0] > interval[1]
            or tuple(interval) != event_interval
            or not isinstance(option, str) or not option.strip()
            or group.get("training_option") != option):
        return None
    phase_key = f"{option}:{interval[0]}:{interval[1]}"
    if resolution.get("phase_key") != phase_key:
        return None

    raw_observations = resolution.get("observations")
    accepted_observations = resolution.get("accepted_observations")
    members = group.get("observations")
    if (not isinstance(raw_observations, list)
            or not isinstance(accepted_observations, list)
            or not accepted_observations or not isinstance(members, list)
            or not members):
        return None

    # The result-group helper validates each member against the actual report
    # reading index and checks the event option, interval, turn ids, and path.
    group_proof = [
        {"source_timestamp_ms": item.get("source_timestamp_ms"),
         "evidence": item.get("evidence")}
        for item in raw_observations
        if isinstance(item, Mapping)
    ]
    if len(group_proof) != len(raw_observations) or _result_group_proof(
            row, group_proof, recording, report) is None:
        return None

    actual_index = _result_reading_index(report, recording)
    member_rows: list[Mapping[str, Any]] = []
    member_identities: set[tuple[int, str]] = set()
    for member in members:
        if not isinstance(member, Mapping):
            return None
        timestamp = member.get("source_timestamp_ms")
        evidence = member.get("evidence")
        if (type(timestamp) is not int or not isinstance(evidence, str)
                or not _looks_like_evidence(evidence)
                or not _evidence_path_allowed(evidence, recording)):
            return None
        normalized = _normalise_evidence(evidence, recording)
        identity = (timestamp, normalized)
        if identity in member_identities:
            return None
        matches = actual_index.get(identity, [])
        if len(matches) != 1:
            return None
        _, actual = matches[0]
        if actual.get("screen") != "training_result":
            return None
        actual_option = actual.get("training_option")
        if actual_option is not None and actual_option != option:
            return None
        member_identities.add(identity)
        member_rows.append(actual)
    member_times = [actual.get("source_timestamp_ms") for actual in member_rows]
    if not member_times or list(interval) != [min(member_times), max(member_times)]:
        return None

    def _proof_identity(item: Any) -> tuple[int, str] | None:
        if not isinstance(item, Mapping):
            return None
        timestamp = item.get("source_timestamp_ms")
        evidence = item.get("evidence")
        if (type(timestamp) is not int or not isinstance(evidence, str)
                or not _looks_like_evidence(evidence)
                or not _evidence_path_allowed(evidence, recording)):
            return None
        return timestamp, _normalise_evidence(evidence, recording)

    raw_identities: set[tuple[int, str]] = set()
    for item in raw_observations:
        identity = _proof_identity(item)
        if identity is None or identity not in member_identities:
            return None
        raw_identities.add(identity)

    accepted_amount = _number(resolution.get("accepted_amount"))
    if (type(accepted_amount) is not int or accepted_amount < 0
            or accepted_amount != _number(provenance.get("value"))):
        return None
    expected_proof: list[dict[str, Any]] = []
    accepted_identities: set[tuple[int, str]] = set()
    for item in accepted_observations:
        identity = _proof_identity(item)
        if (identity is None or identity in accepted_identities
                or identity not in member_identities
                or identity not in raw_identities):
            return None
        accepted_identities.add(identity)
        expected_proof.append({
            "source_timestamp_ms": item["source_timestamp_ms"],
            "evidence": item["evidence"],
            "value": accepted_amount,
        })

    # Re-run the resolver using the report's actual rows.  This intentionally
    # does not pass the event or frozen amount as a selection hint.
    from tracen_replay.training_gain_resolution import resolve_source_clipped_gain
    rebuilt = resolve_source_clipped_gain(member_rows, field_name, phase_key=phase_key)
    if rebuilt != resolution:
        return None
    if (provenance.get("observations") != expected_proof
            or provenance.get("observation_count") != len(expected_proof)
            or provenance.get("source_timestamps_ms") != [
                item["source_timestamp_ms"] for item in expected_proof
            ]):
        return None
    declared_evidence = provenance.get("evidence")
    expected_evidence = [item["evidence"] for item in expected_proof]
    if (not isinstance(declared_evidence, list)
            or any(not isinstance(path, str)
                   or not _looks_like_evidence(path)
                   or not _evidence_path_allowed(path, recording)
                   for path in declared_evidence)
            or [_normalise_evidence(path, recording) for path in declared_evidence]
            != [_normalise_evidence(path, recording) for path in expected_evidence]):
        return None
    field_evidence_value = row.get("field_evidence")
    field_evidence = _source_paths(
        field_evidence_value.get(field_name)
        if isinstance(field_evidence_value, Mapping) else None,
        recording,
    )
    if not set(_normalise_evidence(path, recording) for path in expected_evidence) <= field_evidence:
        return None
    return accepted_amount, {
        value for value in (_number(item) for item in resolution.get("observed_amounts", []))
        if value is not None
    }


def _field_resolution_amounts(row: Mapping[str, Any], field_name: str,
                              recording: str | None = None,
                              report: Mapping[str, Any] | None = None) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    clipped = _source_clipped_resolution(row, field_name, recording, report)
    if clipped is not None:
        result["source_clipped_gain_resolutions"] = clipped[0]
    phase = _phase_resolution_amount(row, field_name, recording, report)
    if phase is not None:
        result[phase[0]] = phase[1]
    prefix = _prefix_resolution(row, field_name, recording, report)
    if prefix is not None:
        result["gain_prefix_resolutions"] = prefix[0]
    return result


def _field_resolution_observed_amounts(
    row: Mapping[str, Any], field_name: str,
    recording: str | None = None,
    report: Mapping[str, Any] | None = None,
) -> dict[str, set[int | float]]:
    """Return the numeric alternatives covered by each validated proof."""
    result: dict[str, set[int | float]] = {}
    clipped = _source_clipped_resolution(row, field_name, recording, report)
    if clipped is not None:
        result["source_clipped_gain_resolutions"] = clipped[1]
    phase = _phase_resolution_amount(row, field_name, recording, report)
    if phase is not None:
        candidates = row.get("gain_phase_candidates")
        item = candidates.get(field_name) if isinstance(candidates, Mapping) else None
        observed = {
            value for value in (_number(item_value) for item_value in _as_list(item.get("observed_values")))
            if value is not None
        } if isinstance(item, Mapping) else set()
        result[phase[0]] = observed
    prefix = _prefix_resolution(row, field_name, recording, report)
    if prefix is not None:
        result["gain_prefix_resolutions"] = prefix[1]
    return result


def _field_resolution_proof(row: Mapping[str, Any], field_name: str,
                            recording: str | None = None,
                            report: Mapping[str, Any] | None = None) -> set[str]:
    """Return source-resolution proof names after validating their contents."""
    return set(_field_resolution_amounts(row, field_name, recording, report))


def _field_status(row: Mapping[str, Any], field_name: str) -> set[str]:
    values: list[Any] = [row.get("status"), _field_map(row, "field_status", field_name)]
    result: set[str] = set()
    for value in values:
        if isinstance(value, str):
            normalised = _normalise_basis(value)
            if normalised is not None:
                result.add(normalised)
        elif isinstance(value, list):
            for item in value:
                normalised = _normalise_basis(item)
                if normalised is not None:
                    result.add(normalised)
    return result


@dataclass
class Candidate:
    """One report-owned claim for one numeric field."""

    key: str
    field: str
    recording: str | None = None
    report: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)
    amount: int | float | None = None
    interval: tuple[int, int] | None = None
    evidence: set[str] = field(default_factory=set)
    event_id: str | None = None
    event_ref: str | None = None
    source_kind: str = "training_event"
    amount_sources: list[str] = field(default_factory=list)
    observed_amounts: set[int | float] = field(default_factory=set)
    conflict_values: set[int | float] = field(default_factory=set)
    explicit_turn_ids: set[str] = field(default_factory=set)
    candidate_turn_ids: set[str] = field(default_factory=set)
    basis_values: set[str] = field(default_factory=set)
    canonical_basis_values: set[str] = field(default_factory=set)
    canonical_evidence: set[str] = field(default_factory=set)
    independent_effect_verification: set[bool] = field(default_factory=set)
    field_resolution_proof: set[str] = field(default_factory=set)
    field_resolution_amounts: dict[str, int | float] = field(default_factory=dict)
    field_resolution_observed_amounts: dict[str, set[int | float]] = field(default_factory=dict)
    ownership_basis: set[str] = field(default_factory=set)
    conflict_state: set[str] = field(default_factory=set)
    raw_conflict_values: set[int | float] = field(default_factory=set)
    raw_conflicts_resolved: bool = False
    foreign_evidence: set[str] = field(default_factory=set)
    canonical_seen: bool = False
    rows: list[str] = field(default_factory=list)
    amount_conflict: bool = False

    def add_row(self, row_ref: str | None) -> None:
        if isinstance(row_ref, str) and row_ref and row_ref not in self.rows:
            self.rows.append(row_ref)

    def add_row_metadata(self, row: Mapping[str, Any], field_name: str,
                         *, row_ref: str | None = None,
                         evidence_override: Iterable[str] | None = None,
                         canonical: bool = False) -> None:
        evidence = set(evidence_override or ())
        self.evidence.update(evidence)
        bases = set(_basis_values(row, field_name))
        self.basis_values.update(bases)
        if canonical:
            self.canonical_seen = True
            self.canonical_basis_values.update(bases)
            self.canonical_evidence.update(evidence)
            if field_name in _as_list(row.get("result_state_derived_fields")):
                self.canonical_basis_values.add("state_derived")
            self.independent_effect_verification.update(
                flag
                for flag in _boolean_field(row, "independent_effect_verification", field_name)
            )
            self.field_resolution_proof.update(
                _field_resolution_proof(row, field_name, self.recording, self.report)
            )
            self.field_resolution_amounts.update(
                _field_resolution_amounts(row, field_name, self.recording, self.report)
            )
            self.field_resolution_observed_amounts.update(
                _field_resolution_observed_amounts(row, field_name, self.recording, self.report)
            )
            statuses = _field_status(row, field_name)
            if statuses & CANONICAL_UNRESOLVED_STATUSES:
                self.conflict_state.add("canonical_unresolved")
            if row.get("conflicts_present") is True:
                self.conflict_state.add("reported_conflict")
        # Raw event/readings preserve all alternatives for audit output, but
        # their existence is not itself an unresolved canonical claim.  A
        # canonical row can carry the same raw diagnostics, so collect them
        # for either provenance kind and resolve them only with the explicit
        # accepted-complete-badge proof.
        self.raw_conflict_values.update(
            number for number in (_number(value) for value in _conflict_values(row, field_name))
            if number is not None
        )
        self.foreign_evidence.update(
            _foreign_evidence_paths(row, self.recording)
        )
        self._refresh_raw_resolution()
        exact, candidates = _owner_values(row)
        self.explicit_turn_ids.update(exact)
        self.candidate_turn_ids.update(candidates)
        values = {
            number for number in (_number(value) for value in _conflict_values(row, field_name))
            if number is not None
        }
        self.conflict_values.update(values)
        for key in ("turn_assignment_basis", "ownership_basis"):
            value = row.get(key)
            if isinstance(value, str) and value:
                self.ownership_basis.add(value)
        if row_ref:
            self.add_row(row_ref)

    def add_amount(self, value: Any, source: str, *, canonical: bool = False) -> None:
        number = _number(value)
        if number is None:
            return
        self.observed_amounts.add(number)
        self.amount_sources.append(source)
        if canonical:
            if self.amount is None:
                self.amount = number
            elif self.amount != number:
                self.amount_conflict = True
            self._refresh_raw_resolution()
        elif self.amount is not None and self.amount != number:
            self.amount_conflict = True

    def _refresh_raw_resolution(self) -> None:
        self.raw_conflicts_resolved = any(
            self.amount == accepted
            and self.raw_conflict_values <= self.field_resolution_observed_amounts.get(name, set())
            for name, accepted in self.field_resolution_amounts.items()
        )

    def direct_info(self) -> tuple[bool, str]:
        return _candidate_direct(self)

    def conflict_states(self) -> set[str]:
        """Return unresolved canonical conflicts plus unresolvable raw claims."""
        states = set(self.conflict_state)
        if self.amount_conflict:
            states.add("amount_source_disagreement")
        if (self.field_resolution_amounts and self.amount is not None
                and self.amount not in self.field_resolution_amounts.values()):
            states.add("resolution_amount_disagreement")
        if self.raw_conflict_values and not self.raw_conflicts_resolved:
            states.add("reported_conflict")
        return states


def _event_rows(report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    data = report.get("gameplay_tracking")
    if isinstance(data, Mapping) and isinstance(data.get("events"), list):
        return [(f"/gameplay_tracking/events/{index}", row)
                for index, row in enumerate(data["events"]) if isinstance(row, Mapping)]
    for key in ("training_events", "events"):
        rows = report.get(key)
        if isinstance(rows, list):
            return [(f"/{key}/{index}", row)
                    for index, row in enumerate(rows) if isinstance(row, Mapping)]
    return []


def _is_training(row: Mapping[str, Any]) -> bool:
    kind = str(row.get("kind", "")).lower()
    return (kind == "training" or row.get("training_option") is not None
            or isinstance(row.get("deltas"), Mapping)
            or isinstance(row.get("training_gains"), Mapping)
            or str(row.get("event_type", "")).lower() == "training")


def _event_evidence(row: Mapping[str, Any], field_name: str, recording: str | None) -> set[str]:
    paths: set[str] = set()
    paths.update(_source_paths(row.get("evidence"), recording))
    for key in ("field_evidence", "performance_evidence"):
        value = row.get(key)
        if isinstance(value, Mapping):
            paths.update(_source_paths(value.get(field_name), recording))
    for key in ("gain_phase_candidates", "gain_reading_disagreements",
                "partial_result_counter_evidence", "training_gain_recovery"):
        value = row.get(key)
        if isinstance(value, Mapping):
            paths.update(_source_paths(value.get(field_name), recording))
            paths.update(_source_paths(value, recording))
        else:
            paths.update(_source_paths(value, recording))
    return paths


def _event_amounts(row: Mapping[str, Any]) -> dict[str, tuple[Any, str]]:
    result: dict[str, tuple[Any, str]] = {}
    deltas = row.get("deltas")
    if isinstance(deltas, Mapping):
        for field_name, amount in deltas.items():
            if isinstance(field_name, str):
                result[field_name] = (amount, "event.deltas")
    gains = row.get("training_gains")
    if isinstance(gains, Mapping):
        for field_name, amount in gains.items():
            if isinstance(field_name, str) and field_name not in result:
                result[field_name] = (amount, "event.training_gains")
    effects = row.get("effects")
    if isinstance(effects, list):
        for index, effect in enumerate(effects):
            if not isinstance(effect, Mapping):
                continue
            field_name = effect.get("field")
            amount = effect.get("amount", effect.get("value"))
            if isinstance(field_name, str) and field_name not in result:
                result[field_name] = (amount, f"event.effects[{index}]")
    field_evidence = row.get("field_evidence")
    if isinstance(field_evidence, Mapping):
        for field_name in field_evidence:
            if isinstance(field_name, str) and field_name not in result:
                result[field_name] = (None, "field_evidence_without_amount")
    return result


def _candidate_from_event(ref: str, row: Mapping[str, Any], field_name: str,
                          amount: Any, amount_source: str,
                          recording: str | None,
                          report: Mapping[str, Any] | None = None) -> Candidate:
    candidate = Candidate(
        key=f"{ref}/{field_name}",
        field=field_name,
        recording=recording,
        report=report,
        interval=_row_interval(row),
        event_id=row.get("id") if isinstance(row.get("id"), str) else None,
        event_ref=ref,
        source_kind="training_event",
    )
    evidence = _event_evidence(row, field_name, recording)
    # Every event row is a canonical event representation, including the
    # important ``field_evidence_without_amount`` case.  Its amount/basis may
    # still be absent; that absence must remain a missing canonical gain.
    candidate.add_row_metadata(row, field_name, row_ref=ref,
                               evidence_override=evidence, canonical=True)
    candidate.add_amount(amount, amount_source, canonical=True)
    return candidate


def _candidate_from_observation(ref: str, row: Mapping[str, Any], field_name: str,
                                amount: Any, recording: str | None,
                                report: Mapping[str, Any] | None = None) -> Candidate:
    candidate = Candidate(
        key=f"{ref}/{field_name}", field=field_name, recording=recording,
        report=report,
        interval=_row_interval(row),
        event_id=row.get("event_id") if isinstance(row.get("event_id"), str) else None,
        event_ref=ref, source_kind="observation",
    )
    evidence = set(_source_paths(row.get("evidence"), recording))
    candidate.add_row_metadata(row, field_name, row_ref=ref,
                               evidence_override=evidence, canonical=True)
    candidate.add_amount(amount, "observation.payload", canonical=True)
    return candidate


def _find_candidate(candidates: list[Candidate], *, event_ref: str | None = None,
                    event_id: str | None = None, field_name: str | None = None) -> list[Candidate]:
    result = []
    for candidate in candidates:
        if field_name is not None and candidate.field != field_name:
            continue
        if event_ref is not None and candidate.event_ref == event_ref:
            result.append(candidate)
        elif event_id is not None and candidate.event_id == event_id:
            result.append(candidate)
    return result


def _turn_windows(report: Mapping[str, Any]) -> list[tuple[int, int, str]]:
    rows: list[Mapping[str, Any]] = []
    ledger = report.get("turn_ledger")
    if isinstance(ledger, Mapping):
        rows.extend(row for row in ledger.get("turns", []) if isinstance(row, Mapping))
        rows.extend(row for row in ledger.get("turn_transitions", []) if isinstance(row, Mapping))
    causal = report.get("causal_accounting")
    if isinstance(causal, Mapping):
        rows.extend(row for row in causal.get("turn_transitions", []) if isinstance(row, Mapping))
    rows.extend(row for row in report.get("turns", []) if isinstance(row, Mapping))
    result: list[tuple[int, int, str]] = []
    for row in rows:
        turn_id = row.get("id", row.get("turn_id", row.get("actual_turn_id")))
        interval = _row_interval(row)
        if isinstance(turn_id, str) and interval is not None:
            result.append((interval[0], interval[1], turn_id))
    # Keep the first declaration when a ledger repeats a transition.
    seen: set[tuple[int, int, str]] = set()
    return [row for row in result if not (row in seen or seen.add(row))]


def _attach_turn_window(candidate: Candidate, windows: list[tuple[int, int, str]]) -> None:
    if candidate.explicit_turn_ids or candidate.candidate_turn_ids:
        return
    owners = []
    for start, end, turn_id in windows:
        if _interval_overlap(candidate.interval, (start, end)):
            owners.append(turn_id)
    if len(owners) == 1:
        candidate.explicit_turn_ids.add(owners[0])
        candidate.ownership_basis.add("turn_window")
    elif owners:
        candidate.candidate_turn_ids.update(owners)
        candidate.ownership_basis.add("overlapping_turn_windows")


def _readings(report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    data = report.get("gameplay_tracking")
    if isinstance(data, Mapping) and isinstance(data.get("readings"), list):
        return [(f"/gameplay_tracking/readings/{index}", row)
                for index, row in enumerate(data["readings"]) if isinstance(row, Mapping)]
    rows = report.get("readings")
    if isinstance(rows, list):
        return [(f"/readings/{index}", row)
                for index, row in enumerate(rows) if isinstance(row, Mapping)]
    return []


def _reading_gain_values(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    facts = row.get("facts")
    if isinstance(facts, Mapping):
        gains = facts.get("training_gains")
        if isinstance(gains, Mapping):
            return gains
    gains = row.get("training_gains")
    return gains if isinstance(gains, Mapping) else None


def _merge_reading_candidates(candidates: list[Candidate], report: Mapping[str, Any],
                             recording: str | None) -> None:
    """Attach raw gain readings; create candidates only if no event exists."""
    event_id_candidates: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.event_id:
            event_id_candidates.setdefault(candidate.event_id, []).append(candidate)
    windows = _turn_windows(report)
    for ref, row in _readings(report):
        gains = _reading_gain_values(row)
        if not gains:
            continue
        facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
        recovery = facts.get("training_gain_recovery") if isinstance(facts, Mapping) else None
        owner_id = recovery.get("owner_id") if isinstance(recovery, Mapping) else None
        interval = _row_interval(row)
        reading_evidence = set(_source_paths(row.get("evidence"), recording))
        if not reading_evidence:
            reading_evidence.update(_source_paths(recovery, recording))
        for field_name, amount in gains.items():
            if not isinstance(field_name, str):
                continue
            target: Candidate | None = None
            if isinstance(owner_id, str):
                owned = [candidate for candidate in event_id_candidates.get(owner_id, [])
                         if candidate.field == field_name]
                if len(owned) == 1:
                    target = owned[0]
            if target is None:
                possible = [candidate for candidate in candidates if candidate.field == field_name
                            and (_evidence_matches(candidate.evidence, reading_evidence)
                                 or _interval_overlap(candidate.interval, interval))]
                if len(possible) == 1:
                    target = possible[0]
            if target is not None:
                target.observed_amounts.add(number) if (number := _number(amount)) is not None else None
                target.evidence.update(reading_evidence)
                if number is not None and target.amount is not None and number != target.amount:
                    # Keep the raw reading for diagnostics.  Whether this is
                    # unresolved is decided from the canonical contribution
                    # and any explicit complete-badge resolution.
                    target.raw_conflict_values.add(number)
                    target.conflict_values.add(number)
                target.add_row(ref)
                target.add_row_metadata(row, field_name, row_ref=ref,
                                        evidence_override=reading_evidence)
                continue
            # A report may expose only raw training-gain readings.  Group
            # repeated readings by owner id; if no owner exists, keep each
            # source timestamp/evidence occurrence separate rather than
            # selecting a value by the frozen amount.
            owner_key = str(owner_id) if isinstance(owner_id, str) else str(
                (_int(row.get("source_timestamp_ms")), sorted(reading_evidence)))
            key = f"reading:{owner_key}:{field_name}"
            existing = next((candidate for candidate in candidates if candidate.key == key), None)
            if existing is None:
                existing = Candidate(key=key, field=field_name, interval=interval,
                                     recording=recording,
                                     event_id=owner_id if isinstance(owner_id, str) else None,
                                     event_ref=None, source_kind="training_reading")
                existing.add_amount(amount, "reading.facts.training_gains", canonical=True)
                candidates.append(existing)
            elif _number(amount) is not None and existing.amount != _number(amount):
                existing.amount_conflict = True
            existing.observed_amounts.update(
                number for number in (_number(amount),) if number is not None)
            existing.evidence.update(reading_evidence)
            existing.add_row_metadata(row, field_name, row_ref=ref,
                                      evidence_override=reading_evidence)
            _attach_turn_window(existing, windows)


def _extract_candidates(report: Mapping[str, Any], recording: str | None) -> list[Candidate]:
    candidates: list[Candidate] = []
    event_rows = _event_rows(report)
    for ref, row in event_rows:
        if not _is_training(row):
            continue
        amounts = _event_amounts(row)
        for field_name, (amount, amount_source) in amounts.items():
            candidates.append(_candidate_from_event(ref, row, field_name, amount,
                                                     amount_source, recording,
                                                     report))

    # Normalized observation reports are useful for focused tests and for
    # future workers that emit report_document-style rows.
    if not event_rows:
        observations = report.get("observations")
        if isinstance(observations, list):
            for index, row in enumerate(observations):
                if not isinstance(row, Mapping) or row.get("category") != "effect":
                    continue
                payload = row.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                field_name = payload.get("field")
                if not isinstance(field_name, str):
                    continue
                amount = payload.get("amount", payload.get("value"))
                candidates.append(_candidate_from_observation(
                    f"/observations/{index}", row, field_name, amount, recording,
                    report))

    # Causal rows supply ownership and basis metadata.  They are merged into a
    # report event by report-local ref/id/evidence/time; frozen event ids are
    # never consulted here.
    causal = report.get("causal_accounting")
    if isinstance(causal, Mapping):
        contributions = causal.get("contributions")
        if isinstance(contributions, list):
            for index, row in enumerate(contributions):
                if not isinstance(row, Mapping):
                    continue
                field_name = row.get("field")
                if not isinstance(field_name, str):
                    continue
                event_ref = _event_parent_ref(row.get("event_ref") or row.get("source_ref"))
                event_id = row.get("event_id") if isinstance(row.get("event_id"), str) else None
                linked = _find_candidate(candidates, event_ref=event_ref, field_name=field_name)
                if not linked and event_id:
                    linked = _find_candidate(candidates, event_id=event_id, field_name=field_name)
                if not linked:
                    row_evidence = set(_source_paths(row.get("evidence"), recording))
                    row_interval = _row_interval(row)
                    linked = [candidate for candidate in candidates
                              if candidate.field == field_name and
                              (_evidence_matches(candidate.evidence, row_evidence)
                               or _interval_overlap(candidate.interval, row_interval))]
                if len(linked) == 1:
                    target = linked[0]
                    target.add_row_metadata(row, field_name, row_ref=f"/causal_accounting/contributions/{index}",
                                            evidence_override=_source_paths(row.get("evidence"), recording),
                                            canonical=True)
                    amount = row.get("amount")
                    number = _number(amount)
                    if number is not None:
                        target.observed_amounts.add(number)
                        if target.amount is None:
                            target.amount = number
                            target.amount_sources.append("causal_accounting.contributions.amount")
                        elif target.amount != number:
                            target.amount_conflict = True
                            target.conflict_state.add("amount_source_disagreement")
                    continue
                # A contribution may be the only final report representation.
                amount = row.get("amount")
                if amount is None and not row.get("evidence"):
                    continue
                target = Candidate(
                    key=f"/causal_accounting/contributions/{index}/{field_name}",
                    field=field_name, recording=recording,
                    report=report,
                    interval=_row_interval(row), event_id=event_id,
                    event_ref=event_ref, source_kind="causal_contribution",
                )
                target.add_row_metadata(row, field_name,
                                        row_ref=f"/causal_accounting/contributions/{index}",
                                        evidence_override=_source_paths(row.get("evidence"), recording),
                                        canonical=True)
                target.add_amount(amount, "causal_accounting.contributions.amount", canonical=True)
                candidates.append(target)

    # Action receipts can carry the only explicit ownership in a report.
    data = report.get("gameplay_tracking")
    if isinstance(data, Mapping) and isinstance(data.get("turn_action_receipts"), list):
        by_event: dict[str, list[Candidate]] = {}
        for candidate in candidates:
            if candidate.event_id:
                by_event.setdefault(candidate.event_id, []).append(candidate)
        for index, row in enumerate(data["turn_action_receipts"]):
            if not isinstance(row, Mapping) or row.get("kind") != "training":
                continue
            event_id = row.get("event_id")
            for target in by_event.get(event_id, []):
                target.add_row_metadata(row, target.field,
                                        row_ref=f"/gameplay_tracking/turn_action_receipts/{index}",
                                        evidence_override=_source_paths(row.get("evidence"), recording))

    _merge_reading_candidates(candidates, report, recording)
    windows = _turn_windows(report)
    for candidate in candidates:
        _attach_turn_window(candidate, windows)
    return candidates


def _candidate_owner(candidate: Candidate, expected_turn: str | None) -> tuple[str, str]:
    exact = set(candidate.explicit_turn_ids)
    possible = set(candidate.candidate_turn_ids) | exact
    if not possible:
        return "unobservable", "unobservable"
    if len(exact) == 1 and possible == exact:
        actual = next(iter(exact))
        return ("correct" if expected_turn is not None and actual == expected_turn else "wrong",
                "explicit" if "turn_window" not in candidate.ownership_basis else "turn_window")
    if expected_turn is not None and exact == {expected_turn} and possible == {expected_turn}:
        return "correct", "explicit"
    return "ambiguous", "candidate_turn_ids"


def _candidate_direct(candidate: Candidate, *, source_comparison: bool = False) -> tuple[bool, str]:
    """Return direct status from canonical accounting and source proof.

    Event/readings evidence is useful for joining and for diagnostics, but it
    cannot by itself establish a direct gain.  The canonical production basis
    and canonical conflicts must be resolved.  The production
    ``independent_effect_verification`` flag is reported separately from this
    evaluator's frozen-source comparison.  Causal accounting intentionally
    leaves that production flag false because it cannot independently verify
    the frozen source.  Once this evaluator has assigned the candidate by
    field, evidence/time, and turn, that assignment is the independent source
    comparison and can support direct credit.  A raw prefix/component
    alternative is allowed to remain in the audit fields when
    ``gain_prefix_resolutions`` accepted the complete badge; it does not
    become an unresolved conflict in that case.
    """
    bases = {
        _normalise_basis(value)
        for value in candidate.canonical_basis_values
    }
    bases.discard(None)
    if "state_derived" in bases:
        return False, "state_derived"
    if "state_constrained" in bases:
        return False, "state_constrained"
    direct_bases = bases & CANONICAL_DIRECT_BASES
    if not direct_bases:
        return False, "canonical_basis_missing" if candidate.canonical_seen else "unobservable"

    conflict_state = candidate.conflict_states()
    if conflict_state:
        if "canonical_unresolved" in conflict_state:
            return False, "canonical_unresolved"
        return False, "reported_conflict"

    # Causal accounting cannot independently verify the frozen source: it
    # therefore emits ``False`` for every contribution.  This evaluator does
    # that independent comparison after assigning a candidate by field,
    # source evidence/time, and ownership.  Permit that explicit production
    # value only when the comparison actually joined the candidate.  Missing
    # metadata and contradictory true/false metadata remain unresolved.
    production_verification = candidate.independent_effect_verification
    if production_verification == {True}:
        pass
    elif production_verification == {False} and source_comparison:
        pass
    else:
        return False, "independent_effect_unverified"
    if not candidate.canonical_evidence:
        return False, "canonical_source_evidence_missing"
    return True, sorted(direct_bases)[0]


def _candidate_json(candidate: Candidate, target: Mapping[str, Any],
                    recording: str | None, expected_turn: str | None,
                    matched_evidence: set[str], overlap: int) -> dict[str, Any]:
    source_comparison = bool(matched_evidence or overlap)
    direct, basis = _candidate_direct(
        candidate, source_comparison=source_comparison
    )
    owner_status, owner_basis = _candidate_owner(candidate, expected_turn)
    conflict_state = candidate.conflict_states()
    return {
        "report_event_ref": candidate.event_ref,
        "report_event_id": candidate.event_id,
        "report_rows": list(candidate.rows),
        "field": candidate.field,
        "predicted_amount": candidate.amount,
        "observed_amounts": sorted(candidate.observed_amounts),
        "conflict_values": sorted(candidate.conflict_values),
        "observation_interval_ms": list(candidate.interval) if candidate.interval else None,
        "evidence": sorted(candidate.evidence),
        "foreign_evidence": sorted(candidate.foreign_evidence),
        "matched_source_evidence": sorted(matched_evidence),
        "match_basis": "source_evidence" if matched_evidence else "source_time",
        "time_overlap_ms": overlap,
        "source_comparison": {
            "status": "matched" if source_comparison else "unmatched",
            "basis": "source_evidence" if matched_evidence else (
                "source_time" if overlap else "none"
            ),
        },
        "ownership": {
            "status": owner_status,
            "basis": owner_basis,
            "actual_turn_id": next(iter(candidate.explicit_turn_ids))
            if len(candidate.explicit_turn_ids) == 1 else None,
            "candidate_turn_ids": sorted(candidate.candidate_turn_ids | candidate.explicit_turn_ids),
        },
        "direct_evidence": direct,
        "direct_evidence_basis": basis,
        "canonical_basis": sorted(candidate.canonical_basis_values),
        "canonical_evidence": sorted(candidate.canonical_evidence),
        "independent_effect_verification": sorted(
            candidate.independent_effect_verification,
            key=lambda value: (not value),
        ),
        "field_resolution_proof": sorted(candidate.field_resolution_proof),
        "conflict_state": sorted(conflict_state),
        "amount_conflict": candidate.amount_conflict,
    }


def _target_units(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    contributions = case.get("contributions")
    if isinstance(contributions, list) and contributions:
        units = []
        for index, contribution in enumerate(contributions):
            if not isinstance(contribution, Mapping):
                continue
            units.append({
                "unit_index": index,
                "source_amount": contribution.get("source_amount", contribution.get("amount")),
                "observation_window_ms": contribution.get("observation_window_ms"),
                "source_evidence": contribution.get("source_evidence", []),
                # Kept for audit only.  It is never used in candidate matching.
                "frozen_event_id": contribution.get("event_id"),
            })
        if units:
            return units
    return [{
        "unit_index": 0,
        "source_amount": case.get("source_amount", case.get("unresolved_change")),
        "observation_window_ms": case.get("observation_window_ms"),
        "source_evidence": case.get("source_evidence", []),
        "frozen_event_id": case.get("event_id"),
    }]


def _unit_matches(unit: Mapping[str, Any], case: Mapping[str, Any],
                  candidates: list[Candidate], recording: str | None) -> list[tuple[Candidate, set[str], int]]:
    field_name = case.get("field")
    # A frozen source path explicitly naming another recording must not fall
    # back to a time-only match.  Source-reference freeze validation catches
    # such a case before grading; this guard keeps the evaluator safe when it
    # is called directly by a consumer or a focused test.
    if _foreign_evidence_paths(unit.get("source_evidence"), recording):
        return []
    source_evidence = _source_paths(unit.get("source_evidence"), recording)
    source_interval = _interval(unit.get("observation_window_ms"))
    matches: list[tuple[Candidate, set[str], int]] = []
    for candidate in candidates:
        if candidate.field != field_name:
            continue
        # A training-gain reading is raw diagnostic input.  It cannot stand
        # in for a missing canonical event/contribution, even when its number
        # and screenshot happen to match the frozen source label.
        if candidate.source_kind == "training_reading":
            continue
        if candidate.foreign_evidence:
            continue
        evidence = _evidence_matches(source_evidence, candidate.evidence)
        overlap = _interval_overlap(source_interval, candidate.interval)
        # Source evidence is preferred, but a source-time-only join is valid
        # when the final report uses a new evidence name.
        if evidence or overlap:
            matches.append((candidate, evidence, overlap))
    return matches


def _amount_status(amount: int | float | None, expected: Any,
                   amount_conflict: bool = False) -> str:
    expected_number = _number(expected)
    if amount is None or expected_number is None:
        return "missing"
    if amount_conflict:
        return "ambiguous"
    return "correct" if amount == expected_number else "wrong"


def _unit_verdict(unit: Mapping[str, Any], case: Mapping[str, Any],
                  candidates: list[Candidate], recording: str | None) -> dict[str, Any]:
    expected_amount = unit.get("source_amount")
    expected_turn = case.get("turn_id") if isinstance(case.get("turn_id"), str) else None
    matches = _unit_matches(unit, case, candidates, recording)
    candidate_rows = [
        _candidate_json(candidate, unit, recording, expected_turn, evidence, overlap)
        for candidate, evidence, overlap in matches
    ]
    conflict_state: set[str] = set()
    for row in candidate_rows:
        conflict_state.update(row.get("conflict_state", []))
    if len(matches) == 0:
        foreign_source = _foreign_evidence_paths(unit.get("source_evidence"), recording)
        foreign_candidate = any(
            candidate.field == case.get("field") and candidate.foreign_evidence
            for candidate in candidates
        )
        if foreign_source or foreign_candidate:
            return {
                "unit_index": unit.get("unit_index"),
                "source_amount": expected_amount,
                "frozen_event_id": unit.get("frozen_event_id"),
                "verdict": "unobservable",
                "passed": False,
                "amount_status": "missing",
                "ownership_status": "unobservable",
                "direct_evidence": False,
                "direct_evidence_basis": "foreign_source_evidence",
                "conflict_state": ["foreign_source_evidence"],
                "matched_candidates": [],
            }
        return {
            "unit_index": unit.get("unit_index"),
            "source_amount": expected_amount,
            "frozen_event_id": unit.get("frozen_event_id"),
            "verdict": "missing",
            "passed": False,
            "amount_status": "missing",
            "ownership_status": "unobservable",
            "direct_evidence": False,
            "direct_evidence_basis": "unobservable",
            "conflict_state": ["missing_candidate"],
            "matched_candidates": [],
        }

    if len(matches) > 1:
        amounts = [candidate.amount for candidate, _, _ in matches]
        numeric_amounts = [amount for amount in amounts if _number(amount) is not None]
        if (len(numeric_amounts) >= 2 and any(amount > 0 for amount in numeric_amounts)
                and any(amount < 0 for amount in numeric_amounts)
                and sum(numeric_amounts) == 0):
            verdict = "cancellation"
            conflict_state.add("cancellation_candidates")
        else:
            verdict = "duplicate"
            conflict_state.add("duplicate_candidates")
        return {
            "unit_index": unit.get("unit_index"),
            "source_amount": expected_amount,
            "frozen_event_id": unit.get("frozen_event_id"),
            "verdict": verdict,
            "passed": False,
            "amount_status": "ambiguous",
            "ownership_status": "ambiguous",
            "direct_evidence": all(row["direct_evidence"] for row in candidate_rows),
            "direct_evidence_basis": "multiple_candidates",
            "conflict_state": sorted(conflict_state),
            "matched_candidates": candidate_rows,
        }

    candidate, matched_evidence, overlap = matches[0]
    amount_status = _amount_status(candidate.amount, expected_amount, candidate.amount_conflict)
    ownership_status, _ = _candidate_owner(candidate, expected_turn)
    direct, direct_basis = _candidate_direct(
        candidate, source_comparison=bool(matched_evidence or overlap)
    )
    if amount_status == "missing":
        verdict = "missing"
    elif ownership_status == "wrong":
        verdict = "wrong_turn"
    elif ownership_status in ("ambiguous", "unobservable"):
        verdict = "ambiguous_attribution" if ownership_status == "ambiguous" else "unobservable"
    elif conflict_state:
        # A canonical amount can numerically agree while its source
        # attribution remains unresolved.  Preserve that state instead of
        # promoting the matching event to a pass.
        verdict = "ambiguous_attribution"
    elif amount_status == "ambiguous":
        verdict = "ambiguous_attribution"
    elif amount_status == "wrong":
        verdict = "wrong_amount"
    elif not direct:
        verdict = "correct_derived" if direct_basis in {
            "state_derived", "state_constrained"
        } else "unobservable"
    else:
        verdict = "correct_direct"
    conflict_state.update(candidate.conflict_states())
    return {
        "unit_index": unit.get("unit_index"),
        "source_amount": expected_amount,
        "frozen_event_id": unit.get("frozen_event_id"),
        "verdict": verdict,
        "passed": verdict == "correct_direct",
        "amount_status": amount_status,
        "predicted_amount": candidate.amount,
        "ownership_status": ownership_status,
        "direct_evidence": direct,
        "direct_evidence_basis": direct_basis,
        "conflict_state": sorted(conflict_state),
        "matched_candidates": candidate_rows,
    }


def _combine_verdicts(units: list[dict[str, Any]]) -> str:
    verdicts = {unit.get("verdict") for unit in units}
    for value in ("cancellation", "duplicate", "missing", "wrong_turn",
                  "ambiguous_attribution", "unobservable", "wrong_amount",
                  "correct_derived"):
        if value in verdicts:
            return value
    return "correct_direct" if verdicts == {"correct_direct"} else "unobservable"


def _case_result(case: Mapping[str, Any], candidates: list[Candidate],
                 recording: str | None, reference_path: Path | None) -> dict[str, Any]:
    units = [_unit_verdict(unit, case, candidates, recording) for unit in _target_units(case)]
    source_amounts = [unit.get("source_amount") for unit in units]
    numeric_source_amounts = [amount for amount in source_amounts if _number(amount) is not None]
    source_amount = case.get("source_amount")
    if source_amount is None and len(numeric_source_amounts) == len(source_amounts):
        source_amount = sum(numeric_source_amounts)
    predicted_amounts = [unit.get("predicted_amount") for unit in units]
    numeric_predictions = [amount for amount in predicted_amounts if _number(amount) is not None]
    predicted_amount = (sum(numeric_predictions)
                        if len(numeric_predictions) == len(predicted_amounts) else None)
    verdict = _combine_verdicts(units)
    case_conflicts = sorted({state for unit in units for state in unit.get("conflict_state", [])})
    direct_evidence = bool(units) and all(unit.get("direct_evidence", False) for unit in units)
    direct_bases = sorted({str(unit.get("direct_evidence_basis")) for unit in units})
    image_hashes: list[str] = []
    for unit in _target_units(case):
        for row in _as_list(unit.get("source_evidence")):
            if isinstance(row, Mapping) and isinstance(row.get("sha256"), str):
                image_hashes.append(row["sha256"].lower())
    return {
        "case_id": case.get("id", case.get("case_id")),
        "recording": case.get("recording", recording),
        "turn_id": case.get("turn_id"),
        "field": case.get("field"),
        "source_amount": source_amount,
        "predicted_amount": predicted_amount,
        "verdict": verdict,
        "status": verdict,
        "passed": verdict == "correct_direct",
        "direct_evidence": direct_evidence,
        "direct_evidence_basis": direct_bases,
        "conflict_state": case_conflicts,
        "reference_image_sha256": _unique_text(image_hashes),
        "units": units,
        "primary_cause": case.get("primary_cause"),
        "root_causes": list(case.get("root_causes", [])) if isinstance(case.get("root_causes"), list) else [],
        "reference_sha256": _sha256(reference_path) if reference_path and reference_path.is_file() else None,
    }


def _performance_entries(report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    return _readings(report)


def _performance_values(row: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return separately localized current and projected performance values."""
    current: dict[str, Any] = {}
    projected: dict[str, Any] = {}
    facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
    for source in (facts, row):
        if not isinstance(source, Mapping):
            continue
        for key in ("performance_points", "current_performance_points", "current_values"):
            values = source.get(key)
            if isinstance(values, Mapping):
                current.update(values)
        for key in ("projected_performance_gains", "projected_values", "projected_gains"):
            values = source.get(key)
            if isinstance(values, Mapping):
                projected.update(values)
        if "current_composure" in source:
            current["composure"] = source["current_composure"]
        if "projected_composure_gain" in source:
            projected["composure"] = source["projected_composure_gain"]
    return current, projected


def _merged_performance_ocr(row: Mapping[str, Any]) -> list[str]:
    merged: list[str] = []
    ocr = row.get("ocr")
    rows = []
    if isinstance(ocr, Mapping):
        rows.extend(_as_list(ocr.get("neural")))
    rows.extend(_as_list(row.get("ocr_neural")))
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        if isinstance(text, str) and re.fullmatch(r"\s*\d+\s*\+\s*\d+\s*", text):
            merged.append(text.strip())
    return _unique_text(merged)


def _composure_case(causes: Mapping[str, Any], report: Mapping[str, Any],
                    recording: str | None) -> dict[str, Any] | None:
    rows = causes.get("additional_targeted_cases")
    if not isinstance(rows, list):
        return None
    source = next(
        (
            row for row in rows
            if isinstance(row, Mapping)
            and (
                str(row.get("kind", "")).startswith("performance_panel")
                or "composure-current-plus-projection" in str(row.get("id", ""))
            )
        ),
        None,
    )
    if not isinstance(source, Mapping):
        return None
    if recording is not None and source.get("recording") not in (None, recording):
        return None
    timestamp = _int(source.get("timestamp_ms"))
    amounts = source.get("source_amounts") if isinstance(source.get("source_amounts"), Mapping) else {}
    expected_current = _number(amounts.get("current_composure", amounts.get("current")))
    expected_projected = _number(amounts.get("projected_composure_gain", amounts.get("projected")))
    if timestamp is None:
        return {"case_id": source.get("id"), "verdict": "unobservable", "passed": False}

    observations: list[dict[str, Any]] = []
    current_values: list[tuple[Any, str, str | None]] = []
    projected_values: list[tuple[Any, str, str | None]] = []
    merged: list[dict[str, Any]] = []
    # A narrow recovery window around the frozen source reading allows a
    # projected value to appear on the next preview frame, while avoiding a
    # state/balance search over the recording.
    for ref, row in _performance_entries(report):
        observed_at = _int(row.get("source_timestamp_ms", row.get("timestamp_ms")))
        if observed_at is None or abs(observed_at - timestamp) > 1000:
            continue
        current, projected = _performance_values(row)
        direct_basis = "source_linked_observation" if row.get("evidence") else "unobservable"
        facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
        for key, value in current.items():
            if key in ("composure", "current_composure") and _number(value) is not None:
                current_values.append((value, direct_basis, ref))
        for key, value in projected.items():
            if key in ("composure", "projected_composure") and _number(value) is not None:
                projected_values.append((value, direct_basis, ref))
        for text_value in _merged_performance_ocr(row):
            merged.append({"text": text_value, "report_ref": ref,
                           "source_timestamp_ms": observed_at,
                           "evidence": row.get("evidence")})
        observations.append({
            "report_ref": ref,
            "source_timestamp_ms": observed_at,
            "evidence": row.get("evidence"),
            "current_keys": sorted(current),
            "projected_keys": sorted(projected),
            "merged_ocr": _merged_performance_ocr(row),
        })

    def component(values: list[tuple[Any, str, str | None]], expected: Any, name: str) -> dict[str, Any]:
        distinct = {value for value, _, _ in values if _number(value) is not None}
        if not distinct:
            return {"field": name, "expected": expected, "actual": None,
                    "status": "missing", "direct_evidence": False,
                    "direct_evidence_basis": "merged_or_unobservable" if merged else "unobservable",
                    "observations": []}
        if len(distinct) > 1:
            status = "ambiguous"
            actual = None
        else:
            actual = next(iter(distinct))
            status = "correct" if actual == expected else "wrong_amount"
        bases = {basis for _, basis, _ in values}
        direct = status == "correct" and bases == {"source_linked_observation"}
        if status == "correct" and not direct:
            status = "correct_derived"
        return {
            "field": name, "expected": expected, "actual": actual, "status": status,
            "direct_evidence": direct,
            "direct_evidence_basis": sorted(bases) if bases else "unobservable",
            "observations": [{"report_ref": ref, "value": value, "basis": basis}
                             for value, basis, ref in values],
        }

    current = component(current_values, expected_current, "current_composure")
    projected = component(projected_values, expected_projected, "projected_composure_gain")
    if current["status"] in ("missing", "ambiguous") or projected["status"] in ("missing", "ambiguous"):
        verdict = "ambiguous" if merged or current["status"] == "ambiguous" or projected["status"] == "ambiguous" else "missing"
    elif current["status"] == "wrong_amount" or projected["status"] == "wrong_amount":
        verdict = "wrong_amount"
    elif current["status"] == "correct_derived" or projected["status"] == "correct_derived":
        verdict = "correct_derived"
    else:
        verdict = "correct_direct"
    return {
        "case_id": source.get("id"),
        "recording": source.get("recording", recording),
        "timestamp_ms": timestamp,
        "verdict": verdict,
        "status": verdict,
        "passed": verdict == "correct_direct",
        "not_combined": True,
        "current": current,
        "projected": projected,
        "merged_ocr": merged,
        "observations": observations,
        "reference_image_sha256": source.get("source_evidence", {}).get("sha256")
        if isinstance(source.get("source_evidence"), Mapping) else None,
    }


def _report_source_hash(report: Mapping[str, Any]) -> str | None:
    source = report.get("source")
    if isinstance(source, Mapping) and isinstance(source.get("sha256"), str):
        return source["sha256"].lower()
    for key in ("source_sha256", "video_sha256"):
        if isinstance(report.get(key), str):
            return report[key].lower()
    return None


def _report_run(report: Mapping[str, Any], report_path: Path | None = None) -> str | None:
    for key in ("run", "recording", "development_recording"):
        value = report.get(key)
        if isinstance(value, str) and value:
            return value
    if report_path:
        match = re.match(r"(.+)-report\.json$", report_path.name)
        if match:
            return match.group(1)
    return None


def _normalise_cases(causes: Mapping[str, Any]) -> list[dict[str, Any]]:
    schema = causes.get("schema_version")
    if schema is not None and schema != CAUSES_SCHEMA:
        raise ValueError(f"Unsupported numeric causes schema: {schema!r}")
    rows = causes.get("cases", causes.get("comparisons"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Numeric causes must contain a nonempty cases array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"numeric causes cases[{index}] must be an object")
        case = dict(row)
        case_id = case.get("id", case.get("case_id"))
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"numeric causes cases[{index}] has no id")
        if case_id in seen:
            raise ValueError(f"Duplicate numeric cause case id: {case_id}")
        seen.add(case_id)
        if not isinstance(case.get("field"), str) or not case["field"]:
            raise ValueError(f"{case_id} has no target field")
        if not isinstance(case.get("recording"), str) or not case["recording"]:
            raise ValueError(f"{case_id} has no recording")
        result.append(case)
    return result


def evaluate_report(causes: Mapping[str, Any], report: Mapping[str, Any], *,
                    report_path: Path | None = None,
                    reference_path: Path | None = None,
                    recording: str | None = None) -> dict[str, Any]:
    """Evaluate the frozen cases belonging to one report."""
    cases = _normalise_cases(causes)
    report_run = recording or _report_run(report, report_path)
    recordings = {case["recording"] for case in cases}
    if report_run is None and len(recordings) == 1:
        report_run = next(iter(recordings))
    if report_run is None:
        raise ValueError("Report run is required when numeric causes contain multiple recordings")
    selected = [case for case in cases if case["recording"] == report_run]
    if not selected:
        raise ValueError(f"No frozen numeric cases belong to report run {report_run!r}")
    candidates = _extract_candidates(report, report_run)
    results = [_case_result(case, candidates, report_run, reference_path) for case in selected]
    composure = _composure_case(causes, report, report_run)
    verdict_counts = Counter(result["verdict"] for result in results)
    unit_results = [unit for result in results for unit in result["units"]]
    unit_counts = Counter(unit["verdict"] for unit in unit_results)
    counts = {key: verdict_counts.get(key, 0) for key in VERDICT_ORDER}
    counts.update({
        "comparisons": len(results),
        "contributions": len(unit_results),
        "direct_evidence_comparisons": sum(result["direct_evidence"] for result in results),
        "correct_direct_comparisons": sum(result["verdict"] == "correct_direct" for result in results),
        "conflicted_comparisons": sum(bool(result["conflict_state"]) for result in results),
        "unit_verdicts": {key: unit_counts.get(key, 0) for key in VERDICT_ORDER},
    })
    numeric_passed = bool(results) and all(result["passed"] for result in results)
    composure_passed = composure is None or composure.get("passed", False)
    report_hash = _sha256(report_path) if report_path and report_path.is_file() else None
    reference_hash = _sha256(reference_path) if reference_path and reference_path.is_file() else None
    return {
        "schema_version": SCHEMA,
        "run": report_run,
        "source_sha256": _report_source_hash(report),
        "report": {"path": str(report_path) if report_path else None, "sha256": report_hash},
        "reference": {"path": str(reference_path) if reference_path else None, "sha256": reference_hash},
        "cases": results,
        "composure": composure,
        "counts": counts,
        "status_counts": dict(counts),
        "numeric_passed": numeric_passed,
        "composure_passed": composure_passed,
        "passed": numeric_passed and composure_passed,
        "matching_rules": {
            "candidate_join": "target field plus intersecting source evidence or overlapping source time",
            "amount_selection": "amount compared only after candidate assignment; expected amount never selects a candidate",
            "event_id_policy": "frozen event ids are audit metadata only; report event ids may be renumbered",
            "balance_policy": "before/after balances and residuals are not read by this grader",
            "direct_policy": "correct_direct requires an exact canonical observed basis, a joined frozen-source evidence/time comparison, and no unresolved canonical conflict; an explicit production independent_effect_verification=false is acceptable only after that independent candidate join",
            "raw_gain_policy": "raw training-gain readings and component/prefix alternatives cannot fill a missing canonical gain; a validated complete/component phase proof may resolve its raw alternative without becoming a conflict",
            "composure_policy": "current and projected values are graded separately; merged 56+19 is not split or summed",
        },
        "limitations": [
            "This is a frozen targeted-case grader, not a whole-recording recall measure.",
            "Missing or ambiguous report ownership remains missing or ambiguous.",
            "State-derived matches are reported separately and never promoted to direct evidence.",
            "Production independent_effect_verification metadata is preserved; this evaluator's frozen-source comparison is a separate verification step and does not promote state-derived or conflicted rows.",
            "No OCR or video processing is performed.",
        ],
    }


def evaluate_reports(causes: Mapping[str, Any], reports: Iterable[tuple[Path | None, Mapping[str, Any]]], *,
                     reference_path: Path | None = None) -> dict[str, Any]:
    results = [evaluate_report(causes, report, report_path=path, reference_path=reference_path)
               for path, report in reports]
    verdict_counts = Counter()
    unit_counts = Counter()
    for result in results:
        verdict_counts.update({key: value for key, value in result["counts"].items()
                               if key in VERDICT_ORDER})
        unit_counts.update(result["counts"].get("unit_verdicts", {}))
    counts = {key: verdict_counts.get(key, 0) for key in VERDICT_ORDER}
    counts.update({
        "comparisons": sum(result["counts"]["comparisons"] for result in results),
        "contributions": sum(result["counts"]["contributions"] for result in results),
        "direct_evidence_comparisons": sum(result["counts"]["direct_evidence_comparisons"] for result in results),
        "correct_direct_comparisons": sum(result["counts"]["correct_direct_comparisons"] for result in results),
        "conflicted_comparisons": sum(result["counts"]["conflicted_comparisons"] for result in results),
        "unit_verdicts": {key: unit_counts.get(key, 0) for key in VERDICT_ORDER},
    })
    return {
        "schema_version": SCHEMA,
        "runs": results,
        "counts": counts,
        "status_counts": dict(counts),
        "passed": bool(results) and all(result["passed"] for result in results),
        "numeric_passed": bool(results) and all(result["numeric_passed"] for result in results),
        "composure_passed": bool(results) and all(result["composure_passed"] for result in results),
        "reference": {
            "path": str(reference_path) if reference_path else None,
            "sha256": _sha256(reference_path) if reference_path and reference_path.is_file() else None,
        },
        "matching_rules": results[0]["matching_rules"] if results else {},
    }


def _report_path_for_run(report_dir: Path, run: str) -> Path:
    candidates = (report_dir / f"{run}-report.json", report_dir / f"{run}.json",
                  report_dir / run / "report.json")
    for path in candidates:
        if path.is_file():
            return path
    raise ValueError(f"Missing report for run {run} in {report_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--causes", "--reference", dest="causes", type=Path,
                        default=Path(".local/final-reliability-v1/numeric-causes.json"))
    parser.add_argument("--report", action="append", type=Path,
                        help="One report; may be supplied more than once")
    parser.add_argument("--report-dir", type=Path,
                        help="Directory containing <run>-report.json files")
    parser.add_argument("--output", type=Path,
                        help="Write machine-readable JSON to this path; otherwise stdout")
    args = parser.parse_args(argv)
    causes = _read_json(args.causes)
    cases = _normalise_cases(causes)
    if args.report and args.report_dir:
        parser.error("use --report or --report-dir, not both")
    report_paths: list[Path]
    if args.report:
        report_paths = args.report
    else:
        report_dir = args.report_dir or args.causes.parent / "before"
        report_paths = [_report_path_for_run(report_dir, run)
                        for run in sorted({case["recording"] for case in cases})]
    pairs = [(path, _read_json(path)) for path in report_paths]
    result = evaluate_reports(causes, pairs, reference_path=args.causes)
    encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
