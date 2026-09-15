"""Build and verify a source-bound matrix for frozen semantic rows.

This is an audit helper.  It preserves the historical grade and labels, and
does not turn a source adjudication into a pass.  A row can be called a fixed
core candidate only when the final prediction is independently bound to the
source row by identity, evidence bytes, time, and unique ownership.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


SCHEMA = "tracen-replay/source-disposition-matrix-v2"
SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)

# These classifications describe an external source review.  They are kept
# advisory even when a final producer happens to contain a matching value.
LABEL_ERROR_CLASSES = {
    "frozen_label_mismatch",
    "source_phase_reference_correction",
    "source_supported_preview_only_frozen_applied_label",
    "race_result_present_phase_ontology_mismatch",
    "source_label_semantic_issue",
    "verified_source_phase_correction",
    "verified_source_field_correction",
    "verified_source_component_correction",
    "verified_source_correction_advisory_freeze_update",
}
AMBIGUOUS_CLASSES = {
    "legitimate_unresolved_source_conflict",
    "committed_price_unresolved_reference_semantics",
    "partial_glyph_read_of_stable_result",
    "verified_source_transient_observations_with_unresolved_occlusion_metadata",
}


class MatrixError(ValueError):
    """Raised when an audit input or generated matrix is not trustworthy."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise MatrixError(f"{label} is not a file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MatrixError(f"Cannot read {label}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise MatrixError(f"{label} must be a JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise MatrixError(f"{label} must be a SHA-256 digest")
    return value.lower()


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _hash_matches(value: Any, expected: str) -> bool:
    return _valid_hash(value) and _valid_hash(expected) and value.lower() == expected.lower()


def _path(path: Path, label: str, *, directory: bool = False) -> Path:
    resolved = Path(path).resolve()
    if directory:
        if not resolved.is_dir():
            raise MatrixError(f"{label} is not a directory: {resolved}")
    elif not resolved.is_file():
        raise MatrixError(f"{label} is not a file: {resolved}")
    return resolved


def _artifact(path: Path, label: str) -> dict[str, str]:
    path = _path(path, label)
    return {"path": str(path), "sha256": _sha(path)}


def _safe_child(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise MatrixError(f"{label} must be a nonempty relative path")
    candidate = Path(relative)
    root = Path(root).resolve()
    if candidate.is_absolute():
        raise MatrixError(f"{label} must be relative: {relative}")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise MatrixError(f"{label} escapes its evidence root: {relative}")
    return resolved


def _unescape_pointer(token: str) -> str:
    # Decode in the order required by RFC 6901.
    return token.replace("~1", "/").replace("~0", "~")


def _pointer_parts(pointer: Any) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise MatrixError(f"Invalid JSON pointer: {pointer!r}")
    return [_unescape_pointer(item) for item in pointer[1:].split("/")]


def _pointer_get(value: Any, pointer: str) -> Any:
    current = value
    for token in _pointer_parts(pointer):
        if isinstance(current, list):
            if re.fullmatch(r"(?:0|[1-9][0-9]*)", token) is None:
                raise MatrixError(f"Noncanonical list index in JSON pointer: {pointer}")
            try:
                index = int(token)
                if index < 0:
                    raise IndexError(token)
                current = current[index]
            except (ValueError, IndexError) as error:
                raise MatrixError(f"JSON pointer does not resolve: {pointer}") from error
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise MatrixError(f"JSON pointer does not resolve: {pointer}")
    return current


def _pointer_get_optional(value: Any, pointer: str) -> Any:
    try:
        return _pointer_get(value, pointer)
    except MatrixError:
        return None


def _interval(value: Any) -> list[int] | None:
    if (isinstance(value, list) and len(value) == 2
            and all(type(item) is int and item >= 0 for item in value)
            and value[0] <= value[1]):
        return list(value)
    return None


def _row_interval(row: Mapping[str, Any]) -> list[int] | None:
    direct = _interval(row.get("source_interval_ms"))
    if direct is not None:
        return direct
    direct = _interval(row.get("interval_ms"))
    if direct is not None:
        return direct
    start = row.get("start_ms", row.get("first_seen_ms"))
    end = row.get("end_ms", row.get("last_seen_ms"))
    if (type(start) is int and type(end) is int
            and start >= 0 and end >= 0 and start <= end):
        return [start, end]
    return None


def _evidence_paths(value: Any) -> list[str]:
    """Normalize strings and proof objects without trusting their hashes."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        path = value.get("path", value.get("evidence"))
        return [path] if isinstance(path, str) and path else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_evidence_paths(item))
        return result
    return []


def _proof_hashes(value: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        path = value.get("path", value.get("evidence"))
        declared = value.get("sha256", value.get("image_sha256"))
        if isinstance(path, str) and isinstance(declared, str) and SHA256.fullmatch(declared):
            result[path] = declared.lower()
        return result
    if isinstance(value, list):
        for item in value:
            result.update(_proof_hashes(item))
    return result


def _reference_rows(reference: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index only typed source observations, avoiding arbitrary nested IDs."""
    rows: dict[str, dict[str, Any]] = {}
    cases = reference.get("cases")
    if not isinstance(cases, list):
        raise MatrixError("Source reference must contain a cases list")
    for case_index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise MatrixError(f"Source reference case {case_index} is not an object")
        observations = case.get("observations", case.get("rows", []))
        if not isinstance(observations, list):
            raise MatrixError(f"Source reference case {case_index} observations are not a list")
        for row_index, row in enumerate(observations):
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise MatrixError(f"Source reference observation {case_index}/{row_index} has no id")
            source_id = row["id"]
            if source_id in rows:
                raise MatrixError(f"Duplicate source reference id: {source_id}")
            rows[source_id] = row
    return rows


def _grade_rows(grade: Mapping[str, Any], label: str) -> list[dict[str, Any]]:
    documents = grade.get("runs")
    if documents is None:
        documents = [grade]
    if not isinstance(documents, list) or not documents:
        raise MatrixError(f"{label} must contain cases or a nonempty runs list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for document_index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise MatrixError(f"{label}.runs[{document_index}] is not an object")
        cases = document.get("cases")
        if not isinstance(cases, list):
            raise MatrixError(f"{label} case list is missing")
        for case_index, case in enumerate(cases):
            if not isinstance(case, dict):
                raise MatrixError(f"{label} case {case_index} is not an object")
            case_id = case.get("case_id")
            case_turn = case.get("turn_id")
            results = case.get("results")
            if not isinstance(case_id, str) or not case_id:
                raise MatrixError(f"{label} case {case_index} has no case_id")
            if not isinstance(results, list):
                raise MatrixError(f"{label} case {case_id} has no results list")
            for result_index, result in enumerate(results):
                if not isinstance(result, dict):
                    raise MatrixError(f"{label} {case_id} result {result_index} is not an object")
                source_id = result.get("source_id")
                if not isinstance(source_id, str) or not source_id:
                    raise MatrixError(f"{label} {case_id} result {result_index} has no source_id")
                if source_id in seen:
                    raise MatrixError(f"Duplicate baseline/final source_id: {source_id}")
                seen.add(source_id)
                rows.append({
                    "source_id": source_id,
                    "case_id": case_id,
                    "case_turn_id": case_turn,
                    "run": document.get("run", grade.get("run")),
                    "raw": deepcopy(result),
                })
    return rows


def _field_value(payload: Any, pointer: Any) -> tuple[bool, Any]:
    if not isinstance(pointer, str):
        return False, None
    if not pointer.startswith("/"):
        return False, None
    try:
        return True, _pointer_get(payload, pointer)
    except MatrixError:
        return False, None


def _source_payload_matches(source: Mapping[str, Any], grade_row: Mapping[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    source_payload = source.get("payload")
    if not isinstance(source_payload, dict):
        return False, ["source_payload_missing"]
    raw = grade_row["raw"]
    if raw.get("category") != source.get("category"):
        errors.append("category_mismatch")
    if raw.get("phase") != source.get("phase"):
        errors.append("phase_mismatch")
    source_interval = _row_interval(source)
    grade_interval = _row_interval(raw)
    if source_interval is None:
        errors.append("source_interval_missing_or_invalid")
    if grade_interval is None:
        errors.append("grade_interval_missing_or_invalid")
    if source_interval is not None and grade_interval is not None and source_interval != grade_interval:
        errors.append("interval_mismatch")
    source_evidence = _evidence_paths(source.get("evidence", source.get("source_evidence")))
    grade_evidence = _evidence_paths(raw.get("source_evidence", raw.get("evidence")))
    if source_evidence != grade_evidence:
        errors.append("source_evidence_alias_mismatch")
    expected_turn = raw.get("expected_turn_id")
    if expected_turn is not None and source.get("expected_turn_id") != expected_turn:
        errors.append("expected_turn_mismatch")
    for field in raw.get("fields", []):
        if not isinstance(field, dict):
            errors.append("malformed_grade_field")
            continue
        ok, actual = _field_value(source_payload, field.get("field"))
        if not ok:
            errors.append(f"source_field_missing:{field.get('field')}")
        elif actual != field.get("expected"):
            errors.append(f"source_field_value_mismatch:{field.get('field')}")
    return not errors, errors


def _source_binding(
    reference: Mapping[str, Any],
    source_rows: Mapping[str, dict[str, Any]],
    source_root: Path,
    grade_row: Mapping[str, Any],
    reference_sha256: str,
) -> dict[str, Any]:
    source_id = grade_row["source_id"]
    source = source_rows.get(source_id)
    checks: dict[str, bool] = {
        "source_row_exists": source is not None,
        "reference_source_sha256_present": isinstance(reference.get("source_sha256"), str),
        "reference_file_hash_present": bool(reference_sha256),
    }
    errors: list[str] = []
    if source is None:
        return {
            "all_passed": False,
            "checks": checks,
            "errors": [f"missing_source_row:{source_id}"],
            "source_id": source_id,
            "evidence": [],
        }
    shape_ok, shape_errors = _source_payload_matches(source, grade_row)
    checks["source_row_matches_baseline"] = shape_ok
    errors.extend(shape_errors)
    image_hashes = reference.get("image_sha256")
    image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
    source_evidence = _evidence_paths(source.get("evidence", source.get("source_evidence")))
    proof_objects = source.get("evidence")
    proof_hashes = _proof_hashes(proof_objects)
    evidence_rows: list[dict[str, Any]] = []
    for alias in source_evidence:
        physical = _safe_child(source_root, alias, f"source evidence {source_id}")
        exists = physical.is_file()
        declared = image_hashes.get(alias)
        if not isinstance(declared, str):
            declared = proof_hashes.get(alias)
        declared_valid = isinstance(declared, str) and SHA256.fullmatch(declared) is not None
        actual = _sha(physical) if exists else None
        hash_ok = bool(exists and declared_valid and actual == declared.lower())
        evidence_rows.append({
            "alias": alias,
            "path": str(physical),
            "exists": exists,
            "declared_sha256": declared.lower() if declared_valid else None,
            "physical_sha256": actual,
            "hash_matches": hash_ok,
        })
        if not exists:
            errors.append(f"missing_source_evidence:{alias}")
        elif not declared_valid:
            errors.append(f"missing_source_evidence_hash:{alias}")
        elif not hash_ok:
            errors.append(f"source_evidence_hash_mismatch:{alias}")
    checks["source_evidence_bound"] = bool(evidence_rows) and all(
        item["hash_matches"] for item in evidence_rows
    )
    checks["source_evidence_nonempty"] = bool(evidence_rows)
    return {
        "all_passed": all(checks.values()),
        "checks": checks,
        "errors": errors,
        "source_id": source_id,
        "source_payload": deepcopy(source.get("payload")),
        "source_interval_ms": _row_interval(source),
        "evidence": evidence_rows,
    }


def _event_context(report: Mapping[str, Any], parts: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    if len(parts) < 3 or parts[0] != "gameplay_tracking":
        return None, None
    collection, index_token = parts[1], parts[2]
    if collection not in {"events", "turn_action_receipts", "checkpoints",
                          "state_observations", "status_observations", "preview_observations"}:
        return None, None
    values = report.get("gameplay_tracking", {}).get(collection)
    if not isinstance(values, list):
        return None, None
    if re.fullmatch(r"(?:0|[1-9][0-9]*)", index_token) is None:
        return None, None
    try:
        item = values[int(index_token)]
    except (ValueError, IndexError):
        return None, None
    return item if isinstance(item, dict) else None, collection


def _phase_for_pointer(parts: list[str], event_collection: str | None) -> str | None:
    if event_collection == "events":
        if "deltas" in parts or "performance_deltas" in parts or "effects" in parts:
            return "applied"
        return None
    return {
        "turn_action_receipts": "committed",
        "checkpoints": "observed",
        "state_observations": "observed",
        "status_observations": "observed",
        "preview_observations": "preview",
    }.get(event_collection)


def _specific_event_evidence(
    context: Mapping[str, Any] | None,
    *,
    kind: str | None,
    field: str | None,
    name: str | None,
) -> list[str] | None:
    """Return a declared field/effect proof, or None when no key is declared.

    A present but empty typed key stays empty.  That prevents a broad event
    screenshot from silently becoming proof for a field whose own evidence
    was explicitly empty.
    """
    if not isinstance(context, Mapping):
        return None
    mapping = context.get("field_evidence")
    if not isinstance(mapping, Mapping):
        return None
    keys: list[str] = []
    if isinstance(kind, str) and isinstance(name, str):
        keys.extend((f"{kind}||{name}", f"{kind}|{name}"))
    if isinstance(kind, str) and isinstance(field, str):
        keys.extend((f"{kind}||{field}", f"{kind}|{field}"))
    if isinstance(field, str):
        keys.append(field)
    if isinstance(name, str):
        keys.append(name)
    for key in dict.fromkeys(keys):
        if key in mapping:
            return _evidence_paths(mapping[key])
    return None


def _actual_view(report: Mapping[str, Any], prediction_id: str) -> dict[str, Any]:
    parts = _pointer_parts(prediction_id)
    value = _pointer_get(report, prediction_id)
    context, collection = _event_context(report, parts)
    actual = deepcopy(value)
    value_evidence = _evidence_paths(value.get("evidence")) if isinstance(value, dict) else []
    evidence: list[str] = list(value_evidence)
    field: str | None = None
    kind: str | None = None
    name: str | None = None
    amount: Any = None
    evidence_key_present = False
    if collection == "events" and len(parts) >= 5 and parts[3] in {"deltas", "performance_deltas"}:
        field = parts[4]
        amount = value
        kind = "performance_change" if parts[3] == "performance_deltas" else "stat_change"
        actual = {"kind": kind, "field": field, "amount": amount}
        if context is not None:
            specific = _specific_event_evidence(
                context, kind=kind, field=field, name=None
            )
            if specific is not None:
                evidence = specific
                evidence_key_present = True
            elif not evidence:
                evidence = _evidence_paths(context.get("evidence"))
    elif isinstance(value, dict):
        kind = value.get("kind") if isinstance(value.get("kind"), str) else None
        field_value = value.get("field", value.get("name"))
        field = field_value if isinstance(field_value, str) else None
        name_value = value.get("name")
        name = name_value if isinstance(name_value, str) else None
        amount = value.get("amount")
        if context is not None:
            specific = _specific_event_evidence(
                context, kind=kind, field=field, name=name
            )
            if specific is not None:
                evidence = specific
                evidence_key_present = True
            elif not evidence:
                evidence = _evidence_paths(context.get("evidence"))
    if not evidence and context is not None and not evidence_key_present:
        evidence = _evidence_paths(context.get("evidence"))
    if not isinstance(name, str):
        name = actual.get("name") if isinstance(actual, dict) else None
    timestamps: list[int] | None = None
    if isinstance(value, dict):
        timestamps = _row_interval(value)
    if timestamps is None and context is not None:
        timestamps = _row_interval(context)
    # Preserve ordering for evidence provenance but remove duplicate aliases.
    evidence = list(dict.fromkeys(item for item in evidence if item))
    return {
        "value": actual,
        "context": deepcopy(context),
        "collection": collection,
        "phase": _phase_for_pointer(parts, collection),
        "kind": kind,
        "field": field,
        "name": name,
        "amount": amount,
        "timestamps_ms": timestamps,
        "evidence_paths": evidence,
        "pointer_parts": parts,
    }


def _report_occurrences(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract typed numeric occurrences for duplicate/cancellation controls."""
    tracking = report.get("gameplay_tracking", {})
    events = tracking.get("events", []) if isinstance(tracking, dict) else []
    result: list[dict[str, Any]] = []
    if not isinstance(events, list):
        return result
    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        for section, kind in (("deltas", "stat_change"), ("performance_deltas", "performance_change")):
            values = event.get(section)
            if not isinstance(values, dict):
                continue
            for field, amount in values.items():
                if type(amount) not in {int, float} or isinstance(amount, bool):
                    continue
                paths = []
                specific = _specific_event_evidence(
                    event, kind=kind, field=field, name=None
                )
                if specific is not None:
                    paths.extend(specific)
                elif not paths:
                    paths.extend(_evidence_paths(event.get("evidence")))
                result.append({
                    "pointer": f"/gameplay_tracking/events/{event_index}/{section}/{field}",
                    "kind": kind,
                    "field": field,
                    "name": None,
                    "amount": amount,
                    "timestamps_ms": _row_interval(event),
                    "evidence_paths": list(dict.fromkeys(paths)),
                })
        effects = event.get("effects")
        if isinstance(effects, list):
            for effect_index, effect in enumerate(effects):
                if not isinstance(effect, dict):
                    continue
                amount = effect.get("amount")
                if type(amount) not in {int, float} or isinstance(amount, bool):
                    continue
                effect_kind = effect.get("kind")
                effect_field = effect.get("field", effect.get("name"))
                effect_name = effect.get("name")
                specific = _specific_event_evidence(
                    event,
                    kind=effect_kind if isinstance(effect_kind, str) else None,
                    field=effect_field if isinstance(effect_field, str) else None,
                    name=effect_name if isinstance(effect_name, str) else None,
                )
                effect_paths = _evidence_paths(effect.get("evidence"))
                if specific is not None:
                    effect_paths = specific
                elif not effect_paths:
                    effect_paths = _evidence_paths(event.get("evidence"))
                result.append({
                    "pointer": f"/gameplay_tracking/events/{event_index}/effects/{effect_index}",
                    "kind": effect_kind,
                    "field": effect_field,
                    "name": effect_name,
                    "amount": amount,
                    "timestamps_ms": _row_interval(event),
                    "evidence_paths": list(dict.fromkeys(effect_paths)),
                })
    return result


def _report_binding(
    report: Mapping[str, Any],
    final_row: Mapping[str, Any] | None,
    source_binding: Mapping[str, Any],
    final_root: Path,
    source_evidence: list[dict[str, Any]],
    all_occurrences: list[dict[str, Any]],
) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "final_row_present": final_row is not None,
        "prediction_pointer_resolves": False,
        "semantic_identity_matches": False,
        "report_evidence_bound": False,
        "source_evidence_identity_matches": False,
        "timestamp_overlaps_source": False,
        "timestamp_exact_source_interval": False,
        "turn_owner_unique": False,
        "unique_occurrence": False,
        "no_conflicting_cancellation": False,
    }
    errors: list[str] = []
    if final_row is None:
        return {"all_passed": False, "checks": checks, "errors": ["missing_final_grade_row"]}
    raw = final_row["raw"]
    prediction_id = raw.get("prediction_id")
    actual: dict[str, Any] | None = None
    if isinstance(prediction_id, str):
        try:
            actual = _actual_view(report, prediction_id)
            checks["prediction_pointer_resolves"] = True
        except MatrixError as error:
            errors.append(str(error))
    else:
        errors.append("final_prediction_pointer_missing")
    if actual is None:
        return {"all_passed": False, "checks": checks, "errors": errors}

    source_payload = source_binding.get("source_payload")
    fields = raw.get("fields", [])
    field_errors: list[str] = []
    if isinstance(source_payload, dict) and isinstance(fields, list):
        expected_semantics = {
            "kind": source_payload.get("kind"),
            "field": source_payload.get("field"),
            "name": source_payload.get("name"),
            "amount": source_payload.get("amount"),
        }
        if actual.get("kind") != expected_semantics.get("kind"):
            # The source row's kind is the authoritative typed identity.  A
            # delta pointer is normalized to stat_change above.
            field_errors.append("kind_mismatch")
        for key in ("field", "name"):
            expected = expected_semantics.get(key)
            if expected is not None and actual.get(key) != expected:
                field_errors.append(f"{key}_mismatch")
        expected_phase = raw.get("phase")
        if expected_phase is not None and actual.get("phase") != expected_phase:
            field_errors.append("phase_mismatch")
        if "amount" in expected_semantics and actual.get("amount") != expected_semantics["amount"]:
            field_errors.append("amount_mismatch")
        for field in fields:
            if not isinstance(field, dict):
                field_errors.append("malformed_final_grade_field")
                continue
            pointer = field.get("field")
            ok, value = _field_value(actual.get("value"), pointer)
            if not ok and pointer == "/kind":
                value, ok = actual.get("kind"), actual.get("kind") is not None
            if not ok and pointer == "/field":
                value, ok = actual.get("field"), actual.get("field") is not None
            if not ok and pointer == "/amount":
                value, ok = actual.get("amount"), actual.get("amount") is not None
            if not ok:
                field_errors.append(f"actual_field_missing:{pointer}")
            elif value != field.get("actual"):
                field_errors.append(f"actual_field_value_mismatch:{pointer}")
    else:
        field_errors.append("source_payload_missing_for_identity")
    checks["semantic_identity_matches"] = not field_errors
    errors.extend(field_errors)

    actual_evidence = []
    for alias in actual.get("evidence_paths", []):
        physical = _safe_child(final_root, alias, "final report evidence")
        exists = physical.is_file()
        digest = _sha(physical) if exists else None
        actual_evidence.append({"alias": alias, "path": str(physical),
                               "exists": exists, "physical_sha256": digest})
        if not exists:
            errors.append(f"missing_final_evidence:{alias}")
    checks["report_evidence_bound"] = bool(actual_evidence) and all(
        item["exists"] for item in actual_evidence
    )
    source_hashes = {item.get("physical_sha256") for item in source_evidence
                     if isinstance(item.get("physical_sha256"), str)}
    actual_hashes = {item.get("physical_sha256") for item in actual_evidence
                     if isinstance(item.get("physical_sha256"), str)}
    checks["source_evidence_identity_matches"] = bool(source_hashes & actual_hashes)
    if not checks["source_evidence_identity_matches"]:
        errors.append("report_source_evidence_does_not_match_reference_bytes")

    source_interval = source_binding.get("source_interval_ms")
    actual_interval = actual.get("timestamps_ms")
    if isinstance(source_interval, list) and isinstance(actual_interval, list):
        checks["timestamp_overlaps_source"] = (
            actual_interval[0] <= source_interval[1]
            and source_interval[0] <= actual_interval[1]
        )
        checks["timestamp_exact_source_interval"] = actual_interval == source_interval
    if not checks["timestamp_overlaps_source"]:
        errors.append("prediction_time_outside_source_interval_or_missing")
    if not checks["timestamp_exact_source_interval"]:
        errors.append("prediction_time_not_exact_source_interval")

    expected_turn = raw.get("expected_turn_id", final_row.get("case_turn_id"))
    prediction_turn = raw.get("prediction_turn_id")
    candidates = raw.get("prediction_candidate_turn_ids")
    checks["turn_owner_unique"] = (
        isinstance(expected_turn, str)
        and prediction_turn == expected_turn
        and isinstance(candidates, list)
        and candidates == [expected_turn]
    )
    if not checks["turn_owner_unique"]:
        errors.append("prediction_turn_owner_not_unique")

    occurrence_key = (
        actual.get("kind"), actual.get("field"),
        actual.get("name"),
        actual.get("amount"), tuple(actual.get("timestamps_ms") or []),
        tuple(sorted(actual_hashes)),
    )
    duplicate_occurrences: list[dict[str, Any]] = []
    selected_pointer = prediction_id
    same_occurrences = [item for item in all_occurrences if (
        item.get("pointer") != selected_pointer
        and isinstance(item.get("timestamps_ms"), list)
        and (
            item.get("kind"), item.get("field"), item.get("name"), item.get("amount"),
            tuple(item.get("timestamps_ms"))
        ) == occurrence_key[:5]
    )]
    if same_occurrences:
        actual_hashes_for_occurrence = set(actual_hashes)
        duplicate_identity: list[dict[str, Any]] = []
        for item in same_occurrences:
            path_overlap = bool(set(item.get("evidence_paths", [])) &
                                set(actual.get("evidence_paths", [])))
            candidate_hashes: set[str] = set()
            for alias in item.get("evidence_paths", []):
                try:
                    candidate_path = _safe_child(final_root, alias, "candidate report evidence")
                except MatrixError:
                    continue
                if candidate_path.is_file():
                    candidate_hashes.add(_sha(candidate_path))
            if path_overlap or actual_hashes_for_occurrence & candidate_hashes:
                duplicate_identity.append({
                    "pointer": item.get("pointer"),
                    "identity": "path_or_byte_overlap",
                })
            else:
                duplicate_identity.append({
                    "pointer": item.get("pointer"),
                    "identity": "same_typed_time_without_byte_identity",
                })
        duplicate_occurrences = duplicate_identity
        checks["unique_occurrence"] = False
        errors.append("duplicate_report_occurrence" if any(
            item["identity"] == "path_or_byte_overlap" for item in duplicate_identity
        ) else "duplicate_report_occurrence_identity_unproven")
    else:
        checks["unique_occurrence"] = True

    opposite = [item for item in all_occurrences if (
        item.get("kind") == actual.get("kind")
        and item.get("field") == actual.get("field")
        and isinstance(actual.get("amount"), (int, float))
        and not isinstance(actual.get("amount"), bool)
        and isinstance(item.get("amount"), (int, float))
        and not isinstance(item.get("amount"), bool)
        and item.get("amount") == -actual.get("amount")
        and set(item.get("evidence_paths", [])) & set(actual.get("evidence_paths", []))
    )]
    checks["no_conflicting_cancellation"] = not opposite
    if opposite:
        errors.append("opposite_same_evidence_cancellation_present")

    return {
        "all_passed": all(checks.values()),
        "checks": checks,
        "errors": list(dict.fromkeys(errors)),
        "prediction_id": prediction_id,
        "actual": actual,
        "actual_evidence": actual_evidence,
        "duplicate_occurrences": duplicate_occurrences,
    }


def _review_records(path: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    document = _read_json(path, "review artifact")
    artifact = _artifact(path, "review artifact")
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(source_id: Any, record: Mapping[str, Any]) -> None:
        if not isinstance(source_id, str) or not source_id:
            return
        classification = record.get("classification")
        if not isinstance(classification, str) or not classification:
            return
        records[source_id].append({
            "artifact": artifact,
            "classification": classification,
            "finding": record.get("finding"),
            "action": record.get("action"),
            "adjudications": deepcopy(record.get("adjudications", [])),
            "source_evidence": deepcopy(record.get("source_evidence", [])),
            "review_record_valid": True,
        })

    dispositions = document.get("dispositions")
    if isinstance(dispositions, list):
        for record in dispositions:
            if isinstance(record, dict):
                add(record.get("source_id", record.get("source_ref")), record)

    findings = document.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            inherited = {
                "classification": finding.get("classification"),
                "finding": finding.get("adjudicated_interpretation", finding.get("finding")),
                "action": finding.get("grade_action", finding.get("action")),
            }
            for collection_name in ("source_refs", "context_refs", "stable_result_refs"):
                collection = finding.get(collection_name)
                if not isinstance(collection, list):
                    continue
                for item in collection:
                    if isinstance(item, dict):
                        combined = dict(inherited)
                        combined["source_evidence"] = item.get("evidence", item.get("source_evidence", []))
                        add(item.get("source_ref", item.get("source_id")), combined)

    corrections = document.get("verified_reference_corrections")
    if isinstance(corrections, list):
        for group in corrections:
            if not isinstance(group, dict):
                continue
            for row in group.get("frozen_rows", []):
                if isinstance(row, dict):
                    add(row.get("source_id"), {
                        "classification": group.get("classification"),
                        "finding": group.get("finding"),
                        "action": group.get("grade_effect"),
                    })
    return artifact, records


def _load_reviews(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    artifacts: list[dict[str, Any]] = []
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        artifact, records = _review_records(Path(path))
        artifacts.append(artifact)
        for source_id, values in records.items():
            index[source_id].extend(values)
    return artifacts, index


def _worker_binding(path: Path | None, final_report_sha256: str) -> dict[str, Any]:
    if path is None:
        return {"status": "missing", "accepted": False, "artifact": None,
                "errors": ["worker_result_not_supplied"]}
    artifact = _artifact(path, "worker result")
    result = _read_json(path, "worker result")
    errors: list[str] = []
    exit_code = result.get("exit_code")
    report_sha = result.get("report_sha256")
    if exit_code != 0:
        errors.append(f"worker_exit_code:{exit_code}")
    if not isinstance(report_sha, str) or report_sha.lower() != final_report_sha256:
        errors.append("worker_report_hash_mismatch")
    for key in ("replay_manifest_unchanged", "lesson_evidence_unchanged",
                "implementation_unchanged", "hint_cache_unchanged"):
        if result.get(key) is not True:
            errors.append(f"worker_{key}_not_verified")
    return {
        "status": "accepted" if not errors else "rejected",
        "accepted": not errors,
        "artifact": artifact,
        "exit_code": exit_code,
        "report_sha256": report_sha.lower() if isinstance(report_sha, str) else None,
        "errors": errors,
    }


def _grade_summary(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    raw = row["raw"]
    return {
        "status": raw.get("status"),
        "turn_status": raw.get("turn_status"),
        "prediction_id": raw.get("prediction_id"),
        "prediction_turn_id": raw.get("prediction_turn_id"),
        "prediction_candidate_turn_ids": deepcopy(raw.get("prediction_candidate_turn_ids", [])),
        "matching_basis": raw.get("matching_basis"),
        "fields": deepcopy(raw.get("fields", [])),
    }


def _review_summary(values: list[dict[str, Any]]) -> dict[str, Any]:
    if not values:
        return {"status": "none", "records": []}
    return {
        "status": "valid",
        "records": [{
            "artifact": deepcopy(value["artifact"]),
            "classification": value.get("classification"),
            "finding": deepcopy(value.get("finding")),
            "action": deepcopy(value.get("action")),
            "adjudications": deepcopy(value.get("adjudications", [])),
        } for value in values],
    }


def _is_label_error(values: list[dict[str, Any]]) -> bool:
    return any(value.get("classification") in LABEL_ERROR_CLASSES for value in values)


def _is_ambiguous(values: list[dict[str, Any]]) -> bool:
    return any(value.get("classification") in AMBIGUOUS_CLASSES for value in values)


def _disposition(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    source: Mapping[str, Any],
    final_source: Mapping[str, Any] | None,
    prediction: Mapping[str, Any],
    reviews: list[dict[str, Any]],
    worker_accepted: bool,
    input_binding_ok: bool,
) -> tuple[str, str, bool]:
    before_status = before["raw"].get("status") if before else None
    after_status = after["raw"].get("status") if after else None
    if _is_label_error(reviews):
        return "frozen_label_error_advisory", "external source review identifies a frozen phase/field/turn mismatch", False
    if _is_ambiguous(reviews):
        return "source_ambiguous", "source review leaves display, amount, ownership, or occlusion unresolved", False
    source_ok = (
        source.get("all_passed") is True
        and isinstance(final_source, Mapping)
        and final_source.get("all_passed") is True
    )
    prediction_ok = prediction.get("all_passed") is True
    after_correct = after_status == "correct" and after["raw"].get("turn_status") == "correct" if after else False
    before_noncorrect = before_status in {"missed", "partial", "incorrect"} or before is None
    if before_noncorrect and after_correct and input_binding_ok and source_ok and prediction_ok:
        return ("fixed_core" if worker_accepted else "fixed_core_candidate",
                "all source and prediction bindings pass; worker status controls acceptance", worker_accepted)
    if before_noncorrect and after_correct:
        return "unresolved", "final grade claims a fix but independent source/prediction binding is incomplete", False
    if before_noncorrect:
        return "unfixed_core", "baseline noncorrect row remains noncorrect or absent after final grading", False
    if before_status == "correct" and after_correct:
        return "unchanged_baseline", "baseline row remains correct", False
    if before_status == "correct" and not after_correct:
        return "regression_unverified", "baseline correct row lost correctness in the final grade", False
    return "unresolved", "row status cannot be classified from the supplied grades", False


def _input_binding(
    baseline_grade: Mapping[str, Any], baseline_grade_path: Path, baseline_report: Mapping[str, Any],
    baseline_report_path: Path, final_grade: Mapping[str, Any], final_grade_path: Path,
    final_report: Mapping[str, Any], final_report_path: Path, reference: Mapping[str, Any],
    reference_path: Path, freeze_path: Path,
) -> dict[str, Any]:
    reference_sha = _sha(reference_path)
    freeze_sha = _sha(freeze_path)
    baseline_report_sha = _sha(baseline_report_path)
    final_report_sha = _sha(final_report_path)
    source_sha = reference.get("source_sha256")
    errors: list[str] = []
    source_sha_valid = _valid_hash(source_sha)
    baseline_grade_report = baseline_grade.get("report_sha256")
    final_grade_report = final_grade.get("report_sha256")
    baseline_grade_reference = baseline_grade.get("reference_sha256")
    final_grade_reference = final_grade.get("reference_sha256")
    baseline_grade_freeze = baseline_grade.get("freeze_manifest_sha256")
    final_grade_freeze = final_grade.get("freeze_manifest_sha256")
    baseline_grade_source = baseline_grade.get("source_sha256")
    final_grade_source = final_grade.get("source_sha256")
    checks = {
        "reference_source_sha256_valid": source_sha_valid,
        "baseline_grade_report_hash": _hash_matches(baseline_grade_report, baseline_report_sha),
        "final_grade_report_hash": _hash_matches(final_grade_report, final_report_sha),
        "baseline_grade_reference_hash": _hash_matches(baseline_grade_reference, reference_sha),
        "final_grade_reference_hash": _hash_matches(final_grade_reference, reference_sha),
        "baseline_grade_freeze_hash": _hash_matches(baseline_grade_freeze, freeze_sha),
        "final_grade_freeze_hash": _hash_matches(final_grade_freeze, freeze_sha),
        "baseline_report_source_hash": baseline_report.get("source", {}).get("sha256") == source_sha,
        "final_report_source_hash": final_report.get("source", {}).get("sha256") == source_sha,
        "baseline_grade_source_hash": _hash_matches(baseline_grade_source, source_sha) if source_sha_valid else False,
        "final_grade_source_hash": _hash_matches(final_grade_source, source_sha) if source_sha_valid else False,
    }
    for key, value in checks.items():
        if not value:
            errors.append(key)
    return {
        "all_passed": all(checks.values()),
        "checks": checks,
        "errors": errors,
        "artifacts": {
            "baseline_grade": _artifact(baseline_grade_path, "baseline grade"),
            "baseline_report": _artifact(baseline_report_path, "baseline report"),
            "final_grade": _artifact(final_grade_path, "final grade"),
            "final_report": _artifact(final_report_path, "final report"),
            "source_reference": _artifact(reference_path, "source reference"),
            "freeze_manifest": _artifact(freeze_path, "freeze manifest"),
        },
        "source_sha256": source_sha,
    }


def _full_report_changes(
    path: Path | None,
    *,
    baseline_report_path: Path,
    final_report_path: Path,
    source_sha256: Any,
) -> dict[str, Any]:
    if path is None:
        return {
            "status": "pending_external_source_binding",
            "comparison_artifact": None,
            "records": [],
            "requirement": (
                "Bind every added, removed, or evidence-changed accepted action/effect "
                "to source identity and physical evidence before G7 acceptance."
            ),
        }
    artifact = _artifact(path, "full-report atom comparison")
    comparison = _read_json(path, "full-report atom comparison")
    source_ok = _hash_matches(comparison.get("source_sha256"), source_sha256)
    declared_inputs = comparison.get("input_sha256")
    baseline_report_sha = _sha(baseline_report_path)
    final_report_sha = _sha(final_report_path)
    input_binding_mode = None
    input_ok = False
    if isinstance(declared_inputs, dict) and len(declared_inputs) == 2:
        # Batch grades use stable semantic keys.  The standalone atom
        # comparator uses the two report paths as keys.  Both forms bind each
        # side explicitly; a two-value set is insufficient because it could
        # swap before and after or bind an unrelated report.
        if set(declared_inputs) == {"before", "after"}:
            input_binding_mode = "before_after"
            input_ok = (
                _hash_matches(declared_inputs.get("before"), baseline_report_sha)
                and _hash_matches(declared_inputs.get("after"), final_report_sha)
            )
        else:
            expected_by_path = {
                str(Path(baseline_report_path).resolve()): baseline_report_sha,
                str(Path(final_report_path).resolve()): final_report_sha,
            }
            normalized: dict[str, Any] = {}
            for key, value in declared_inputs.items():
                if not isinstance(key, str):
                    continue
                try:
                    normalized[str(Path(key).resolve())] = value
                except (OSError, ValueError):
                    continue
            input_binding_mode = "report_paths"
            input_ok = (
                set(normalized) == set(expected_by_path)
                and all(_hash_matches(normalized.get(key), expected)
                        for key, expected in expected_by_path.items())
            )
    records: list[dict[str, Any]] = []
    for kind in ("added", "removed", "evidence_changes"):
        values = comparison.get(kind, [])
        if not isinstance(values, list):
            raise MatrixError(f"atom comparison {kind} is not a list")
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise MatrixError(f"atom comparison {kind}[{index}] is not an object")
            records.append({
                "change_kind": kind,
                "index": index,
                "raw": deepcopy(value),
                "binding_status": "unresolved_external_source_binding",
            })
    arrays_present = all(isinstance(comparison.get(kind), list)
                         for kind in ("added", "removed", "evidence_changes"))
    if not source_ok or not input_ok or not arrays_present:
        status = "pending_invalid_comparison_binding"
    elif records:
        status = "pending_external_source_binding"
    else:
        status = "no_atom_changes_reported"
    return {
        "status": status,
        "comparison_artifact": artifact,
        "input_binding": {
            "source_sha256_matches": source_ok,
            "report_hashes_match": input_ok,
            "report_hash_binding_mode": input_binding_mode,
            "arrays_present": arrays_present,
            "declared_source_sha256": comparison.get("source_sha256"),
            "declared_input_sha256": deepcopy(declared_inputs),
            "baseline_report": str(Path(baseline_report_path).resolve()),
            "final_report": str(Path(final_report_path).resolve()),
        },
        "records": records,
        "requirement": (
            "The atom comparison pairs by report payload/evidence only; each record still "
            "requires source identity, physical evidence, and semantic review."
        ),
    }


def build_matrix(
    *,
    baseline_grade_path: Path,
    baseline_report_path: Path,
    final_grade_path: Path,
    final_report_path: Path,
    source_reference_path: Path,
    freeze_manifest_path: Path,
    baseline_evidence_root: Path,
    final_evidence_root: Path,
    worker_result_path: Path | None = None,
    review_paths: Iterable[Path] = (),
    atom_comparison_path: Path | None = None,
) -> dict[str, Any]:
    baseline_grade_path = _path(baseline_grade_path, "baseline grade")
    baseline_report_path = _path(baseline_report_path, "baseline report")
    final_grade_path = _path(final_grade_path, "final grade")
    final_report_path = _path(final_report_path, "final report")
    source_reference_path = _path(source_reference_path, "source reference")
    freeze_manifest_path = _path(freeze_manifest_path, "freeze manifest")
    baseline_evidence_root = _path(baseline_evidence_root, "baseline evidence root", directory=True)
    final_evidence_root = _path(final_evidence_root, "final evidence root", directory=True)
    baseline_grade = _read_json(baseline_grade_path, "baseline grade")
    final_grade = _read_json(final_grade_path, "final grade")
    baseline_report = _read_json(baseline_report_path, "baseline report")
    final_report = _read_json(final_report_path, "final report")
    reference = _read_json(source_reference_path, "source reference")
    _read_json(freeze_manifest_path, "freeze manifest")
    baseline_rows = _grade_rows(baseline_grade, "baseline grade")
    final_rows = _grade_rows(final_grade, "final grade")
    baseline_by_id = {row["source_id"]: row for row in baseline_rows}
    final_by_id = {row["source_id"]: row for row in final_rows}
    source_rows = _reference_rows(reference)
    input_binding = _input_binding(
        baseline_grade, baseline_grade_path, baseline_report, baseline_report_path,
        final_grade, final_grade_path, final_report, final_report_path,
        reference, source_reference_path, freeze_manifest_path,
    )
    review_artifacts, review_index = _load_reviews(review_paths)
    worker = _worker_binding(worker_result_path, input_binding["artifacts"]["final_report"]["sha256"])
    report_occurrences = _report_occurrences(final_report)
    rows: list[dict[str, Any]] = []
    for source_id in (row["source_id"] for row in baseline_rows):
        before = baseline_by_id[source_id]
        after = final_by_id.get(source_id)
        source_binding = _source_binding(
            reference, source_rows, baseline_evidence_root, before,
            input_binding["artifacts"]["source_reference"]["sha256"],
        )
        final_source_binding = _source_binding(
            reference, source_rows, baseline_evidence_root, after,
            input_binding["artifacts"]["source_reference"]["sha256"],
        ) if after is not None else {
            "all_passed": False,
            "checks": {"final_grade_row_exists": False},
            "errors": [f"missing_final_grade_row:{source_id}"],
            "source_id": source_id,
            "evidence": [],
        }
        prediction_binding = _report_binding(
            final_report, after, source_binding, final_evidence_root,
            source_binding.get("evidence", []), report_occurrences,
        )
        review = _review_summary(review_index.get(source_id, []))
        disposition, reason, counts_as_fix = _disposition(
            before, after, source_binding, final_source_binding, prediction_binding,
            review_index.get(source_id, []), worker["accepted"],
            input_binding["all_passed"],
        )
        rows.append({
            "source_id": source_id,
            "case_id": before["case_id"],
            "expected_turn_id": before["raw"].get("expected_turn_id", before.get("case_turn_id")),
            "before": _grade_summary(before),
            "after": _grade_summary(after),
            "source_binding": source_binding,
            "final_source_binding": final_source_binding,
            "prediction_binding": prediction_binding,
            "review": review,
            "disposition": disposition,
            "disposition_reason": reason,
            "counts_as_fixed_core": counts_as_fix,
            "raw_before": deepcopy(before["raw"]),
            "raw_after": deepcopy(after["raw"]) if after else None,
        })
    baseline_ids = [row["source_id"] for row in baseline_rows]
    final_extra = sorted(set(final_by_id) - set(baseline_by_id))
    changes = _full_report_changes(
        atom_comparison_path,
        baseline_report_path=baseline_report_path,
        final_report_path=final_report_path,
        source_sha256=input_binding.get("source_sha256"),
    )
    unresolved = [row["source_id"] for row in rows
                  if row["disposition"] in {"unresolved", "unfixed_core", "regression_unverified"}]
    advisory = [row["source_id"] for row in rows
                if row["disposition"] in {"frozen_label_error_advisory", "source_ambiguous"}]
    # This matrix is an evidence inventory, not the G6/G7 acceptance gate.
    # Even a hash-bound empty atom comparison cannot establish that the full
    # report was exhaustively compared, and worker flags do not independently
    # prove source/run/protocol scope.  Keep row dispositions useful for
    # review while making the top-level result permanently diagnostic.
    acceptance_eligible = False
    status = (
        "diagnostic_only_worker_rejected" if not worker["accepted"]
        else "diagnostic_only_external_g7_review_required"
    )
    acceptance_blockers = [
        "source_disposition_matrix_is_diagnostic_only",
        "external_g7_source_review_required_for_full_report_changes",
    ]
    if not worker["accepted"]:
        acceptance_blockers.append("worker_result_not_accepted")
    if unresolved:
        acceptance_blockers.append("unresolved_or_regression_rows_present")
    if advisory:
        acceptance_blockers.append("advisory_source_dispositions_present")
    if final_extra:
        acceptance_blockers.append("final_grade_has_unaccounted_source_ids")
    return {
        "schema_version": SCHEMA,
        "status": status,
        "acceptance_eligible": acceptance_eligible,
        "scope": {
            "baseline_row_count": len(baseline_rows),
            "final_row_count": len(final_rows),
            "matrix_row_count": len(rows),
            "baseline_source_ids_exactly_once": len(baseline_ids) == len(set(baseline_ids)),
            "baseline_rows_accounted_exactly_once": [row["source_id"] for row in rows] == baseline_ids,
            "final_extra_source_ids": final_extra,
            "unresolved_or_core_rows": unresolved,
            "advisory_rows": advisory,
            "acceptance_blockers": acceptance_blockers,
        },
        "root_bindings": {
            "baseline_evidence_root": str(baseline_evidence_root),
            "final_evidence_root": str(final_evidence_root),
        },
        "inputs": {
            "binding": input_binding,
            "review_artifacts": review_artifacts,
            "worker_result": worker,
        },
        "rows": rows,
        "full_report_changes": changes,
        "invariants": {
            "raw_grade_statuses_preserved": True,
            "frozen_labels_scores_denominator_untouched": True,
            "unknown_or_hidden_is_not_zero": True,
            "amount_or_balance_not_used_as_occurrence_selector": True,
            "source_hashes_are_binding_checks_not_visual_semantic_proof": True,
            "acceptance_eligible_always_false": True,
            "worker_integrity_flags_are_diagnostic_only": True,
        },
        "limitations": [
            "This matrix is permanently diagnostic; it cannot establish G6/G7 acceptance.",
            "A source adjudication classification is advisory and never creates a fixed_core disposition.",
            "Full-report atom changes remain pending independent source review; even a hash-bound empty comparison does not prove complete coverage.",
            "A broad report interval that merely overlaps a source interval is not exact timestamp ownership.",
            "A source image hash proves byte identity only; it does not by itself prove what a human can read.",
            "Worker report hashes and integrity flags are diagnostic bindings; they do not independently verify source, run, or protocol scope.",
        ],
    }


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise MatrixError(f"Matrix {label} differs from recomputed input")


def verify_matrix(matrix_path: Path, *, require_acceptance: bool = False) -> dict[str, Any]:
    matrix_path = _path(matrix_path, "matrix")
    matrix = _read_json(matrix_path, "matrix")
    if matrix.get("schema_version") != SCHEMA:
        raise MatrixError("Unsupported source-disposition matrix schema")
    inputs = matrix.get("inputs")
    roots = matrix.get("root_bindings")
    if not isinstance(inputs, dict) or not isinstance(roots, dict):
        raise MatrixError("Matrix inputs and root_bindings are required")
    binding = inputs.get("binding")
    if not isinstance(binding, dict):
        raise MatrixError("Matrix input binding is missing")
    def input_path(name: str) -> Path:
        item = binding.get("artifacts", {}).get(name)
        if not isinstance(item, dict):
            raise MatrixError(f"Matrix input artifact missing: {name}")
        path = _path(Path(item.get("path", "")), f"matrix input {name}")
        expected = _hash(item.get("sha256"), f"matrix input {name} hash")
        if _sha(path) != expected:
            raise MatrixError(f"Matrix input {name} changed")
        return path
    baseline_grade_path = input_path("baseline_grade")
    baseline_report_path = input_path("baseline_report")
    final_grade_path = input_path("final_grade")
    final_report_path = input_path("final_report")
    source_reference_path = input_path("source_reference")
    freeze_path = input_path("freeze_manifest")
    baseline_root = _path(Path(roots.get("baseline_evidence_root", "")),
                          "matrix baseline evidence root", directory=True)
    final_root = _path(Path(roots.get("final_evidence_root", "")),
                       "matrix final evidence root", directory=True)
    review_paths = []
    for artifact in inputs.get("review_artifacts", []):
        if not isinstance(artifact, dict):
            raise MatrixError("Malformed review artifact binding")
        path = _path(Path(artifact.get("path", "")), "matrix review artifact")
        if _sha(path) != _hash(artifact.get("sha256"), "matrix review artifact hash"):
            raise MatrixError(f"Matrix review artifact changed: {path}")
        review_paths.append(path)
    worker_item = inputs.get("worker_result")
    worker_path = None
    if isinstance(worker_item, dict) and isinstance(worker_item.get("artifact"), dict):
        worker_path = _path(Path(worker_item["artifact"].get("path", "")), "matrix worker result")
        if _sha(worker_path) != _hash(worker_item["artifact"].get("sha256"), "matrix worker result hash"):
            raise MatrixError("Matrix worker result changed")
    comparison_item = matrix.get("full_report_changes", {}).get("comparison_artifact")
    comparison_path = None
    if isinstance(comparison_item, dict):
        comparison_path = _path(Path(comparison_item.get("path", "")), "matrix atom comparison")
        if _sha(comparison_path) != _hash(comparison_item.get("sha256"), "matrix atom comparison hash"):
            raise MatrixError("Matrix atom comparison changed")
    recomputed = build_matrix(
        baseline_grade_path=baseline_grade_path,
        baseline_report_path=baseline_report_path,
        final_grade_path=final_grade_path,
        final_report_path=final_report_path,
        source_reference_path=source_reference_path,
        freeze_manifest_path=freeze_path,
        baseline_evidence_root=baseline_root,
        final_evidence_root=final_root,
        worker_result_path=worker_path,
        review_paths=review_paths,
        atom_comparison_path=comparison_path,
    )
    rows = matrix.get("rows")
    if not isinstance(rows, list):
        raise MatrixError("Matrix rows are missing")
    recomputed_rows = recomputed["rows"]
    if len(rows) != len(recomputed_rows):
        raise MatrixError("Matrix row count differs from recomputed baseline coverage")
    actual_ids = [row.get("source_id") for row in rows if isinstance(row, dict)]
    expected_ids = [row["source_id"] for row in recomputed_rows]
    if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
        raise MatrixError("Matrix baseline rows are missing, duplicated, or reordered")
    for actual, expected in zip(rows, recomputed_rows):
        if not isinstance(actual, dict):
            raise MatrixError("Matrix row is not an object")
        _assert_equal(actual.get("case_id"), expected.get("case_id"),
                      f"{actual.get('source_id')} case id")
        _assert_equal(actual.get("expected_turn_id"), expected.get("expected_turn_id"),
                      f"{actual.get('source_id')} expected turn")
        _assert_equal(actual.get("before"), expected.get("before"), f"{actual.get('source_id')} before status")
        _assert_equal(actual.get("after"), expected.get("after"), f"{actual.get('source_id')} after status")
        _assert_equal(actual.get("raw_before"), expected.get("raw_before"),
                      f"{actual.get('source_id')} raw before row")
        _assert_equal(actual.get("raw_after"), expected.get("raw_after"),
                      f"{actual.get('source_id')} raw after row")
        _assert_equal(actual.get("source_binding"), expected.get("source_binding"),
                      f"{actual.get('source_id')} source binding")
        _assert_equal(actual.get("final_source_binding"), expected.get("final_source_binding"),
                      f"{actual.get('source_id')} final source binding")
        _assert_equal(actual.get("prediction_binding"), expected.get("prediction_binding"),
                      f"{actual.get('source_id')} prediction binding")
        _assert_equal(actual.get("review"), expected.get("review"), f"{actual.get('source_id')} review")
        _assert_equal(actual.get("disposition"), expected.get("disposition"),
                      f"{actual.get('source_id')} disposition")
        _assert_equal(actual.get("disposition_reason"), expected.get("disposition_reason"),
                      f"{actual.get('source_id')} disposition reason")
        _assert_equal(actual.get("counts_as_fixed_core"), expected.get("counts_as_fixed_core"),
                      f"{actual.get('source_id')} fixed-core flag")
    _assert_equal(matrix.get("scope"), recomputed.get("scope"), "scope")
    _assert_equal(matrix.get("status"), recomputed.get("status"), "status")
    _assert_equal(matrix.get("inputs"), recomputed.get("inputs"), "inputs")
    _assert_equal(matrix.get("root_bindings"), recomputed.get("root_bindings"), "root_bindings")
    _assert_equal(matrix.get("full_report_changes"), recomputed.get("full_report_changes"),
                  "full_report_changes")
    _assert_equal(matrix.get("acceptance_eligible"), recomputed.get("acceptance_eligible"),
                  "acceptance eligibility")
    if require_acceptance and not recomputed["acceptance_eligible"]:
        raise MatrixError("Matrix is diagnostic only and cannot establish acceptance")
    return {
        "verified": True,
        "acceptance_eligible": recomputed["acceptance_eligible"],
        "status": recomputed["status"],
        "baseline_row_count": len(recomputed_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-grade", type=Path)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--final-grade", type=Path)
    parser.add_argument("--final-report", type=Path)
    parser.add_argument("--source-reference", type=Path)
    parser.add_argument("--freeze-manifest", type=Path)
    parser.add_argument("--baseline-evidence-root", type=Path)
    parser.add_argument("--final-evidence-root", type=Path)
    parser.add_argument("--worker-result", type=Path)
    parser.add_argument("--review", type=Path, action="append", default=[])
    parser.add_argument("--atom-comparison", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--require-acceptance", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify_matrix(args.output, require_acceptance=args.require_acceptance)
    else:
        required = {
            "--baseline-grade": args.baseline_grade,
            "--baseline-report": args.baseline_report,
            "--final-grade": args.final_grade,
            "--final-report": args.final_report,
            "--source-reference": args.source_reference,
            "--freeze-manifest": args.freeze_manifest,
            "--baseline-evidence-root": args.baseline_evidence_root,
            "--final-evidence-root": args.final_evidence_root,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error("missing required arguments: " + ", ".join(missing))
        matrix = build_matrix(
            baseline_grade_path=args.baseline_grade,
            baseline_report_path=args.baseline_report,
            final_grade_path=args.final_grade,
            final_report_path=args.final_report,
            source_reference_path=args.source_reference,
            freeze_manifest_path=args.freeze_manifest,
            baseline_evidence_root=args.baseline_evidence_root,
            final_evidence_root=args.final_evidence_root,
            worker_result_path=args.worker_result,
            review_paths=args.review,
            atom_comparison_path=args.atom_comparison,
        )
        output = Path(args.output)
        if output.exists():
            raise MatrixError(f"Refusing to overwrite matrix output: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result = {
            "status": matrix["status"],
            "acceptance_eligible": matrix["acceptance_eligible"],
            "baseline_row_count": matrix["scope"]["baseline_row_count"],
            "unresolved_count": len(matrix["scope"]["unresolved_or_core_rows"]),
        }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except MatrixError as error:
        raise SystemExit(str(error)) from error
