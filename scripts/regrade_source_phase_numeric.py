"""Regrade source-phase event projections with the frozen numeric evaluator.

This is an intermediate audit utility.  It reads the existing fresh candidate
reports used by ``write_source_phase_verification.py``, replaces only the
training-event list with events recomputed from those report readings, and
writes a separate targeted projection.  The original reports, frozen labels,
and source evidence are never modified.  The projection retains the complete
reading rows, source metadata, turn ledger, causal accounting, and action
receipts needed by the independent evaluator to bind evidence and ownership.

The evaluator is called through ``evaluate_report`` from
``evaluate_final_numeric_cases.py``.  Its verdicts are kept separate from the
producer's source-phase proof so a join or canonical-interface mismatch is
reported rather than repaired by relaxing a proof rule.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracen_replay.transactions import checkpoints, training_events
from scripts import evaluate_final_numeric_cases as numeric_evaluator

evaluate_report = numeric_evaluator.evaluate_report


AUDIT_DIR = ROOT / ".local" / "final-reliability-v1"
SOURCE_PHASE = AUDIT_DIR / "source-phase-verification-v1.json"
NUMERIC_CAUSES = AUDIT_DIR / "numeric-causes.json"
OUT_DIR = AUDIT_DIR / "source-phase-regrade-v1"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def bound_file(path: Path) -> dict[str, Any]:
    return {
        "path": relative(path),
        "exists": path.is_file(),
        "sha256": digest(path) if path.is_file() else None,
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }


def report_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def replace_training_events(report: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep non-training report events and replace matching training events."""

    projection = deepcopy(report)
    gameplay = projection.get("gameplay_tracking")
    if not isinstance(gameplay, dict):
        raise ValueError("candidate report has no gameplay_tracking object")
    readings = gameplay.get("readings")
    if not isinstance(readings, list):
        raise ValueError("candidate report has no gameplay_tracking.readings list")
    recomputed = training_events(readings, checkpoints(readings))
    recomputed_by_id = {event.get("id"): event for event in recomputed
                        if isinstance(event.get("id"), str)}
    prior_events = gameplay.get("events", [])
    if not isinstance(prior_events, list):
        prior_events = []
    events: list[dict[str, Any]] = []
    used: set[str] = set()
    for event in prior_events:
        event_id = event.get("id") if isinstance(event, dict) else None
        replacement = recomputed_by_id.get(event_id)
        if replacement is not None:
            events.append(replacement)
            used.add(event_id)
        else:
            events.append(event)
    prior_ids = {item.get("id") for item in prior_events if isinstance(item, dict)}
    events.extend(event for event in recomputed
                  if event.get("id") not in used
                  and event.get("id") not in prior_ids)
    gameplay["events"] = events
    projection["phase_regrade_projection"] = {
        "schema_version": "tracen-replay/source-phase-regrade-projection-v1",
        "event_policy": "replace_training_events_from_current_readings_keep_nontraining_report_events",
        "source_reading_count": len(readings),
        "recomputed_training_event_count": len(recomputed),
        "projection_event_count": len(events),
        "source_report_event_count": len(prior_events),
    }
    return projection, recomputed


