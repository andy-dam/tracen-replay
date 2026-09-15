"""Regrade source-phase cases after a full cached-reading assembly.

This is an intermediate audit utility.  It starts from the immutable
candidate reports used by the source-phase audit, keeps their source-bound
reading rows, and calls the current ``full_recording.assemble`` so that event
ownership, action receipts, the turn ledger, and causal accounting are all
rebuilt together.  It does not decode video or rerun OCR, and it does not
modify the original reports or the earlier partial event replacement.

The resulting evaluator grade is deliberately separate from the producer's
source-phase acceptance.  In particular, the script records the exact phase
proof, action-identity, and event-anchor paths that remain absent or
unanchored after full assembly; it never adds identity evidence to make a
case pass.
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

from tracen_replay.full_recording import assemble
from scripts import evaluate_final_numeric_cases as numeric_evaluator
from scripts.regrade_source_phase_numeric import (
    AUDIT_DIR,
    NUMERIC_CAUSES,
    SOURCE_PHASE,
    bound_file,
    case_diagnostics,
    digest,
    extended_causes,
    historical_cases,
    interface_diagnosis,
    phase_interface_probe,
    producer_entries,
    read,
    relative,
)


OUT_DIR = AUDIT_DIR / "source-phase-full-assembly-v1"
PARTIAL_REGRADE = AUDIT_DIR / "source-phase-regrade-v1" / "source-phase-regrade-v1.json"


def _source_inputs(report: dict[str, Any]) -> dict[str, Any]:
    """Return only source-bound auxiliary inputs already carried by a report."""

    gameplay = report.get("gameplay_tracking", {})
    if not isinstance(gameplay, dict):
        gameplay = {}
    choice_source = gameplay.get("event_choice_observations")
    inspected = report.get("choice_inspection")
    if isinstance(inspected, dict) and isinstance(inspected.get("observations"), list):
        # ``inspect_choices.load`` emits scalar evidence paths, which is the
        # shape consumed by choice_evidence.reconstruct.  Keep the adapter's
        # complete source envelope separately below.
        choice_observations = deepcopy(inspected["observations"])
        choice_origin = "choice_inspection.observations"
    elif isinstance(choice_source, dict):
        # event_choice_observations stores one-element evidence lists because
        # its rows can carry multiple source proofs.  The legacy temporal
        # choice consumer accepts one scalar path; retain the complete rows in
        # event_choice_observations and pass the first (and in these reports
        # only) physical path to reconstruction.
        choice_observations = []
        for raw in choice_source.get("observations", []):
            if not isinstance(raw, dict):
                continue
            row = deepcopy(raw)
            evidence = row.get("evidence")
            if isinstance(evidence, list):
                if not evidence:
                    continue
                row["source_evidence"] = deepcopy(evidence)
                row["evidence"] = evidence[0]
            if isinstance(row.get("evidence"), str):
                choice_observations.append(row)
        choice_origin = "gameplay_tracking.event_choice_observations.scalarized"
    else:
        choice_observations = []
        choice_origin = "none"
    if isinstance(choice_source, dict):
        committed_choices = choice_source.get("committed_choices", [])
        event_choice_observations = deepcopy(choice_source)
    else:
        # The third report predates the event-choice adapter.  Its completed
        # choices are still safe as committed inputs; no preview is promoted
        # into an observation stream.
        committed_choices = gameplay.get("dialogue_choices", [])
        event_choice_observations = None
        choice_origin = "gameplay_tracking.dialogue_choices_fallback" if not choice_observations else choice_origin
    races = gameplay.get("race_reward_observations", [])
    if not isinstance(races, list):
        races = []
    hints = gameplay.get("hint_card_observations", [])
    if not isinstance(hints, list):
        hints = []
    if not isinstance(choice_observations, list):
        choice_observations = []
    if not isinstance(committed_choices, list):
        committed_choices = []
    return {
        "choice_observations": deepcopy(choice_observations),
        "committed_choices": deepcopy(committed_choices),
        "event_choice_observations": event_choice_observations,
        "race_reward_observations": deepcopy(races),
        "hint_card_observations": deepcopy(hints),
        "choice_origin": choice_origin,
    }


def assemble_cached_readings(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run full assembly against report-owned readings and auxiliary inputs."""

    gameplay = report.get("gameplay_tracking")
    if not isinstance(gameplay, dict) or not isinstance(gameplay.get("readings"), list):
        raise ValueError("candidate report has no gameplay_tracking.readings list")
    inputs = _source_inputs(report)
    projection = deepcopy(report)
    context = projection.get("evaluation_context")
    source_root = (
        context.get("evidence_root")
        if isinstance(context, dict) and context.get("evidence_root")
        else None
    )
    assembled = assemble(
        projection,
        deepcopy(gameplay["readings"]),
        inputs["choice_observations"],
        inputs["race_reward_observations"],
        inputs["hint_card_observations"],
        committed_choices=inputs["committed_choices"],
        event_choice_observations=inputs["event_choice_observations"],
        source_root=source_root,
    )
    return assembled, inputs


