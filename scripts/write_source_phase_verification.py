"""Write a durable source-bound record for the frozen numeric phase cases.

This audit reads existing fresh candidate reports and the frozen numeric/source
case documents.  It recomputes training events with the current phase helper,
records the source evidence and hashes, and does not run OCR or rewrite any
replay input.  The output is intentionally separate from the final evaluator;
it records source evidence and producer acceptance without turning a source
observation into a final numeric grade.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from tracen_replay.transactions import checkpoints, training_events


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".local" / "final-reliability-v1"
NUMERIC_CASES = OUT_DIR / "numeric-causes.json"
SELECTION = OUT_DIR / "source-case-selection.json"
BASELINE = OUT_DIR / "baseline-manifest.json"
FREEZE = OUT_DIR / "reference-freeze-v2.json"

RUNS = {
    "v1": {
        "report": OUT_DIR / "integration-replay-v4" / "v1" / "candidate-report.json",
        "input_manifest": OUT_DIR / "replay-inputs" / "v1-replay-input-manifest.json",
        "snapshot_manifest": OUT_DIR / "integration-snapshot-v4" / "code-manifest.json",
        "source_reference": OUT_DIR / "source-references" / "v1.json",
    },
    "independent-01": {
        "report": OUT_DIR / "integration-replay-v4" / "independent-01" / "candidate-report.json",
        "input_manifest": OUT_DIR / "replay-inputs" / "independent-01-replay-input-manifest.json",
        "snapshot_manifest": OUT_DIR / "integration-snapshot-v4" / "code-manifest.json",
        "source_reference": OUT_DIR / "source-references" / "independent-01.json",
    },
    "independent-02": {
        "report": OUT_DIR / "integration-replay-v3" / "independent-02" / "candidate-report.json",
        "input_manifest": OUT_DIR / "replay-inputs" / "independent-02-replay-input-manifest.json",
        "snapshot_manifest": OUT_DIR / "integration-snapshot-v3" / "code-manifest.json",
        "source_reference": OUT_DIR / "source-references" / "independent-02.json",
    },
}

HISTORICAL = (
    {
        "source_id": "v1-turn-017-applied-speed",
        "recording": "v1",
        "turn_id": "turn-017",
        "field": "speed",
        "event_id": "training-0019",
        "source_amount": 19,
        "prediction_path_before": "/gameplay_tracking/events/60/deltas/speed",
    },
    {
        "source_id": "v1-turn-028-applied-wit",
        "recording": "v1",
        "turn_id": "turn-028",
        "field": "wit",
        "event_id": "training-0031",
        "source_amount": 21,
        "prediction_path_before": "/gameplay_tracking/events/120/deltas/wit",
    },
    {
        "source_id": "v1-turn-068-applied-stamina",
        "recording": "v1",
        "turn_id": "turn-068",
        "field": "stamina",
        "event_id": "training-0070",
        "source_amount": 44,
        "prediction_path_before": "/gameplay_tracking/events/292/deltas/stamina",
    },
    {
        "source_id": "v1-turn-068-applied-guts",
        "recording": "v1",
        "turn_id": "turn-068",
        "field": "guts",
        "event_id": "training-0070",
        "source_amount": 22,
        "prediction_path_before": "/gameplay_tracking/events/292/deltas/guts",
    },
    {
        "source_id": "t053-result-power",
        "recording": "independent-01",
        "turn_id": "turn-053",
        "field": "power",
        "event_id": "training-0049",
        "source_amount": 12,
        "prediction_path_before": "/gameplay_tracking/events/212/deltas/power",
    },
)


def read(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def evidence_path(raw: str, recording: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    text = raw.replace("\\", "/")
    if text.startswith(".local/"):
        return ROOT / Path(text)
    return ROOT / ".local" / "full-recording" / recording / Path(raw)


def file_binding(raw: str | Path, recording: str | None = None, declared: str | None = None) -> dict[str, Any]:
    path = raw if isinstance(raw, Path) else evidence_path(raw, recording or "")
    result: dict[str, Any] = {
        "path": rel(path),
        "exists": path.is_file(),
    }
    if path.is_file():
        actual = digest(path)
        result["sha256"] = actual
        if declared is not None:
            result["declared_sha256"] = declared
            result["sha256_match"] = actual == declared
    elif declared is not None:
        result["declared_sha256"] = declared
        result["sha256_match"] = False
    return result


def source_evidence_binding(item: dict[str, Any], recording: str) -> dict[str, Any]:
    raw = item.get("path") or item.get("evidence") or ""
    bound = file_binding(raw, recording, item.get("sha256"))
    for key in ("timestamp_ms", "visual", "source_frame_sha256", "raw_sidecar"):
        if key in item:
            bound[key] = item[key]
    if "raw_ocr" in item:
        bound["raw_ocr"] = item["raw_ocr"]
    return bound


def producer_observation(item: dict[str, Any], recording: str, role: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": role,
        "source_timestamp_ms": item.get("source_timestamp_ms"),
        "value": item.get("value"),
        "evidence": file_binding(item.get("evidence", ""), recording),
    }
    for key in (
        "shape",
        "crop_basis",
        "crop_conflict_state",
        "crop_region",
        "crop_candidate_amounts",
    ):
        if key in item:
            result[key] = item[key]
    return result


def compact_direct(direct: dict[str, Any] | None, recording: str) -> dict[str, Any] | None:
    if not isinstance(direct, dict):
        return None
    result = {
        "value": direct.get("value"),
        "basis": direct.get("basis"),
        "observation_count": direct.get("observation_count"),
        "source_timestamps_ms": direct.get("source_timestamps_ms", []),
        "observations": [
            producer_observation(item, recording, "direct")
            for item in direct.get("observations", [])
        ],
    }
    return result


def source_proof(event: dict[str, Any] | None, field: str, recording: str) -> dict[str, Any]:
    if event is None:
        return {
            "status": "event_not_found",
            "accepted_value": None,
            "basis": None,
            "full_observations": [],
            "component_observations": [],
        }
    phase = event.get("gain_phase_candidates", {}).get(field)
    direct = event.get("direct_gain_provenance", {}).get(field)
    result: dict[str, Any] = {
        "status": "accepted" if field in event.get("deltas", {}) else "not_promoted",
        "accepted_value": event.get("deltas", {}).get(field),
        "conflicting_readings": event.get("conflicting_readings", {}).get(field, []),
        "basis": (phase or {}).get("basis") if isinstance(phase, dict) else (direct or {}).get("basis"),
        "phase_order": (phase or {}).get("phase_order") if isinstance(phase, dict) else None,
        "observed_values": (phase or {}).get("observed_values", []) if isinstance(phase, dict) else [],
        "direct_provenance": compact_direct(direct, recording),
        "full_observations": [],
        "component_observations": [],
        "ignored_unproven_values": [],
    }
    if isinstance(phase, dict):
        resolution = phase.get("source_resolution", {})
        result["full_observations"] = [
            producer_observation(item, recording, "full")
            for item in resolution.get("full_observations", [])
        ]
        result["component_observations"] = [
            producer_observation(item, recording, "component")
            for item in resolution.get("component_observations", [])
        ]
        result["ignored_unproven_values"] = resolution.get("ignored_unproven_values", [])
        result["ignored_unproven_observations"] = [
            producer_observation(item, recording, "ignored_unproven")
            for item in resolution.get("ignored_unproven_observations", [])
        ]
        result["source_phase_rule"] = resolution.get("source_phase_rule")
        result["full_last_seen_ms"] = resolution.get("full_last_seen_ms")
        result["component_first_seen_ms"] = resolution.get("component_first_seen_ms")
        result["source_full_observation_count"] = resolution.get("source_full_observation_count")
    return result


def report_event_index(report: dict[str, Any], event_id: str) -> int | None:
    for index, event in enumerate(report.get("gameplay_tracking", {}).get("events", [])):
        if event.get("id") == event_id:
            return index
    return None


def run_context(run: str, config: dict[str, Path]) -> dict[str, Any]:
    report = read(config["report"])
    manifest = read(config["input_manifest"])
    reference = read(config["source_reference"])
    snapshot = read(config["snapshot_manifest"])
    readings = report["gameplay_tracking"]["readings"]
    events = training_events(readings, checkpoints(readings))
    event_by_id = {event["id"]: event for event in events}
    report_events = report.get("gameplay_tracking", {}).get("events", [])
    return {
        "run": run,
        "report": {
            **file_binding(config["report"]),
            "source": report.get("source"),
            "reading_count": len(readings),
            "recomputed_training_event_count": len(events),
        },
        "source": {
            "name": report.get("source", {}).get("name"),
            "sha256": report.get("source", {}).get("sha256"),
            "size_bytes": report.get("source", {}).get("size_bytes"),
            "duration_ms": report.get("source", {}).get("duration_ms"),
            "binding": {
                "report_source_sha256": report.get("source", {}).get("sha256"),
                "input_manifest_source_sha256": manifest.get("source_sha256"),
                "source_reference_sha256": reference.get("source_sha256"),
                "all_equal": len({
                    report.get("source", {}).get("sha256"),
                    manifest.get("source_sha256"),
                    reference.get("source_sha256"),
                }) == 1,
            },
        },
        "input_manifest": {
            **file_binding(config["input_manifest"]),
            "schema_version": manifest.get("schema_version"),
            "base": {
                key: manifest.get("base", {}).get(key)
                for key in ("capture", "capture_sha256", "folder", "frame_count", "neural")
            },
            "inspection_manifests": [
                {
                    key: item.get(key)
                    for key in ("id", "folder", "merge", "manifest", "manifest_sha256", "row_count")
                }
                for item in manifest.get("inspections", [])
            ],
            "recovery_manifests": [
                {
                    key: item.get(key)
                    for key in ("folder", "kind", "merge", "manifest", "manifest_sha256", "row_count")
                }
                for item in manifest.get("recovery", [])
            ],
            "supplement_count": len(manifest.get("supplements", [])),
        },
        "source_reference": {
            **file_binding(config["source_reference"]),
            "reference_sha256": digest(config["source_reference"]),
            "image_count": len(reference.get("image_sha256", {})),
        },
        "snapshot_manifest": {
            **file_binding(config["snapshot_manifest"]),
            "scope": snapshot.get("scope"),
            "file_count": len(snapshot.get("files", {})),
            "relevant_hashes": {
                key: snapshot.get("files", {}).get(key)
                for key in (
                    "tracen_replay\\training_gain_phases.py",
                    "tracen_replay\\transactions.py",
                    "tests\\test_source_temporal_phase.py",
                )
                if key in snapshot.get("files", {})
            },
        },
        "training_gain_recovery": {
            key: report.get("training_gain_recovery", {}).get(key)
            for key in (
                "manifest_sha256",
                "source_samples",
                "new_frames",
                "fps",
                "processed_windows",
                "pending_windows",
            )
        },
        # Keep the recomputed events private to this process.  The durable
        # artifact records only the selected proof for the frozen cases; it
        # should not duplicate every event from each 8k-row report.
        "_events": event_by_id,
        "report_event_indices": {event.get("id"): index for index, event in enumerate(report_events)},
    }


def numeric_case_entry(case: dict[str, Any], unit: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    run = unit.get("recording", case.get("recording"))
    context = contexts[run]
    event_id = unit.get("event_id")
    event = context["_events"].get(event_id)
    field = unit.get("field", case.get("field"))
    proof = source_proof(event, field, run)
    return {
        "contribution_id": unit.get("id") or (
            f"{case['id']}/{event_id}/{field}"
            if case.get("contributions")
            else f"{case['id']}/{field}"
        ),
        "comparison_id": case["id"],
        "recording": run,
        "turn_id": unit.get("turn_id", case.get("turn_id")),
        "field": field,
        "event_id": event_id,
        "report_event_index": context["report_event_indices"].get(event_id),
        "source_amount": unit.get("source_amount"),
        "accepted_value": proof.get("accepted_value"),
        "accepted_matches_source": proof.get("accepted_value") == unit.get("source_amount"),
        "source_proof_status": (
            "accepted_source_observation"
            if proof.get("status") == "accepted"
            and proof.get("accepted_value") == unit.get("source_amount")
            else "not_accepted_or_mismatch"
        ),
        "observation_window_ms": unit.get("observation_window_ms"),
        "primary_cause": unit.get("primary_cause", case.get("primary_cause")),
        "root_causes": unit.get("root_causes", case.get("root_causes", [])),
        "source_review_evidence": [
            source_evidence_binding(item, run)
            for item in unit.get("source_evidence", [])
        ],
        "negative_evidence": unit.get("negative_evidence", case.get("negative_evidence", [])),
        "producer_source_proof": proof,
    }


def paired_change(path: Path, source_id: str) -> dict[str, Any] | None:
    document = read(path)
    for run in document.get("runs", {}).values():
        for change in run.get("changes", []):
            if change.get("source_id") == source_id:
                return change
    return None


def historical_entry(spec: dict[str, Any], contexts: dict[str, dict[str, Any]], paired: dict[str, Any]) -> dict[str, Any]:
    run = spec["recording"]
    context = contexts[run]
    event = context["_events"].get(spec["event_id"])
    proof = source_proof(event, spec["field"], run)
    change = paired_change(paired, spec["source_id"]) or {}
    after = change.get("after", {})
    return {
        **spec,
        "accepted_value": proof.get("accepted_value"),
        "accepted_matches_source": proof.get("accepted_value") == spec["source_amount"],
        "source_proof_status": (
            "accepted_source_observation"
            if proof.get("status") == "accepted"
            and proof.get("accepted_value") == spec["source_amount"]
            else "not_accepted_or_mismatch"
        ),
        "report_event_index": context["report_event_indices"].get(spec["event_id"]),
        "source_interval_ms": after.get("source_interval_ms"),
        "source_evidence": [
            source_evidence_binding({"path": path}, run)
            for path in after.get("source_evidence", [])
        ],
        "paired_regression": {
            "artifact": rel(OUT_DIR / "integration-replay-v1" / "paired-semantic-grade.json"),
            "before_status": change.get("before", {}).get("status"),
            "before_prediction_id": change.get("before", {}).get("prediction_id"),
            "after_status": after.get("status"),
            "after_prediction_id": after.get("prediction_id"),
            "after_fields": after.get("fields", []),
        },
        "producer_source_proof": proof,
    }


def markdown(document: dict[str, Any]) -> str:
    lines = [
        "# Source phase verification",
        "",
        "This record binds the 15 frozen numeric comparisons (16 contribution units) and five historical semantic regression fields to fresh source reports, replay input manifests, source frame hashes, and current source-only training-event proofs.",
        "",
        "The artifact is an evidence audit. `accepted_matches_source` means the current producer's recomputed event field equals the frozen source amount and has source observations. It is separate from the final numeric evaluator grade; it does not use a balance or a claimed amount to select a reading.",
        "",
        "## Integrity",
        "",
        f"- Generated: `{document['generated_at_utc']}`",
        f"- Reference freeze: `{document['integrity']['reference_freeze']['sha256']}`",
        f"- Source selection: `{document['integrity']['source_selection']['sha256']}`",
        f"- Baseline manifest: `{document['integrity']['baseline']['sha256']}`",
        f"- Target source-evidence entries: `{document['integrity']['source_evidence_entries']}`; unique physical IDs: `{document['integrity']['source_evidence_unique_physical_ids']}`; reused across fields/contributions: `{document['integrity']['source_evidence_reused_across_contributions']}`.",
        "",
        "| Run | Fresh report | Report SHA256 | Source SHA256 | Input manifest SHA256 | Training manifest SHA256 |",
        "|---|---|---|---|---|---|",
    ]
    for run, info in document["runs"].items():
        lines.append(
            f"| {run} | `{info['report']['path']}` | `{info['report']['sha256']}` | `{info['source']['sha256']}` | `{info['input_manifest']['sha256']}` | `{info['training_gain_recovery'].get('manifest_sha256')}` |"
        )
    lines += [
        "",
        "## Frozen numeric comparisons",
        "",
        "| Comparison | Contribution | Field | Event | Source | Accepted | Basis | Source proof timestamps |",
        "|---|---|---|---|---:|---:|---|---|",
    ]
    for case in document["target_comparisons"]:
        for unit in case["contributions"]:
            proof = unit["producer_source_proof"]
            timestamps = [
                item.get("source_timestamp_ms")
                for item in proof.get("full_observations", []) + proof.get("component_observations", [])
            ]
            if not timestamps and proof.get("direct_provenance"):
                timestamps = proof["direct_provenance"].get("source_timestamps_ms", [])
            lines.append(
                f"| `{case['id']}` | `{unit['contribution_id']}` | {unit['field']} | `{unit['event_id']}` | {unit['source_amount']} | {unit['accepted_value']} | `{proof.get('basis')}` | `{sorted(set(timestamps))}` |"
            )
    lines += [
        "",
        "## Historical regression fields",
        "",
        "| Source ID | Turn/field | Event | Source | Accepted | Current proof | Paired before -> after | Source evidence |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for item in document["historical_regressions"]:
        paired = item["paired_regression"]
        evidence = [entry["path"] for entry in item["source_evidence"]]
        lines.append(
            f"| `{item['source_id']}` | {item['turn_id']} / {item['field']} | `{item['event_id']}` | {item['source_amount']} | {item['accepted_value']} | {item['source_proof_status']} | {paired.get('before_status')} -> {paired.get('after_status')} | `{evidence}` |"
        )
    lines += [
        "",
        "## Commands",
        "",
    ]
    lines.extend(f"- `{command}`" for command in document["commands"])
    lines += [
        "",
        "## Limits",
        "",
        "- The three source videos are bound by the SHA256 values already present in the fresh reports, replay input manifests, and source references; this audit does not hash the multi-gigabyte video files again.",
        "- Physical source frames are hashed and compared to the frozen numeric case declarations where available.",
        "- Multiple crop views within one contribution are listed as one proof set. They are not counted as additional contributions.",
        "- Full-history accuracy and recall remain unmeasured.",
        "- The v1 and independent-01 fresh report files are v4 inputs; independent-02 uses the existing fresh v3 report. Final full replay acceptance remains the parent task's gate.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    numeric = read(NUMERIC_CASES)
    contexts = {run: run_context(run, config) for run, config in RUNS.items()}
    target_comparisons = []
    for case in numeric["cases"]:
        units = case.get("contributions") or [case]
        entries = [numeric_case_entry(case, unit, contexts) for unit in units]
        target_comparisons.append(
            {
                "id": case["id"],
                "recording": case.get("recording"),
                "turn_id": case.get("turn_id"),
                "field": case.get("field"),
                "primary_cause": case.get("primary_cause"),
                "root_causes": case.get("root_causes", []),
                "contribution_count": len(entries),
                "contributions": entries,
            }
        )

    paired = OUT_DIR / "integration-replay-v1" / "paired-semantic-grade.json"
    historical = [historical_entry(spec, contexts, paired) for spec in HISTORICAL]
    integrity = {
        "reference_freeze": {
            "path": rel(FREEZE),
            "sha256": digest(FREEZE),
            "selection_sha256": read(FREEZE).get("selection_sha256"),
            "baseline_sha256": read(FREEZE).get("baseline_sha256"),
        },
        "source_selection": {"path": rel(SELECTION), "sha256": digest(SELECTION)},
        "baseline": {"path": rel(BASELINE), "sha256": digest(BASELINE)},
        "numeric_cases": {"path": rel(NUMERIC_CASES), "sha256": digest(NUMERIC_CASES)},
        "source_evidence_frame_count": sum(
            len(unit["source_review_evidence"])
            for case in target_comparisons
            for unit in case["contributions"]
        ),
        "source_evidence_hash_mismatches": [
            evidence["path"]
            for case in target_comparisons
            for unit in case["contributions"]
            for evidence in unit["source_review_evidence"]
            if evidence.get("sha256_match") is False
        ],
    }
    source_evidence = [
        evidence
        for case in target_comparisons
        for unit in case["contributions"]
        for evidence in unit["source_review_evidence"]
    ]
    integrity["source_evidence_entries"] = len(source_evidence)
    integrity["source_evidence_unique_physical_ids"] = len(
        {
            evidence.get("source_frame_sha256")
            or evidence.get("sha256")
            or evidence.get("path")
            for evidence in source_evidence
        }
    )
    integrity["source_evidence_reused_across_contributions"] = (
        integrity["source_evidence_entries"]
        - integrity["source_evidence_unique_physical_ids"]
    )
    relevant_code = {
        path: digest(ROOT / Path(path.replace("\\", "/")))
        for path in (
            "tracen_replay/training_gain_phases.py",
            "tracen_replay/transactions.py",
            "tests/test_source_temporal_phase.py",
        )
    }
    commands = [
        ".\\.venv\\Scripts\\python.exe -m unittest tests.test_source_temporal_phase tests.test_training_gain_phase_resolution_v2 tests.test_training_gain_phases tests.test_recording_regressions tests.test_result_counter",
        ".\\.venv\\Scripts\\python.exe .local/final-reliability-v1/source-temporal-phase-review-probes-v1.py",
        ".\\.venv\\Scripts\\python.exe scripts/write_source_phase_verification.py",
    ]
    document: dict[str, Any] = {
        "schema_version": "tracen-replay/source-phase-verification-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "target_comparisons": len(target_comparisons),
            "target_contribution_units": sum(item["contribution_count"] for item in target_comparisons),
            "historical_regression_fields": len(historical),
            "source_selection_expected_cases": read(SELECTION).get("expected_cases"),
        },
        "integrity": integrity,
        "current_relevant_code_sha256": relevant_code,
        "runs": {
            run: {key: value for key, value in context.items() if key != "_events"}
            for run, context in contexts.items()
        },
        "target_comparisons": target_comparisons,
        "historical_regressions": historical,
        "commands": commands,
        "limitations": [
            "This artifact binds source observations and current producer output; it is not the final numeric evaluator result.",
            "No balance, expected amount, or result counter was supplied to source phase selection.",
            "Repeated crop views are retained inside one contribution proof and are not counted repeatedly.",
            "Full-history accuracy remains unmeasured.",
        ],
        "generator": {
            "path": rel(Path(__file__)),
            "sha256": digest(Path(__file__)),
        },
    }
    output_json = OUT_DIR / "source-phase-verification-v1.json"
    output_md = OUT_DIR / "source-phase-verification-v1.md"
    output_json.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.write_text(markdown(document), encoding="utf-8")
    print(json.dumps({
        "json": rel(output_json),
        "markdown": rel(output_md),
        "target_comparisons": document["scope"]["target_comparisons"],
        "target_contribution_units": document["scope"]["target_contribution_units"],
        "historical_regression_fields": document["scope"]["historical_regression_fields"],
        "source_evidence_hash_mismatches": integrity["source_evidence_hash_mismatches"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
