"""Build a compact, source-bound taxonomy for the frozen semantic baseline."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


BASELINE = Path(".local/final-reliability-v1/before-frozen-semantic-grade-v2.json")
REFERENCE_DIR = Path(".local/final-reliability-v1/source-references")
SELECTION = Path(".local/final-reliability-v1/source-case-selection.json")
FREEZE = Path(".local/final-reliability-v1/reference-freeze-v2.json")
SOURCE_ADJUDICATION = Path(".local/final-reliability-v1/source-adjudication-v1.json")
SCHEMA = "tracen-replay/final-reliability-failure-taxonomy-v1"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_index() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    rows: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for path in sorted(REFERENCE_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        document = _read(path)
        hashes[path.name] = _sha(path)
        for case in document.get("cases", []):
            for observation in case.get("observations", []):
                rows[observation["id"]] = observation
    return rows, hashes


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    keep = ("kind", "field", "channel", "training_option", "training_name",
            "name", "result", "amount", "value", "values", "caps")
    return {key: payload[key] for key in keep if key in payload}


def _classify(source: dict[str, Any], result: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    """Return primary class, reason, shared fix, and secondary causes.

    This is a review taxonomy, not a grader.  It deliberately uses the frozen
    source label, result fields, and canonical prediction identity only; no
    amount or residual is used to choose a source occurrence.
    """
    source_payload = source.get("payload") or {}
    kind = source_payload.get("kind")
    phase = source.get("phase")
    source_id = result["source_id"]
    fields = set(result.get("fields") and [
        field["field"] for field in result["fields"]
        if field.get("status") != "correct"
    ] or [])
    secondary: list[str] = []
    if "transient" in source_id.casefold():
        secondary.append("source_frame_contains_partial_glyph_reading")

    if result.get("status") == "ambiguous":
        return ("legitimate_uncertainty", "explicit_source_or_turn_ambiguity",
                "Keep the observation and its candidate turns/uncertainty; do not certify attribution.", secondary)
    if fields and any(field.startswith("/caps/") for field in fields):
        if result.get("turn_status") == "ambiguous":
            secondary.append("turn_attribution_ambiguous")
        return ("adapter_projection", "same_frame_caps_not_projected",
                "Consume accepted stat_caps only from a reading whose evidence is owned by the exact state checkpoint.", secondary)
    if kind == "skill" and result.get("status") == "partial":
        return ("legitimate_uncertainty", "partial_transaction_identity_or_balance",
                "Preserve visible names and completeness while leaving unreadable identity, price, and balance fields unknown.", secondary)
    if kind == "infirmary" and result.get("status") == "partial":
        return ("adapter_projection", "explicit_recovery_result_not_projected",
                "Project success and the confirmation-to-result interval only from the accepted recovery receipt and result evidence.", secondary)
    if phase == "preview":
        return ("canonical_promotion", "preview_effect_not_promoted",
                "Expose preview facts through an explicitly accepted preview observation; never treat a preview as applied.", secondary)
    if kind == "race_reward":
        return ("actual_recognition", "canonical_items_snapshot_disagrees_with_source",
                "Recheck the readable Items section and keep bonus quantities in their separate section.", secondary)
    if kind == "race" and phase == "observed":
        return ("canonical_promotion", "results_only_race_phase_not_preserved",
                "Retain the observed results phase and its fields without converting it into a committed click receipt.", secondary)
    if kind == "training" and "/result" in fields:
        return ("actual_recognition", "training_outcome_not_resolved",
                "Resolve success/failure from the accepted outcome evidence; do not infer it from a state residual.", secondary)
    if kind == "training" and "/training_name" in fields:
        return ("canonical_promotion", "training_identity_not_promoted",
                "Carry a same-action accepted training_name and its own identity evidence into the committed action.", secondary)
    if kind in {"mood_status", "hype_status", "condition_removed", "item_reward",
                "event_choice", "skill_hint_change", "training_modifier_change",
                "stat_cap_change", "lesson"}:
        return ("canonical_promotion", "source_effect_kind_not_in_canonical_observations",
                "Add a typed accepted observation with independent evidence; retain raw candidates without grading them as facts.", secondary)
    if kind == "race" or kind == "outing" or kind == "rest":
        return ("actual_recognition", "accepted_action_outside_source_time_window",
                "Retain the canonical action but repair its independently evidenced interval/phase before matching.", secondary)
    if phase == "applied" and kind in {"stat_change", "performance_change"}:
        return ("actual_recognition", "accepted_effect_outside_source_time_window",
                "Preserve the field-specific evidence timestamp and recover the source-readable effect occurrence.", secondary)
    if kind == "state":
        return ("actual_recognition", "state_not_recovered_at_source_interval",
                "Recover the readable state endpoint and preserve field-level timing and ownership uncertainty.", secondary)
    if phase == "applied":
        return ("actual_recognition", "applied_effect_not_recovered",
                "Recover the typed applied effect from its own evidence; do not manufacture it from a net balance.", secondary)
    return ("actual_recognition", "accepted_core_observation_missing",
            "Recover or explicitly preserve the source-readable core fact with its evidence and time limits.", secondary)


def _partial_glyph_findings(
    baseline_rows: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Record the frozen T63 partial-glyph labels for separate adjudication.

    The finding is advisory provenance for review.  It deliberately does not
    remove or rewrite a frozen observation and cannot change a baseline grade.
    The dense frame contains clipped glyphs from the later stable result; it
    must not be treated as three additional causal awards.
    """
    findings: list[dict[str, Any]] = []
    for path in sorted(REFERENCE_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        document = _read(path)
        image_hashes = document.get("image_sha256")
        image_hashes = image_hashes if isinstance(image_hashes, Mapping) else {}
        run = document.get("run")
        for case in document.get("cases", []):
            if not isinstance(case, Mapping):
                continue
            observations = [
                observation for observation in case.get("observations", [])
                if isinstance(observation, Mapping)
            ]
            for observation in observations:
                source_id = observation.get("id")
                payload = observation.get("payload")
                if not isinstance(source_id, str) or not source_id.startswith("t063-transient-"):
                    continue
                if not isinstance(payload, Mapping) or payload.get("kind") != "stat_change":
                    continue
                if observation.get("phase") != "applied":
                    continue
                field = payload.get("field")
                start = observation.get("start_ms")
                if not isinstance(field, str) or not isinstance(start, (int, float)):
                    continue
                stable_followup_candidates = []
                for later in observations:
                    later_payload = later.get("payload")
                    later_start = later.get("start_ms")
                    if (
                        not isinstance(later_payload, Mapping)
                        or later_payload.get("kind") != "stat_change"
                        or later_payload.get("field") != field
                        or later.get("phase") != "applied"
                        or not isinstance(later_start, (int, float))
                        or later_start <= start
                    ):
                        continue
                    stable_followup_candidates.append({
                        "source_ref": later.get("id"),
                        "interval_ms": [later.get("start_ms"), later.get("end_ms")],
                        "payload": dict(later_payload),
                    })
                stable_followups = (
                    [min(stable_followup_candidates,
                         key=lambda row: row["interval_ms"][0])]
                    if stable_followup_candidates else []
                )
                if not stable_followups:
                    continue
                evidence = observation.get("evidence")
                evidence = evidence if isinstance(evidence, list) else []
                proof_hashes = {
                    evidence_path: image_hashes[evidence_path]
                    for evidence_path in evidence
                    if isinstance(evidence_path, str)
                    and isinstance(image_hashes.get(evidence_path), str)
                }
                findings.append({
                    "run": run,
                    "case_id": case.get("case_id"),
                    "source_ref": source_id,
                    "classification": "source_label_semantic_issue",
                    "issue": "partial_glyph_read_of_stable_result",
                    "source_phase": observation.get("phase"),
                    "source_interval_ms": [observation.get("start_ms"), observation.get("end_ms")],
                    "source_payload": dict(payload),
                    "evidence": evidence,
                    "evidence_sha256": proof_hashes,
                    "stable_followups": stable_followups,
                    "baseline_grades": baseline_rows.get(source_id, []),
                    "metric_treatment": {
                        "frozen_labels_unchanged": True,
                        "original_scores_preserved": True,
                        "original_denominator_preserved": True,
                        "recommendation": "retain as ambiguous display evidence in the separate source-adjudication artifact; do not add a causal effect or alter the frozen denominator",
                    },
                })
    return findings


def build(baseline_path: Path = BASELINE) -> dict[str, Any]:
    baseline = _read(baseline_path)
    labels, reference_hashes = _source_index()
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    semantic_turn_ambiguities = []
    baseline_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    for run in baseline.get("runs", []):
        for case in run.get("cases", []):
            for result in case.get("results", []):
                source_id = result.get("source_id")
                if isinstance(source_id, str):
                    baseline_rows[source_id].append({
                        "run": run.get("run"),
                        "case_id": case.get("case_id"),
                        "status": result.get("status"),
                        "prediction_ref": result.get("prediction_id"),
                    })
                if result.get("status") == "correct" and result.get("turn_status") == "ambiguous":
                    semantic_turn_ambiguities.append({
                        "run": run.get("run"),
                        "case_id": case.get("case_id"),
                        "source_ref": result["source_id"],
                        "prediction_ref": result.get("prediction_id"),
                        "source_interval_ms": result.get("source_interval_ms"),
                        "candidate_turn_ids": result.get("prediction_candidate_turn_ids", []),
                    })
                    continue
                if result.get("status") == "correct":
                    continue
                source = labels.get(result["source_id"], {})
                classification, reason, fix, secondary = _classify(source, result)
                row = {
                    "run": run.get("run"),
                    "case_id": case.get("case_id"),
                    "source_ref": result["source_id"],
                    "prediction_ref": result.get("prediction_id"),
                    "source_interval_ms": result.get("source_interval_ms"),
                    "source_phase": result.get("phase"),
                    "source_payload": _payload_summary(source.get("payload") or {}),
                    "baseline_status": result.get("status"),
                    "turn_status": result.get("turn_status"),
                    "noncorrect_fields": sorted(
                        field["field"] for field in result.get("fields", [])
                        if field.get("status") != "correct"
                    ),
                }
                if secondary:
                    row["secondary_causes"] = secondary
                groups[(classification, reason, fix)].append(row)
                total += 1

    ordered_groups = []
    for (classification, reason, fix), rows in sorted(groups.items()):
        ordered_groups.append({
            "classification": classification,
            "reason": reason,
            "shared_fix": fix,
            "rows": rows,
        })
    counts = Counter()
    for group in ordered_groups:
        counts[group["classification"]] += len(group["rows"])
    partial_glyph_findings = _partial_glyph_findings(baseline_rows)
    return {
        "schema_version": SCHEMA,
        "generated_on": "2026-09-11",
        "scope": "Noncorrect source-core rows in the frozen v2 semantic baseline; this taxonomy does not change gold labels or grading scope.",
        "inputs": {
            "baseline_path": str(baseline_path),
            "baseline_sha256": _sha(baseline_path),
            "freeze_manifest_path": str(FREEZE),
            "freeze_manifest_sha256": _sha(FREEZE),
            "selection_path": str(SELECTION),
            "selection_sha256": _sha(SELECTION),
            "source_reference_sha256": reference_hashes,
        },
        "row_counts": {
            "baseline_noncorrect": total,
            "by_classification": dict(sorted(counts.items())),
        },
        "classification_definitions": {
            "actual_recognition": "The source-readable fact has no accepted exact canonical occurrence, or the accepted occurrence is outside the source field/time evidence.",
            "canonical_promotion": "Raw or typed source information is present without an accepted canonical observation/action field; raw candidates cannot certify it.",
            "adapter_projection": "An accepted canonical field already exists but report-to-observation adaptation omitted it without borrowing another occurrence.",
            "legitimate_uncertainty": "The source or accepted transaction explicitly leaves identity, amount, balance, or ownership unresolved.",
        },
        "groups": ordered_groups,
        "semantic_correct_turn_ambiguity": semantic_turn_ambiguities,
        "source_semantic_findings": partial_glyph_findings,
        "source_semantic_finding_note": "Advisory findings preserve the frozen source labels and original scores. T63 transient rows are partial glyph readings of the later stable result; the separate source-adjudication artifact records this without adding causal effects or changing the denominator.",
        "source_adjudication_artifact": {
            "path": str(SOURCE_ADJUDICATION),
            "sha256": _sha(SOURCE_ADJUDICATION) if SOURCE_ADJUDICATION.is_file() else None,
        },
        "turn_attribution_ambiguity_note": "Rows with correct semantic values but multiple candidate turns are retained separately by the frozen grade; a semantic failure group may carry turn_attribution_ambiguous as a secondary cause.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--output", type=Path,
                        default=Path(".local/final-reliability-v1/semantic-failure-taxonomy-v1.json"))
    args = parser.parse_args()
    output = build(args.baseline)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["row_counts"], sort_keys=True))


if __name__ == "__main__":
    main()