def _proof_paths(event: dict[str, Any] | None, field: str | None, recording: str) -> dict[str, list[str]]:
    """Expose concrete proof path sets for the report and markdown audit."""

    if not isinstance(event, dict) or not isinstance(field, str):
        return {"phase": [], "action_identity": [], "event_anchor": []}
    phase_map = event.get("gain_phase_candidates")
    phase = phase_map.get(field) if isinstance(phase_map, dict) else None
    resolution = phase.get("source_resolution") if isinstance(phase, dict) else None
    proof_items = []
    if isinstance(resolution, dict):
        for key in ("full_observations", "component_observations", "ignored_unproven_observations"):
            items = resolution.get(key, [])
            if isinstance(items, list):
                proof_items.extend(item for item in items if isinstance(item, dict))
    phase_paths = sorted({
        numeric_evaluator._normalise_evidence(item["evidence"], recording)
        for item in proof_items
        if isinstance(item.get("evidence"), str)
    })
    action_paths = sorted(numeric_evaluator._source_paths(
        event.get("action_identity_evidence"), recording
    ))
    event_paths = set(numeric_evaluator._source_paths(event.get("evidence"), recording))
    field_evidence = event.get("field_evidence")
    if isinstance(field_evidence, dict):
        event_paths.update(numeric_evaluator._source_paths(field_evidence.get(field), recording))
    return {
        "phase": phase_paths,
        "action_identity": action_paths,
        "event_anchor": sorted(event_paths),
    }


