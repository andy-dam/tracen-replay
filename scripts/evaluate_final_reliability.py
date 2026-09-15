"""Evaluate frozen source-reviewed turn cases against a gameplay report.

The source documents are sealed before this evaluator reads a report.  Case
matching delegates to the shared observation evaluator; this module adds the
run/case seal, explicit review-coverage limits, and report-turn attribution.
It intentionally reports scoped agreement only.  It does not establish
complete event history or whole-recording recall.

The default grade retains path-based evidence matching.  An explicitly supplied
evidence root enables a separate grade with byte- and timestamp-verified image
aliases.  Save that result separately from the default grade; its diagnostic
proofs explain each alias without changing frozen labels or producer values.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracen_replay.evaluation_adapters import report_document
from tracen_replay.observation_evaluate import (
    CATEGORIES,
    PHASES,
    _eligible,
    _same_identity,
    evaluate as evaluate_observations,
)


SOURCE_SCHEMA = "final-reliability-source-reference-v1"
SCHEMA = "tracen-replay/final-reliability-evaluation-v1"
SELECTION_SCHEMA = "final-reliability-source-selection-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _image_hashes(value: Any) -> dict[str, str] | str:
    """Accept the worker's evidence-path map and compact fixture digests."""
    if isinstance(value, str):
        return _hash(value, "image_sha256")
    if not isinstance(value, dict):
        raise ValueError("image_sha256 must be a SHA-256 digest or evidence hash map")
    result = {}
    for path, digest in value.items():
        if not isinstance(path, str) or not path:
            raise ValueError("image_sha256 map keys must be nonempty evidence paths")
        result[path] = _hash(digest, f"image_sha256[{path!r}]")
    return result


def _interval(value: Any, name: str, *, allow_empty: bool = False) -> list[int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(type(item) is not int for item in value)
            or value[0] < 0 or value[0] >= value[1]):
        raise ValueError(f"{name} must be a positive half-open [start_ms, end_ms] interval")
    return list(value)


def _intervals(value: Any, name: str) -> list[list[int]]:
    # A single [start, end] is convenient in hand-authored review sheets;
    # [[start, end], ...] is the canonical form.
    if isinstance(value, list) and len(value) == 2 and all(type(x) is int for x in value):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must contain at least one interval")
    return [_interval(item, f"{name}[{index}]") for index, item in enumerate(value)]


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _coverage(value: Any, case_scope: list[int]) -> dict[str, Any]:
    """Normalize explicit review coverage without inventing a broad scope."""
    if isinstance(value, list):
        rows = value
        value = {"entries": rows}
    if not isinstance(value, dict):
        raise ValueError("review_coverage must be an object")

    categories = _first(value, "categories", "reviewed_categories", "core_categories")
    fields = _first(value, "fields", "reviewed_fields", "core_fields")
    category_aliases = {"actions": "action", "effects": "effect", "states": "state",
                        "purchases": "purchase", "contexts": "context"}
    intervals = _first(value, "intervals_ms", "time_intervals_ms", "scopes_ms", "interval_ms")
    if intervals is None and "scope_ms" in value:
        intervals = value["scope_ms"]

    # Also accept a compact per-category form used by source-review workers.
    by_category = _first(value, "fields_by_category", "reviewed_fields_by_category")
    if categories is None:
        inferred = [category_aliases[key] for key in value if key in category_aliases]
        if inferred:
            categories = inferred
    if isinstance(categories, str):
        categories = [categories]
    if isinstance(by_category, dict):
        if categories is None:
            categories = list(by_category)
        if fields is None:
            fields = [field for values in by_category.values()
                      for field in (values if isinstance(values, list) else [values])]

    if isinstance(fields, str):
        fields = [fields]
    if isinstance(fields, dict):
        fields = [field for values in fields.values()
                  for field in (values if isinstance(values, list) else [values])]

    entries = value.get("entries")
    if isinstance(entries, list):
        entry_categories, entry_fields, entry_intervals = set(), set(), []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(f"review_coverage.entries[{index}] must be an object")
            item_category = _first(entry, "category", "categories")
            item_field = _first(entry, "field", "fields")
            item_time = _first(entry, "interval_ms", "scope_ms", "intervals_ms")
            if isinstance(item_category, str):
                entry_categories.add(item_category)
            elif isinstance(item_category, list):
                entry_categories.update(item_category)
            if isinstance(item_field, str):
                entry_fields.add(item_field)
            elif isinstance(item_field, list):
                entry_fields.update(item_field)
            if item_time is not None:
                entry_intervals.extend(_intervals(item_time, f"review_coverage.entries[{index}]"))
        if categories is None:
            categories = sorted(entry_categories)
        if fields is None:
            fields = sorted(entry_fields)
        if intervals is None:
            intervals = entry_intervals

    if isinstance(categories, list):
        categories = [category_aliases.get(category, category) for category in categories]
    if not isinstance(categories, list) or not categories:
        raise ValueError("review_coverage must explicitly list reviewed categories")
    if any(category not in CATEGORIES for category in categories) or len(set(categories)) != len(categories):
        raise ValueError("review_coverage contains an unsupported or duplicate category")
    explicit_time = intervals is not None
    normalized_intervals = (_intervals(intervals, "review_coverage.intervals_ms")
                            if explicit_time else [list(case_scope)])
    if any(start < case_scope[0] or end > case_scope[1]
           for start, end in normalized_intervals):
        raise ValueError("review_coverage interval lies outside the case scope")
    normalized_by_category = None
    if isinstance(by_category, dict):
        normalized_by_category = {}
        for category, values in by_category.items():
            category = category_aliases.get(category, category)
            if category not in CATEGORIES:
                raise ValueError("review_coverage fields_by_category contains an unsupported category")
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list) or any(not isinstance(field, str) or not field.strip()
                                                   for field in values):
                raise ValueError("review_coverage fields_by_category must contain text arrays")
            if len(set(values)) != len(values):
                raise ValueError("review_coverage fields_by_category fields must be unique")
            normalized_by_category[category] = set(values)
    if fields is not None:
        if not isinstance(fields, list) or any(not isinstance(field, str) or not field.strip() for field in fields):
            raise ValueError("review_coverage fields must be nonempty text")
        if len(set(fields)) != len(fields):
            raise ValueError("review_coverage fields must be unique")
        fields = set(fields)
    return {
        "categories": set(categories),
        "fields": fields,
        "intervals_ms": normalized_intervals,
        "fields_by_category": normalized_by_category,
        "explicit_fields": fields is not None,
        "explicit_time": explicit_time,
    }