def historical_cases(source_phase: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the five source-phase history entries to evaluator cases."""

    result = []
    for item in source_phase.get("historical_regressions", []):
        result.append({
            "id": item["source_id"],
            "recording": item["recording"],
            "turn_id": item["turn_id"],
            "field": item["field"],
            "source_amount": item["source_amount"],
            "event_id": item["event_id"],
            "observation_window_ms": item.get("source_interval_ms"),
            "source_evidence": [
                {
                    "path": evidence.get("path"),
                    "sha256": evidence.get("sha256"),
                }
                for evidence in item.get("source_evidence", [])
            ],
            "audit_origin": "source-phase-verification-v1",
        })
    return result


def extended_causes(numeric: dict[str, Any], source_phase: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(numeric)
    result["cases"] = list(result.get("cases", [])) + historical_cases(source_phase)
    result["audit_extension"] = {
        "schema_version": "tracen-replay/source-phase-regrade-extension-v1",
        "source_phase_path": relative(SOURCE_PHASE),
        "source_phase_sha256": digest(SOURCE_PHASE),
        "historical_case_count": len(historical_cases(source_phase)),
        "purpose": "evaluate five producer-accepted historical fields alongside the frozen 15 numeric comparisons",
    }
    return result


def producer_entries(source_phase: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for case in source_phase.get("target_comparisons", []):
        for index, unit in enumerate(case.get("contributions", [])):
            result[f"target:{case['id']}:{index}"] = {
                "comparison_id": case["id"],
                "unit_index": index,
                "source_amount": unit.get("source_amount"),
                "accepted_value": unit.get("accepted_value"),
                "source_proof_status": unit.get("source_proof_status"),
                "basis": unit.get("producer_source_proof", {}).get("basis"),
                "event_id": unit.get("event_id"),
                "field": unit.get("field"),
                "producer_matches_source": unit.get("accepted_matches_source"),
            }
    for item in source_phase.get("historical_regressions", []):
        result[f"historical:{item['source_id']}:0"] = {
            "comparison_id": item["source_id"],
            "unit_index": 0,
            "source_amount": item.get("source_amount"),
            "accepted_value": item.get("accepted_value"),
            "source_proof_status": item.get("source_proof_status"),
            "basis": item.get("producer_source_proof", {}).get("basis"),
            "event_id": item.get("event_id"),
            "field": item.get("field"),
            "producer_matches_source": item.get("accepted_matches_source"),
        }
    return result


def result_units(result: dict[str, Any], case_id: str) -> list[dict[str, Any]]:
    for case in result.get("cases", []):
        if case.get("case_id") == case_id:
            return case.get("units", [])
    return []


def mismatch_reason(unit: dict[str, Any]) -> str:
    verdict = unit.get("verdict")
    if verdict == "correct_direct":
        return "independent_evaluator_accepts"
    if verdict == "correct_derived":
        return "evaluator_classifies_as_derived"
    if verdict == "missing":
        return "candidate_missing_after_field_evidence_time_join"
    if verdict == "ambiguous_attribution":
        return "candidate_joined_but_canonical_conflict_or_ownership_remains"
    if verdict == "unobservable":
        return "candidate_joined_without_independent_source_or_ownership_proof"
    if verdict == "wrong_amount":
        return "joined_candidate_amount_differs_from_frozen_source_amount"
    if verdict == "wrong_turn":
        return "joined_candidate_has_wrong_turn_owner"
    if verdict == "duplicate":
        return "multiple_candidates_share_source_join"
    return f"evaluator_verdict:{verdict}"


def interface_diagnosis(
    unit: dict[str, Any], phase_probe: dict[str, Any]
) -> str:
    """Name the narrow producer/evaluator boundary exposed by a verdict."""

    if unit.get("verdict") == "correct_direct":
        return "independent_evaluator_accepts"
    phase_status = phase_probe.get("status")
    if phase_status == "nested_phase_evidence_missing_from_action_identity_evidence":
        return "source_phase_proof_not_admitted_missing_action_identity_paths"
    if phase_status == "nested_phase_evidence_not_anchored_to_event_evidence":
        return "source_phase_proof_not_admitted_event_anchor_paths"
    if phase_status == "declared_phase_schema_or_temporal_constraint_rejected":
        return "source_phase_envelope_rejected_by_evaluator_schema_or_constraints"
    if unit.get("ownership_status") in {"ambiguous", "unobservable"}:
        return "source_amount_joined_but_turn_ownership_is_ambiguous"
    if unit.get("conflict_state"):
        return "source_phase_or_amount_joined_but_raw_conflict_remains"
    return mismatch_reason(unit)


def phase_interface_probe(
    event: dict[str, Any] | None,
    field: str | None,
    recording: str,
) -> dict[str, Any]:
    """Explain why a declared phase proof did or did not validate."""

    if not isinstance(event, dict) or not isinstance(field, str):
        return {"status": "event_or_field_unavailable"}
    phase_map = event.get("gain_phase_candidates")
    phase = phase_map.get(field) if isinstance(phase_map, dict) else None
    if not isinstance(phase, dict):
        return {"status": "no_declared_gain_phase"}
    resolution = phase.get("source_resolution")
    if not isinstance(resolution, dict):
        return {
            "status": "declared_phase_has_no_source_resolution",
            "phase_basis": phase.get("basis"),
        }
    proof_items = [
        item
        for key in (
            "full_observations",
            "component_observations",
            "ignored_unproven_observations",
        )
        for item in resolution.get(key, [])
        if isinstance(item, dict) and isinstance(item.get("evidence"), str)
    ]
    proof_paths = {
        numeric_evaluator._normalise_evidence(item["evidence"], recording)
        for item in proof_items
    }
    action_paths = numeric_evaluator._source_paths(
        event.get("action_identity_evidence"), recording
    )
    event_paths = numeric_evaluator._source_paths(event.get("evidence"), recording)
    field_evidence = event.get("field_evidence")
    if isinstance(field_evidence, dict):
        event_paths.update(
            numeric_evaluator._source_paths(field_evidence.get(field), recording)
        )
    missing_action = sorted(proof_paths - action_paths)
    unanchored = sorted(
        path for path in proof_paths
        if not numeric_evaluator._evidence_matches({path}, event_paths)
    )
    validated = numeric_evaluator._phase_resolution_amount(
        event, field, recording
    )
    if validated is not None:
        failure = "validated_by_evaluator"
    elif missing_action:
        failure = "nested_phase_evidence_missing_from_action_identity_evidence"
    elif unanchored:
        failure = "nested_phase_evidence_not_anchored_to_event_evidence"
    else:
        failure = "declared_phase_schema_or_temporal_constraint_rejected"
    return {
        "status": failure,
        "validated_amount": validated[1] if validated is not None else None,
        "phase_basis": phase.get("basis"),
        "resolution_basis": resolution.get("basis"),
        "phase_accepted": phase.get("accepted"),
        "proof_path_count": len(proof_paths),
        "action_identity_path_count": len(action_paths),
        "event_anchor_path_count": len(event_paths),
        "missing_action_identity_paths": missing_action,
        "unanchored_phase_paths": unanchored,
        "event_conflicting_readings": event.get("conflicting_readings", {}).get(field, [])
        if isinstance(event.get("conflicting_readings"), dict)
        else [],
    }


def case_diagnostics(
    evaluation: dict[str, Any],
    producer: dict[str, dict[str, Any]],
    prefix: str,
    events_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for case in evaluation.get("cases", []):
        case_id = case.get("case_id")
        for index, unit in enumerate(case.get("units", [])):
            source_key = f"{prefix}:{case_id}:{index}"
            producer_row = producer.get(source_key, {})
            matched = unit.get("matched_candidates", [])
            probe = phase_interface_probe(
                events_by_id.get(producer_row.get("event_id")),
                producer_row.get("field"),
                case.get("recording") or "",
            )
            rows.append({
                "comparison_id": case_id,
                "unit_index": index,
                "recording": case.get("recording"),
                "field": case.get("field"),
                "source_amount": unit.get("source_amount"),
                "producer_accepted_value": producer_row.get("accepted_value"),
                "producer_source_proof_status": producer_row.get("source_proof_status"),
                "producer_basis": producer_row.get("basis"),
                "producer_event_id": producer_row.get("event_id"),
                "evaluator_verdict": unit.get("verdict"),
                "evaluator_passed": unit.get("passed"),
                "evaluator_predicted_amount": unit.get("predicted_amount"),
                "evaluator_amount_status": unit.get("amount_status"),
                "evaluator_ownership_status": unit.get("ownership_status"),
                "evaluator_direct_evidence": unit.get("direct_evidence"),
                "evaluator_direct_evidence_basis": unit.get("direct_evidence_basis"),
                "evaluator_conflict_state": unit.get("conflict_state", []),
                "matched_candidate_count": len(matched),
                "matched_candidates": matched,
                "phase_interface_probe": probe,
                "interface_diagnosis": interface_diagnosis(unit, probe),
            })
    return rows


def markdown(document: dict[str, Any]) -> str:
    lines = [
        "# Source-phase numeric regrade",
        "",
        "This intermediate audit replaces only the training-event list with events recomputed from the existing source-bound report readings, then invokes the current `evaluate_final_numeric_cases.py` evaluator. Original reports and frozen labels remain untouched. It is not a fresh parser replay or final acceptance grade.",
        "",
        f"- Generated: `{document['generated_at_utc']}`",
        f"- Evaluator SHA256: `{document['integrity']['evaluator_sha256']}`",
        f"- Source-phase verification SHA256: `{document['integrity']['source_phase_sha256']}`",
        f"- Numeric causes SHA256: `{document['integrity']['numeric_causes_sha256']}`",
        "",
        "## Projection inputs",
        "",
        "| Run | Original report SHA256 | Projection SHA256 | Source SHA256 | Readings | Recomputed training events |",
        "|---|---|---|---|---:|---:|",
    ]
    for run, info in document["runs"].items():
        lines.append(
            f"| {run} | `{info['original_report']['sha256']}` | `{info['projection']['sha256']}` | `{info['source_sha256']}` | {info['reading_count']} | {info['recomputed_training_event_count']} |"
        )
    lines += [
        "",
        "## Numeric comparison outcomes",
        "",
        "| Comparison | Unit | Field | Source | Producer | Evaluator | Predicted | Ownership | Conflict | Phase interface | Diagnosis |",
        "|---|---:|---|---:|---:|---|---:|---|---|---|---|",
    ]
    for row in document["diagnostics"]:
        lines.append(
            f"| `{row['comparison_id']}` | {row['unit_index']} | {row['field']} | {row['source_amount']} | {row['producer_accepted_value']} | `{row['evaluator_verdict']}` | {row['evaluator_predicted_amount']} | `{row['evaluator_ownership_status']}` | `{','.join(row['evaluator_conflict_state'])}` | `{row['phase_interface_probe'].get('status')}` | {row['interface_diagnosis']} |"
        )
    lines += [
        "",
        "## Aggregate counts",
        "",
        f"- Producer source-phase accepted: `{document['aggregate']['producer_accepted_numeric_comparisons']}/15` numeric comparisons and `{document['aggregate']['producer_accepted_historical_fields']}/5` historical fields.",
        f"- Independent evaluator `correct_direct`: `{document['aggregate']['evaluator_correct_direct_numeric_comparisons']}/15` numeric comparisons, `{document['aggregate']['evaluator_correct_direct_historical_fields']}/5` historical fields, and `{document['aggregate']['evaluator_correct_direct_contribution_units']}/21` contribution units.",
        f"- Remaining evaluator `ambiguous_attribution`: `{document['aggregate']['evaluator_ambiguous_contribution_units']}/21` contribution units; see the phase interface probe and diagnosis columns for the boundary cause.",
        "",
    ]
    for run, info in document["runs"].items():
        lines.append(f"- `{run}` original numeric counts: `{info['original_numeric_counts']}`")
        lines.append(f"- `{run}` recomputed numeric counts: `{info['recomputed_numeric_counts']}`")
        lines.append(f"- `{run}` recomputed extended counts (five historical fields included): `{info['recomputed_extended_counts']}`")
    lines += [
        "",
        "## Interpretation",
        "",
        "- `producer_source_proof_status=accepted_source_observation` records the source-bound producer result from `source-phase-verification-v1.json`.",
        "- Evaluator verdicts are independent joins over report-owned event/readings evidence and ownership metadata. A non-pass is retained as an interface diagnosis; no evaluator rule or source proof was relaxed.",
        "- Historical paired statuses are preserved in the source-phase artifact; this regrade checks their current recomputed event fields as additional evaluator cases.",
        "- Repeated crop views remain inside one contribution unit. Aggregate contribution counts are not inflated by evidence-frame multiplicity.",
        "- Full-history accuracy remains unmeasured; these projections are bounded to the frozen 15 comparisons and five historical fields.",
        "",
        "## Command",
        "",
        f"- `{document['command']}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    source_phase = read(SOURCE_PHASE)
    numeric = read(NUMERIC_CAUSES)
    extended = extended_causes(numeric, source_phase)
    producer = producer_entries(source_phase)
    runs: dict[str, dict[str, Any]] = {}
    all_diagnostics: list[dict[str, Any]] = []

    for run, run_info in source_phase["runs"].items():
        original_path = report_path(run_info["report"]["path"])
        original = read(original_path)
        projection, recomputed = replace_training_events(original)
        projection["phase_regrade_projection"].update({
            "source_report_path": relative(original_path),
            "source_report_sha256": digest(original_path),
            "source_phase_verification_path": relative(SOURCE_PHASE),
            "source_phase_verification_sha256": digest(SOURCE_PHASE),
            "input_manifest_path": run_info["input_manifest"]["path"],
            "input_manifest_sha256": run_info["input_manifest"]["sha256"],
            "source_sha256": run_info["source"]["sha256"],
        })
        projection_path = OUT_DIR / run / "recomputed-training-events-report.json"
        projection_path.parent.mkdir(parents=True, exist_ok=True)
        projection_path.write_text(
            json.dumps(projection, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        projection_hash = digest(projection_path)

        original_grade = evaluate_report(
            numeric, original, report_path=original_path,
            reference_path=NUMERIC_CAUSES, recording=run,
        )
        recomputed_grade = evaluate_report(
            numeric, projection, report_path=projection_path,
            reference_path=NUMERIC_CAUSES, recording=run,
        )
        recomputed_extended = evaluate_report(
            extended, projection, report_path=projection_path,
            reference_path=NUMERIC_CAUSES, recording=run,
        )
        events_by_id = {
            event["id"]: event
            for event in projection["gameplay_tracking"].get("events", [])
            if isinstance(event, dict) and isinstance(event.get("id"), str)
        }
        # The extended evaluator returns both frozen and historical cases. Use
        # the matching producer namespace for each group without changing any
        # evaluator verdict.
        target_ids = {case["id"] for case in numeric.get("cases", [])}
        historical_ids = {
            item["source_id"]
            for item in source_phase.get("historical_regressions", [])
        }
        diagnostics = [
            row for row in case_diagnostics(
                recomputed_extended, producer, "target", events_by_id
            )
            if row["comparison_id"] in target_ids
        ] + [
            row for row in case_diagnostics(
                recomputed_extended, producer, "historical", events_by_id
            )
            if row["comparison_id"] in historical_ids
        ]
        all_diagnostics.extend(diagnostics)
        runs[run] = {
            "original_report": bound_file(original_path),
            "projection": bound_file(projection_path),
            "source_sha256": run_info["source"]["sha256"],
            "input_manifest": {
                "path": run_info["input_manifest"]["path"],
                "sha256": run_info["input_manifest"]["sha256"],
            },
            "reading_count": len(original["gameplay_tracking"]["readings"]),
            "recomputed_training_event_count": len(recomputed),
            "original_numeric_counts": original_grade["counts"],
            "recomputed_numeric_counts": recomputed_grade["counts"],
            "recomputed_extended_counts": recomputed_extended["counts"],
            "original_numeric_passed": original_grade["passed"],
            "recomputed_numeric_passed": recomputed_grade["passed"],
            "recomputed_extended_passed": recomputed_extended["passed"],
        }

    evaluator_path = ROOT / "scripts" / "evaluate_final_numeric_cases.py"
    generator_path = Path(__file__).resolve()
    target_ids = {case["id"] for case in numeric.get("cases", [])}
    historical_ids = {
        item["source_id"]
        for item in source_phase.get("historical_regressions", [])
    }
    numeric_rows = [row for row in all_diagnostics if row["comparison_id"] in target_ids]
    historical_rows = [
        row for row in all_diagnostics if row["comparison_id"] in historical_ids
    ]

    def comparison_count(rows: list[dict[str, Any]], verdict: str) -> int:
        by_case: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_case.setdefault(row["comparison_id"], []).append(row)
        return sum(
            bool(units) and all(row["evaluator_verdict"] == verdict for row in units)
            for units in by_case.values()
        )

    document: dict[str, Any] = {
        "schema_version": "tracen-replay/source-phase-regrade-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "numeric_comparisons": len(numeric.get("cases", [])),
            "numeric_contribution_units": sum(
                len(case.get("contributions", [])) or 1
                for case in numeric.get("cases", [])
            ),
            "historical_fields": len(source_phase.get("historical_regressions", [])),
            "projection_kind": "existing_report_readings_with_current_training_events",
            "fresh_parser_rebuild": False,
            "original_reports_preserved": True,
        },
        "integrity": {
            "source_phase_path": relative(SOURCE_PHASE),
            "source_phase_sha256": digest(SOURCE_PHASE),
            "numeric_causes_path": relative(NUMERIC_CAUSES),
            "numeric_causes_sha256": digest(NUMERIC_CAUSES),
            "evaluator_path": relative(evaluator_path),
            "evaluator_sha256": digest(evaluator_path),
            "generator_path": relative(generator_path),
            "generator_sha256": digest(generator_path),
        },
        "aggregate": {
            "producer_accepted_numeric_comparisons": len(target_ids),
            "producer_accepted_historical_fields": len(historical_ids),
            "evaluator_correct_direct_numeric_comparisons": comparison_count(
                numeric_rows, "correct_direct"
            ),
            "evaluator_correct_direct_historical_fields": comparison_count(
                historical_rows, "correct_direct"
            ),
            "evaluator_correct_direct_contribution_units": sum(
                row["evaluator_verdict"] == "correct_direct"
                for row in all_diagnostics
            ),
            "evaluator_ambiguous_contribution_units": sum(
                row["evaluator_verdict"] == "ambiguous_attribution"
                for row in all_diagnostics
            ),
        },
        "runs": runs,
        "diagnostics": all_diagnostics,
        "command": ".\\.venv\\Scripts\\python.exe scripts\\regrade_source_phase_numeric.py",
        "limitations": [
            "The projection uses existing candidate-report readings and source paths; it does not rerun video/OCR parsing.",
            "Producer source-phase acceptance and evaluator direct credit are recorded separately.",
            "No balance, expected amount, or state residual is supplied to candidate selection.",
            "Full-history accuracy remains unmeasured.",
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_json = OUT_DIR / "source-phase-regrade-v1.json"
    output_md = OUT_DIR / "source-phase-regrade-v1.md"
    output_json.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.write_text(markdown(document), encoding="utf-8")
    print(json.dumps({
        "json": relative(output_json),
        "markdown": relative(output_md),
        "scope": document["scope"],
        "runs": {
            run: {
                "original_numeric_counts": info["original_numeric_counts"],
                "recomputed_numeric_counts": info["recomputed_numeric_counts"],
                "recomputed_extended_counts": info["recomputed_extended_counts"],
            }
            for run, info in runs.items()
        },
    }, sort_keys=True))


if __name__ == "__main__":
    main()
