"""Record manual adjudications for known frozen-source display ambiguities.

This produces a separate review artifact.  It never edits a source-reference
document and does not alter any reliability grade or denominator.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


REFERENCE_DIR = Path(".local/final-reliability-v1/source-references")
FULL_RECORDING_DIR = Path(".local/full-recording")
FREEZE_MANIFEST = Path(".local/final-reliability-v1/reference-freeze-v2.json")
SCHEMA = "tracen-replay/final-reliability-source-adjudication-v1"
APPLICATION_SCHEMA = "tracen-replay/final-reliability-source-adjudication-application-v1"
MODIFIER_SCHEMA = "tracen-replay/final-reliability-source-adjudication-modifiers-v1"
PREVIEW_SCHEMA = "tracen-replay/final-reliability-preview-phase-adjudication-v1"
RACE_ITEM_SCHEMA = "tracen-replay/final-reliability-race-item-adjudication-v1"
ACTION_INTERVAL_SCHEMA = "tracen-replay/semantic-action-interval-verdict-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha(value: Any) -> str:
    """Hash structured provenance without depending on object key order."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _observation(document: dict[str, Any], source_ref: str) -> dict[str, Any]:
    for case in document.get("cases", []):
        for observation in case.get("observations", []):
            if observation.get("id") == source_ref:
                return observation
    raise ValueError(f"Missing frozen source observation: {source_ref}")


def _proof(document: dict[str, Any], run: str, evidence: Iterable[str],
           recording_dir: Path) -> list[dict[str, Any]]:
    image_hashes = document.get("image_sha256")
    image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
    result = []
    for evidence_path in evidence:
        if not isinstance(evidence_path, str) or not evidence_path:
            continue
        preserved = _safe_recording_path(recording_dir, run, evidence_path)
        if not preserved.is_file():
            raise ValueError(f"Missing preserved evidence image: {preserved}")
        actual_sha256 = _sha(preserved)
        expected_sha256 = image_hashes.get(evidence_path)
        if isinstance(expected_sha256, str) and expected_sha256.lower() != actual_sha256:
            raise ValueError(f"Frozen image hash mismatch for {evidence_path}")
        result.append({
            "path": evidence_path,
            "sha256": actual_sha256,
            "reference_sha256": expected_sha256.lower()
            if isinstance(expected_sha256, str) else None,
            "hash_origin": "frozen_reference_image_sha256"
            if isinstance(expected_sha256, str) else "preserved_image_bytes",
        })
    if not result:
        raise ValueError("Adjudication finding requires preserved image proof")
    return result


def _original(document: dict[str, Any], source_ref: str,
              recording_dir: Path) -> dict[str, Any]:
    row = _observation(document, source_ref)
    evidence = row.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence]
    return {
        "source_ref": source_ref,
        "source_row_sha256": _json_sha(row),
        "phase": row.get("phase"),
        "interval_ms": [row.get("start_ms"), row.get("end_ms")],
        "payload": row.get("payload"),
        "expected_turn_id": row.get("expected_turn_id"),
        "evidence_paths": list(evidence),
        "evidence": _proof(document, document["run"], evidence, recording_dir),
        "status": row.get("status"),
    }


def _finding_t063(document: dict[str, Any], recording_dir: Path) -> dict[str, Any]:
    run = document["run"]
    source_refs = [
        "t063-transient-speed-dense",
        "t063-transient-wit-dense",
        "t063-transient-skill-points-dense",
    ]
    stable_refs = [
        "t063-applied-speed-dense",
        "t063-applied-wit-dense",
        "t063-applied-skill-points-dense",
    ]
    return {
        "finding_id": "independent-01-turn-063/partial-glyph-result",
        "run": run,
        "case_id": "independent-01-turn-063",
        "classification": "partial_glyph_read_of_stable_result",
        "source_refs": [_original(document, ref, recording_dir) for ref in source_refs],
        "stable_result_refs": [_original(document, ref, recording_dir) for ref in stable_refs],
        "comparison_evidence": _proof(
            document, run,
            ["training-inspection/1347500/frame-000019.png"],
            recording_dir,
        ),
        "adjudicated_interpretation": {
            "partial_glyphs": {"speed": 1, "wit": 2, "skill_points": 1},
            "stable_result": {"speed": 12, "wit": 20, "skill_points": 14},
            "explanation": (
                "The dense frame shows partial white glyphs from the same result "
                "badges. The later clear frame confirms the complete +12/+20/+14 "
                "result; the partial glyphs are not three additional awards."
            ),
        },
        "grade_action": {
            "frozen_labels_unchanged": True,
            "original_scores_preserved": True,
            "original_denominator_preserved": True,
            "production_effect_count_change": False,
            "recommendation": (
                "Treat the partial readings as ambiguous display evidence and "
                "report this adjudication separately; do not add causal effects."
            ),
        },
    }


def _finding_t042(document: dict[str, Any], recording_dir: Path) -> dict[str, Any]:
    run = document["run"]
    source_refs = [
        "t042-state-after-performance",
    ]
    context_refs = [
        "t042-performance-vocal",
        "t042-performance-composure",
    ]
    return {
        "finding_id": "independent-01-turn-042/current-performance-overlay",
        "run": run,
        "case_id": "independent-01-turn-042",
        "classification": "current_value_overlay_misread_as_post_training_state",
        "source_refs": [_original(document, ref, recording_dir) for ref in source_refs],
        "context_refs": [_original(document, ref, recording_dir) for ref in context_refs],
        "comparison_evidence": _proof(
            document, run,
            ["gameplay/part-006-frame-000261.png"],
            recording_dir,
        ),
        "adjudicated_interpretation": {
            "visible_current_values": {"vocal": 84, "composure": 100},
            "visible_overlays": {"vocal": 15, "composure": 15},
            "frozen_observed_values": {"vocal": 99, "composure": 115},
            "explanation": (
                "The frame displays current Vocal 84 and Composure 100 with "
                "+15 overlays. The frozen 99/115 values are therefore a source "
                "semantic disagreement; they must not be reconstructed by "
                "summing the overlay into the current value."
            ),
        },
        "grade_action": {
            "frozen_labels_unchanged": True,
            "original_scores_preserved": True,
            "original_denominator_preserved": True,
            "production_effect_count_change": False,
            "recommendation": (
                "Retain the strict frozen score and report the current-versus-"
                "overlay disagreement for source adjudication."
            ),
        },
    }


def build(
    reference_dir: Path = REFERENCE_DIR,
    full_recording_dir: Path = FULL_RECORDING_DIR,
    freeze_manifest: Path = FREEZE_MANIFEST,
) -> dict[str, Any]:
    reference_dir = Path(reference_dir)
    recording_dir = Path(full_recording_dir)
    freeze_path = Path(freeze_manifest)
    if not freeze_path.is_file():
        raise ValueError(f"Frozen reference manifest is required: {freeze_path}")
    documents = {
        run: _read(reference_dir / f"{run}.json")
        for run in ("independent-01",)
    }
    for run, document in documents.items():
        if document.get("run") != run:
            raise ValueError(f"Reference run mismatch: {run}")
    reference_hashes = {
        run: {
            "path": str((reference_dir / f"{run}.json").resolve()),
            "sha256": _sha(reference_dir / f"{run}.json"),
            "source_sha256": documents[run].get("source_sha256"),
        }
        for run in documents
    }
    findings = [_finding_t063(documents["independent-01"], recording_dir),
                _finding_t042(documents["independent-01"], recording_dir)]
    return {
        "schema_version": SCHEMA,
        "generated_on": "2026-09-11",
        "purpose": (
            "Separate source adjudication for confirmed display ambiguities; "
            "this artifact is advisory and cannot rewrite frozen gold."
        ),
        "frozen_inputs": {
            "reference_documents": reference_hashes,
            "freeze_manifest": {
                "path": str(freeze_path.resolve()),
                "sha256": _sha(freeze_path),
            },
        },
        "policy": {
            "original_labels_immutable": True,
            "original_scores_immutable": True,
            "original_denominator_immutable": True,
            "unknown_or_partial_display_is_not_zero": True,
            "no_fictitious_causal_effects": True,
        },
        "findings": findings,
    }