def _canonical_effect_alias(row: dict[str, Any]) -> dict[str, Any]:
    """Compare energy-capacity schemas symmetrically without rewriting labels."""
    result = deepcopy(row)
    payload = result.get("payload")
    if (result.get("category") == "effect" and isinstance(payload, dict)
            and payload.get("kind") == "max_energy_change"
            and payload.get("field") in (None, "energy")):
        result.setdefault("original_payload", deepcopy(payload))
        payload.update(kind="stat_cap_change", field="energy")
    return result


def _validate_observation(row: Any, case: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"{case['case_id']}.observations[{index}] must be an object")
    name = f"{case['case_id']}.observations[{index}]"
    identity = _text(row.get("id"), f"{name}.id")
    if row.get("category") not in CATEGORIES or row.get("phase") not in PHASES:
        raise ValueError(f"{name} requires a supported category and phase")
    start, end = row.get("start_ms"), row.get("end_ms")
    if type(start) is not int or type(end) is not int or start > end:
        raise ValueError(f"{name} has an invalid observation interval")
    case_start, case_end = case["scope_ms"]
    if start < case_start or start >= case_end or end > case_end:
        raise ValueError(f"{name} lies outside its case scope")
    if "payload" not in row or not isinstance(row["payload"], dict) or not row["payload"]:
        raise ValueError(f"{name}.payload must be a nonempty object")
    evidence = row.get("evidence", [])
    if not isinstance(evidence, list) or any(not isinstance(item, str) or not item for item in evidence):
        raise ValueError(f"{name}.evidence must be an array of source identifiers")
    status = row.get("status")
    if status not in ("observed", "unobservable", "ambiguous"):
        raise ValueError(f"{name}.status must be observed, unobservable, or ambiguous")
    if "expected_turn_id" not in row:
        raise ValueError(f"{name}.expected_turn_id is required")
    expected_turn = row.get("expected_turn_id")
    if expected_turn is not None and (not isinstance(expected_turn, str) or not expected_turn):
        raise ValueError(f"{name}.expected_turn_id must be nonempty text or null")
    return _canonical_effect_alias(row)


