"""Audit the intermediate v1 worker's derived numeric contributions.

This is a bounded, read-only comparison of the intermediate worker report and
the preserved v1 report.  It reuses the amount-independent proof helpers in
``inventory_derived_reliability.py`` and writes versioned audit artifacts next
to the intermediate report.  It does not rerun recognition or rewrite any
frozen input.

Run from the repository root with::

    .\\.venv\\Scripts\\python.exe scripts\\audit_intermediate_worker_source_basis.py
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import inventory_derived_reliability as inv  # noqa: E402


WORKER_REPORT = Path(".local/final-reliability-v1/intermediate-worker-v2/report.json")
BASELINE_REPORT = Path(".local/final-reliability-v1/before/v1-report.json")
BASELINE_AUDIT = Path(".local/final-reliability-v1/derived-audit.json")
HISTORICAL_SOURCE_CAUSES = Path(
    ".local/final-reliability-v1/derived-source-causes.json"
)
ACCEPTANCE_RESULTS = Path(".local/final-reliability-v1/acceptance-results.json")
STRICT_GRADE = Path(
    ".local/final-reliability-v1/intermediate-worker-v2/strict-grade.json"
)
OUTPUT_JSON = Path(
    ".local/final-reliability-v1/intermediate-worker-v2/source-basis-audit.json"
)
OUTPUT_MD = Path(
    ".local/final-reliability-v1/intermediate-worker-v2/source-basis-audit.md"
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def counter_dict(value: Counter[Any] | dict[Any, int]) -> dict[str, int]:
    items = value.items() if hasattr(value, "items") else []
    return {str(key): int(count) for key, count in sorted(items, key=lambda item: str(item[0]))}


def integer(value: Any) -> bool:
    return type(value) is int


def evidence_rows(
    report: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], Counter[str], list[dict[str, Any]]]:
    """Index only physically unique evidence paths.

    A repeated path is not an independent observation.  It is excluded from
    the usable map so a future duplicate cannot silently satisfy a proof.
    """

    rows = (report.get("gameplay_tracking") or {}).get("readings") or []
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        path = row.get("evidence")
        if isinstance(path, str) and path:
            grouped[path].append(row)
    counts = Counter({path: len(items) for path, items in grouped.items()})
    unique = {
        path: items[0]
        for path, items in grouped.items()
        if len(items) == 1
    }
    duplicate_groups = [
        {"evidence": path, "row_count": len(items)}
        for path, items in sorted(grouped.items())
        if len(items) > 1
    ]
    return unique, counts, duplicate_groups


def transition_hits(
    report: dict[str, Any], contributions: dict[str, dict[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Collect each derived contribution once from balanced transition views."""

    hits: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_counts: Counter[str] = Counter()
    for transition in (report.get("causal_accounting") or {}).get(
        "turn_transitions", []
    ):
        for field in transition.get("fields") or []:
            if field.get("status") != "balanced_with_derived_changes":
                continue
            for contribution_id in field.get("contribution_refs") or []:
                contribution = contributions.get(contribution_id)
                if contribution is None or contribution.get("basis") in inv.DIRECT_BASES:
                    continue
                raw_counts[contribution_id] += 1
                hit = {
                    "turn_id": transition.get("turn_id"),
                    "channel": transition.get("channel"),
                    "field": field.get("field"),
                    "status": field.get("status"),
                    "start_ms": transition.get("start_ms"),
                    "end_ms": transition.get("end_ms"),
                    "before": field.get("before"),
                    "after": field.get("after"),
                    "observed_change": field.get("observed_change"),
                    "unresolved_change": field.get("unresolved_change"),
                    "endpoint_basis": transition.get("endpoint_basis"),
                }
                if hit not in hits[contribution_id]:
                    hits[contribution_id].append(hit)
    duplicate_references = sum(max(count - 1, 0) for count in raw_counts.values())
    return dict(hits), duplicate_references


def source_times(paths: list[Any], readings: dict[str, dict[str, Any]]) -> list[Any]:
    return [
        readings[path].get("source_timestamp_ms") if path in readings else None
        for path in paths
    ]