def _attach_path_comparisons(
    diagnostics: list[dict[str, Any]],
    partial: dict[str, Any] | None,
    events_by_id: dict[str, dict[str, Any]],
    partial_events_by_run: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Add full-vs-partial proof path differences without changing verdicts."""

    old_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    if isinstance(partial, dict):
        for row in partial.get("diagnostics", []):
            if not isinstance(row, dict):
                continue
            key = (str(row.get("recording")), str(row.get("comparison_id")), str(row.get("unit_index")))
            old_by_key[key] = row
    output = []
    for row in diagnostics:
        key = (str(row.get("recording")), str(row.get("comparison_id")), str(row.get("unit_index")))
        old = old_by_key.get(key, {})
        full_probe = row.get("phase_interface_probe", {})
        event = events_by_id.get(row.get("producer_event_id"))
        full_paths = _proof_paths(event, row.get("field"), row.get("recording") or "")
        partial_probe = old.get("phase_interface_probe", {})
        partial_event = (
            (partial_events_by_run or {}).get(str(row.get("recording")), {})
            .get(row.get("producer_event_id"))
        )
        partial_paths = _proof_paths(partial_event, row.get("field"), row.get("recording") or "")
        # Older partial artifacts did not expose path sets.  Preserve any
        # already recorded sets when a direct event lookup is unavailable.
        if not any(partial_paths.values()):
            partial_paths = {
                "phase": sorted(partial_probe.get("phase_proof_paths", [])),
                "action_identity": sorted(partial_probe.get("action_identity_paths", [])),
                "event_anchor": sorted(partial_probe.get("event_anchor_paths", [])),
            }
        enriched = deepcopy(row)
        enriched["phase_interface_probe"] = deepcopy(full_probe)
        enriched["phase_interface_probe"].update({
            "phase_proof_paths": full_paths["phase"],
            "action_identity_paths": full_paths["action_identity"],
            "event_anchor_paths": full_paths["event_anchor"],
        })
        enriched["partial_comparison"] = {
            "partial_evaluator_verdict": old.get("evaluator_verdict"),
            "full_evaluator_verdict": row.get("evaluator_verdict"),
            "partial_interface_diagnosis": old.get("interface_diagnosis"),
            "full_interface_diagnosis": row.get("interface_diagnosis"),
            "partial_phase_proof_paths": partial_paths["phase"],
            "partial_action_identity_paths": partial_paths["action_identity"],
            "partial_event_anchor_paths": partial_paths["event_anchor"],
            "full_phase_proof_paths": full_paths["phase"],
            "full_action_identity_paths": full_paths["action_identity"],
            "full_event_anchor_paths": full_paths["event_anchor"],
            "phase_paths_added_by_full_assembly": sorted(
                set(full_paths["phase"]) - set(partial_paths["phase"])
            ),
            "action_identity_paths_added_by_full_assembly": sorted(
                set(full_paths["action_identity"]) - set(partial_paths["action_identity"])
            ),
            "event_anchor_paths_added_by_full_assembly": sorted(
                set(full_paths["event_anchor"]) - set(partial_paths["event_anchor"])
            ),
        }
        output.append(enriched)
    return output


def _comparison_count(rows: list[dict[str, Any]], verdict: str) -> int:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(str(row.get("comparison_id")), []).append(row)
    return sum(bool(units) and all(row.get("evaluator_verdict") == verdict for row in units)
               for units in by_case.values())


def _markdown(document: dict[str, Any]) -> str:
    lines = [
        "# Source-phase full-assembly numeric regrade",
        "",
        "This intermediate audit runs the current `full_recording.assemble` over the existing source-bound report readings. It rebuilds events, action receipts, the turn ledger, and causal accounting together; it does not rerun video/OCR parsing and does not alter the original reports or the earlier partial projection.",
        "",
        f"- Generated: `{document['generated_at_utc']}`",
        f"- Full assembly SHA256: `{document['integrity']['full_recording_sha256']}`",
        f"- Evaluator SHA256: `{document['integrity']['evaluator_sha256']}`",
        "",
        "## Projection inputs",
        "",
        "| Run | Original report SHA256 | Projection SHA256 | Source SHA256 | Readings | Events | Actions | Choices | Races | Hints |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for run, info in document["runs"].items():
        aux = info["auxiliary_inputs"]
        lines.append(
            f"| {run} | `{info['original_report']['sha256']}` | `{info['projection']['sha256']}` | `{info['source_sha256']}` | {info['reading_count']} | {info['event_count']} | {info['action_count']} | {aux['choice_observation_count']}/{aux['committed_choice_count']} | {aux['race_observation_count']} | {aux['hint_observation_count']} |"
        )
    lines += [
        "",
        "## Numeric comparison outcomes",
        "",
        "| Recording | Comparison | Unit | Field | Source | Full evaluator | Partial evaluator | Full phase interface | Full vs partial |",
        "|---|---|---:|---|---:|---|---|---|---|",
    ]
    for row in document["diagnostics"]:
        comparison = row.get("partial_comparison", {})
        lines.append(
            f"| {row.get('recording')} | `{row.get('comparison_id')}` | {row.get('unit_index')} | {row.get('field')} | {row.get('source_amount')} | `{row.get('evaluator_verdict')}` | `{comparison.get('partial_evaluator_verdict')}` | `{row.get('phase_interface_probe', {}).get('status')}` | {row.get('interface_diagnosis')} |"
        )
    lines += [
        "",
        "## Aggregate counts",
        "",
        f"- Producer source-phase accepted: `{document['aggregate']['producer_accepted_numeric_comparisons']}/15` numeric comparisons and `{document['aggregate']['producer_accepted_historical_fields']}/5` historical fields.",
        f"- Full assembly evaluator `correct_direct`: `{document['aggregate']['full_evaluator_correct_direct_numeric_comparisons']}/15` numeric comparisons, `{document['aggregate']['full_evaluator_correct_direct_historical_fields']}/5` historical fields, and `{document['aggregate']['full_evaluator_correct_direct_contribution_units']}/21` contribution units.",
        f"- Full assembly evaluator `ambiguous_attribution`: `{document['aggregate']['full_evaluator_ambiguous_contribution_units']}/21` contribution units.",
        f"- Full assembly evaluator `missing`: `{document['aggregate']['full_evaluator_missing_contribution_units']}/21` contribution units.",
        "",
        "## Source path differences",
        "",
        "The JSON diagnostics contain concrete `phase_proof_paths`, `action_identity_paths`, and `event_anchor_paths` for every unit, plus their full-vs-partial set differences. These paths are reported as evidence only; no missing path is synthesized.",
        "",
        "| Recording | Comparison | Unit | Full phase proof paths | Full action identity paths | Full event anchor paths | Added by full assembly |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in document["diagnostics"]:
        comparison = row.get("partial_comparison", {})
        lines.append(
            f"| {row.get('recording')} | `{row.get('comparison_id')}` | {row.get('unit_index')} | {len(comparison.get('full_phase_proof_paths', []))} | {len(comparison.get('full_action_identity_paths', []))} | {len(comparison.get('full_event_anchor_paths', []))} | phase={len(comparison.get('phase_paths_added_by_full_assembly', []))}, action={len(comparison.get('action_identity_paths_added_by_full_assembly', []))}, event={len(comparison.get('event_anchor_paths_added_by_full_assembly', []))} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- Full-assembly evaluator verdicts are independent of producer source-phase acceptance.",
        "- A verdict that remains ambiguous after full assembly is an integration or proof-binding diagnosis, not evidence that the source phase was absent.",
        "- Original reports and `source-phase-regrade-v1` partial projections were hash-checked and preserved.",
        "- This is cached-reading reassembly, not a fresh parser replay; full-history accuracy remains unmeasured.",
        "",
        f"- Command: `{document['command']}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    source_phase = read(SOURCE_PHASE)
    numeric = read(NUMERIC_CAUSES)
    extended = extended_causes(numeric, source_phase)
    producer = producer_entries(source_phase)
    partial = read(PARTIAL_REGRADE) if PARTIAL_REGRADE.is_file() else None
    full_recording_path = ROOT / "tracen_replay" / "full_recording.py"
    evaluator_path = ROOT / "scripts" / "evaluate_final_numeric_cases.py"
    generator_path = Path(__file__).resolve()
    runs: dict[str, dict[str, Any]] = {}
    all_diagnostics: list[dict[str, Any]] = []
    partial_events_by_run: dict[str, dict[str, dict[str, Any]]] = {}
    for run in source_phase["runs"]:
        partial_path = OUT_DIR.parent / "source-phase-regrade-v1" / run / "recomputed-training-events-report.json"
        if not partial_path.is_file():
            continue
        partial_report = read(partial_path)
        partial_events_by_run[run] = {
            event["id"]: event
            for event in partial_report.get("gameplay_tracking", {}).get("events", [])
            if isinstance(event, dict) and isinstance(event.get("id"), str)
        }

    for run, run_info in source_phase["runs"].items():
        original_path = Path(run_info["report"]["path"])
        if not original_path.is_absolute():
            original_path = ROOT / original_path
        original = read(original_path)
        original_hash = digest(original_path)
        assembled, aux = assemble_cached_readings(original)
        projection_meta = {
            "schema_version": "tracen-replay/source-phase-full-assembly-projection-v1",
            "assembly_kind": "full_recording.assemble_over_existing_report_readings",
            "fresh_parser_rebuild": False,
            "cached_reading_reassembly": True,
            "original_report_path": relative(original_path),
            "original_report_sha256": original_hash,
            "source_sha256": run_info["source"]["sha256"],
            "source_phase_path": relative(SOURCE_PHASE),
            "source_phase_sha256": digest(SOURCE_PHASE),
            "input_manifest_path": run_info["input_manifest"]["path"],
            "input_manifest_sha256": run_info["input_manifest"]["sha256"],
            "auxiliary_input_policy": "reuse_report_owned_event_choice_observations_committed_choices_race_reward_observations_hint_card_observations",
        }
        assembled["source_phase_full_assembly_projection"] = projection_meta
        projection_path = OUT_DIR / run / "full-assembly-report.json"
        projection_path.parent.mkdir(parents=True, exist_ok=True)
        projection_path.write_text(
            json.dumps(assembled, separators=(",", ":"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        projection_hash = digest(projection_path)
        original_grade = numeric_evaluator.evaluate_report(
            numeric, original, report_path=original_path,
            reference_path=NUMERIC_CAUSES, recording=run,
        )
        full_grade = numeric_evaluator.evaluate_report(
            numeric, assembled, report_path=projection_path,
            reference_path=NUMERIC_CAUSES, recording=run,
        )
        full_extended_grade = numeric_evaluator.evaluate_report(
            extended, assembled, report_path=projection_path,
            reference_path=NUMERIC_CAUSES, recording=run,
        )
        events_by_id = {
            event["id"]: event
            for event in assembled.get("gameplay_tracking", {}).get("events", [])
            if isinstance(event, dict) and isinstance(event.get("id"), str)
        }
        target_ids = {case["id"] for case in numeric.get("cases", [])}
        historical_ids = {item["source_id"] for item in source_phase.get("historical_regressions", [])}
        diagnostics = [
            row for row in case_diagnostics(full_extended_grade, producer, "target", events_by_id)
            if row["comparison_id"] in target_ids
        ] + [
            row for row in case_diagnostics(full_extended_grade, producer, "historical", events_by_id)
            if row["comparison_id"] in historical_ids
        ]
        diagnostics = _attach_path_comparisons(
            diagnostics, partial, events_by_id, partial_events_by_run
        )
        all_diagnostics.extend(diagnostics)
        gameplay = assembled.get("gameplay_tracking", {})
        actions = gameplay.get("turn_action_receipts", []) if isinstance(gameplay, dict) else []
        events = gameplay.get("events", []) if isinstance(gameplay, dict) else []
        runs[run] = {
            "original_report": bound_file(original_path),
            "projection": bound_file(projection_path),
            "source_sha256": run_info["source"]["sha256"],
            "input_manifest": {
                "path": run_info["input_manifest"]["path"],
                "sha256": run_info["input_manifest"]["sha256"],
            },
            "reading_count": len(gameplay.get("readings", [])) if isinstance(gameplay, dict) else 0,
            "event_count": len(events) if isinstance(events, list) else 0,
            "action_count": len(actions) if isinstance(actions, list) else 0,
            "auxiliary_inputs": {
                "choice_origin": aux["choice_origin"],
                "choice_observation_count": len(aux["choice_observations"]),
                "committed_choice_count": len(aux["committed_choices"]),
                "event_choice_observations_carried": aux["event_choice_observations"] is not None,
                "race_observation_count": len(aux["race_reward_observations"]),
                "hint_observation_count": len(aux["hint_card_observations"]),
            },
            "original_numeric_counts": original_grade["counts"],
            "full_numeric_counts": full_grade["counts"],
            "full_extended_counts": full_extended_grade["counts"],
            "original_numeric_passed": original_grade["passed"],
            "full_numeric_passed": full_grade["passed"],
            "full_extended_passed": full_extended_grade["passed"],
        }

    target_ids = {case["id"] for case in numeric.get("cases", [])}
    historical_ids = {item["source_id"] for item in source_phase.get("historical_regressions", [])}
    numeric_rows = [row for row in all_diagnostics if row["comparison_id"] in target_ids]
    historical_rows = [row for row in all_diagnostics if row["comparison_id"] in historical_ids]
    document: dict[str, Any] = {
        "schema_version": "tracen-replay/source-phase-full-assembly-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "numeric_comparisons": len(target_ids),
            "numeric_contribution_units": sum(len(case.get("contributions", [])) or 1 for case in numeric.get("cases", [])),
            "historical_fields": len(historical_ids),
            "projection_kind": "full_recording_assemble_over_existing_source_bound_report_readings",
            "fresh_parser_rebuild": False,
            "original_reports_preserved": True,
            "partial_projection_preserved": True,
        },
        "integrity": {
            "source_phase_path": relative(SOURCE_PHASE),
            "source_phase_sha256": digest(SOURCE_PHASE),
            "numeric_causes_path": relative(NUMERIC_CAUSES),
            "numeric_causes_sha256": digest(NUMERIC_CAUSES),
            "partial_regrade_path": relative(PARTIAL_REGRADE),
            "partial_regrade_sha256": digest(PARTIAL_REGRADE) if PARTIAL_REGRADE.is_file() else None,
            "full_recording_path": relative(full_recording_path),
            "full_recording_sha256": digest(full_recording_path),
            "evaluator_path": relative(evaluator_path),
            "evaluator_sha256": digest(evaluator_path),
            "generator_path": relative(generator_path),
            "generator_sha256": digest(generator_path),
            "transactions_sha256": digest(ROOT / "tracen_replay" / "transactions.py"),
            "turn_ledger_sha256": digest(ROOT / "tracen_replay" / "turn_ledger.py"),
            "causal_accounting_sha256": digest(ROOT / "tracen_replay" / "causal_accounting.py"),
            "event_choice_adapter_sha256": digest(ROOT / "tracen_replay" / "event_choice_adapter.py"),
        },
        "aggregate": {
            "producer_accepted_numeric_comparisons": len(target_ids),
            "producer_accepted_historical_fields": len(historical_ids),
            "full_evaluator_correct_direct_numeric_comparisons": _comparison_count(numeric_rows, "correct_direct"),
            "full_evaluator_correct_direct_historical_fields": _comparison_count(historical_rows, "correct_direct"),
            "full_evaluator_correct_direct_contribution_units": sum(row["evaluator_verdict"] == "correct_direct" for row in all_diagnostics),
            "full_evaluator_ambiguous_contribution_units": sum(row["evaluator_verdict"] == "ambiguous_attribution" for row in all_diagnostics),
            "full_evaluator_missing_contribution_units": sum(row["evaluator_verdict"] == "missing" for row in all_diagnostics),
            "full_evaluator_wrong_amount_contribution_units": sum(row["evaluator_verdict"] == "wrong_amount" for row in all_diagnostics),
            "full_evaluator_wrong_turn_contribution_units": sum(row["evaluator_verdict"] == "wrong_turn" for row in all_diagnostics),
        },
        "runs": runs,
        "diagnostics": all_diagnostics,
        "command": ".\\.venv\\Scripts\\python.exe scripts\\regrade_source_phase_full_assembly.py",
        "limitations": [
            "The projections reuse existing candidate-report readings and source paths; they do not rerun video decoding or OCR.",
            "Report-owned choice, race, and hint observations are passed to assemble only when present; no preview is promoted into source evidence.",
            "Producer source-phase acceptance and evaluator direct credit are recorded separately.",
            "Full-history accuracy remains unmeasured.",
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_json = OUT_DIR / "source-phase-full-assembly-v1.json"
    output_md = OUT_DIR / "source-phase-full-assembly-v1.md"
    output_json.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.write_text(_markdown(document), encoding="utf-8")
    print(json.dumps({
        "json": relative(output_json),
        "markdown": relative(output_md),
        "scope": document["scope"],
        "aggregate": document["aggregate"],
        "runs": {
            run: {
                "full_numeric_counts": info["full_numeric_counts"],
                "full_extended_counts": info["full_extended_counts"],
                "event_count": info["event_count"],
                "action_count": info["action_count"],
            }
            for run, info in runs.items()
        },
    }, sort_keys=True))


if __name__ == "__main__":
    main()