def validate_source_reference(document: dict[str, Any], selection_cases: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Validate a sealed source document before any prediction is adapted."""
    if document.get("schema_version") != SOURCE_SCHEMA:
        raise ValueError(f"Unsupported source reference schema: {document.get('schema_version')!r}")
    run = _text(document.get("run"), "run")
    source_sha256 = _hash(document.get("source_sha256"), "source_sha256")
    image_sha256 = _image_hashes(document.get("image_sha256"))
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Source reference must contain cases")
    seen_cases: set[str] = set()
    normalized_cases = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"cases[{index}] must be an object")
        case_id = _text(case.get("case_id"), f"cases[{index}].case_id")
        if case_id in seen_cases:
            raise ValueError(f"Duplicate source case ID: {case_id}")
        seen_cases.add(case_id)
        turn_id = _text(case.get("turn_id"), f"{case_id}.turn_id")
        scope = _interval(case.get("scope_ms"), f"{case_id}.scope_ms")
        if selection_cases is not None:
            selected = selection_cases.get(case_id)
            if selected is None:
                raise ValueError(f"Source case {case_id} is absent from the frozen selection manifest")
            if selected.get("run") != run:
                raise ValueError(f"Source case {case_id} belongs to another run")
            if selected.get("turn_id") != turn_id or selected.get("start_ms") != scope[0] or selected.get("end_ms") != scope[1]:
                raise ValueError(f"Source case {case_id} does not match its selected turn scope")
            if not isinstance(selected.get("source_sha256"), str) or selected["source_sha256"].lower() != source_sha256:
                raise ValueError(f"Source hash mismatch for selected case {case_id}")
        complete = case.get("reference_complete")
        if type(complete) is not bool:
            raise ValueError(f"{case_id}.reference_complete must be boolean")
        coverage = _coverage(case.get("review_coverage"), scope)
        unobservable = case.get("unobservable_intervals")
        if not isinstance(unobservable, list):
            raise ValueError(f"{case_id}.unobservable_intervals must be an array")
        unobservable = _intervals(unobservable, f"{case_id}.unobservable_intervals") if unobservable else []
        if any(start < scope[0] or end > scope[1] for start, end in unobservable):
            raise ValueError(f"{case_id}.unobservable_intervals lies outside case scope")
        notes = case.get("notes")
        if isinstance(notes, str):
            notes = [notes]
        if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
            raise ValueError(f"{case_id}.notes must be text or an array of text")
        observations = case.get("observations")
        if not isinstance(observations, list):
            raise ValueError(f"{case_id}.observations must be an array")
        seen_observations: set[str] = set()
        normalized_observations = []
        for obs_index, row in enumerate(observations):
            normalized = _validate_observation(row, {"case_id": case_id, "scope_ms": scope}, obs_index)
            if normalized["id"] in seen_observations:
                raise ValueError(f"Duplicate source observation ID: {normalized['id']}")
            seen_observations.add(normalized["id"])
            normalized_observations.append(normalized)
        normalized_cases.append({
            "case_id": case_id, "turn_id": turn_id, "scope_ms": scope,
            "reference_complete": complete, "review_coverage": coverage,
            "observations": normalized_observations,
            "unobservable_intervals": unobservable, "notes": notes,
        })
    if selection_cases is not None:
        selected_for_run = {case_id for case_id, selected in selection_cases.items()
                            if selected.get("run") == run}
        if seen_cases != selected_for_run:
            missing = sorted(selected_for_run - seen_cases)
            extra = sorted(seen_cases - selected_for_run)
            raise ValueError(f"Source cases do not exactly match selected cases (missing={missing}, extra={extra})")
    return {
        "schema_version": SOURCE_SCHEMA, "run": run, "source_sha256": source_sha256,
        "image_sha256": image_sha256, "cases": normalized_cases,
    }


def validate_selection(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if document.get("schema_version") != SELECTION_SCHEMA:
        raise ValueError("Unsupported final reliability selection schema")
    rows = document.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Selection manifest must contain cases")
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"selection cases[{index}] must be an object")
        case_id = _text(row.get("id"), f"selection cases[{index}].id")
        if case_id in result:
            raise ValueError(f"Duplicate selected case ID: {case_id}")
        _text(row.get("run"), f"selection {case_id}.run")
        _text(row.get("turn_id"), f"selection {case_id}.turn_id")
        _interval([row.get("start_ms"), row.get("end_ms")], f"selection {case_id}.scope_ms")
        _hash(row.get("source_sha256"), f"selection {case_id}.source_sha256")
        result[case_id] = row
    return result


def _source_hash(report: dict[str, Any]) -> str | None:
    if isinstance(report.get("source"), dict):
        value = report["source"].get("sha256")
    else:
        value = report.get("source_sha256")
    return value.lower() if isinstance(value, str) else value


def _bind_frozen_inputs(manifest: dict[str, Any], selection_path: Path | None,
                        reference_paths: list[Path]) -> None:
    """Ensure the files being graded are the files covered by the freeze."""
    frozen_selection = Path(manifest["selection_path"]).resolve()
    if selection_path is not None and Path(selection_path).resolve() != frozen_selection:
        raise ValueError("Selection path is not the one bound by the freeze manifest")
    frozen_refs = {Path(path).resolve(): path for path in manifest["reference_paths"]}
    for path in reference_paths:
        resolved = Path(path).resolve()
        if resolved not in frozen_refs:
            raise ValueError(f"Reference path is not bound by the freeze manifest: {path}")
        expected = manifest["immutable_files_sha256"].get(frozen_refs[resolved])
        if expected != _sha(resolved):
            raise ValueError(f"Reference hash is not bound by the freeze manifest: {path}")


def _selection_from_freeze(manifest: dict[str, Any], selection: dict[str, dict[str, Any]] | None,
                           document_run: str) -> dict[str, dict[str, Any]]:
    """Load the sealed selection and permit only a matching run subset."""
    frozen = validate_selection(_read(Path(manifest["selection_path"])))
    if selection is not None:
        for case_id, row in selection.items():
            if frozen.get(case_id) != row:
                raise ValueError(f"Selection data is not the one bound by the freeze manifest: {case_id}")
        return selection
    return {case_id: row for case_id, row in frozen.items() if row.get("run") == document_run}


def _pointer(value: Any, path: str) -> Any:
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


def _add_turn(index: dict[str, Any], reference: Any, exact: Any = None, candidates: Any = None) -> None:
    if not isinstance(reference, str) or not reference:
        return
    index["known"].add(reference)
    if isinstance(exact, str) and exact:
        index["exact"][reference].add(exact)
    if isinstance(candidates, str) and candidates:
        candidates = [candidates]
    if isinstance(candidates, list):
        index["candidates"][reference].update(c for c in candidates if isinstance(c, str) and c)


def _turn_index(report: dict[str, Any]) -> dict[str, Any]:
    index: dict[str, Any] = {"known": set(), "exact": defaultdict(set),
                             "candidates": defaultdict(set), "windows": []}

    def add_row(row: Any, reference: Any = None) -> None:
        if not isinstance(row, dict):
            return
        ref = reference or row.get("source_ref") or row.get("report_ref") or row.get("ref")
        _add_turn(index, ref, row.get("turn_id", row.get("actual_turn_id")),
                  row.get("candidate_turn_ids", row.get("turn_ids")))

    # Some reports expose a compact turn-ref index; preserve it when present.
    refs = report.get("turn_refs")
    if isinstance(refs, dict):
        for ref, row in refs.items():
            if isinstance(row, dict):
                add_row(row, ref)
            elif isinstance(row, list):
                _add_turn(index, ref, candidates=row)
            else:
                _add_turn(index, ref, exact=row)
    elif isinstance(refs, list):
        for row in refs:
            add_row(row)

    ledger = report.get("turn_ledger")
    if isinstance(ledger, dict):
        for row in ledger.get("timeline", []):
            add_row(row)
        by_entry = {row.get("id"): row for row in ledger.get("timeline", [])
                    if isinstance(row, dict) and row.get("id")}
        for turn in ledger.get("turns", []):
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                continue
            turn_id = turn["id"]
            index["windows"].append((turn.get("start_ms"), turn.get("end_ms"), turn_id))
            for entry_id in turn.get("timeline_refs", []):
                _add_turn(index, by_entry.get(entry_id, {}).get("source_ref"), turn_id, [turn_id])
            comparisons = turn.get("comparisons", {})
            if isinstance(comparisons, dict):
                for refs_for_channel in comparisons.values():
                    if isinstance(refs_for_channel, list):
                        for ref in refs_for_channel:
                            _add_turn(index, ref, turn_id, [turn_id])
            states = turn.get("states", {})
            if isinstance(states, dict):
                for state in states.values():
                    if not isinstance(state, dict):
                        continue
                    for side in ("opening", "closing"):
                        # A non-final closing point can be a copied view of
                        # the following turn's opening checkpoint.  The
                        # ledger keeps that point for display and arithmetic,
                        # but it is not a second source ownership claim.  If
                        # we index it as an exact owner too, a shared
                        # checkpoint acquires two exact turn IDs and every
                        # state case using it becomes falsely ambiguous.
                        if (side == "closing"
                                and state.get("closing_basis") ==
                                "next_turn_first_observed_state"):
                            point = state.get(side)
                            if isinstance(point, dict):
                                # Keep this source reference known even when
                                # the following turn's opening is absent or
                                # points elsewhere.  Otherwise adapt_report's
                                # time-window fallback would silently assign
                                # the copied close to the previous turn.
                                reference = point.get("source_ref")
                                if isinstance(reference, str) and reference:
                                    index["known"].add(reference)
                            continue
                        point = state.get(side)
                        if isinstance(point, dict):
                            _add_turn(index, point.get("source_ref"), turn_id, [turn_id])
        for row in ledger.get("turn_transitions", []):
            add_row(row)

    causal = report.get("causal_accounting")
    if isinstance(causal, dict):
        for row in causal.get("turn_transitions", []):
            add_row(row)
        for row in causal.get("contributions", []):
            add_row(row)

    # Direct row-level fields are authoritative when workers provided them.
    data = report.get("gameplay_tracking")
    if isinstance(data, dict):
        for collection in ("readings", "events", "checkpoints", "intervals", "turn_action_receipts",
                           "lesson_purchases", "skill_purchases", "races", "concerts",
                           "state_observations", "status_observations", "preview_observations"):
            rows = data.get(collection)
            if isinstance(rows, dict):
                rows = rows.get("observations", [])
            if isinstance(rows, list):
                for index_number, row in enumerate(rows):
                    if isinstance(row, dict):
                        ref = f"/gameplay_tracking/{collection}/{index_number}"
                        if any(key in row for key in ("turn_id", "actual_turn_id", "candidate_turn_ids", "turn_ids")):
                            add_row(row, ref)

        # Persisted snapshot helpers retain the underlying reading refs.  When
        # those refs already carry an exact/candidate turn, propagate the
        # mapping to the grouped snapshot without borrowing a neighboring
        # checkpoint's owner.  If none is available, adapt_report's bounded
        # turn-window fallback remains authoritative.
        for collection in ("state_observations", "status_observations"):
            rows = data.get(collection)
            if isinstance(rows, dict):
                rows = rows.get("observations", [])
            if not isinstance(rows, list):
                continue
            for index_number, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                ref = row.get("source_ref") or row.get("id")
                if not isinstance(ref, str) or not ref:
                    ref = f"/gameplay_tracking/{collection}/{index_number}"
                exact, candidates = set(), set()
                for source in row.get("source_observations", []):
                    if not isinstance(source, dict):
                        continue
                    source_ref = source.get("source_ref") or source.get("report_ref")
                    if not isinstance(source_ref, str):
                        continue
                    exact.update(index["exact"].get(source_ref, set()))
                    candidates.update(index["candidates"].get(source_ref, set()))
                candidates.update(exact)
                if len(exact) == 1 and candidates == exact:
                    _add_turn(index, ref, next(iter(exact)), sorted(candidates))
                elif candidates:
                    _add_turn(index, ref, candidates=sorted(candidates))

    index["windows"] = [row for row in index["windows"]
                         if type(row[0]) is int and type(row[1]) is int and row[0] < row[1]]
    return index


def _raw_occurrence_key(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("occurrence_key")
    return value if isinstance(value, str) and value else None


def _raw_transaction_key(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    for key in ("transaction_ref", "transaction_id", "receipt_id", "transaction_key"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            # Transaction IDs are used for duplicate diagnostics. They are not
            # promoted to occurrence keys because a source occurrence may use
            # a different namespace while still being structurally matchable.
            return f"transaction:{value}"
    return None


def adapt_report(report: dict[str, Any]) -> dict[str, Any]:
    """Adapt a full report and attach actual turn provenance to each row."""
    if isinstance(report.get("observations"), list) and "gameplay_tracking" not in report:
        prediction = deepcopy(report)
        prediction.setdefault("source_sha256", _source_hash(report))
        prediction.setdefault("auxiliary_log_used", False)
    else:
        prediction = report_document(report)
    if prediction.get("auxiliary_log_used") is None:
        prediction["auxiliary_log_used"] = False
    if prediction.get("auxiliary_log_used") is not False:
        raise ValueError("Only gameplay-only reports can be graded")
    if _source_hash(report) is not None:
        prediction["source_sha256"] = _source_hash(report)

    # ``report_document`` represents state checkpoints by channel and values;
    # final source references also carry the canonical state kind.  Supply the
    # adapter's implied kind so endpoint fields can be compared without making
    # a state row fail solely because of that representation detail.
    for row in prediction.get("observations", []):
        if row.get("category") == "state" and isinstance(row.get("payload"), dict):
            row["payload"].setdefault("kind", "state")

    prediction["observations"] = [
        _canonical_effect_alias(row) for row in prediction.get("observations", [])
    ]
    turns = _turn_index(report)
    for row in prediction.get("observations", []):
        ref = row.get("source_ref") or row.get("id")
        raw = _pointer(report, ref) if ref else None
        actual = row.get("turn_id")
        candidates = row.get("candidate_turn_ids", [])
        if isinstance(raw, dict):
            actual = raw.get("turn_id", raw.get("actual_turn_id", actual))
            candidates = raw.get("candidate_turn_ids", raw.get("turn_ids", candidates))
        if ((isinstance(actual, str) and actual)
                or (isinstance(candidates, str) and candidates)
                or (isinstance(candidates, list) and candidates)):
            _add_turn(turns, ref, actual, candidates)
        exact = turns["exact"].get(ref, set())
        candidate_set = set(turns["candidates"].get(ref, set()))
        if isinstance(candidates, str) and candidates:
            candidate_set.add(candidates)
        elif isinstance(candidates, list):
            candidate_set.update(item for item in candidates if isinstance(item, str) and item)
        if len(exact) == 1:
            actual_turn = next(iter(exact))
            candidate_set.add(actual_turn)
        else:
            actual_turn = None
        known = ref in turns["known"]
        if not known:
            start = row.get("start_ms")
            containing = [turn_id for left, right, turn_id in turns["windows"]
                          if type(start) is int and left <= start < right]
            if len(containing) == 1:
                actual_turn = containing[0]
                candidate_set.add(actual_turn)
            elif containing:
                candidate_set.update(containing)
        row["actual_turn_id"] = actual_turn
        row["candidate_turn_ids"] = sorted(candidate_set)
        row["turn_id"] = actual_turn
        occurrence_key = row.get("occurrence_key") or _raw_occurrence_key(raw)
        transaction_key = _raw_transaction_key(raw) or _raw_transaction_key(row)
        if occurrence_key:
            row["occurrence_key"] = occurrence_key
            row["dedup_key"] = occurrence_key
        elif transaction_key:
            row["dedup_key"] = transaction_key
        elif not isinstance(row.get("dedup_key"), str) or not row["dedup_key"]:
            # A source ref is a useful dedup key, but it is not asserted as an
            # atomic occurrence key and therefore cannot affect matching.
            row["dedup_key"] = ref or row.get("id")
    return prediction


def _field_tokens(row: dict[str, Any]) -> set[str]:
    payload = row.get("payload", {})
    tokens = set()
    if isinstance(payload, dict):
        tokens.update(key for key in payload if isinstance(key, str))
        for key in ("kind", "field", "channel", "training_option", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                tokens.add(value)
        values = payload.get("values")
        if isinstance(values, dict):
            tokens.update(key for key in values if isinstance(key, str))
        for value in payload.values():
            if isinstance(value, dict):
                tokens.update(key for key in value if isinstance(key, str))
    return tokens


def _coverage_allows(row: dict[str, Any], coverage: dict[str, Any]) -> bool:
    if (not coverage.get("explicit_time")
            or row.get("category") not in coverage["categories"]):
        return False
    start, end = row.get("start_ms"), row.get("end_ms")
    if type(start) is not int or type(end) is not int:
        return False
    if not any(start < right and end >= left for left, right in coverage["intervals_ms"]):
        return False
    fields_by_category = coverage.get("fields_by_category") or {}
    fields = fields_by_category.get(row.get("category"), coverage["fields"])
    if fields is None:
        return False
    if "*" in fields:
        return True
    tokens = _field_tokens(row)
    payload = row.get("payload", {})
    # Coverage uses the frozen source vocabulary; canonical energy-capacity
    # rows must retain its equivalent scope without admitting energy recovery.
    if (row.get("category") == "effect" and isinstance(payload, dict)
            and payload.get("kind") == "stat_cap_change"
            and payload.get("field") == "energy"):
        tokens.add("max_energy_change")
    if tokens & fields:
        return True
    payload = row.get("payload", {})
    kind = payload.get("kind") if isinstance(payload, dict) else None
    field = payload.get("field") if isinstance(payload, dict) else None
    category = row.get("category")
    compound = {
        f"{kind}:{field}", f"{category}:{field}", f"{category}:{kind}:{field}",
        f"{kind}.{field}", f"{category}.{field}", f"{category}.{kind}.{field}",
    }
    return bool(compound & fields)


def _transaction_signature(row: dict[str, Any]) -> str:
    """Identify the semantic line claimed by a transaction, excluding values."""
    payload = row.get("payload", {})
    if isinstance(payload, dict):
        payload = {key: value for key, value in payload.items()
                   if key not in ("amount", "cost", "value", "direction")}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _readable_core_copy(row: dict[str, Any], case: dict[str, Any]) -> bool:
    """Keep a copy gradeable when it matches a reviewed readable occurrence.

    An interval-level unknown only exempts effects for which no reviewed,
    source-readable occurrence could be the same event.  This prevents a broad
    hidden interval from excusing a duplicate of a known effect while keeping a
    genuinely different hidden effect ungraded.
    """
    if not _coverage_allows(row, case["review_coverage"]):
        return False
    return any(
        source.get("status") == "observed"
        and _same_identity(source, row)
        and _eligible(source, row, ownership=False)
        for source in case["observations"]
    )


def _turn_status(source: dict[str, Any], actual: dict[str, Any] | None) -> str:
    if source.get("status") != "observed":
        return source.get("status", "unobservable")
    if actual is None:
        return "missing"
    expected = source.get("expected_turn_id")
    if expected is None:
        # The source confirms an occurrence but cannot establish its owner.
        # Keep the semantic result while refusing to certify attribution.
        return "ambiguous" if actual.get("actual_turn_id") or actual.get("candidate_turn_ids") else "unobservable"
    actual_turn = actual.get("actual_turn_id")
    candidates = set(actual.get("candidate_turn_ids", []))
    if actual_turn is None and not candidates:
        return "unobservable"
    if actual_turn == expected:
        return "ambiguous" if len(candidates) > 1 else "correct"
    if expected in candidates:
        return "ambiguous"
    return "incorrect"


def evaluate_case(case: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    reference = {
        "source_sha256": prediction["source_sha256"],
        "scope_ms": case["scope_ms"],
        "reference_complete": case["reference_complete"],
        "observations": case["observations"],
        "negative_scope_reviewed": case["reference_complete"] and not case["unobservable_intervals"],
    }
    score = evaluate_observations(reference, prediction)
    source_by_id = {row["id"]: row for row in case["observations"]}
    prediction_by_id = {row["id"]: row for row in prediction["observations"]}
    wrong_turns, ambiguous_turns = [], []
    for result in score["results"]:
        source = source_by_id[result["source_id"]]
        actual = prediction_by_id.get(result.get("prediction_id"))
        status = _turn_status(source, actual)
        result["expected_turn_id"] = source.get("expected_turn_id")
        result["prediction_turn_id"] = actual.get("actual_turn_id") if actual else None
        result["prediction_candidate_turn_ids"] = actual.get("candidate_turn_ids", []) if actual else []
        result["turn_status"] = status
        # Explicitly unobservable/ambiguous source labels preserve source
        # uncertainty; they are not parser attribution failures.  The strict
        # case score still fails on their semantic status below.
        if source.get("status") == "observed" and status == "incorrect":
            wrong_turns.append({"source_id": result["source_id"], "prediction_id": result.get("prediction_id"),
                                "expected_turn_id": source.get("expected_turn_id"),
                                "actual_turn_id": actual.get("actual_turn_id") if actual else None,
                                "candidate_turn_ids": actual.get("candidate_turn_ids", []) if actual else []})
        elif source.get("status") == "observed" and status == "ambiguous":
            ambiguous_turns.append({"source_id": result["source_id"], "prediction_id": result.get("prediction_id"),
                                    "expected_turn_id": source.get("expected_turn_id"),
                                    "candidate_turn_ids": actual.get("candidate_turn_ids", []) if actual else []})

    # A wrong-turn effect may have moved outside the selected case window and
    # therefore cannot be a candidate in the shared scoped score.  Surface it
    # as an attribution failure while leaving the source occurrence missed.
    flagged = {(row["source_id"], row.get("prediction_id")) for row in wrong_turns}
    for result in score["results"]:
        source = source_by_id[result["source_id"]]
        if source.get("status") != "observed":
            continue
        expected_turn = source.get("expected_turn_id")
        for actual in prediction["observations"]:
            actual_turn = actual.get("actual_turn_id")
            if not actual_turn or actual_turn == expected_turn:
                continue
            if (result.get("prediction_id") == actual["id"]
                    or not _same_identity(source, actual)):
                continue
            source_key, actual_key = source.get("occurrence_key"), actual.get("occurrence_key")
            if source_key is not None and actual_key is not None and source_key != actual_key:
                continue
            same_occurrence = (source_key is not None and source_key == actual_key)
            same_ref = (source.get("source_ref") and source.get("source_ref") == actual.get("source_ref"))
            shared_evidence = bool(set(source.get("evidence", [])) & set(actual.get("evidence", [])))
            if not (same_occurrence or same_ref or shared_evidence):
                # Repeated effects such as +10 speed are common. Structural
                # identity alone cannot establish that a later occurrence is
                # the selected source event.
                continue
            marker = (source["id"], actual["id"])
            if marker in flagged:
                continue
            wrong_turns.append({"source_id": source["id"], "prediction_id": actual["id"],
                                "expected_turn_id": expected_turn, "actual_turn_id": actual_turn,
                                "candidate_turn_ids": actual.get("candidate_turn_ids", []),
                                "match_basis": "structural_identity_outside_case_scope"})
            flagged.add(marker)

    # The shared evaluator intentionally grades only semantic fields.  Limit
    # false-positive claims to the explicitly reviewed core category/field/time
    # coverage; all other predictions remain visible but ungraded.
    for extra in score["unmatched_predictions"]:
        actual = prediction_by_id.get(extra["prediction_id"])
        if actual is None:
            continue
        extra["actual_turn_id"] = actual.get("actual_turn_id")
        extra["candidate_turn_ids"] = actual.get("candidate_turn_ids", [])
        if not case["reference_complete"]:
            extra["status"] = "ungraded"
            extra["reason"] = "incomplete_reference"
        elif extra.get("status") == "extra":
            start, end = actual.get("start_ms"), actual.get("end_ms")
            hidden = any(start < right and end >= left
                         for left, right in case["unobservable_intervals"])
            readable_copy = _readable_core_copy(actual, case)
            if (not _coverage_allows(actual, case["review_coverage"])
                    or (hidden and not readable_copy)):
                extra["status"] = "ungraded"
                extra["reason"] = ("unobservable_interval" if hidden
                                    else "outside_explicit_review_coverage")

    # The shared evaluator scopes matching to one case. Keep predictions that
    # do not intersect that case visible as ungraded records so a per-case
    # score cannot imply that the whole report was reviewed.
    in_scope_ids = {row["prediction_id"] for row in score["unmatched_predictions"]}
    outside_scope = []
    left, right = case["scope_ms"]
    for actual in prediction["observations"]:
        start, end = actual.get("start_ms"), actual.get("end_ms")
        if type(start) is not int or type(end) is not int or (start < right and end >= left):
            continue
        outside_scope.append(actual["id"])
        if actual["id"] not in in_scope_ids:
            score["unmatched_predictions"].append({
                "prediction_id": actual["id"], "status": "ungraded",
                "reason": "outside_case_scope", "actual_turn_id": actual.get("actual_turn_id"),
                "candidate_turn_ids": actual.get("candidate_turn_ids", []),
            })
    score["out_of_scope_predictions"] = outside_scope

    # A transaction can be represented by more than one report row.  Count
    # duplicate claims within the same semantic category/phase only; a purchase
    # debit and its separately observed receipt effect remain distinct events.
    dedup_groups: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for row in prediction["observations"]:
        key = row.get("dedup_key")
        if key:
            dedup_groups[(row.get("category"), row.get("phase"), str(key),
                          _transaction_signature(row))].append(row["id"])
    all_duplicate_transactions = [sorted(ids) for ids in dedup_groups.values() if len(ids) > 1]
    matched_ids = {row.get("prediction_id") for row in score["results"]
                   if row.get("prediction_id") is not None}
    duplicate_transactions, ungraded_duplicates = [], []
    for ids in all_duplicate_transactions:
        gradeable = False
        for prediction_id in ids:
            row = prediction_by_id[prediction_id]
            start, end = row.get("start_ms"), row.get("end_ms")
            hidden = any(start < right and end >= left
                         for left, right in case["unobservable_intervals"])
            if (prediction_id in matched_ids
                    or _readable_core_copy(row, case)
                    or (not hidden and _coverage_allows(row, case["review_coverage"]))):
                gradeable = True
                break
        if gradeable:
            duplicate_transactions.append(ids)
        else:
            ungraded_duplicates.append({"prediction_ids": ids,
                                        "reason": "outside_explicit_review_coverage"})
    score["duplicate_transactions"] = duplicate_transactions
    score["ungraded_duplicate_transactions"] = ungraded_duplicates
    score["wrong_turn_effects"] = [row for row in wrong_turns
                                    if source_by_id[row["source_id"]].get("category") == "effect"]
    score["wrong_turn_predictions"] = wrong_turns
    score["ambiguous_turn_predictions"] = ambiguous_turns
    score["turn_status_counts"] = dict(Counter(row["turn_status"] for row in score["results"]))
    score["field_status_counts"] = dict(Counter(
        field["status"] for row in score["results"] for field in row.get("fields", [])))
    score["incorrect_amounts"] = [
        {"source_id": row["source_id"], "prediction_id": row.get("prediction_id"),
         "expected": field.get("expected"), "actual": field.get("actual")}
        for row in score["results"] for field in row.get("fields", [])
        if field.get("status") == "incorrect" and field.get("field", "").endswith("/amount")]
    score["review_coverage"] = {
        "categories": sorted(case["review_coverage"]["categories"]),
        "fields": sorted(case["review_coverage"]["fields"]) if case["review_coverage"]["fields"] is not None else None,
        "fields_by_category": {
            category: sorted(fields)
            for category, fields in (case["review_coverage"].get("fields_by_category") or {}).items()
        } or None,
        "intervals_ms": case["review_coverage"]["intervals_ms"],
        "explicit_fields": case["review_coverage"]["explicit_fields"],
        "explicit_time": case["review_coverage"]["explicit_time"],
    }
    if not case["review_coverage"]["explicit_fields"]:
        score.setdefault("score_blockers", []).append("missing_explicit_reviewed_fields")
    if not case["review_coverage"]["explicit_time"]:
        score.setdefault("score_blockers", []).append("missing_explicit_reviewed_time")
    if wrong_turns:
        score.setdefault("score_blockers", []).append("wrong_turn_attribution")
    if ambiguous_turns:
        score.setdefault("score_blockers", []).append("ambiguous_turn_attribution")
    if any(row["turn_status"] == "unobservable"
           and source_by_id[row["source_id"]].get("status") == "observed"
           for row in score["results"]):
        score.setdefault("score_blockers", []).append("unobservable_turn_attribution")
    if duplicate_transactions:
        score.setdefault("score_blockers", []).append("duplicate_transaction_claim")
    gradeable_extras = [row for row in score["unmatched_predictions"] if row.get("status") == "extra"]
    score["gradeable_extra_predictions"] = [row["prediction_id"] for row in gradeable_extras]
    observed_results = [row for row in score["results"]
                        if source_by_id[row["source_id"]].get("status") == "observed"]
    readable_scope = (
        bool(observed_results)
        and all(row["status"] == "correct" and row["turn_status"] == "correct"
                for row in observed_results)
        and case["review_coverage"]["explicit_fields"]
        and case["review_coverage"]["explicit_time"]
    )
    negative_scope = (
        not case["observations"]
        and case["reference_complete"]
        and not score.get("score_blockers")
    )
    score["observed_scope_passed"] = (
        (readable_scope or negative_scope)
        and not gradeable_extras and not wrong_turns and not ambiguous_turns and not duplicate_transactions
    )
    score["passed"] = (not score.get("score_blockers") and
                        all(row["status"] == "correct" for row in score["results"]) and
                        not gradeable_extras and not wrong_turns and not ambiguous_turns and
                        not duplicate_transactions and bool(case["review_coverage"]["explicit_fields"])
                        and bool(case["review_coverage"]["explicit_time"]))
    score["case_id"] = case["case_id"]
    score["turn_id"] = case["turn_id"]
    score["unobservable_intervals"] = case["unobservable_intervals"]
    score["notes"] = list(case["notes"])
    return score


def evaluate_document(document: dict[str, Any], report: dict[str, Any],
                      selection: dict[str, dict[str, Any]] | None = None,
                      *, reference_path: Path | None = None,
                      report_path: Path | None = None,
                      selection_path: Path | None = None,
                      freeze_manifest_path: Path | None = None,
                      evidence_root: Path | None = None,
                      selection_sha256: str | None = None) -> dict[str, Any]:
    """Grade all frozen cases in one run document."""
    manifest = None
    if freeze_manifest_path is not None:
        manifest = _verified_manifest(freeze_manifest_path, selection_sha256)
        _bind_frozen_inputs(manifest, selection_path,
                            [reference_path] if reference_path is not None else [])
    selected = selection
    if manifest is not None:
        selected = _selection_from_freeze(manifest, selected, str(document.get("run", "")))
    validated = validate_source_reference(document, selected)
    # Source identity is checked before report adaptation or semantic matching.
    report_sha = _source_hash(report)
    if report_sha != validated["source_sha256"]:
        raise ValueError("Report and frozen source reference belong to different recordings")
    prediction = adapt_report(report)
    if prediction.get("source_sha256") != validated["source_sha256"]:
        raise ValueError("Adapted report source hash does not match the frozen source reference")
    alias_diagnostics = None
    if evidence_root is not None:
        from tracen_replay.evidence_aliases import resolve_evidence_aliases
        prediction, alias_diagnostics = resolve_evidence_aliases(
            validated, report, prediction, evidence_root)
    cases = [evaluate_case(case, prediction) for case in validated["cases"]]
    semantic_counts = Counter()
    turn_counts = Counter()
    for case_score in cases:
        semantic_counts.update(case_score.get("status_counts", {}))
        turn_counts.update(case_score.get("turn_status_counts", {}))
    return {
        "schema_version": SCHEMA,
        "run": validated["run"],
        "source_sha256": validated["source_sha256"],
        "image_sha256": validated["image_sha256"],
        "reference_sha256": _sha(reference_path) if reference_path else None,
        "report_sha256": _sha(report_path) if report_path else None,
        "freeze_manifest_sha256": _sha(freeze_manifest_path) if freeze_manifest_path else None,
        "selection_validated": selected is not None,
        "evidence_alias_validation": alias_diagnostics,
        "cases": cases,
        "status_counts": dict(semantic_counts),
        "turn_status_counts": dict(turn_counts),
        "passed": bool(cases) and all(case_score["passed"] for case_score in cases),
        "observed_scope_passed": bool(cases) and all(
            case_score["observed_scope_passed"] for case_score in cases),
        "scoped_readable_correctness": bool(cases) and all(
            case_score["observed_scope_passed"] for case_score in cases),
        "complete_event_history": False,
        "full_recording_recall_measured": False,
        "limitations": [
            "Agreement is limited to each source-reviewed case scope and explicit review coverage.",
            "Missing or hidden source content remains unknown; it is never scored as a zero effect.",
            "Turn attribution uses report-owned source refs, turn IDs, candidates, and bounded turn windows.",
            "Numeric agreement is graded after structural/time/occurrence assignment and never selects a match.",
            "Scoped readable correctness does not establish complete event history or whole-run recall.",
        ],
    }


def evaluate(reference: dict[str, Any], report: dict[str, Any],
             selection: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Public short name for grading one validated run document."""
    return evaluate_document(reference, report, selection)


def _bound_evidence_root(bindings: dict[str, Any], run: str,
                         report_path: Path, document: dict[str, Any]) -> Path:
    """Require explicit report and source bindings before reading alias files."""
    record = bindings.get(run) if isinstance(bindings, dict) else None
    if (not isinstance(record, dict)
            or record.get("source_sha256") != document.get("source_sha256")
            or record.get("report_sha256") != _sha(report_path)
            or not isinstance(record.get("evidence_root"), str)
            or not record["evidence_root"].strip()):
        raise ValueError(f"Evidence root is not bound to this report and source: {run}")
    root = Path(record["evidence_root"])
    if not root.is_absolute() or not root.is_dir():
        raise ValueError(f"Evidence root must be an existing absolute directory: {run}")
    return root.resolve()


def _verified_manifest(freeze_manifest_path: Path, selection_sha256: str | None) -> dict[str, Any]:
    """Verify the freeze; a re-issued selection (turn ids re-mapped by time) must be named by its hash."""
    from scripts.freeze_final_reliability import verify_manifest
    if selection_sha256:
        return verify_manifest(freeze_manifest_path, expected_selection_sha256=selection_sha256)
    return verify_manifest(freeze_manifest_path)


def evaluate_all(reference_dir: Path, report_dir: Path, selection_path: Path | None = None,
                 freeze_manifest_path: Path | None = None,
                 *, evidence_roots: dict[str, Any] | None = None,
                 selection_sha256: str | None = None) -> dict[str, Any]:
    reference_dir, report_dir = Path(reference_dir), Path(report_dir)
    manifest = None
    if freeze_manifest_path is not None:
        manifest = _verified_manifest(freeze_manifest_path, selection_sha256)
        if selection_path is None:
            selection_path = Path(manifest["selection_path"])
        _bind_frozen_inputs(manifest, selection_path, [])
    selection_doc = _read(selection_path) if selection_path else None
    selected = validate_selection(selection_doc) if selection_doc is not None else None
    runs = sorted({row["run"] for row in selected.values()}) if selected else sorted(
        path.stem for path in reference_dir.glob("*.json"))
    results = []
    for run in runs:
        reference_path = reference_dir / f"{run}.json"
        if not reference_path.is_file():
            raise ValueError(f"Missing frozen source reference: {reference_path}")
        if manifest is not None:
            _bind_frozen_inputs(manifest, selection_path, [reference_path])
        report_candidates = [report_dir / f"{run}-report.json", report_dir / f"{run}.json",
                             report_dir / run / "report.json"]
        report_path = next((path for path in report_candidates if path.is_file()), None)
        if report_path is None:
            raise ValueError(f"Missing report for run {run} in {report_dir}")
        document = _read(reference_path)
        evidence_root = (_bound_evidence_root(evidence_roots, run, report_path, document)
                         if evidence_roots is not None else None)
        case_selection = {case_id: row for case_id, row in (selected or {}).items()
                          if row.get("run") == run} if selected else None
        result = evaluate_document(document, _read(report_path), case_selection,
                                   reference_path=reference_path, report_path=report_path,
                                   selection_path=selection_path,
                                   freeze_manifest_path=freeze_manifest_path,
                                   evidence_root=evidence_root,
                                   selection_sha256=selection_sha256)
        results.append(result)
    return {
        "schema_version": SCHEMA,
        "runs": results,
        "source_runs": [result["run"] for result in results],
        "passed": bool(results) and all(result["passed"] for result in results),
        "observed_scope_passed": bool(results) and all(
            result["observed_scope_passed"] for result in results),
        "selection_sha256": _sha(selection_path) if selection_path else None,
        "selection_validated": selected is not None,
        "freeze_manifest_sha256": _sha(freeze_manifest_path) if freeze_manifest_path else None,
        "complete_event_history": False,
        "full_recording_recall_measured": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path,
                        help="Optional reference/report paths (or reference/report directories)")
    parser.add_argument("--reference", type=Path, help="One frozen run source-reference document")
    parser.add_argument("--report", type=Path, help="One gameplay report corresponding to --reference")
    parser.add_argument("--reference-dir", "--references", dest="reference_dir", type=Path,
                        default=Path(".local/final-reliability-v1/source-references"))
    parser.add_argument("--report-dir", type=Path, default=Path(".local/final-reliability-v1/before"))
    parser.add_argument("--selection", type=Path,
                        default=Path(".local/final-reliability-v1/source-case-selection.json"))
    parser.add_argument("--freeze-manifest", type=Path,
                        help="Optional final-reliability-reference-freeze-v1 manifest to verify first")
    parser.add_argument("--selection-sha256",
                        help="sha256 of a re-issued selection bound by the freeze manifest (turn ids re-mapped "
                             "by time); without it only the original frozen benchmark is accepted")
    evidence_options = parser.add_mutually_exclusive_group()
    evidence_options.add_argument("--evidence-root", type=Path,
                                  help="Single-run source evidence directory for verified image aliases")
    evidence_options.add_argument("--evidence-roots", type=Path,
                                  help="Batch JSON mapping of runs to report/source hashes and evidence roots")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.paths:
        if len(args.paths) != 2 or args.reference or args.report:
            parser.error("provide exactly two positional paths, or use --reference/--report")
        if args.paths[0].is_file():
            args.reference, args.report = args.paths
        else:
            args.reference_dir, args.report_dir = args.paths
    if bool(args.reference) != bool(args.report):
        parser.error("--reference and --report must be supplied together")
    if args.evidence_root is not None and not args.reference:
        parser.error("--evidence-root requires a single --reference/--report pair")
    if args.evidence_roots is not None and args.reference:
        parser.error("--evidence-roots is for batch evaluation; use --evidence-root for one run")
    if args.reference:
        selection_doc = _read(args.selection) if args.selection else None
        selected = validate_selection(selection_doc) if selection_doc else None
        document = _read(args.reference)
        case_selection = {key: value for key, value in (selected or {}).items()
                          if value.get("run") == document.get("run")} if selected else None
        result = evaluate_document(document, _read(args.report), case_selection,
                                   reference_path=args.reference, report_path=args.report,
                                   selection_path=args.selection,
                                   freeze_manifest_path=args.freeze_manifest,
                                   evidence_root=args.evidence_root.resolve() if args.evidence_root else None,
                                   selection_sha256=args.selection_sha256)
    else:
        result = evaluate_all(args.reference_dir, args.report_dir, args.selection, args.freeze_manifest,
                              evidence_roots=_read(args.evidence_roots) if args.evidence_roots else None,
                              selection_sha256=args.selection_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