def _grade_rows(grade: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index source result rows in either a one-run or all-runs grade."""
    run_documents = grade.get("runs")
    if not isinstance(run_documents, list):
        run_documents = [grade]
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for run_document in run_documents:
        if not isinstance(run_document, dict):
            continue
        run = run_document.get("run")
        if not isinstance(run, str) or not run:
            continue
        for case in run_document.get("cases", []):
            if not isinstance(case, dict):
                continue
            case_id = case.get("case_id")
            for row in case.get("results", []):
                if not isinstance(row, dict) or not isinstance(row.get("source_id"), str):
                    continue
                key = (run, row["source_id"])
                if key in result:
                    raise ValueError(f"Duplicate grade row: {run}/{row['source_id']}")
                result[key] = {
                    "case_id": case_id,
                    "result": row,
                }
    return result


def _reference_case(document: dict[str, Any], case_id: str) -> dict[str, Any]:
    for case in document.get("cases", []):
        if isinstance(case, dict) and case.get("case_id") == case_id:
            return case
    raise ValueError(f"Unknown frozen source case: {case_id}")


def _reference_row(case: dict[str, Any], source_ref: str) -> dict[str, Any]:
    for row in case.get("observations", []):
        if isinstance(row, dict) and row.get("id") == source_ref:
            return row
    raise ValueError(f"Unknown frozen source observation: {source_ref}")


def _safe_recording_path(recording_dir: Path, run: str, evidence_path: str) -> Path:
    root = (Path(recording_dir) / run).resolve()
    candidate = (root / Path(evidence_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Evidence path escapes preserved recording: {evidence_path}") from exc
    return candidate


def _safe_rooted_path(root: Path, evidence_path: str) -> Path:
    root = Path(root).resolve()
    candidate = (root / Path(evidence_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Evidence path escapes preserved recording: {evidence_path}") from exc
    return candidate


def _verify_source_snapshot(snapshot: dict[str, Any], row: dict[str, Any],
                           document: dict[str, Any], recording_dir: Path) -> None:
    """Reject an adjudication if its frozen row or proof no longer agrees."""
    row_evidence = row.get("evidence", [])
    row_evidence = [row_evidence] if isinstance(row_evidence, str) else list(row_evidence)
    if snapshot.get("source_row_sha256") != _json_sha(row):
        raise ValueError(f"Frozen source row changed: {snapshot.get('source_ref')}")
    expected = {
        "phase": row.get("phase"),
        "interval_ms": [row.get("start_ms"), row.get("end_ms")],
        "payload": row.get("payload"),
        "expected_turn_id": row.get("expected_turn_id"),
        "evidence_paths": row_evidence,
        "status": row.get("status"),
    }
    for key, value in expected.items():
        if snapshot.get(key) != value:
            raise ValueError(f"Frozen source {key} changed: {snapshot.get('source_ref')}")
    proofs = snapshot.get("evidence")
    proof_paths = [proof.get("path") for proof in proofs
                   if isinstance(proof, dict)] if isinstance(proofs, list) else []
    if not isinstance(proofs, list) or proof_paths != row_evidence:
        raise ValueError(f"Frozen source image proof set changed: {snapshot.get('source_ref')}")
    image_hashes = document.get("image_sha256")
    image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
    for proof in proofs:
        if not isinstance(proof, dict) or not isinstance(proof.get("path"), str):
            raise ValueError(f"Malformed source proof: {snapshot.get('source_ref')}")
        path = proof["path"]
        expected_reference = proof.get("reference_sha256")
        if expected_reference is not None and image_hashes.get(path) != expected_reference:
            raise ValueError(f"Frozen image proof changed: {path}")
        preserved = _safe_recording_path(recording_dir, document["run"], path)
        if not preserved.is_file() or _sha(preserved) != proof.get("sha256"):
            raise ValueError(f"Preserved image proof changed or is missing: {path}")


def _verify_comparison_proofs(finding: dict[str, Any], document: dict[str, Any],
                              recording_dir: Path) -> None:
    image_hashes = document.get("image_sha256")
    image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
    for proof in finding.get("comparison_evidence", []):
        if not isinstance(proof, dict) or not isinstance(proof.get("path"), str):
            raise ValueError(f"Malformed comparison proof: {finding.get('finding_id')}")
        expected_reference = proof.get("reference_sha256")
        if expected_reference is not None and image_hashes.get(proof["path"]) != expected_reference:
            raise ValueError(f"Frozen comparison image proof changed: {proof['path']}")
        preserved = _safe_recording_path(recording_dir, document["run"], proof["path"])
        if not preserved.is_file() or _sha(preserved) != proof.get("sha256"):
            raise ValueError(f"Comparison image proof changed or is missing: {proof['path']}")


def _grade_summary(grade: dict[str, Any], rows: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    run_documents = grade.get("runs")
    if not isinstance(run_documents, list):
        run_documents = [grade]
    denominators = {}
    status_counts: dict[str, int] = {}
    turn_status_counts: dict[str, int] = {}
    for run_document in run_documents:
        if not isinstance(run_document, dict):
            continue
        run = run_document.get("run")
        for key, target in (("status_counts", status_counts),
                            ("turn_status_counts", turn_status_counts)):
            counts = run_document.get(key)
            if isinstance(counts, dict):
                for status, count in counts.items():
                    if isinstance(status, str) and type(count) is int:
                        target[status] = target.get(status, 0) + count
        for case in run_document.get("cases", []):
            if isinstance(run, str) and isinstance(case, dict):
                denominators[f"{run}/{case.get('case_id')}"] = len(case.get("results", []))
    if isinstance(grade.get("status_counts"), dict):
        status_counts = deepcopy(grade["status_counts"])
    if isinstance(grade.get("turn_status_counts"), dict):
        turn_status_counts = deepcopy(grade["turn_status_counts"])
    return {
        "grade_sha256": _json_sha(grade),
        "passed": grade.get("passed"),
        "observed_scope_passed": grade.get("observed_scope_passed"),
        "status_counts": status_counts,
        "turn_status_counts": turn_status_counts,
        "case_result_denominators": denominators,
        "indexed_source_rows": len(rows),
    }


def _json_pointer(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"Invalid source observation pointer: {pointer!r}")
    current = value
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"Unknown source observation pointer: {pointer}") from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ValueError(f"Unknown source observation pointer: {pointer}")
    return current


def _resolve_provenance_path(value: Any, base_dir: Path | None = None) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid provenance path: {value!r}")
    path = Path(value)
    if not path.is_absolute():
        path = (Path(base_dir) if base_dir is not None else Path.cwd()) / path
    return path.resolve()


def _verify_modifier_images(case: dict[str, Any], document: dict[str, Any],
                            metadata: dict[str, Any], recording_dir: Path) -> list[dict[str, Any]]:
    image_hashes = document.get("image_sha256")
    image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
    evidence_root = metadata.get("evidence_root")
    root = (_resolve_provenance_path(evidence_root)
            if isinstance(evidence_root, str) and evidence_root
            else (Path(recording_dir) / document["run"]).resolve())
    result = []
    for role in ("cited_evidence", "nearby_context"):
        entries = case.get(role, [])
        if not isinstance(entries, list):
            raise ValueError(f"Modifier {role} must be an array: {case.get('finding_id')}")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError(f"Malformed modifier image proof: {case.get('finding_id')}")
            expected = entry.get("sha256")
            if not isinstance(expected, str) or len(expected) != 64:
                raise ValueError(f"Malformed modifier image hash: {entry.get('path')}")
            expected = expected.lower()
            reference_hash = image_hashes.get(entry["path"])
            if not isinstance(reference_hash, str) or reference_hash.lower() != expected:
                raise ValueError(f"Frozen modifier image hash mismatch: {entry['path']}")
            path = _safe_rooted_path(root, entry["path"])
            if not path.is_file() or _sha(path) != expected:
                raise ValueError(f"Modifier image proof changed or is missing: {entry['path']}")
            result.append({"role": role, "path": entry["path"], "sha256": expected})
    if not result:
        raise ValueError(f"Modifier finding has no image proof: {case.get('finding_id')}")
    return result


def _modifier_documents(artifact: dict[str, Any], recording_dir: Path) -> tuple[
        dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if artifact.get("schema_version") != MODIFIER_SCHEMA:
        raise ValueError("Unsupported modifier adjudication schema")
    policy = artifact.get("policy")
    required = ("gameplay_only", "original_labels_immutable", "strict_scores_immutable",
                "unknown_or_partial_display_is_not_applied")
    if not isinstance(policy, dict) or any(policy.get(key) is not True for key in required):
        raise ValueError("Modifier adjudication artifact lacks immutable-score policy")
    frozen = artifact.get("frozen_inputs")
    source_metadata = frozen.get("source_references") if isinstance(frozen, dict) else None
    if not isinstance(source_metadata, dict) or not source_metadata:
        raise ValueError("Modifier adjudication has no frozen source references")
    documents, resolved_metadata = {}, {}
    for run, metadata in source_metadata.items():
        if not isinstance(metadata, dict):
            raise ValueError(f"Malformed modifier source metadata: {run}")
        path = _resolve_provenance_path(metadata.get("path"))
        if not path.is_file() or _sha(path) != metadata.get("sha256"):
            raise ValueError(f"Frozen modifier source reference changed or is missing: {run}")
        document = _read(path)
        if (document.get("run") != run
                or document.get("source_sha256") != metadata.get("source_video_sha256")):
            raise ValueError(f"Frozen modifier source identity changed: {run}")
        before_report = metadata.get("before_report")
        if not isinstance(before_report, dict):
            raise ValueError(f"Modifier before report provenance is missing: {run}")
        report_path = _resolve_provenance_path(before_report.get("path"))
        if not report_path.is_file() or _sha(report_path) != before_report.get("sha256"):
            raise ValueError(f"Frozen modifier before report changed or is missing: {run}")
        documents[run] = document
        resolved_metadata[run] = dict(metadata, path=str(path), before_report_path=str(report_path))

    strict_files = (frozen or {}).get("strict_score_files", []) if isinstance(frozen, dict) else []
    if not isinstance(strict_files, list) or not strict_files:
        raise ValueError("Modifier adjudication has no strict score provenance")
    for entry in strict_files:
        if not isinstance(entry, dict):
            raise ValueError("Malformed strict score provenance")
        path = _resolve_provenance_path(entry.get("path"))
        if not path.is_file() or _sha(path) != entry.get("sha256"):
            raise ValueError(f"Frozen strict score changed or is missing: {path}")
    return documents, resolved_metadata


def _verify_modifier_artifact_document(artifact: dict[str, Any],
                                       artifact_path: Path | None) -> None:
    """Bind an in-memory modifier document to the file named in the result."""
    if artifact_path is None:
        return
    path = Path(artifact_path)
    if not path.is_file() or _json_sha(_read(path)) != _json_sha(artifact):
        raise ValueError("Supplied modifier artifact does not match its frozen file")


def _verify_modifier_case(case: dict[str, Any], document: dict[str, Any],
                          metadata: dict[str, Any], recording_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    gold = case.get("gold")
    if not isinstance(gold, dict) or not isinstance(gold.get("id"), str):
        raise ValueError(f"Modifier finding has malformed gold row: {case.get('finding_id')}")
    pointer = case.get("source_observation_pointer")
    current = _json_pointer(document, pointer)
    if not isinstance(current, dict):
        raise ValueError(f"Modifier source pointer is not an observation: {pointer}")
    case_pointer = case.get("source_case_pointer")
    source_case = _json_pointer(document, case_pointer)
    if not isinstance(source_case, dict) or not isinstance(source_case.get("turn_id"), str):
        raise ValueError(f"Modifier source case pointer is not a turn case: {case_pointer}")
    if (not isinstance(pointer, str)
            or not isinstance(case_pointer, str)
            or not pointer.startswith(case_pointer.rstrip("/") + "/observations/")):
        raise ValueError(f"Modifier source observation is outside its case: {pointer}")
    if current.get("expected_turn_id") != source_case.get("turn_id"):
        raise ValueError(f"Modifier source turn binding changed: {gold.get('id')}")
    expected = {
        "id": gold.get("id"),
        "category": gold.get("category"),
        "phase": gold.get("phase"),
        "interval_ms": [current.get("start_ms"), current.get("end_ms")],
        "payload": gold.get("payload"),
        "evidence": gold.get("evidence"),
        "status": gold.get("status"),
    }
    actual = {
        "id": current.get("id"),
        "category": current.get("category"),
        "phase": current.get("phase"),
        "interval_ms": [current.get("start_ms"), current.get("end_ms")],
        "payload": current.get("payload"),
        "evidence": current.get("evidence"),
        "status": current.get("status"),
    }
    expected["interval_ms"] = gold.get("interval_ms")
    if actual != expected:
        raise ValueError(f"Frozen modifier gold row changed: {gold.get('id')}")
    proofs = _verify_modifier_images(case, document, metadata, recording_dir)
    cited_entries = case.get("cited_evidence", [])
    cited_paths = [entry.get("path") for entry in cited_entries
                   if isinstance(entry, dict)]
    row_evidence = current.get("evidence", [])
    row_evidence = [row_evidence] if isinstance(row_evidence, str) else list(row_evidence)
    if cited_paths != row_evidence:
        raise ValueError(f"Modifier cited proof set changed: {gold.get('id')}")
    interval = [current.get("start_ms"), current.get("end_ms")]
    for entry in cited_entries:
        timestamp = entry.get("source_timestamp_ms") if isinstance(entry, dict) else None
        if type(timestamp) is int:
            proof_interval = [timestamp, timestamp]
        elif (isinstance(timestamp, list) and len(timestamp) == 2
              and all(type(value) is int for value in timestamp)):
            proof_interval = list(timestamp)
        else:
            raise ValueError(f"Malformed modifier source timestamp: {gold.get('id')}")
        if proof_interval != interval:
            raise ValueError(f"Modifier source timestamp changed: {gold.get('id')}")
    return current, proofs


def _apply_modifier_adjudications(
    grade: dict[str, Any],
    artifact: dict[str, Any],
    *,
    recording_dir: Path,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    _verify_modifier_artifact_document(artifact, artifact_path)
    documents, metadata = _modifier_documents(artifact, recording_dir)
    cases = artifact.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Modifier adjudication has no cases")
    summary = artifact.get("summary")
    if not isinstance(summary, dict) or summary.get("gold_modifier_rows") != len(cases):
        raise ValueError("Modifier adjudication summary does not bind all rows")
    grade_rows = _grade_rows(grade)
    rows, changed, unchanged = [], [], []
    classifications = Counter()
    run_counts = Counter()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Malformed modifier adjudication case")
        run = case.get("run")
        if run not in documents:
            raise ValueError(f"Unknown modifier adjudication run: {run}")
        reference_path = case.get("source_reference")
        if _resolve_provenance_path(reference_path) != Path(metadata[run]["path"]):
            raise ValueError(f"Modifier source path is not frozen: {case.get('finding_id')}")
        current, proofs = _verify_modifier_case(case, documents[run], metadata[run], recording_dir)
        source_id = current["id"]
        adjudication = case.get("adjudication")
        if not isinstance(adjudication, dict):
            raise ValueError(f"Modifier adjudication decision is missing: {source_id}")
        classification = adjudication.get("classification")
        if not isinstance(classification, str) or not classification:
            raise ValueError(f"Modifier adjudication classification is missing: {source_id}")
        classifications[classification] += 1
        run_counts[run] += 1
        row = {
            "finding_id": case.get("finding_id"),
            "run": run,
            "source_ref": source_id,
            "source_observation_pointer": case.get("source_observation_pointer"),
            "source_row_sha256": _json_sha(current),
            "original_grade": deepcopy(grade_rows[(run, source_id)]["result"])
            if (run, source_id) in grade_rows else None,
            "adjudication": deepcopy(adjudication),
            "proofs": proofs,
            "score_effect": "advisory_only",
        }
        is_unchanged = (
            classification == "source_supported_applied"
            and adjudication.get("gold_phase_consistent") is True
            and adjudication.get("eligible_for_applied_effect_use") is True
        )
        row["changed"] = not is_unchanged
        rows.append(row)
        (unchanged if is_unchanged else changed).append(row)

    expected_counts = summary.get("adjudication_counts")
    if isinstance(expected_counts, dict):
        classification_keys = set(classifications) | {
            "source_supported_applied",
            "source_supported_preview_only",
            "cited_proof_mismatch_with_preview_context",
        }
        expected_classifications = {
            key: value for key, value in expected_counts.items()
            if key in classification_keys and value
        }
        if dict(classifications) != expected_classifications:
            raise ValueError("Modifier adjudication counts do not match its frozen summary")
        eligible = len(unchanged)
        if expected_counts.get("rows_eligible_for_applied_effect_use") != eligible:
            raise ValueError("Modifier eligibility count does not match its frozen summary")
        if expected_counts.get("rows_not_eligible_for_applied_effect_use") != len(changed):
            raise ValueError("Modifier ineligible count does not match its frozen summary")
    expected_runs = summary.get("rows_by_run")
    if isinstance(expected_runs, dict) and dict(run_counts) != {
            key: value for key, value in expected_runs.items() if value}:
        raise ValueError("Modifier adjudication run counts do not match its frozen summary")
    return {
        "schema_version": MODIFIER_SCHEMA,
        "artifact_sha256": (_sha(Path(artifact_path)) if artifact_path is not None
                            else _json_sha(artifact)),
        "frozen_source_reference_sha256": {
            run: metadata[run]["sha256"] for run in metadata
        },
        "strict_score_files": deepcopy((artifact.get("frozen_inputs") or {}).get("strict_score_files", [])),
        "summary": deepcopy(summary),
        "rows": rows,
        "changed_rows": changed,
        "unchanged_rows": unchanged,
        "score_preservation": {
            "original_scores_preserved": True,
            "original_denominator_preserved": True,
            "adjudicated_score": None,
            "adjudications_are_advisory_only": True,
        },
    }


def _local_provenance_path(value: Any) -> Path:
    """Resolve a repository provenance path and keep it inside this checkout."""
    path = _resolve_provenance_path(value, REPO_ROOT)
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Provenance path escapes repository: {value}") from exc
    return path


def _verify_declared_file(path_value: Any, expected_sha256: Any,
                          label: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise ValueError(f"Malformed provenance path: {label}")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError(f"Malformed provenance hash: {label}")
    path = _local_provenance_path(path_value)
    expected = expected_sha256.lower()
    if not path.is_file() or _sha(path) != expected:
        raise ValueError(f"Frozen provenance file changed or is missing: {label}")
    return path


def _verify_declared_files(value: Any, label: str) -> None:
    """Verify every ``{path, sha256}`` object in a proposal input section."""
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            _verify_declared_file(value.get("path"), value.get("sha256"), label)
        for key, child in value.items():
            if key not in {"path", "sha256"}:
                _verify_declared_files(child, f"{label}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _verify_declared_files(child, f"{label}/{index}")


def _verify_advisory_artifact_document(artifact: dict[str, Any],
                                       artifact_path: Path | None) -> None:
    """Bind an in-memory proposal to the exact JSON document supplied."""
    if artifact_path is None:
        return
    path = Path(artifact_path)
    if not path.is_file() or _json_sha(_read(path)) != _json_sha(artifact):
        raise ValueError("Supplied adjudication artifact does not match its frozen file")


def _preserved_recording_path(recording_dir: Path, run: str,
                              value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Malformed preserved recording path: {value!r}")
    root = (Path(recording_dir) / run).resolve()
    path = Path(value)
    if path.is_absolute():
        candidate = path.resolve()
    elif path.parts and path.parts[0].lower() == ".local":
        candidate = (REPO_ROOT / path).resolve()
    else:
        candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Evidence path escapes preserved recording: {value}") from exc
    return candidate


def _recording_relative_path(recording_dir: Path, run: str,
                             value: Any) -> str:
    path = _preserved_recording_path(recording_dir, run, value)
    root = (Path(recording_dir) / run).resolve()
    return path.relative_to(root).as_posix()


def _verify_advisory_image(image_entry: dict[str, Any],
                           document: dict[str, Any],
                           recording_dir: Path, run: str,
                           label: str) -> dict[str, Any]:
    image = image_entry.get("image") if isinstance(image_entry.get("image"), dict) else image_entry
    if not isinstance(image, dict) or not isinstance(image.get("path"), str):
        raise ValueError(f"Malformed advisory image proof: {label}")
    expected = image.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"Malformed advisory image hash: {label}")
    expected = expected.lower()
    path = _preserved_recording_path(recording_dir, run, image["path"])
    if not path.is_file() or _sha(path) != expected:
        raise ValueError(f"Advisory image proof changed or is missing: {label}")
    image_hashes = document.get("image_sha256")
    image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
    reference_hash = image.get("reference_sha256")
    known_hash = image_hashes.get(image["path"])
    if isinstance(reference_hash, str):
        if not isinstance(known_hash, str) or known_hash.lower() != reference_hash.lower():
            raise ValueError(f"Frozen advisory image hash mismatch: {label}")
        if expected != reference_hash.lower():
            raise ValueError(f"Advisory image proof disagrees with its reference hash: {label}")
    elif isinstance(known_hash, str) and known_hash.lower() != expected:
        raise ValueError(f"Frozen advisory image hash mismatch: {label}")

    sidecar = image_entry.get("sidecar")
    sidecar_result = None
    if sidecar is not None:
        if not isinstance(sidecar, dict) or not isinstance(sidecar.get("path"), str):
            raise ValueError(f"Malformed advisory sidecar proof: {label}")
        sidecar_sha = sidecar.get("sha256")
        if not isinstance(sidecar_sha, str) or len(sidecar_sha) != 64:
            raise ValueError(f"Malformed advisory sidecar hash: {label}")
        sidecar_sha = sidecar_sha.lower()
        sidecar_path = _preserved_recording_path(recording_dir, run, sidecar["path"])
        if not sidecar_path.is_file() or _sha(sidecar_path) != sidecar_sha:
            raise ValueError(f"Advisory sidecar proof changed or is missing: {label}")
        sidecar_data = _read(sidecar_path)
        timestamp = sidecar.get("source_timestamp_ms")
        if type(timestamp) is int and sidecar_data.get("source_timestamp_ms") != timestamp:
            raise ValueError(f"Advisory sidecar timestamp changed: {label}")
        evidence = sidecar_data.get("evidence")
        relative_image = _recording_relative_path(recording_dir, run, image["path"])
        if (isinstance(evidence, str)
                and relative_image != evidence
                and not relative_image.endswith("/" + evidence)):
            raise ValueError(f"Advisory sidecar image mapping changed: {label}")
        sidecar_result = {"path": sidecar["path"], "sha256": sidecar_sha}
        if type(timestamp) is int:
            sidecar_result["source_timestamp_ms"] = timestamp
    return {"path": image["path"], "sha256": expected,
            "reference_sha256": (reference_hash.lower()
                                  if isinstance(reference_hash, str) else None),
            "sidecar": sidecar_result}


def _verify_advisory_evidence(evidence: Any, document: dict[str, Any],
                              recording_dir: Path, run: str,
                              label: str) -> list[dict[str, Any]]:
    if not isinstance(evidence, dict):
        raise ValueError(f"Advisory finding has no evidence: {label}")
    primary = evidence.get("primary")
    if not isinstance(primary, dict):
        raise ValueError(f"Advisory finding has no primary evidence: {label}")
    entries = [primary]
    comparisons = evidence.get("comparisons", [])
    if not isinstance(comparisons, list):
        raise ValueError(f"Advisory comparisons must be an array: {label}")
    entries.extend(comparisons)
    return [_verify_advisory_image(entry, document, recording_dir, run,
                                   f"{label}/{index}")
            for index, entry in enumerate(entries)]


def _preview_documents(artifact: dict[str, Any],
                       recording_dir: Path) -> dict[str, dict[str, Any]]:
    frozen = artifact.get("frozen_inputs")
    metadata = frozen.get("source_references") if isinstance(frozen, dict) else None
    if not isinstance(metadata, dict) or not metadata:
        raise ValueError("Preview adjudication has no frozen source references")
    documents: dict[str, dict[str, Any]] = {}
    for run, source_meta in metadata.items():
        if not isinstance(source_meta, dict):
            raise ValueError(f"Malformed preview source metadata: {run}")
        path = _verify_declared_file(source_meta.get("path"), source_meta.get("sha256"),
                                     f"preview/source_references/{run}")
        document = _read(path)
        if (document.get("run") != run
                or document.get("source_sha256") != source_meta.get("source_sha256")):
            raise ValueError(f"Frozen preview source identity changed: {run}")
        evidence_root = source_meta.get("evidence_root")
        expected_root = (Path(recording_dir) / run).resolve()
        if isinstance(evidence_root, str):
            if _local_provenance_path(evidence_root) != expected_root:
                raise ValueError(f"Preview evidence root is not the preserved recording: {run}")
        documents[run] = document
    for key, value in frozen.items():
        if key != "source_references":
            _verify_declared_files(value, f"preview/frozen_inputs/{key}")
    return documents


def _verify_proposal_source_snapshot(snapshot: dict[str, Any], finding: dict[str, Any],
                                     document: dict[str, Any], recording_dir: Path) -> tuple[
                                         dict[str, Any], list[dict[str, Any]]]:
    source_ref = snapshot.get("source_ref")
    pointer = snapshot.get("source_pointer")
    if not isinstance(source_ref, str) or not isinstance(pointer, str):
        raise ValueError(f"Malformed frozen preview source row: {finding.get('finding_id')}")
    current = _json_pointer(document, pointer)
    if not isinstance(current, dict) or current.get("id") != source_ref:
        raise ValueError(f"Preview source pointer does not identify {source_ref}")
    source_case = _reference_case(document, finding.get("case_id"))
    if not any(row is current for row in source_case.get("observations", [])):
        raise ValueError(f"Preview source pointer is outside its case: {source_ref}")
    row_evidence = current.get("evidence", [])
    row_evidence = [row_evidence] if isinstance(row_evidence, str) else list(row_evidence)
    snapshot_evidence = snapshot.get("evidence_paths")
    if snapshot_evidence != row_evidence:
        raise ValueError(f"Frozen preview evidence paths changed: {source_ref}")
    proofs = _proof(document, document["run"], row_evidence, recording_dir)
    canonical = deepcopy(snapshot)
    canonical["evidence"] = proofs
    _verify_source_snapshot(canonical, current, document, recording_dir)
    if current.get("status") != "observed":
        raise ValueError(f"Preview source row is not observed: {source_ref}")
    return current, proofs


def _apply_preview_phase_adjudications(
    grade: dict[str, Any],
    artifact: dict[str, Any],
    *,
    recording_dir: Path,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    _verify_advisory_artifact_document(artifact, artifact_path)
    if artifact.get("schema_version") != PREVIEW_SCHEMA:
        raise ValueError("Unsupported preview phase adjudication schema")
    policy = artifact.get("policy")
    required = ("original_labels_immutable", "original_scores_immutable",
                "original_denominator_immutable", "unknown_or_partial_display_is_not_zero",
                "no_fictitious_causal_effects")
    if not isinstance(policy, dict) or any(policy.get(key) is not True for key in required):
        raise ValueError("Preview adjudication lacks immutable-score policy")
    documents = _preview_documents(artifact, recording_dir)
    grade_rows = _grade_rows(grade)
    findings = artifact.get("findings")
    if not isinstance(findings, list) or not findings:
        raise ValueError("Preview adjudication has no findings")

    rows: list[dict[str, Any]] = []
    changed_rows: list[dict[str, Any]] = []
    unchanged_rows: list[dict[str, Any]] = []
    validated_refs: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, str]] = set()
    counts = Counter()

    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Malformed preview adjudication finding")
        run = finding.get("run")
        case_id = finding.get("case_id")
        if run not in documents or not isinstance(case_id, str):
            raise ValueError(f"Unknown preview adjudication case: {run}/{case_id}")
        case = _reference_case(documents[run], case_id)
        source_entries = finding.get("source_refs")
        if not isinstance(source_entries, list) or not source_entries:
            raise ValueError(f"Preview finding has no source rows: {finding.get('finding_id')}")
        proofs = _verify_advisory_evidence(
            finding.get("evidence"), documents[run], recording_dir, run,
            str(finding.get("finding_id")),
        )
        primary_path = proofs[0]["path"]
        finding_action = finding.get("grade_action")
        if not isinstance(finding_action, dict) or any(
                finding_action.get(key) is not True
                for key in ("frozen_labels_unchanged", "original_scores_preserved",
                            "original_denominator_preserved", "adjudications_are_advisory_only")):
            raise ValueError(f"Preview finding lacks immutable grade action: {finding.get('finding_id')}")
        classification = finding.get("classification")
        if not isinstance(classification, str) or not classification:
            raise ValueError(f"Preview finding classification is missing: {finding.get('finding_id')}")
        finding_rows = []
        for snapshot in source_entries:
            if not isinstance(snapshot, dict):
                raise ValueError(f"Malformed preview source row: {finding.get('finding_id')}")
            current, source_proofs = _verify_proposal_source_snapshot(
                snapshot, finding, documents[run], recording_dir,
            )
            source_ref = current["id"]
            key = (run, source_ref)
            if key in seen_refs:
                raise ValueError(f"Duplicate preview source row: {run}/{source_ref}")
            seen_refs.add(key)
            row_grade = grade_rows.get(key)
            if row_grade is None:
                raise ValueError(f"Preview source row is absent from strict grade: {run}/{source_ref}")
            if primary_path not in (current.get("evidence", [])
                                    if isinstance(current.get("evidence"), list)
                                    else [current.get("evidence")]):
                raise ValueError(f"Preview primary proof is not source evidence: {source_ref}")
            row = {
                "finding_id": finding.get("finding_id"),
                "run": run,
                "case_id": case_id,
                "source_ref": source_ref,
                "source_pointer": snapshot.get("source_pointer"),
                "source_row_sha256": _json_sha(current),
                "phase": current.get("phase"),
                "interval_ms": [current.get("start_ms"), current.get("end_ms")],
                "payload": deepcopy(current.get("payload")),
                "expected_turn_id": current.get("expected_turn_id"),
                "status": current.get("status"),
                "evidence": source_proofs,
                "original_grade": deepcopy(row_grade["result"]),
                "adjudicated_classification": classification,
                "adjudicated_interpretation": deepcopy(
                    finding.get("adjudicated_interpretation")),
                "recommendation": finding.get("recommendation"),
                "grade_action": deepcopy(finding_action),
                "score_effect": "advisory_only",
                "changed": classification == "result_animation_not_browsed_preview",
            }
            finding_rows.append(row)
            validated_refs.append({
                "finding_id": finding.get("finding_id"), "run": run,
                "case_id": case_id, "source_ref": source_ref,
            })
            counts[classification] += 1
        rows.extend(finding_rows)
        for row in finding_rows:
            (changed_rows if row["changed"] else unchanged_rows).append(row)

    summary = artifact.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("Preview adjudication has no summary")
    expected_total = summary.get("v1_preview_rows_audited")
    if type(expected_total) is int and expected_total != len(rows):
        raise ValueError("Preview summary row count does not match frozen rows")
    expected_reclassified = summary.get("v1_rows_proposed_result_animation_reclassification")
    if type(expected_reclassified) is int and expected_reclassified != len(changed_rows):
        raise ValueError("Preview summary reclassification count does not match frozen rows")
    expected_browse = summary.get("v1_rows_confirmed_browse_preview")
    if type(expected_browse) is int and expected_browse != len(unchanged_rows):
        raise ValueError("Preview summary browse count does not match frozen rows")
    expected_classifications = {
        "result_animation_not_browsed_preview": len(changed_rows),
        "browse_preview_confirmed": len(unchanged_rows),
    }
    if dict(counts) != expected_classifications:
        raise ValueError("Preview adjudication classifications do not match frozen summary")

    controls = artifact.get("second_source_controls", [])
    validated_controls = []
    control_refs: set[tuple[str, str]] = set()
    if controls:
        if not isinstance(controls, list):
            raise ValueError("Preview source controls must be an array")
        for control in controls:
            if not isinstance(control, dict):
                raise ValueError("Malformed preview source control")
            run = control.get("run")
            case_id = control.get("case_id")
            if run not in documents or not isinstance(case_id, str):
                raise ValueError(f"Unknown preview source control case: {run}/{case_id}")
            _reference_case(documents[run], case_id)
            control_evidence = _verify_advisory_evidence(
                control.get("evidence"), documents[run], recording_dir, run,
                str(control.get("finding_id")),
            )
            for snapshot in control.get("source_refs", []):
                if not isinstance(snapshot, dict):
                    raise ValueError(f"Malformed preview source control: {control.get('finding_id')}")
                current, source_proofs = _verify_proposal_source_snapshot(
                    snapshot, control, documents[run], recording_dir,
                )
                key = (run, current["id"])
                if key in control_refs:
                    raise ValueError(f"Duplicate preview source control: {run}/{current['id']}")
                control_refs.add(key)
                row_grade = grade_rows.get(key)
                validated_controls.append({
                    "finding_id": control.get("finding_id"), "run": run,
                    "case_id": case_id, "source_ref": current["id"],
                    "source_row_sha256": _json_sha(current),
                    "evidence": source_proofs,
                    "primary_evidence": control_evidence[0],
                    "original_grade": deepcopy(row_grade["result"])
                    if row_grade is not None else None,
                    "classification": control.get("classification"),
                    "score_effect": "advisory_only",
                })

    return {
        "schema_version": PREVIEW_SCHEMA,
        "artifact_sha256": (_sha(Path(artifact_path)) if artifact_path is not None
                            else _json_sha(artifact)),
        "frozen_inputs": deepcopy(artifact.get("frozen_inputs")),
        "summary": deepcopy(summary),
        "rows": rows,
        "changed_rows": changed_rows,
        "unchanged_rows": unchanged_rows,
        "validated_source_rows": validated_refs,
        "validated_control_rows": validated_controls,
        "score_preservation": {
            "original_scores_preserved": True,
            "original_denominator_preserved": True,
            "adjudicated_score": None,
            "adjudications_are_advisory_only": True,
        },
    }


def _verify_report_pointer(report_meta: dict[str, Any], label: str) -> tuple[
        Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    path = _verify_declared_file(report_meta.get("path"), report_meta.get("sha256"), label)
    report = _read(path)
    receipt_ref = report_meta.get("receipt_ref")
    event_ref = report_meta.get("event_ref")
    receipt = _json_pointer(report, receipt_ref)
    event = _json_pointer(report, event_ref)
    if not isinstance(receipt, dict) or not isinstance(event, dict):
        raise ValueError(f"Action report pointers are not objects: {label}")
    expected_receipt = report_meta.get("receipt")
    if not isinstance(expected_receipt, dict):
        raise ValueError(f"Action report receipt provenance is missing: {label}")
    for key, value in expected_receipt.items():
        actual_value = receipt.get(key)
        def _path_equivalent(expected_path: Any, actual_path: Any) -> bool:
            if not isinstance(expected_path, str) or not isinstance(actual_path, str):
                return False
            expected_norm = Path(expected_path).as_posix()
            actual_norm = Path(actual_path).as_posix()
            return (expected_norm == actual_norm
                    or expected_norm.endswith("/" + actual_norm)
                    or actual_norm.endswith("/" + expected_norm))

        equivalent_paths = False
        if isinstance(value, list) and isinstance(actual_value, list):
            if len(value) <= len(actual_value):
                cursor = 0
                equivalent_paths = True
                for expected_path in value:
                    while cursor < len(actual_value) and not _path_equivalent(
                            expected_path, actual_value[cursor]):
                        cursor += 1
                    if cursor == len(actual_value):
                        equivalent_paths = False
                        break
                    cursor += 1
        if actual_value != value and not equivalent_paths:
            raise ValueError(f"Action report receipt changed: {label}/{key}")
    expected_interval = report_meta.get("event_interval_ms")
    if not (isinstance(expected_interval, list) and len(expected_interval) == 2):
        raise ValueError(f"Action report event interval is missing: {label}")
    actual_interval = [event.get("first_seen_ms"), event.get("last_seen_ms")]
    if actual_interval != expected_interval:
        raise ValueError(f"Action report event interval changed: {label}")
    if receipt.get("event_id") != event.get("id"):
        raise ValueError(f"Action report event binding changed: {label}")
    context_title = report_meta.get("event_context_title")
    if context_title is not None and event.get("context_title") != context_title:
        raise ValueError(f"Action report event context changed: {label}")
    return path, report, receipt, event


def _verify_action_evidence_entry(entry: dict[str, Any], document: dict[str, Any],
                                  recording_dir: Path, run: str,
                                  label: str) -> dict[str, Any]:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        raise ValueError(f"Malformed action evidence: {label}")
    expected = entry.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"Malformed action evidence hash: {label}")
    expected = expected.lower()
    path = _preserved_recording_path(recording_dir, run, entry["path"])
    if not path.is_file() or _sha(path) != expected:
        raise ValueError(f"Action evidence changed or is missing: {label}")
    relative = path.relative_to((Path(recording_dir) / run).resolve()).as_posix()
    sidecar = entry.get("raw_sidecar")
    sidecar_out = None
    if not isinstance(sidecar, dict) or not isinstance(sidecar.get("path"), str):
        raise ValueError(f"Action evidence has no raw sidecar: {label}")
    sidecar_sha = sidecar.get("sha256")
    if not isinstance(sidecar_sha, str) or len(sidecar_sha) != 64:
        raise ValueError(f"Malformed action sidecar hash: {label}")
    sidecar_sha = sidecar_sha.lower()
    sidecar_path = _preserved_recording_path(recording_dir, run, sidecar["path"])
    if not sidecar_path.is_file() or _sha(sidecar_path) != sidecar_sha:
        raise ValueError(f"Action sidecar changed or is missing: {label}")
    sidecar_data = _read(sidecar_path)
    timestamp = entry.get("source_timestamp_ms")
    if type(timestamp) is not int or sidecar_data.get("source_timestamp_ms") != timestamp:
        raise ValueError(f"Action evidence timestamp changed: {label}")
    sidecar_evidence = sidecar_data.get("evidence")
    if (not isinstance(sidecar_evidence, str)
            or (sidecar_evidence != relative
                and not relative.endswith("/" + sidecar_evidence))):
        raise ValueError(f"Action evidence sidecar mapping changed: {label}")
    # The source-reference image map is authoritative when the path is listed;
    # the sidecar's OCR metadata cannot silently substitute another image.
    image_hashes = document.get("image_sha256")
    image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
    known = image_hashes.get(relative)
    if isinstance(known, str) and known.lower() != expected:
        raise ValueError(f"Action source image hash mismatch: {label}")
    sidecar_out = {"path": sidecar["path"], "sha256": sidecar_sha,
                   "source_timestamp_ms": timestamp}
    return {"path": entry["path"], "sha256": expected,
            "source_timestamp_ms": timestamp, "role": entry.get("role"),
            "raw_sidecar": sidecar_out}


def _verify_race_frame(frame: dict[str, Any], document: dict[str, Any],
                       recording_dir: Path, run: str, label: str) -> dict[str, Any]:
    if not isinstance(frame, dict):
        raise ValueError(f"Malformed race source frame: {label}")
    timestamp = frame.get("source_timestamp_ms")
    if type(timestamp) is not int:
        raise ValueError(f"Race source frame has no timestamp: {label}")
    result = {"source_timestamp_ms": timestamp}
    for path_key, sha_key in (("source_frame_path", "source_frame_sha256"),
                              ("gameplay_crop_path", "gameplay_crop_sha256"),
                              ("raw_sidecar_path", "raw_sidecar_sha256")):
        path = _preserved_recording_path(recording_dir, run, frame.get(path_key))
        expected = frame.get(sha_key)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"Malformed race source frame hash: {label}/{path_key}")
        expected = expected.lower()
        if not path.is_file() or _sha(path) != expected:
            raise ValueError(f"Race source frame changed or is missing: {label}/{path_key}")
        result[path_key] = str(frame.get(path_key))
        result[sha_key] = expected
        if path_key == "raw_sidecar_path":
            sidecar_data = _read(path)
            if sidecar_data.get("source_timestamp_ms") != timestamp:
                raise ValueError(f"Race source sidecar timestamp changed: {label}")
            gameplay_path = frame.get("gameplay_crop_path")
            relative = _recording_relative_path(recording_dir, run, gameplay_path)
            sidecar_evidence = sidecar_data.get("evidence")
            if (not isinstance(sidecar_evidence, str)
                    or (sidecar_evidence != relative
                        and not relative.endswith("/" + sidecar_evidence))):
                raise ValueError(f"Race source sidecar image mapping changed: {label}")
        if path_key == "gameplay_crop_path":
            image_hashes = document.get("image_sha256")
            image_hashes = image_hashes if isinstance(image_hashes, dict) else {}
            known = image_hashes.get(str(frame.get(path_key)))
            if isinstance(known, str) and known.lower() != expected:
                raise ValueError(f"Race source image hash mismatch: {label}")
    return result


def _apply_race_item_adjudication(
    grade: dict[str, Any],
    artifact: dict[str, Any],
    *,
    recording_dir: Path,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    _verify_advisory_artifact_document(artifact, artifact_path)
    if artifact.get("schema_version") != RACE_ITEM_SCHEMA:
        raise ValueError("Unsupported race item adjudication schema")
    policy = artifact.get("policy")
    required = ("gameplay_only", "frozen_reference_immutable", "frozen_grade_immutable",
                "producer_expected_values_unused", "unreadable_or_hidden_content_is_unknown")
    if not isinstance(policy, dict) or any(policy.get(key) is not True for key in required):
        raise ValueError("Race item adjudication lacks immutable-score policy")
    frozen = artifact.get("frozen_inputs")
    source_meta = frozen.get("source_reference") if isinstance(frozen, dict) else None
    if not isinstance(source_meta, dict):
        raise ValueError("Race item adjudication has no frozen source reference")
    source_path = _verify_declared_file(source_meta.get("path"), source_meta.get("sha256"),
                                        "race/source_reference")
    document = _read(source_path)
    run = document.get("run")
    if run != artifact.get("finding", {}).get("run"):
        raise ValueError("Race source run identity changed")
    if document.get("source_sha256") != source_meta.get("source_sha256"):
        raise ValueError("Race source recording identity changed")
    for key, value in frozen.items():
        if key != "source_reference":
            _verify_declared_files(value, f"race/frozen_inputs/{key}")
    source_pointer = source_meta.get("observation_pointer")
    current = _json_pointer(document, source_pointer)
    finding = artifact.get("finding")
    if not isinstance(finding, dict):
        raise ValueError("Race item adjudication finding is missing")
    source_id = finding.get("source_id")
    if not isinstance(current, dict) or current.get("id") != source_id:
        raise ValueError("Race source observation pointer changed")
    source_case = _reference_case(document, finding.get("case_id"))
    if not any(row is current for row in source_case.get("observations", [])):
        raise ValueError("Race source observation is outside its case")
    if _json_sha(current) != source_meta.get("observation_sha256"):
        raise ValueError("Race source observation changed")
    frozen_row = artifact.get("frozen_source_reference")
    if not isinstance(frozen_row, dict):
        raise ValueError("Race frozen source row is missing")
    expected_row = {
        "source_id": current.get("id"),
        "phase": current.get("phase"),
        "interval_ms": [current.get("start_ms"), current.get("end_ms")],
        "evidence": current.get("evidence"),
        "payload": current.get("payload"),
        "expected_turn_id": current.get("expected_turn_id"),
        "status": current.get("status"),
    }
    actual_row = {key: frozen_row.get(key) for key in expected_row}
    if actual_row != expected_row:
        raise ValueError("Frozen race source row changed")
    if current.get("status") != "observed":
        raise ValueError("Race source row is not observed")
    source_image = (artifact.get("source_first_transcription") or {}).get("source_image")
    if not isinstance(source_image, dict):
        raise ValueError("Race source transcription image is missing")
    source_image_result = _verify_advisory_image(
        {"image": {
            "path": source_image.get("path"),
            "sha256": source_image.get("sha256"),
            "reference_sha256": source_image.get("sha256"),
        }, "sidecar": {
            "path": source_image.get("raw_sidecar_path"),
            "sha256": source_image.get("raw_sidecar_sha256"),
        }}, document, recording_dir, run, "race/source_first_transcription",
    )
    stable_frames = artifact.get("stable_source_frames")
    if not isinstance(stable_frames, dict) or not isinstance(stable_frames.get("frames"), list):
        raise ValueError("Race stable source frames are missing")
    frame_results = [_verify_race_frame(frame, document, recording_dir, run,
                                        f"race/stable_source_frames/{index}")
                     for index, frame in enumerate(stable_frames["frames"])]
    timestamps = [frame["source_timestamp_ms"] for frame in frame_results]
    declared_timestamps = stable_frames.get("stable_timestamps_ms")
    if timestamps != declared_timestamps or len(set(timestamps)) != len(timestamps):
        raise ValueError("Race stable source timestamps changed")

    candidate_meta = frozen.get("candidate_report")
    if not isinstance(candidate_meta, dict):
        raise ValueError("Race candidate report provenance is missing")
    candidate_path = _verify_declared_file(candidate_meta.get("path"), candidate_meta.get("sha256"),
                                           "race/candidate_report")
    candidate = _read(candidate_path)
    prediction_pointer = candidate_meta.get("prediction_pointer")
    snapshot = _json_pointer(candidate, prediction_pointer)
    if not isinstance(snapshot, dict) or _json_sha(snapshot) != candidate_meta.get("snapshot_sha256"):
        raise ValueError("Race candidate snapshot changed")
    race_pointer = prediction_pointer.rsplit("/visible_item_reward_snapshots/", 1)[0]
    race = _json_pointer(candidate, race_pointer)
    if not isinstance(race, dict) or _json_sha(race) != candidate_meta.get("race_sha256"):
        raise ValueError("Race candidate identity changed")
    producer = artifact.get("producer_source_evidence")
    if not isinstance(producer, dict) or producer.get("prediction_pointer") != prediction_pointer:
        raise ValueError("Race producer prediction pointer changed")
    producer_items = producer.get("snapshot_items")
    if snapshot.get("items") != producer_items:
        raise ValueError("Race producer item rows changed")
    frozen_values = (frozen_row.get("payload") or {}).get("values")
    actual_values = [item.get("quantity") for item in producer_items]
    proposed = (artifact.get("adjudication") or {}).get("proposed_reference_values")
    if not isinstance(frozen_values, list) or not isinstance(proposed, list):
        raise ValueError("Race quantity proposal is malformed")
    source_sections = (artifact.get("source_first_transcription") or {}).get("sections")
    visible_values = []
    if isinstance(source_sections, list):
        for section in source_sections:
            if not isinstance(section, dict):
                continue
            for entry in section.get("entries", []):
                if isinstance(entry, dict) and type(entry.get("quantity")) is int:
                    visible_values.append(entry["quantity"])
    if visible_values != proposed or actual_values != proposed:
        raise ValueError("Race source and producer quantities do not agree with proposal")
    if (frozen_values == proposed
            or (artifact.get("adjudication") or {}).get("producer_aggregation_fix_needed") is not False):
        raise ValueError("Race omission adjudication does not describe a distinct added row")

    grade_rows = _grade_rows(grade)
    grade_row = grade_rows.get((run, source_id))
    if grade_row is None:
        raise ValueError(f"Race source row is absent from strict grade: {run}/{source_id}")
    row = {
        "finding_id": finding.get("finding_id"),
        "run": run,
        "case_id": finding.get("case_id"),
        "source_ref": source_id,
        "source_row_sha256": _json_sha(current),
        "original_grade": deepcopy(grade_row["result"]),
        "source_image": source_image_result,
        "stable_source_frames": frame_results,
        "frozen_expected_values": deepcopy(frozen_values),
        "source_visible_values": deepcopy(visible_values),
        "producer_actual_values": deepcopy(actual_values),
        "adjudication": deepcopy(artifact.get("adjudication")),
        "score_effect": "advisory_only",
        "changed": True,
    }
    return {
        "schema_version": RACE_ITEM_SCHEMA,
        "artifact_sha256": (_sha(Path(artifact_path)) if artifact_path is not None
                            else _json_sha(artifact)),
        "frozen_inputs": deepcopy(frozen),
        "summary": {"findings": 1, "source_rows": 1,
                     "frozen_expected_values": frozen_values,
                     "producer_values": actual_values,
                     "proposed_visible_values": proposed},
        "rows": [row], "changed_rows": [row], "unchanged_rows": [],
        "score_preservation": {
            "original_scores_preserved": True,
            "original_denominator_preserved": True,
            "adjudicated_score": None,
            "adjudications_are_advisory_only": True,
        },
    }


def _apply_action_interval_adjudication(
    grade: dict[str, Any],
    artifact: dict[str, Any],
    *,
    recording_dir: Path,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    _verify_advisory_artifact_document(artifact, artifact_path)
    if artifact.get("schema_version") != ACTION_INTERVAL_SCHEMA:
        raise ValueError("Unsupported action interval verdict schema")
    scope = artifact.get("scope")
    if not isinstance(scope, dict) or scope.get("read_only_source_review") is not True:
        raise ValueError("Action interval verdict is not a read-only source review")
    if scope.get("recognizer_edits") is not False or scope.get("gold_edits") is not False:
        raise ValueError("Action interval verdict permits source or gold edits")
    inputs = artifact.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("Action interval verdict has no frozen inputs")
    for index, entry in enumerate(inputs):
        if not isinstance(entry, dict):
            raise ValueError("Malformed action interval input")
        _verify_declared_file(entry.get("path"), entry.get("sha256"),
                              f"action/inputs/{index}")
    cases = artifact.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Action interval verdict has no cases")
    summary = artifact.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("Action interval verdict has no summary")
    if type(summary.get("cases_reviewed")) is int and summary["cases_reviewed"] != len(cases):
        raise ValueError("Action interval summary case count changed")
    documents: dict[str, dict[str, Any]] = {}
    for entry in inputs:
        role = entry.get("role", "")
        if "source reference" not in role:
            continue
        path = _local_provenance_path(entry["path"])
        document = _read(path)
        run = document.get("run")
        if isinstance(run, str):
            documents[run] = document
    grade_rows = _grade_rows(grade)
    rows = []
    seen_refs: set[tuple[str, str]] = set()
    for index, finding in enumerate(cases):
        if not isinstance(finding, dict):
            raise ValueError(f"Malformed action interval case: {index}")
        run = finding.get("run")
        case_id = finding.get("case_id")
        source_ref = finding.get("source_ref")
        if run not in documents or not isinstance(case_id, str) or not isinstance(source_ref, str):
            raise ValueError(f"Unknown action interval source identity: {run}/{source_ref}")
        key = (run, source_ref)
        if key in seen_refs:
            raise ValueError(f"Duplicate action interval source row: {run}/{source_ref}")
        seen_refs.add(key)
        document = documents[run]
        source_proof = finding.get("source_proof")
        if not isinstance(source_proof, dict):
            raise ValueError(f"Action source proof is missing: {source_ref}")
        source_reference = source_proof.get("source_reference")
        source_meta = next((entry for entry in inputs
                            if isinstance(entry, dict)
                            and isinstance(entry.get("path"), str)
                            and _local_provenance_path(entry["path"])
                            == _local_provenance_path(source_reference)), None)
        if source_meta is None or source_meta.get("sha256") != source_proof.get("source_reference_sha256"):
            raise ValueError(f"Action source reference binding changed: {source_ref}")
        source_case = _reference_case(document, case_id)
        source_rows = [row for row in source_case.get("observations", [])
                       if isinstance(row, dict) and row.get("id") == source_ref]
        if len(source_rows) != 1:
            raise ValueError(f"Action source observation is missing or duplicated: {source_ref}")
        current = source_rows[0]
        reference_observation = source_proof.get("reference_observation")
        if not isinstance(reference_observation, dict):
            raise ValueError(f"Action reference observation is missing: {source_ref}")
        expected = {
            "interval_ms": [current.get("start_ms"), current.get("end_ms")],
            "phase": current.get("phase"),
            "payload": current.get("payload"),
            "evidence": current.get("evidence"),
        }
        actual = {key: reference_observation.get(key) for key in expected}
        if actual != expected:
            raise ValueError(f"Action source observation changed: {source_ref}")
        case_turn = ("turn-" + case_id.split("-turn-", 1)[1]
                     if "-turn-" in case_id else None)
        if case_turn is not None and current.get("expected_turn_id") != case_turn:
            raise ValueError(f"Action source turn binding changed: {source_ref}")
        if source_proof.get("media_sha256") != document.get("source_sha256"):
            raise ValueError(f"Action source media binding changed: {source_ref}")

        evidence = source_proof.get("gameplay_evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"Action gameplay evidence is missing: {source_ref}")
        evidence_results = [_verify_action_evidence_entry(
            entry, document, recording_dir, run,
            f"action/{source_ref}/gameplay/{entry_index}")
            for entry_index, entry in enumerate(evidence)]
        frozen_paths = set(current.get("evidence", [])
                           if isinstance(current.get("evidence"), list)
                           else [current.get("evidence")])
        for entry, result in zip(evidence, evidence_results):
            if entry.get("frozen_reference_evidence") is True:
                relative = _recording_relative_path(recording_dir, run, entry.get("path"))
                if relative not in frozen_paths:
                    raise ValueError(f"Action frozen evidence binding changed: {source_ref}")

        reports = finding.get("intermediate_reports")
        if not isinstance(reports, list) or not reports:
            raise ValueError(f"Action intermediate reports are missing: {source_ref}")
        report_results = []
        for report_index, report_meta in enumerate(reports):
            if not isinstance(report_meta, dict):
                raise ValueError(f"Malformed action report: {source_ref}/{report_index}")
            _, report, receipt, event = _verify_report_pointer(
                report_meta, f"action/{source_ref}/reports/{report_index}")
            report_results.append({
                "stage": report_meta.get("stage"),
                "path": report_meta.get("path"),
                "sha256": report_meta.get("sha256"),
                "receipt_ref": report_meta.get("receipt_ref"),
                "event_ref": report_meta.get("event_ref"),
                "source_timestamp_ms": receipt.get("source_timestamp_ms"),
                "confirmation_timestamp_ms": receipt.get("confirmation_timestamp_ms"),
                "result_interval_ms": [receipt.get("result_first_seen_ms"),
                                        receipt.get("result_last_seen_ms")],
                "event_interval_ms": [event.get("first_seen_ms"), event.get("last_seen_ms")],
            })
        comparison = finding.get("timestamp_comparison")
        verdict = finding.get("verdict")
        if not isinstance(comparison, dict) or not isinstance(verdict, dict):
            raise ValueError(f"Action timing verdict is incomplete: {source_ref}")
        source_interval = [current.get("start_ms"), current.get("end_ms")]
        if comparison.get("source_committed_interval_ms") != source_interval:
            raise ValueError(f"Action source interval changed: {source_ref}")
        predicted_timestamp = comparison.get("predicted_action_source_timestamp_ms")
        final_receipt = report_results[-1]
        if predicted_timestamp != final_receipt.get("source_timestamp_ms"):
            raise ValueError(f"Action predicted timestamp binding changed: {source_ref}")
        if verdict.get("gold_payload_mislabeled") is not False:
            raise ValueError(f"Action source payload is not source-supported: {source_ref}")
        if verdict.get("producer_interval_error") is not True:
            raise ValueError(f"Action interval verdict is not a producer finding: {source_ref}")
        grade_row = grade_rows.get(key)
        if grade_row is None:
            raise ValueError(f"Action source row is absent from strict grade: {run}/{source_ref}")
        row = {
            "finding_id": f"{case_id}/{source_ref}",
            "run": run,
            "case_id": case_id,
            "source_ref": source_ref,
            "source_row_sha256": _json_sha(current),
            "original_grade": deepcopy(grade_row["result"]),
            "source_phase": current.get("phase"),
            "source_committed_interval_ms": source_interval,
            "source_payload": deepcopy(current.get("payload")),
            "source_evidence": evidence_results,
            "reports": report_results,
            "timestamp_comparison": deepcopy(comparison),
            "verdict": deepcopy(verdict),
            "phase_application": {
                "applicability": "source_supported",
                "committed_interval_ms": source_interval,
                "result_interval_is_separate": True,
                "matching_must_not_widen_source_interval": True,
            },
            "score_effect": "advisory_only",
            "changed": True,
        }
        rows.append(row)

    expected_producer = summary.get("genuine_producer_interval_bug_cases")
    if type(expected_producer) is int and expected_producer != len(rows):
        raise ValueError("Action interval summary producer count changed")
    return {
        "schema_version": ACTION_INTERVAL_SCHEMA,
        "artifact_sha256": (_sha(Path(artifact_path)) if artifact_path is not None
                            else _json_sha(artifact)),
        "inputs": deepcopy(inputs),
        "summary": deepcopy(summary),
        "rows": rows,
        "changed_rows": rows,
        "score_preservation": {
            "original_scores_preserved": True,
            "original_denominator_preserved": True,
            "adjudicated_score": None,
            "adjudications_are_advisory_only": True,
        },
    }


def apply_adjudications(
    grade: dict[str, Any],
    artifact: dict[str, Any],
    *,
    recording_dir: Path = FULL_RECORDING_DIR,
    artifact_path: Path | None = None,
    modifier_artifact: dict[str, Any] | None = None,
    modifier_artifact_path: Path | None = None,
    preview_artifact: dict[str, Any] | None = None,
    preview_artifact_path: Path | None = None,
    race_artifact: dict[str, Any] | None = None,
    race_artifact_path: Path | None = None,
    action_interval_artifact: dict[str, Any] | None = None,
    action_interval_artifact_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and attach advisory adjudications beside an original grade.

    The returned ``changed_rows`` are comparisons only.  No source row, grade
    status, score, or denominator is rewritten, and an adjudication cannot be
    applied if its frozen row or image proof has changed.  An optional modifier
    artifact is attached under ``modifier_adjudication`` using the same
    immutable, advisory-only contract.  The optional preview, race-item, and
    action-interval proposals are attached under their own sections and are
    likewise comparisons only.
    """
    if modifier_artifact is None and modifier_artifact_path is not None:
        modifier_artifact = _read(Path(modifier_artifact_path))
    if preview_artifact is None and preview_artifact_path is not None:
        preview_artifact = _read(Path(preview_artifact_path))
    if race_artifact is None and race_artifact_path is not None:
        race_artifact = _read(Path(race_artifact_path))
    if action_interval_artifact is None and action_interval_artifact_path is not None:
        action_interval_artifact = _read(Path(action_interval_artifact_path))

    if artifact.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported source adjudication schema")
    policy = artifact.get("policy")
    required_policy = ("original_labels_immutable", "original_scores_immutable",
                       "original_denominator_immutable", "unknown_or_partial_display_is_not_zero",
                       "no_fictitious_causal_effects")
    if not isinstance(policy, dict) or any(policy.get(key) is not True for key in required_policy):
        raise ValueError("Adjudication artifact does not carry immutable-score policy")
    frozen_documents = (artifact.get("frozen_inputs") or {}).get("reference_documents")
    if not isinstance(frozen_documents, dict):
        raise ValueError("Adjudication artifact has no frozen reference hashes")
    freeze_metadata = (artifact.get("frozen_inputs") or {}).get("freeze_manifest")
    if not isinstance(freeze_metadata, dict) or not isinstance(freeze_metadata.get("path"), str):
        raise ValueError("Adjudication artifact has no freeze manifest provenance")
    freeze_path = Path(freeze_metadata["path"])
    if (not freeze_path.is_file()
            or _sha(freeze_path) != freeze_metadata.get("sha256")):
        raise ValueError("Frozen reference manifest changed or is missing")

    documents: dict[str, dict[str, Any]] = {}
    for run, metadata in frozen_documents.items():
        if not isinstance(metadata, dict) or not isinstance(metadata.get("path"), str):
            raise ValueError(f"Malformed frozen reference metadata: {run}")
        path = Path(metadata["path"])
        if not path.is_file() or _sha(path) != metadata.get("sha256"):
            raise ValueError(f"Frozen reference changed or is missing: {run}")
        document = _read(path)
        if document.get("run") != run or document.get("source_sha256") != metadata.get("source_sha256"):
            raise ValueError(f"Frozen reference identity changed: {run}")
        documents[run] = document

    rows = _grade_rows(grade)
    changed_rows = []
    validated_refs = []
    for finding in artifact.get("findings", []):
        if not isinstance(finding, dict):
            raise ValueError("Malformed adjudication finding")
        run = finding.get("run")
        case_id = finding.get("case_id")
        if run not in documents or not isinstance(case_id, str):
            raise ValueError(f"Unknown adjudication case: {run}/{case_id}")
        case = _reference_case(documents[run], case_id)
        source_entries = list(finding.get("source_refs", []))
        stable_entries = list(finding.get("stable_result_refs", []))
        context_entries = list(finding.get("context_refs", []))
        if not source_entries and not stable_entries and not context_entries:
            raise ValueError(f"Adjudication has no source rows: {finding.get('finding_id')}")
        for role, entries in (("source", source_entries),
                              ("stable_result", stable_entries),
                              ("context", context_entries)):
            for snapshot in entries:
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get("source_ref"), str):
                    raise ValueError(f"Malformed adjudication source row: {finding.get('finding_id')}")
                source_ref = snapshot["source_ref"]
                row = _reference_row(case, source_ref)
                _verify_source_snapshot(snapshot, row, documents[run], recording_dir)
                validated_refs.append({"finding_id": finding.get("finding_id"),
                                       "role": role, "run": run,
                                       "case_id": case_id, "source_ref": source_ref})
                if role == "source":
                    grade_row = rows.get((run, source_ref))
                    changed_rows.append({
                        "finding_id": finding.get("finding_id"),
                        "run": run,
                        "case_id": case_id,
                        "source_ref": source_ref,
                        "original_grade": deepcopy(grade_row["result"])
                        if grade_row is not None else None,
                        "adjudicated_classification": finding.get("classification"),
                        "adjudicated_interpretation": deepcopy(
                            finding.get("adjudicated_interpretation", {})),
                        "grade_action": deepcopy(finding.get("grade_action", {})),
                        "score_effect": "advisory_only",
                    })
        _verify_comparison_proofs(finding, documents[run], recording_dir)

    summary = _grade_summary(grade, rows)
    result = {
        "schema_version": APPLICATION_SCHEMA,
        "artifact_sha256": _sha(Path(artifact_path)) if artifact_path is not None else _json_sha(artifact),
        "frozen_reference_sha256": {
            run: metadata.get("sha256") for run, metadata in frozen_documents.items()
        },
        "original_grade": summary,
        "validated_source_rows": validated_refs,
        "changed_rows": changed_rows,
        "score_preservation": {
            "original_scores_preserved": True,
            "original_denominator_preserved": True,
            "adjudicated_score": None,
            "adjudications_are_advisory_only": True,
        },
    }
    if modifier_artifact is not None:
        result["modifier_adjudication"] = _apply_modifier_adjudications(
            grade,
            modifier_artifact,
            recording_dir=recording_dir,
            artifact_path=modifier_artifact_path,
        )
    if preview_artifact is not None:
        result["preview_phase_adjudication"] = _apply_preview_phase_adjudications(
            grade,
            preview_artifact,
            recording_dir=recording_dir,
            artifact_path=preview_artifact_path,
        )
    if race_artifact is not None:
        result["race_item_adjudication"] = _apply_race_item_adjudication(
            grade,
            race_artifact,
            recording_dir=recording_dir,
            artifact_path=race_artifact_path,
        )
    if action_interval_artifact is not None:
        result["action_interval_adjudication"] = _apply_action_interval_adjudication(
            grade,
            action_interval_artifact,
            recording_dir=recording_dir,
            artifact_path=action_interval_artifact_path,
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, default=REFERENCE_DIR)
    parser.add_argument("--full-recording-dir", type=Path, default=FULL_RECORDING_DIR)
    parser.add_argument("--freeze-manifest", type=Path, default=FREEZE_MANIFEST)
    parser.add_argument(
        "--output", type=Path,
        default=Path(".local/final-reliability-v1/source-adjudication-v1.json"),
    )
    parser.add_argument(
        "--apply-grade", type=Path,
        help="Attach this existing grade as advisory comparisons to the artifact.",
    )
    parser.add_argument(
        "--artifact", type=Path,
        help="Existing adjudication artifact used with --apply-grade.",
    )
    parser.add_argument(
        "--modifier-artifact", type=Path,
        help="Optional frozen modifier adjudication artifact attached as advisory comparisons.",
    )
    parser.add_argument(
        "--preview-artifact", type=Path,
        help="Optional frozen preview-phase adjudication proposal artifact.",
    )
    parser.add_argument(
        "--race-artifact", type=Path,
        help="Optional frozen race-item adjudication artifact.",
    )
    parser.add_argument(
        "--action-interval-artifact", type=Path,
        help="Optional frozen action-interval verdict artifact.",
    )
    args = parser.parse_args()
    if args.apply_grade is not None:
        if args.artifact is None:
            parser.error("--artifact is required with --apply-grade")
        artifact = _read(args.artifact)
        grade = _read(args.apply_grade)
        modifier_artifact = (_read(args.modifier_artifact)
                             if args.modifier_artifact is not None else None)
        preview_artifact = (_read(args.preview_artifact)
                            if args.preview_artifact is not None else None)
        race_artifact = (_read(args.race_artifact)
                         if args.race_artifact is not None else None)
        action_interval_artifact = (_read(args.action_interval_artifact)
                                    if args.action_interval_artifact is not None else None)
        result = apply_adjudications(
            grade, artifact, recording_dir=args.full_recording_dir,
            artifact_path=args.artifact,
            modifier_artifact=modifier_artifact,
            modifier_artifact_path=args.modifier_artifact,
            preview_artifact=preview_artifact,
            preview_artifact_path=args.preview_artifact,
            race_artifact=race_artifact,
            race_artifact_path=args.race_artifact,
            action_interval_artifact=action_interval_artifact,
            action_interval_artifact_path=args.action_interval_artifact,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(json.dumps({"changed_rows": len(result["changed_rows"]),
                          "output": str(args.output)}))
        return
    result = build(args.reference_dir, args.full_recording_dir, args.freeze_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"findings": len(result["findings"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