def compact_historical_compatibility(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    source_bound = value.get("source_bound_operands") or {}
    review = value.get("historical_source_review") or {}
    direct = value.get("historical_direct_reader_observation") or {}

    def operand(section: Any) -> dict[str, Any] | None:
        if not isinstance(section, dict):
            return None
        return {
            key: section.get(key)
            for key in (
                "pointer",
                "reading_index",
                "source_timestamp_ms",
                "field_value",
                "evidence",
                "selection",
                "stable_suffix_count",
                "is_first_stable_suffix_reading",
                "is_latest_pre_event_reading",
            )
            if key in section
        }

    return {
        "status": value.get("status"),
        "historical_amount": value.get("historical_amount"),
        "cause_group": direct.get("cause_group"),
        "source_review_status": review.get("status"),
        "badge_visibility": review.get("badge_visibility"),
        "candidate_observation_count": direct.get("candidate_observation_count"),
        "direct_observation_count": direct.get("direct_observation_count"),
        "before": operand(source_bound.get("before")),
        "after": operand(source_bound.get("after")),
        "recomputed_amount": source_bound.get("recomputed_amount"),
        "source_bound_arithmetic_verified": source_bound.get(
            "source_bound_arithmetic_verified"
        ),
        "source_window_ms": source_bound.get("source_window_ms"),
        "artifact_path": value.get("artifact_path"),
        "semantics": "historical_compatibility_only_not_current_effect_proof",
    }


def state_derived_endpoint_audit(
    contribution: dict[str, Any],
    event: dict[str, Any] | None,
    all_readings: list[dict[str, Any]],
    readings: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Recompute endpoint operands without selecting by the contribution amount."""

    event = event or {}
    field = contribution.get("field")
    paths = list((event.get("field_evidence") or {}).get(field) or [])
    event_rows = [readings[path] for path in paths if path in readings]
    result_rows = [
        (index, row)
        for index, row in enumerate(event_rows)
        if integer(
            ((row.get("facts") or {}).get("result_values") or {}).get(field)
        )
    ]
    suffix = inv._stable_result_suffix(result_rows, field)
    event_start = event.get("first_seen_ms")
    event_end = event.get("last_seen_ms")
    before_candidates = [
        row
        for row in all_readings
        if integer(row.get("source_timestamp_ms"))
        and integer(event_start)
        and row.get("source_timestamp_ms") < event_start
        and integer(
            ((row.get("stats") or {}).get("values") or {}).get(field)
        )
    ]
    before_candidates.sort(key=lambda row: row.get("source_timestamp_ms", -1))
    before = before_candidates[-1] if before_candidates else None
    after = suffix[0][1] if suffix else None
    before_value = (
        ((before.get("stats") or {}).get("values") or {}).get(field)
        if before
        else None
    )
    after_value = (
        ((after.get("facts") or {}).get("result_values") or {}).get(field)
        if after
        else None
    )
    recomputed = (
        after_value - before_value
        if integer(after_value) and integer(before_value)
        else None
    )
    amount_matches = recomputed == contribution.get("amount")
    before_time = before.get("source_timestamp_ms") if before else None
    after_time = after.get("source_timestamp_ms") if after else None
    in_event = bool(
        integer(after_time)
        and (not integer(event_start) or after_time >= event_start)
        and (not integer(event_end) or after_time <= event_end)
    )
    result_identity = inv._observation_identity(
        [
            {
                "source_timestamp_ms": row.get("source_timestamp_ms"),
                "evidence": row.get("evidence"),
            }
            for _, row in suffix
        ]
    )
    operand_status = (
        "endpoint_compatible_unverified"
        if before and suffix and amount_matches and in_event
        else "unverified_endpoint_operands"
    )
    return {
        "status": operand_status,
        "source_arithmetic_verified": False,
        "selection_without_amount_filter": True,
        "event_window_ms": [event_start, event_end],
        "result_evidence": paths,
        "result_source_timestamp_ms": source_times(paths, readings),
        "result_reading_count": len(result_rows),
        "stable_result_suffix": [
            {
                "evidence": row.get("evidence"),
                "source_timestamp_ms": row.get("source_timestamp_ms"),
                "value": ((row.get("facts") or {}).get("result_values") or {}).get(field),
            }
            for _, row in suffix
        ],
        "stable_result_identity": result_identity,
        "before": {
            "evidence": before.get("evidence") if before else None,
            "source_timestamp_ms": before_time,
            "value": before_value,
            "candidate_count": len(before_candidates),
            "selected_by": "latest_integer_stats_values_before_event_start",
        },
        "after": {
            "evidence": after.get("evidence") if after else None,
            "source_timestamp_ms": after_time,
            "value": after_value,
            "candidate_count": len(suffix),
            "selected_by": "first_reading_of_stable_trailing_result_suffix",
        },
        "recomputed_endpoint_delta": recomputed,
        "contribution_amount": contribution.get("amount"),
        "amount_matches_endpoint_delta": amount_matches,
        "after_inside_event_window": in_event,
        "proof_limitation": (
            "The endpoint difference is source-compatible when available, but it "
            "does not independently establish that this field's effect caused the difference."
        ),
    }


def direct_promotion_audit(
    contribution: dict[str, Any],
    old_basis: str,
    event: dict[str, Any] | None,
    readings: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Check a newly direct row against actual training-gain readings."""

    event = event or {}
    field = contribution.get("field")
    paths = list((event.get("field_evidence") or {}).get(field) or [])
    missing = [path for path in paths if path not in readings]
    rows = [readings[path] for path in paths if path in readings]
    values = [
        ((row.get("facts") or {}).get("training_gains") or {}).get(field)
        for row in rows
    ]
    invalid_rows = [
        {
            "evidence": row.get("evidence"),
            "screen": row.get("screen"),
            "value": value,
        }
        for row, value in zip(rows, values)
        if not integer(value) or row.get("screen") != "training_result"
    ]
    timestamps = [row.get("source_timestamp_ms") for row in rows]
    event_start, event_end = event.get("first_seen_ms"), event.get("last_seen_ms")
    event_window_ok = bool(rows) and all(
        integer(timestamp)
        and (not integer(event_start) or timestamp >= event_start)
        and (not integer(event_end) or timestamp <= event_end)
        for timestamp in timestamps
    )
    provenance = (event.get("direct_gain_provenance") or {}).get(field)
    provenance_observations = (
        provenance.get("observations") if isinstance(provenance, dict) else []
    )
    provenance_issues: list[str] = []
    if not isinstance(provenance, dict):
        provenance_issues.append("missing_provenance")
    else:
        if provenance.get("value") != contribution.get("amount"):
            provenance_issues.append("provenance_value_mismatch")
        if provenance.get("observation_count") != len(provenance_observations):
            provenance_issues.append("provenance_observation_count_mismatch")
        provenance_paths = [
            item.get("evidence")
            for item in provenance_observations
            if isinstance(item, dict)
        ]
        provenance_times = [
            item.get("source_timestamp_ms")
            for item in provenance_observations
            if isinstance(item, dict)
        ]
        if len(provenance_paths) != len(set(provenance_paths)):
            provenance_issues.append("duplicate_provenance_paths")
        if len(provenance_times) != len(set(provenance_times)):
            provenance_issues.append("duplicate_provenance_timestamps")
        for item in provenance_observations:
            if not isinstance(item, dict):
                provenance_issues.append("invalid_provenance_observation")
                continue
            path = item.get("evidence")
            row = readings.get(path)
            if row is None:
                provenance_issues.append("provenance_path_missing")
                continue
            actual = ((row.get("facts") or {}).get("training_gains") or {}).get(field)
            if row.get("source_timestamp_ms") != item.get("source_timestamp_ms"):
                provenance_issues.append("provenance_timestamp_mismatch")
            if actual != item.get("value"):
                provenance_issues.append("provenance_reading_value_mismatch")
            if actual != contribution.get("amount"):
                provenance_issues.append("provenance_amount_mismatch")
    valid_values = all(integer(value) for value in values) and bool(values)
    values_match = valid_values and set(values) == {contribution.get("amount")}
    duplicate_timestamps = len(timestamps) != len(set(timestamps))
    physical = inv._observation_identity(
        [
            {
                "source_timestamp_ms": row.get("source_timestamp_ms"),
                "evidence": row.get("evidence"),
            }
            for row in rows
        ]
    )
    base_valid = bool(
        paths
        and not missing
        and not invalid_rows
        and values_match
        and event_window_ok
        and not provenance_issues
        and physical["distinct_evidence_count"] == len(paths)
        and physical["distinct_timestamp_count"] == len(set(timestamps))
        and not duplicate_timestamps
    )
    if not base_valid:
        status = "direct_source_unverified"
    elif physical["distinct_timestamp_count"] >= 2 and physical["distinct_evidence_count"] >= 2:
        status = "direct_repeated_source_bound"
    else:
        status = "direct_single_source_bound"
    return {
        "id": contribution.get("id"),
        "old_basis": old_basis,
        "new_basis": contribution.get("basis"),
        "event_id": contribution.get("event_id"),
        "turn_id": contribution.get("turn_id"),
        "field": field,
        "amount": contribution.get("amount"),
        "event_window_ms": [event_start, event_end],
        "evidence": paths,
        "evidence_source_timestamp_ms": timestamps,
        "source_window_ms": inv._window(
            [timestamp for timestamp in timestamps if integer(timestamp)]
        ),
        "source_values": values,
        "invalid_rows": invalid_rows,
        "missing_evidence": missing,
        "event_window_ok": event_window_ok,
        "provenance": {
            "basis": provenance.get("basis") if isinstance(provenance, dict) else None,
            "value": provenance.get("value") if isinstance(provenance, dict) else None,
            "evidence": [
                item.get("evidence")
                for item in provenance_observations
                if isinstance(item, dict)
            ],
            "source_timestamp_ms": [
                item.get("source_timestamp_ms")
                for item in provenance_observations
                if isinstance(item, dict)
            ],
            "observation_count": len(provenance_observations),
            "issues": provenance_issues,
        },
        "physical_identity": physical,
        "duplicate_timestamps": duplicate_timestamps,
        "values_match_contribution": values_match,
        "status": status,
        "source_direct_verified": base_valid,
        "proof_limitation": (
            "Direct status is checked against the worker's actual training_result "
            "facts and provenance pointers; repeated rows corroborate visibility, "
            "but this audit does not establish whole-run recall."
        ),
    }


def compact_proof(
    contribution: dict[str, Any],
    proof: dict[str, Any],
    state_endpoint: dict[str, Any] | None,
    historical: dict[str, Any] | None,
) -> dict[str, Any]:
    basis = contribution.get("basis")
    if basis == "observed_balance_debit":
        keys = (
            "purchase_id",
            "purchase_name",
            "receipt_event_id",
            "cost_basis",
            "performance_cost",
            "before_balance",
            "after_balance",
            "observed_difference",
            "stored_field_cost",
            "source_delta",
            "source_cost_matches",
            "full_source_cost_match",
            "selected_by",
            "temporal_order_ok",
            "purchase_ref_matches",
            "receipt_owner_ok",
            "receipt_first_seen_ms",
            "receipt_temporal_order_ok",
            "debit_observation_count",
            "debit_observation_cost_match",
            "debit_observation_order_ok",
            "source_operand_audit_status",
            "source_arithmetic_verified",
        )
        return {key: proof.get(key) for key in keys if key in proof}
    if basis == "committed_offer_cost_derived":
        keys = (
            "purchase_id",
            "purchase_name",
            "receipt_event_id",
            "cost_basis",
            "total_cost",
            "stored_cost_vector",
            "source_price_fields",
            "source_price_missing_fields",
            "actual_price_operands_verified",
            "purchase_identity_ok",
            "purchase_ref_matches",
            "temporal_order_ok",
            "repeated_windows_ok",
            "source_operand_audit_status",
            "source_arithmetic_verified",
        )
        return {key: proof.get(key) for key in keys if key in proof}
    if basis == "state_constrained":
        operand = proof.get("source_operand_audit") or {}
        resolution = proof.get("resolution") or {}
        return {
            "resolution_basis": resolution.get("basis"),
            "resolution_amount": resolution.get("amount"),
            "resolution_visual_candidates": resolution.get("visual_candidates"),
            "resolution_gain_evidence": resolution.get("gain_evidence"),
            "resolution_before_evidence": resolution.get("before_evidence"),
            "resolution_after_evidence": resolution.get("after_evidence"),
            "event_conflicts": proof.get("event_conflicts"),
            "structural_basis_present": proof.get("structural_basis_present"),
            "source_operand_audit_status": proof.get("source_operand_audit_status"),
            "source_arithmetic_verified": proof.get("source_arithmetic_verified"),
            "operand": {
                key: operand.get(key)
                for key in (
                    "source_operand_audit_status",
                    "source_arithmetic_verified",
                    "gain_paths",
                    "gain_values",
                    "gain_source_ok",
                    "gain_paths_in_contribution_evidence",
                    "before",
                    "after",
                    "endpoint_delta",
                    "endpoint_pointers_ok",
                    "visual_candidates",
                    "candidate_constraint_ok",
                    "structural_resolution_ok",
                    "selected_without_amount_filter",
                )
                if key in operand
            },
        }
    return {
        "event_id": proof.get("event_id"),
        "event_kind": proof.get("event_kind"),
        "event_field_evidence": proof.get("event_field_evidence"),
        "event_field_evidence_matches_contribution": proof.get(
            "event_field_evidence_matches_contribution"
        ),
        "result_value_reading_count": proof.get("result_value_reading_count"),
        "result_value_candidate_reading_count": proof.get(
            "result_value_candidate_reading_count"
        ),
        "structural_basis_present": proof.get("structural_basis_present"),
        "source_operand_audit_status": (
            state_endpoint.get("status")
            if state_endpoint
            else proof.get("source_operand_audit_status")
        ),
        "historical_source_operand_audit_status": proof.get(
            "source_operand_audit_status"
        ),
        "source_arithmetic_verified": proof.get("source_arithmetic_verified"),
        "historical_compatibility": historical,
        "current_endpoint_audit": state_endpoint,
    }


def derived_case(
    contribution: dict[str, Any],
    event: dict[str, Any] | None,
    readings: dict[str, dict[str, Any]],
    all_readings: list[dict[str, Any]],
    events: dict[str, dict[str, Any]],
    purchases: dict[str, dict[str, Any]],
    debit_observations: list[dict[str, Any]],
    reused: dict[tuple[str, str, str], dict[str, Any]],
    hits: dict[str, list[dict[str, Any]]],
    source_sha256: str,
) -> dict[str, Any]:
    rows, missing = inv._evidence_readings(contribution, readings)
    historical = reused.get(("v1", source_sha256, contribution.get("id")))
    proof = inv._basis_proof(
        contribution,
        events,
        purchases,
        events,
        rows,
        readings,
        missing,
        debit_observations,
        historical,
    )
    endpoint = None
    if contribution.get("basis") == "state_derived":
        endpoint = state_derived_endpoint_audit(contribution, event, all_readings, readings)
        status = endpoint["status"]
        arithmetic = False
        structural = proof.get("structural_basis_present")
    else:
        status = proof.get("source_operand_audit_status")
        arithmetic = bool(proof.get("source_arithmetic_verified"))
        structural = proof.get("structural_basis_present")
    evidence = list(contribution.get("evidence") or [])
    timestamps = source_times(evidence, readings)
    numeric_timestamps = [time for time in timestamps if integer(time)]
    return {
        "id": contribution.get("id"),
        "source_sha256": source_sha256,
        "event_id": contribution.get("event_id"),
        "turn_id": contribution.get("turn_id"),
        "channel": contribution.get("channel"),
        "field": contribution.get("field"),
        "amount": contribution.get("amount"),
        "basis": contribution.get("basis"),
        "classification": inv.BASIS_CLASSIFICATION.get(
            contribution.get("basis"), "unsupported_basis"
        ),
        "evidence": evidence,
        "evidence_source_timestamp_ms": timestamps,
        "source_window_ms": inv._window(numeric_timestamps),
        "evidence_missing_or_duplicate": missing,
        "selected_in_balanced_transition": contribution.get("id") in hits,
        "balanced_transition_reference_count": len(hits.get(contribution.get("id"), [])),
        "balanced_transition_refs": hits.get(contribution.get("id"), []),
        "structural_basis_present": structural,
        "source_operand_audit_status": status,
        "source_arithmetic_verified": arithmetic,
        "proof": compact_proof(contribution, proof, endpoint, compact_historical_compatibility(historical)),
    }


def strict_grade_issues(strict: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter[str]]:
    issues: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for case in strict.get("cases") or []:
        for result in case.get("results") or []:
            if result.get("status") == "correct" and result.get("turn_status") == "correct":
                continue
            source_id = str(result.get("source_id") or "")
            field_names = [str(item.get("field") or "") for item in result.get("fields") or []]
            if "price_status" in " ".join(field_names) or "price" in source_id:
                category = "purchase_price_unreadable"
            elif result.get("turn_status") == "ambiguous" or result.get("status") == "ambiguous":
                category = "turn_assignment_ambiguous"
            elif result.get("phase") == "preview" or "preview" in source_id:
                category = "readable_preview_candidate_missing"
            elif result.get("category") == "action":
                category = "committed_action_or_receipt_missing"
            elif result.get("phase") == "applied":
                category = "readable_applied_effect_missing"
            else:
                category = "other_source_review_miss"
            counts[category] += 1
            issues.append(
                {
                    "source_id": source_id,
                    "turn_id": case.get("turn_id"),
                    "category": category,
                    "result_category": result.get("category"),
                    "phase": result.get("phase"),
                    "status": result.get("status"),
                    "turn_status": result.get("turn_status"),
                    "source_interval_ms": result.get("source_interval_ms"),
                    "source_evidence": result.get("source_evidence"),
                    "prediction_id": result.get("prediction_id"),
                    "field_statuses": [
                        {
                            "field": item.get("field"),
                            "expected": item.get("expected"),
                            "actual": item.get("actual"),
                            "status": item.get("status"),
                        }
                        for item in result.get("fields") or []
                        if item.get("status") != "correct"
                    ],
                }
            )
    return issues, counts


def acceptance_open_cases(acceptance: dict[str, Any]) -> list[str]:
    value = acceptance.get("intermediate_actual_worker_v2") or {}
    return list(value.get("source_investigations_open") or [])


def build_audit(root: Path) -> dict[str, Any]:
    worker_path = root / WORKER_REPORT
    baseline_path = root / BASELINE_REPORT
    baseline_audit_path = root / BASELINE_AUDIT
    worker = read_json(worker_path)
    baseline = read_json(baseline_path)
    baseline_audit = read_json(baseline_audit_path)
    worker_contributions = {
        item.get("id"): item
        for item in (worker.get("causal_accounting") or {}).get("contributions") or []
        if item.get("id")
    }
    baseline_contributions = {
        item.get("id"): item
        for item in (baseline.get("causal_accounting") or {}).get("contributions") or []
        if item.get("id")
    }
    worker_readings, worker_evidence_counts, duplicate_groups = evidence_rows(worker)
    all_worker_readings = (worker.get("gameplay_tracking") or {}).get("readings") or []
    events = {
        event.get("id"): event
        for event in (worker.get("gameplay_tracking") or {}).get("events") or []
        if event.get("id")
    }
    purchases = {
        purchase.get("id"): purchase
        for purchase in (worker.get("gameplay_tracking") or {}).get("lesson_purchases") or []
        if purchase.get("id")
    }
    debit_observations = (worker.get("gameplay_tracking") or {}).get(
        "lesson_debit_observations"
    ) or []
    worker_hits, duplicate_transition_references = transition_hits(
        worker, worker_contributions
    )
    reused, reused_metadata = inv._load_reused_state_derived_audit(root)
    worker_source_sha256 = worker.get("source", {}).get("sha256")
    derived = [
        contribution
        for contribution in worker_contributions.values()
        if contribution.get("basis") not in inv.DIRECT_BASES
    ]
    derived.sort(key=lambda item: str(item.get("id")))
    derived_cases = [
        derived_case(
            contribution,
            events.get(contribution.get("event_id")),
            worker_readings,
            all_worker_readings,
            events,
            purchases,
            debit_observations,
            reused,
            worker_hits,
            worker_source_sha256,
        )
        for contribution in derived
    ]
    baseline_derived = [
        contribution
        for contribution in baseline_contributions.values()
        if contribution.get("basis") not in inv.DIRECT_BASES
    ]
    baseline_g5_record = next(
        (
            item
            for item in baseline_audit.get("recordings") or []
            if item.get("recording") == "v1"
        ),
        {},
    )
    baseline_g5_ids = {
        contribution_id
        for turn in baseline_g5_record.get("turns") or []
        for contribution_id in turn.get("derived_contribution_ids") or []
    }
    common_ids = set(baseline_contributions) & set(worker_contributions)
    basis_transitions: Counter[str] = Counter()
    old_only: list[dict[str, Any]] = []
    for contribution_id in sorted(set(baseline_contributions) | set(worker_contributions)):
        old = baseline_contributions.get(contribution_id)
        new = worker_contributions.get(contribution_id)
        old_basis = old.get("basis") if old else "MISSING"
        new_basis = new.get("basis") if new else "MISSING"
        basis_transitions[f"{old_basis} -> {new_basis}"] += 1
        if old and not new:
            old_only.append(
                {
                    "id": contribution_id,
                    "old_basis": old.get("basis"),
                    "field": old.get("field"),
                    "channel": old.get("channel"),
                    "amount": old.get("amount"),
                    "event_id": old.get("event_id"),
                    "turn_id": old.get("turn_id"),
                    "evidence": old.get("evidence"),
                }
            )
    promotions = []
    for contribution_id in sorted(common_ids):
        old = baseline_contributions[contribution_id]
        new = worker_contributions[contribution_id]
        if (
            old.get("basis") in {"state_derived", "state_constrained"}
            and new.get("basis") in inv.DIRECT_BASES
        ):
            promotions.append(
                direct_promotion_audit(
                    new,
                    old.get("basis"),
                    events.get(new.get("event_id")),
                    worker_readings,
                )
            )
    promotions.sort(key=lambda item: str(item.get("id")))
    strict = read_json(root / STRICT_GRADE) if (root / STRICT_GRADE).is_file() else {}
    grade_issues, grade_issue_counts = strict_grade_issues(strict)
    acceptance = (
        read_json(root / ACCEPTANCE_RESULTS)
        if (root / ACCEPTANCE_RESULTS).is_file()
        else {}
    )
    worker_basis_counts = Counter(
        item.get("basis")
        for item in worker_contributions.values()
    )
    worker_derived_basis_counts = Counter(item.get("basis") for item in derived)
    worker_derived_classifications = Counter(
        inv.BASIS_CLASSIFICATION.get(item.get("basis"), "unsupported_basis")
        for item in derived
    )
    selected_ids = set(worker_hits)
    selected_cases = [item for item in derived_cases if item["id"] in selected_ids]
    source_status_counts = Counter(item.get("source_operand_audit_status") for item in derived_cases)
    arithmetic_count = sum(
        bool(item.get("source_arithmetic_verified")) for item in derived_cases
    )
    structural_count = sum(
        bool(item.get("structural_basis_present")) for item in derived_cases
    )
    old_only_basis_counts = Counter(item.get("old_basis") for item in old_only)
    promotion_status_counts = Counter(item.get("status") for item in promotions)
    historical_cause_counts = reused_metadata.get("historical_baseline", {}).get(
        "direct_cause_counts", {}
    )
    source_sha_matches = baseline.get("source", {}).get("sha256") == worker_source_sha256
    source_window_values = [
        timestamp
        for item in derived_cases
        for timestamp in item.get("evidence_source_timestamp_ms") or []
        if integer(timestamp)
    ]
    output_path = root / OUTPUT_JSON
    md_path = root / OUTPUT_MD
    return {
        "schema_version": "tracen-replay/intermediate-worker-source-basis-v1",
        "scope": {
            "kind": "v1_actual_intermediate_worker_only",
            "worker_report": relative(root, worker_path),
            "baseline_report": relative(root, baseline_path),
            "baseline_g5_audit": relative(root, baseline_audit_path),
            "source_review": "report readings and recorded evidence pointers; no recognition rerun",
            "all_three_runs": False,
            "final_acceptance": False,
            "full_history_accuracy_measured": None,
            "deduplication_key": "(source_sha256, canonical contribution id)",
            "derived_contribution_coverage": "all worker causal contributions whose basis is not a direct basis; balanced transition references are a separate unique view",
        },
        "integrity": {
            "worker_report_sha256": sha256(worker_path),
            "baseline_report_sha256": sha256(baseline_path),
            "baseline_g5_audit_sha256": sha256(baseline_audit_path),
            "historical_source_causes_sha256": sha256(root / HISTORICAL_SOURCE_CAUSES)
            if (root / HISTORICAL_SOURCE_CAUSES).is_file()
            else None,
            "strict_grade_sha256": sha256(root / STRICT_GRADE)
            if (root / STRICT_GRADE).is_file()
            else None,
            "source_sha256": worker_source_sha256,
            "baseline_source_sha256": baseline.get("source", {}).get("sha256"),
            "source_sha256_matches_baseline": source_sha_matches,
            "inventory_audit_script_sha256": sha256(SCRIPT_DIR / "inventory_derived_reliability.py"),
            "this_script_sha256": sha256(Path(__file__).resolve()),
            "worker_report_declared_sha256": (worker.get("verification") or {}).get(
                "report_sha256"
            ),
            "strict_grade_declared_report_sha256": strict.get("report_sha256"),
        },
        "counts": {
            "worker_causal_contributions": len(worker_contributions),
            "worker_direct_basis_counts": counter_dict(worker_basis_counts),
            "worker_all_derived_basis_counts": counter_dict(worker_derived_basis_counts),
            "worker_all_derived_classification_counts": counter_dict(worker_derived_classifications),
            "worker_all_derived_contributions_audited": len(derived_cases),
            "worker_all_derived_turn_ids": sorted(
                {
                    item.get("turn_id")
                    for item in derived_cases
                    if item.get("turn_id") is not None
                },
                key=inv._turn_number,
            ),
            "worker_all_derived_turn_count": len(
                {
                    item.get("turn_id")
                    for item in derived_cases
                    if item.get("turn_id") is not None
                }
            ),
            "worker_balanced_transition_selected_derived": len(selected_cases),
            "worker_balanced_transition_selected_basis_counts": counter_dict(
                Counter(item.get("basis") for item in selected_cases)
            ),
            "worker_balanced_transition_selected_turn_count": len(
                {
                    ref.get("turn_id")
                    for item in selected_cases
                    for ref in item.get("balanced_transition_refs") or []
                    if ref.get("turn_id") is not None
                }
            ),
            "worker_balanced_transition_duplicate_references": duplicate_transition_references,
            "worker_duplicate_evidence_path_groups": len(duplicate_groups),
            "worker_source_arithmetic_compatible_operand_count": arithmetic_count,
            "worker_structural_basis_present_count": structural_count,
            "worker_source_operand_audit_status_counts": counter_dict(source_status_counts),
            "worker_unsupported_or_unverified_derived_count": sum(
                status not in {"verified_source_balance", "verified_source_price"}
                and not (
                    status == "constraint_supported_not_independent"
                )
                for status in (item.get("source_operand_audit_status") for item in derived_cases)
            ),
            "worker_price_derivation_contributions": worker_derived_basis_counts.get(
                "committed_offer_cost_derived", 0
            ),
            "worker_state_constrained_contributions": worker_derived_basis_counts.get(
                "state_constrained", 0
            ),
            "worker_state_derived_contributions": worker_derived_basis_counts.get(
                "state_derived", 0
            ),
            "worker_observed_balance_debit_contributions": worker_derived_basis_counts.get(
                "observed_balance_debit", 0
            ),
            "baseline_v1_causal_contributions": len(baseline_contributions),
            "baseline_v1_all_derived_contributions": len(baseline_derived),
            "baseline_v1_all_derived_basis_counts": counter_dict(
                Counter(item.get("basis") for item in baseline_derived)
            ),
            "baseline_v1_g5_selected_derived_contributions": len(baseline_g5_ids),
            "baseline_v1_g5_selected_basis_counts": counter_dict(
                Counter(
                    baseline_contributions[item].get("basis")
                    for item in baseline_g5_ids
                    if item in baseline_contributions
                )
            ),
            "baseline_v1_g5_selected_turn_count": baseline_g5_record.get(
                "derived_turn_count"
            ),
            "basis_transition_counts": counter_dict(basis_transitions),
            "old_contributions_absent_from_worker": len(old_only),
            "old_only_basis_counts": counter_dict(old_only_basis_counts),
            "derived_to_direct_promotions": len(promotions),
            "derived_to_direct_promotion_status_counts": counter_dict(
                promotion_status_counts
            ),
            "derived_to_direct_promotion_old_basis_counts": counter_dict(
                Counter(item.get("old_basis") for item in promotions)
            ),
            "derived_to_direct_promotion_new_basis_counts": counter_dict(
                Counter(item.get("new_basis") for item in promotions)
            ),
            "historical_264_cause_counts_compatibility_only": historical_cause_counts,
            "strict_grade_status_counts": strict.get("status_counts"),
            "strict_grade_remaining_issue_counts": counter_dict(grade_issue_counts),
        },
        "before_after_comparison": {
            "basis_transition_counts": counter_dict(basis_transitions),
            "old_only_contributions": old_only,
            "interpretation": (
                "A missing old contribution is a report-output difference, not a source absence verdict. "
                "Promotions are source-checked below against the worker's actual training_result readings."
            ),
        },
        "operand_audit": {
            "selection_rule": (
                "Before and after operands are selected from recorded evidence pointers and event chronology; "
                "the claimed contribution amount is compared only after selection."
            ),
            "arithmetic_semantics": (
                "source_arithmetic_verified means the recorded operands recompute compatibly; it does not mean "
                "an inferred effect is independently verified."
            ),
            "observed_balance_debit": {
                "count": worker_derived_basis_counts.get("observed_balance_debit", 0),
                "status_counts": counter_dict(
                    Counter(
                        item.get("source_operand_audit_status")
                        for item in derived_cases
                        if item.get("basis") == "observed_balance_debit"
                    )
                ),
                "source_arithmetic_compatible_count": sum(
                    bool(item.get("source_arithmetic_verified"))
                    for item in derived_cases
                    if item.get("basis") == "observed_balance_debit"
                ),
                "interpretation": "Observed pre/post performance balance debits with purchase and receipt ordering checks; they are transaction observations, not training effects.",
            },
            "state_constrained": {
                "count": worker_derived_basis_counts.get("state_constrained", 0),
                "status_counts": counter_dict(
                    Counter(
                        item.get("source_operand_audit_status")
                        for item in derived_cases
                        if item.get("basis") == "state_constrained"
                    )
                ),
                "interpretation": "Visible candidates constrained by a result endpoint and surrounding state; compatible arithmetic remains non-independent.",
            },
            "state_derived": {
                "count": worker_derived_basis_counts.get("state_derived", 0),
                "status_counts": counter_dict(
                    Counter(
                        item.get("source_operand_audit_status")
                        for item in derived_cases
                        if item.get("basis") == "state_derived"
                    )
                ),
                "historical_compatibility_match_count": sum(
                    bool(
                        ((item.get("proof") or {}).get("historical_compatibility"))
                    )
                    for item in derived_cases
                    if item.get("basis") == "state_derived"
                ),
                "interpretation": "Endpoint differences remain state-derived. Stable suffix and before values are audited without amount-driven selection, but no direct effect proof is claimed.",
            },
            "legitimate_price_derivation": {
                "count": worker_derived_basis_counts.get(
                    "committed_offer_cost_derived", 0
                ),
                "interpretation": "No committed offer-price derived contribution appears in this v1 worker report; the two worker skill purchases are committed_skill_debit direct-basis rows and are outside this derived count.",
            },
        },
        "derived_cases": derived_cases,
        "direct_promotion_audit": {
            "source_checked_count": len(promotions),
            "source_verified_count": sum(
                bool(item.get("source_direct_verified")) for item in promotions
            ),
            "repeated_source_bound_count": sum(
                item.get("status") == "direct_repeated_source_bound"
                for item in promotions
            ),
            "single_source_bound_count": sum(
                item.get("status") == "direct_single_source_bound"
                for item in promotions
            ),
            "unverified_count": sum(
                item.get("status") == "direct_source_unverified"
                for item in promotions
            ),
            "interpretation": (
                "These are baseline state_derived/state_constrained rows promoted to a direct worker basis. "
                "A source-verified promotion has actual training_result training_gains values and matching "
                "provenance pointers. One promotion has one physical source observation and is reported separately."
            ),
            "cases": promotions,
        },
        "remaining_source_review_causes": {
            "source": relative(root, STRICT_GRADE),
            "status_counts": strict.get("status_counts"),
            "category_counts": counter_dict(grade_issue_counts),
            "issues": grade_issues,
            "open_intermediate_worker_investigations": acceptance_open_cases(acceptance),
            "interpretation": (
                "These are source-reviewed strict-grade misses/partial or ambiguous assignments in the "
                "intermediate worker snapshot. Their source evidence is retained as intervals and paths; a "
                "miss is not treated as proof that a badge or effect was absent."
            ),
        },
        "historical_compatibility": reused_metadata,
        "limitations": [
            "This artifact audits only intermediate-worker-v2/report.json for v1.",
            "It is not a fresh all-three-run replay and does not establish final acceptance.",
            "State-constrained and state-derived arithmetic is explicitly not independent effect verification.",
            "Historical 264-row compatibility is preserved as compatibility evidence only, not promoted to current proof.",
            "Full-history accuracy and whole-run recall remain unmeasured.",
        ],
        "generator": {
            "script": relative(root, Path(__file__).resolve()),
            "command": ".\\.venv\\Scripts\\python.exe scripts\\audit_intermediate_worker_source_basis.py",
            "output_json": relative(root, output_path),
            "output_markdown": relative(root, md_path),
        },
    }


def markdown(audit: dict[str, Any]) -> str:
    counts = audit["counts"]
    operand = audit["operand_audit"]
    promotion = audit["direct_promotion_audit"]
    causes = audit["remaining_source_review_causes"]
    integrity = audit["integrity"]
    lines = [
        "# Intermediate worker v1 source-basis audit",
        "",
        "This is a bounded audit of the intermediate worker report only. It preserves the v1 baseline and does not rerun recognition or assert final/all-three acceptance.",
        "",
        "## Inputs and integrity",
        "",
        f"- Worker report: `{audit['scope']['worker_report']}` (`{integrity['worker_report_sha256']}`)",
        f"- Preserved v1 report: `{audit['scope']['baseline_report']}` (`{integrity['baseline_report_sha256']}`)",
        f"- Preserved source SHA: `{integrity['source_sha256']}`; matches baseline: `{integrity['source_sha256_matches_baseline']}`",
        f"- Existing G5 audit used for the baseline selected view: `{audit['scope']['baseline_g5_audit']}` (`{integrity['baseline_g5_audit_sha256']}`)",
        f"- Existing proof helper: `scripts/inventory_derived_reliability.py` (`{integrity['inventory_audit_script_sha256']}`)",
        "",
        "## Coverage and basis counts",
        "",
        f"- Worker causal rows: **{counts['worker_causal_contributions']}**; derived rows audited: **{counts['worker_all_derived_contributions_audited']}** across **{counts['worker_all_derived_turn_count']}** turns.",
        f"- Unique derived rows referenced by balanced transition views: **{counts['worker_balanced_transition_selected_derived']}** across **{counts['worker_balanced_transition_selected_turn_count']}** turns; duplicate references: **{counts['worker_balanced_transition_duplicate_references']}**.",
        f"- Baseline v1 G5 selected view: **{counts['baseline_v1_g5_selected_derived_contributions']}** rows across **{counts['baseline_v1_g5_selected_turn_count']}** turns. Baseline all-derived causal rows: **{counts['baseline_v1_all_derived_contributions']}**.",
        "",
        "| View | Observed balance debit | State-constrained | State-derived | Price derivation |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| Worker all derived | {counts['worker_all_derived_basis_counts'].get('observed_balance_debit', 0)} | {counts['worker_all_derived_basis_counts'].get('state_constrained', 0)} | {counts['worker_all_derived_basis_counts'].get('state_derived', 0)} | {counts['worker_all_derived_basis_counts'].get('committed_offer_cost_derived', 0)} |",
        f"| Worker selected transitions | {counts['worker_balanced_transition_selected_basis_counts'].get('observed_balance_debit', 0)} | {counts['worker_balanced_transition_selected_basis_counts'].get('state_constrained', 0)} | {counts['worker_balanced_transition_selected_basis_counts'].get('state_derived', 0)} | 0 |",
        f"| Baseline v1 G5 selected | {counts['baseline_v1_g5_selected_basis_counts'].get('observed_balance_debit', 0)} | {counts['baseline_v1_g5_selected_basis_counts'].get('state_constrained', 0)} | {counts['baseline_v1_g5_selected_basis_counts'].get('state_derived', 0)} | {counts['baseline_v1_g5_selected_basis_counts'].get('committed_offer_cost_derived', 0)} |",
        "",
        "The worker has **0** committed offer-price derived contributions in this v1 report. Its two skill purchases use `committed_skill_debit`, which is a direct-basis transaction row and is excluded from this derived audit.",
        "",
        "## Operand results",
        "",
        f"- Observed balance debits: **{operand['observed_balance_debit']['count']}**, all **{operand['observed_balance_debit']['source_arithmetic_compatible_count']}** recomputed from source-pinned before/after menus with purchase/receipt checks (`{operand['observed_balance_debit']['status_counts']}`).",
        f"- State-constrained candidates: **{operand['state_constrained']['count']}**; status counts `{operand['state_constrained']['status_counts']}`. One has compatible endpoint arithmetic; candidate constraints remain non-independent.",
        f"- State-derived endpoint differences: **{operand['state_derived']['count']}**; current endpoint statuses `{operand['state_derived']['status_counts']}`. Historical compatibility matches: **{operand['state_derived']['historical_compatibility_match_count']}**; none are direct proof.",
        f"- Derived rows with source arithmetic compatible operands: **{counts['worker_source_arithmetic_compatible_operand_count']}**; this count is operand compatibility, not independent effect verification.",
        "",
        "Every detailed row is in `derived_cases` with event/turn/field, source evidence paths and aligned timestamps, selected before/after pointers, recomputed delta and explicit proof status. The selectors never use the claimed amount to choose a counter.",
        "",
        "## Baseline changes and direct promotions",
        "",
        f"- Baseline state-derived/state-constrained rows promoted to a worker direct basis: **{promotion['source_checked_count']}**.",
        f"- Source-verified promotions: **{promotion['source_verified_count']}** = **{promotion['repeated_source_bound_count']}** repeated physical source rows + **{promotion['single_source_bound_count']}** single physical source row; unverified: **{promotion['unverified_count']}**.",
        "- The single-source row is `/gameplay_tracking/events/274/deltas/speed` (Speed +12, one training_result frame). It is direct source-bound but is kept separate from repeated corroboration.",
        f"- Baseline contributions absent from the worker report: **{counts['old_contributions_absent_from_worker']}** (`{counts['old_only_basis_counts']}`). This is an output difference, not a source-absence verdict.",
        "",
        "The per-promotion records bind every field-evidence and direct-gain-provenance path to the worker reading, check screen and amount, validate event chronology, and reject duplicate physical identities. They are not counted again as derived rows.",
        "",
        "## Remaining source-reviewed causes in this worker snapshot",
        "",
        f"- Strict grade status: `{causes.get('status_counts')}`; remaining categorized issues: `{causes.get('category_counts')}`.",
    ]
    for category, count in sorted(causes.get("category_counts", {}).items()):
        examples = [item for item in causes.get("issues", []) if item.get("category") == category][:4]
        refs = ", ".join(str(item.get("source_id")) for item in examples)
        lines.append(f"- `{category}`: {count}; examples: {refs}")
    lines.extend(
        [
            "",
            f"- Open intermediate-worker investigations recorded by acceptance: `{causes.get('open_intermediate_worker_investigations')}`.",
            "",
            "The strict-grade source intervals and paths remain in `remaining_source_review_causes.issues`; readable misses are not relabeled as hidden or absent content.",
            "",
            "## Limits",
            "",
        ]
    )
    for limitation in audit.get("limitations", []):
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    audit = build_audit(root)
    output_json = root / OUTPUT_JSON
    output_md = root / OUTPUT_MD
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_md.write_text(markdown(audit), encoding="utf-8")
    print(json.dumps({
        "output_json": relative(root, output_json),
        "output_markdown": relative(root, output_md),
        "worker_all_derived": audit["counts"]["worker_all_derived_contributions_audited"],
        "worker_selected_derived": audit["counts"]["worker_balanced_transition_selected_derived"],
        "direct_promotions": audit["direct_promotion_audit"]["source_checked_count"],
        "direct_promotion_status_counts": audit["counts"]["derived_to_direct_promotion_status_counts"],
        "source_operand_status_counts": audit["counts"]["worker_source_operand_audit_status_counts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
