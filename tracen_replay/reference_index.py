"""Build and score a provenance-preserving index of source references.

The index is deliberately an organizer for existing evidence.  It partitions
registered reference intervals at every declared boundary, chooses one
candidate at the finest declared cadence, and keeps all alternatives attached
to that partition.  It never merges reference effect lists or turns sampled
coverage into a whole-recording recall claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping


SCHEMA = "tracen-replay/reference-index-v1"


class ReferenceIndexError(ValueError):
    """Raised when an index cannot be built or safely scored."""


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of *path* as lowercase hexadecimal."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReferenceIndexError(f"Unable to read file: {path}") from exc


def _portable(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_path(run_root: Path, relative: str, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ReferenceIndexError(f"{label} path is missing.")
    path = (run_root / Path(relative)).resolve()
    if not path.is_relative_to(run_root):
        raise ReferenceIndexError(f"{label} path leaves the run directory: {relative}")
    return path


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ReferenceIndexError(f"Missing {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceIndexError(f"Malformed {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ReferenceIndexError(f"{label} must be a JSON object: {path}")
    return value


def _load_coverage(
    coverage: Mapping[str, Any] | Path | str,
) -> tuple[dict[str, Any], str | None]:
    if isinstance(coverage, (Path, str)):
        path = Path(coverage)
        return _read_json(path, label="coverage registry"), _portable(path)
    if not isinstance(coverage, Mapping):
        raise ReferenceIndexError("Coverage registry must be an object or JSON path.")
    return dict(coverage), None


def _validate_registry_interval(
    record: Mapping[str, Any], *, duration_ms: int, label: str
) -> tuple[int, int, int]:
    start, end, cadence = (
        record.get("start_ms"),
        record.get("end_ms"),
        record.get("sample_interval_ms"),
    )
    if not (_is_int(start) and _is_int(end) and 0 <= start < end <= duration_ms):
        raise ReferenceIndexError(f"Invalid {label} interval.")
    if not _is_int(cadence) or cadence <= 0:
        raise ReferenceIndexError(f"Invalid {label} sample interval.")
    return start, end, cadence


def _validate_reference_file(
    data: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    source_sha256: str,
    duration_ms: int,
    label: str,
) -> tuple[int, int, int]:
    start, end, cadence = _validate_registry_interval(
        registry, duration_ms=duration_ms, label=f"registry entry for {label}"
    )
    if data.get("source_sha256") != source_sha256:
        raise ReferenceIndexError(f"Source hash mismatch in {label}.")
    file_start, file_end, file_cadence = _validate_registry_interval(
        data, duration_ms=duration_ms, label=label
    )
    if (file_start, file_end, file_cadence) != (start, end, cadence):
        raise ReferenceIndexError(f"Registry/file interval mismatch in {label}.")
    return start, end, cadence


def _exposure_flag(data: Mapping[str, Any]) -> bool:
    if any(
        data.get(key) is True
        for key in (
            "predictions_seen_before_labels",
            "predictions_seen_before_revision",
            "predictions_seen_before_adjudication",
            "canonical_report_inspected_before_revision",
            "canonical_report_inspected_before_v2_seal",
        )
    ):
        return True
    # Several preserved revision files put the exposure disclosure in a
    # nested ``revision`` or ``reference_revision`` object.  It is still a
    # provenance fact even when the base reference has no direct boolean.
    for key in ("revision", "reference_revision", "adjudication"):
        nested = data.get(key)
        if isinstance(nested, Mapping) and any(
            nested_key in nested
            and nested.get(nested_key) is True
            for nested_key in (
                "predictions_seen_before_revision",
                "predictions_seen_before_adjudication",
                "canonical_report_inspected_before_revision",
                "canonical_report_inspected_after_initial_seal",
                "canonical_report_inspected_before_v2_seal",
            )
        ):
            return True
    return False


def _descriptor(
    *,
    path: str,
    digest: str,
    registry: Mapping[str, Any],
    data: Mapping[str, Any],
    registry_position: int | None,
    canonical_path: str | None = None,
    mapping: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    complete = data.get("reference_complete")
    if complete is True:
        completeness = "complete"
    elif complete is False:
        completeness = "incomplete"
    else:
        completeness = "legacy_unspecified"

    timing_basis = data.get("timing_basis", "event_start")
    mapped_exposure = (
        mapping is not None
        and any(
            mapping.get(key) is True
            for key in ("prediction_informed", "predictions_seen_before_revision")
        )
    )
    flags: list[str] = []
    if complete is False:
        flags.append("incomplete_reference")
    if not isinstance(data.get("groups"), list):
        flags.append("effect_groups_missing")
    if not isinstance(data.get("reviewed_samples"), list):
        flags.append("reviewed_samples_missing")
    if not isinstance(data.get("scope"), str) or not data["scope"].strip():
        flags.append("scope_missing")
    if timing_basis not in ("event_start", "first_exact_effect_observation"):
        flags.append("unsupported_timing_basis")
    if data.get("independent_recording") is True:
        flags.append("independent_recording_declared")
    if _exposure_flag(data):
        flags.append("prediction_or_canonical_report_exposure")

    result: dict[str, Any] = {
        "path": _portable(path),
        "sha256": digest,
        "registry_position": registry_position,
        "source_span_ms": [registry["start_ms"], registry["end_ms"]],
        "sample_interval_ms": registry["sample_interval_ms"],
        "sample_count": registry.get("samples"),
        "schema": data.get("schema"),
        "scope": data.get("scope"),
        "reference_complete": complete,
        "completeness": completeness,
        "timing_basis": timing_basis,
        "include_training_results": data.get("include_training_results", False),
        "independent_recording": data.get("independent_recording"),
        "independent_initial_evaluation": data.get("independent_initial_evaluation"),
        "prediction_or_report_exposed": _exposure_flag(data) or mapped_exposure,
        "metadata_blockers": flags,
        "groups_present": isinstance(data.get("groups"), list),
        "reviewed_samples_present": isinstance(data.get("reviewed_samples"), list),
        "selected_as_correction": mapping is not None,
    }
    if canonical_path is not None:
        result["canonical_path"] = _portable(canonical_path)
        result["variant_of"] = _portable(canonical_path)
    if mapping is not None:
        result["mapping"] = dict(mapping)
    return result


def _mapping_parts(value: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(value, str):
        return value, {}
    if isinstance(value, Mapping):
        path = value.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ReferenceIndexError("Version mapping must contain a path.")
        return path, dict(value)
    raise ReferenceIndexError("Version mapping must be a path or object.")


def _group_crosses_partition(
    data: Mapping[str, Any], start: int, end: int
) -> bool:
    groups = data.get("groups")
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        group_start, group_end = group.get("start_ms"), group.get("end_ms")
        if _is_int(group_start) and _is_int(group_end):
            if group_start < start or group_end >= end:
                return True
    return False


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _coverage_percent(intervals: list[list[int]], duration_ms: int) -> float:
    covered = sum(end - start for start, end in intervals)
    return round(covered / duration_ms * 100, 3) if duration_ms else 0.0


def build_reference_index(
    coverage: Mapping[str, Any] | Path | str,
    run_root: Path | str,
    *,
    version_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic non-overlapping index from a coverage registry.

    ``version_map`` maps a registered reference path to an explicitly selected
    immutable variant.  The original registry path and hash remain in every
    partition candidate.  Variants must have the same source hash, interval,
    and cadence as their registered parent.
    """

    run_root = Path(run_root).resolve()
    coverage_data, coverage_path = _load_coverage(coverage)
    source = coverage_data.get("source_sha256")
    duration = coverage_data.get("source_duration_ms")
    if not isinstance(source, str) or not source:
        raise ReferenceIndexError("Coverage registry source_sha256 is missing.")
    if not _is_int(duration) or duration <= 0:
        raise ReferenceIndexError("Coverage registry source_duration_ms is invalid.")
    raw_refs = coverage_data.get("references")
    if not isinstance(raw_refs, list) or not raw_refs:
        raise ReferenceIndexError("Coverage registry has no references.")

    normalized_refs: list[dict[str, Any]] = []
    by_path: dict[str, dict[str, Any]] = {}
    duplicate_paths: list[str] = []
    for position, raw in enumerate(raw_refs, 1):
        if not isinstance(raw, Mapping):
            raise ReferenceIndexError(f"Registry reference {position} is invalid.")
        record = dict(raw)
        path_text = record.get("path")
        if not isinstance(path_text, str) or not path_text.strip():
            raise ReferenceIndexError(f"Registry reference {position} has no path.")
        if path_text in by_path:
            duplicate_paths.append(path_text)
        if not isinstance(record.get("sha256"), str) or not record["sha256"]:
            raise ReferenceIndexError(f"Registry reference has no hash: {path_text}")
        start, end, cadence = _validate_registry_interval(
            record, duration_ms=duration, label=path_text
        )
        path = _safe_path(run_root, path_text, label="Reference")
        actual_hash = sha256_file(path)
        if actual_hash.lower() != record["sha256"].lower():
            raise ReferenceIndexError(f"Reference hash mismatch: {path_text}")
        data = _read_json(path, label=f"reference {path_text}")
        _validate_reference_file(
            data,
            record,
            source_sha256=source,
            duration_ms=duration,
            label=path_text,
        )
        normalized = {
            "path": path_text,
            "sha256": actual_hash,
            "registry": {
                "path": path_text,
                "sha256": actual_hash,
                "start_ms": start,
                "end_ms": end,
                "sample_interval_ms": cadence,
                "samples": record.get("samples"),
            },
            "data": data,
            "registry_position": position,
        }
        normalized_refs.append(normalized)
        by_path.setdefault(path_text, normalized)

    mapping_input = dict(version_map or {})
    unknown_mappings = sorted(set(mapping_input) - set(by_path))
    if unknown_mappings:
        raise ReferenceIndexError(
            "Version mapping has no registered parent: " + ", ".join(unknown_mappings)
        )

    mappings: dict[str, dict[str, Any]] = {}
    effective_by_canonical: dict[str, dict[str, Any]] = {}
    for canonical_path, mapping_value in mapping_input.items():
        variant_path, mapping = _mapping_parts(mapping_value)
        parent = by_path[canonical_path]
        variant_file = _safe_path(run_root, variant_path, label="Version mapping")
        actual_hash = sha256_file(variant_file)
        expected_hash = mapping.get("sha256")
        if expected_hash is not None and (
            not isinstance(expected_hash, str)
            or actual_hash.lower() != expected_hash.lower()
        ):
            raise ReferenceIndexError(f"Version mapping hash mismatch: {variant_path}")
        variant_data = _read_json(variant_file, label=f"mapped reference {variant_path}")
        _validate_reference_file(
            variant_data,
            parent["registry"],
            source_sha256=source,
            duration_ms=duration,
            label=variant_path,
        )
        mapping_record = dict(mapping)
        mapping_record.update(
            {
                "canonical_path": canonical_path,
                "canonical_sha256": parent["sha256"],
                "path": variant_path,
                "sha256": actual_hash,
                "source_span_ms": [
                    parent["registry"]["start_ms"],
                    parent["registry"]["end_ms"],
                ],
                "sample_interval_ms": parent["registry"]["sample_interval_ms"],
            }
        )
        mappings[canonical_path] = mapping_record
        effective_by_canonical[canonical_path] = {
            "path": variant_path,
            "sha256": actual_hash,
            "data": variant_data,
            "mapping": mapping_record,
        }

    # Keep the first registry entry for duplicate paths as the deterministic
    # candidate.  The duplicate remains visible in the registry metadata and
    # top-level blockers instead of being counted twice.
    unique_refs = list(by_path.values())
    boundaries = sorted(
        {
            boundary
            for ref in unique_refs
            for boundary in (ref["registry"]["start_ms"], ref["registry"]["end_ms"])
        }
    )
    partitions: list[dict[str, Any]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        candidates = [
            ref
            for ref in unique_refs
            if ref["registry"]["start_ms"] <= start
            and end <= ref["registry"]["end_ms"]
        ]
        if not candidates:
            continue
        ordered = sorted(
            candidates,
            key=lambda ref: (
                ref["registry"]["sample_interval_ms"],
                ref["registry_position"],
                ref["path"],
            ),
        )
        selected = ordered[0]
        selected_canonical = selected["path"]
        effective = effective_by_canonical.get(selected_canonical)
        selected_path = effective["path"] if effective else selected_canonical
        selected_hash = effective["sha256"] if effective else selected["sha256"]
        selected_data = effective["data"] if effective else selected["data"]
        selected_mapping = effective["mapping"] if effective else None
        same_cadence = [
            ref
            for ref in candidates
            if ref["registry"]["sample_interval_ms"]
            == selected["registry"]["sample_interval_ms"]
        ]
        blockers: list[str] = []
        if len(candidates) > 1:
            blockers.append("overlapping_reference_candidates")
        if len(same_cadence) > 1:
            blockers.append("same_cadence_tie_requires_adjudication")
        selected_span = [
            selected["registry"]["start_ms"],
            selected["registry"]["end_ms"],
        ]
        if selected_span != [start, end]:
            blockers.append("selected_reference_crosses_partition_boundary")
        if _group_crosses_partition(selected_data, start, end):
            blockers.append("reference_group_crosses_partition_boundary")
        selected_descriptor = _descriptor(
            path=selected_path,
            digest=selected_hash,
            registry=selected["registry"],
            data=selected_data,
            registry_position=selected["registry_position"],
            canonical_path=selected_canonical if effective else None,
            mapping=selected_mapping,
        )
        partitions.append(
            {
                "partition_id": f"{start:010d}-{end:010d}",
                "partition_ms": [start, end],
                "selected_path": selected_path,
                "selected_canonical_path": selected_canonical,
                "selected": selected_descriptor,
                "candidate_paths": [ref["path"] for ref in sorted(candidates, key=lambda ref: ref["registry_position"])],
                "candidates": [
                    _descriptor(
                        path=ref["path"],
                        digest=ref["sha256"],
                        registry=ref["registry"],
                        data=ref["data"],
                        registry_position=ref["registry_position"],
                        canonical_path=(
                            ref["path"] if ref["path"] in effective_by_canonical else None
                        ),
                        mapping=(
                            effective_by_canonical[ref["path"]]["mapping"]
                            if ref["path"] in effective_by_canonical
                            else None
                        ),
                    )
                    for ref in sorted(candidates, key=lambda ref: ref["registry_position"])
                ],
                "candidate_count": len(candidates),
                "tie": len(same_cadence) > 1,
                "overlap": len(candidates) > 1,
                "selected_source_span_ms": selected_span,
                "selected_source_slice_ms": [start, end],
                "requires_derived_slice": selected_span != [start, end],
                "blockers": sorted(set(blockers + selected_descriptor["metadata_blockers"])),
                "selection_reason": (
                    "finest_declared_cadence"
                    if len(candidates) == 1
                    else "finest_declared_cadence_with_preserved_alternatives"
                ),
            }
        )

    registry_descriptors = []
    for ref in unique_refs:
        descriptor = _descriptor(
            path=ref["path"],
            digest=ref["sha256"],
            registry=ref["registry"],
            data=ref["data"],
            registry_position=ref["registry_position"],
        )
        effective = effective_by_canonical.get(ref["path"])
        if effective:
            descriptor["selected_correction"] = {
                "path": effective["path"],
                "sha256": effective["sha256"],
                "mapping": effective["mapping"],
            }
        registry_descriptors.append(descriptor)

    union = _merge_intervals(
        [
            (ref["registry"]["start_ms"], ref["registry"]["end_ms"])
            for ref in unique_refs
        ]
    )
    gaps: list[list[int]] = []
    cursor = 0
    for start, end in union:
        if cursor < start:
            gaps.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < duration:
        gaps.append([cursor, duration])
    overlap_partitions = [p for p in partitions if p["overlap"]]
    tie_partitions = [p for p in partitions if p["tie"]]
    partition_blockers = sorted(
        {
            blocker
            for partition in partitions
            for blocker in partition["blockers"]
        }
    )
    top_blockers = set(partition_blockers)
    if duplicate_paths:
        top_blockers.add("duplicate_registry_paths_preserved_once")
    if gaps:
        top_blockers.add("uncovered_source_intervals")

    return {
        "schema": SCHEMA,
        "version": 1,
        "status": "provisional",
        "source": {
            "sha256": source,
            "duration_ms": duration,
            "coverage_registry_path": coverage_path,
        },
        "registry": {
            "reference_count": len(raw_refs),
            "unique_reference_count": len(unique_refs),
            "duplicate_paths": sorted(set(duplicate_paths)),
            "path_order_is_registry_order": True,
            "sha256": (
                sha256_file(Path(coverage_path))
                if coverage_path is not None and Path(coverage_path).is_file()
                else None
            ),
        },
        "version_mappings": list(mappings.values()),
        "references": registry_descriptors,
        "partitions": partitions,
        "coverage": {
            "union_intervals_ms": union,
            "uncovered_intervals_ms": gaps,
            "declared_sampled_percent": _coverage_percent(union, duration),
            "finest_declared_sample_interval_ms": min(
                ref["registry"]["sample_interval_ms"] for ref in unique_refs
            ),
            "partition_count": len(partitions),
            "overlap_partition_count": len(overlap_partitions),
            "tie_partition_count": len(tie_partitions),
            "partition_blocker_count": sum(bool(p["blockers"]) for p in partitions),
            "full_recording_effect_recall_measured": False,
        },
        "blockers": sorted(top_blockers),
        "claims": {
            "full_recording_effect_recall_measured": False,
            "aggregate_accuracy_claimed": False,
            "independent_test_established": False,
            "native_frame_recall_established": False,
        },
        "selection_policy": {
            "interval_convention": "half_open",
            "partition_boundaries": "all registered start_ms/end_ms values",
            "candidate_rule": "minimum sample_interval_ms then registry position",
            "tie_rule": "preserve all equal-cadence candidates and mark adjudication blocker",
            "overlap_rule": "one provisional candidate per partition; never union effect lists",
            "variant_rule": "explicit mapped variants preserve canonical path/hash and are never silently substituted into the original score",
        },
    }


def _load_index(index: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(index, (Path, str)):
        return _read_json(Path(index), label="reference index")
    if not isinstance(index, Mapping):
        raise ReferenceIndexError("Reference index must be an object or JSON path.")
    return dict(index)


def _issue_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for missing in result.get("missing", []) or []:
        if isinstance(missing, Mapping):
            effect = missing.get("effect", missing)
            if isinstance(effect, Mapping):
                detail = {
                    key: effect.get(key)
                    for key in ("kind", "field", "name", "amount", "value")
                    if key in effect
                }
            else:
                detail = {"value": effect}
            detail["group_start_ms"] = missing.get("start_ms")
        else:
            detail = {"value": missing}
        issues.append({"kind": "missing", "detail": detail})
    for extra in result.get("extra_predictions", []) or []:
        if isinstance(extra, Mapping):
            effect = extra.get("effect", {})
            detail = {
                "event_id": extra.get("event_id"),
                "time": extra.get("time"),
                "effect": effect,
                "conflicted": extra.get("conflicted"),
            }
        else:
            detail = {"value": extra}
        issues.append({"kind": "extra", "detail": detail})
    return issues


def _score_summary(
    result: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    *,
    path: str,
    role: str,
) -> dict[str, Any]:
    issues = _issue_rows(result)
    metadata_blockers = list(descriptor.get("metadata_blockers", []))
    score_blockers: list[str] = []
    if descriptor.get("reference_complete") is False:
        score_blockers.append("incomplete_reference_cannot_pass")
    if descriptor.get("prediction_or_report_exposed"):
        score_blockers.append("source_or_report_exposure_blocks_independent_claim")
    if descriptor.get("independent_recording") is True:
        score_blockers.append("independent_recording_provenance_requires_resolution")
    if result.get("evidence_errors"):
        score_blockers.append("evidence_errors")
    if result.get("timing_errors"):
        score_blockers.append("timing_errors")
    if not result.get("passed", False):
        score_blockers.append("reference_score_failed")
    return {
        "path": path,
        "role": role,
        "scope": descriptor.get("scope"),
        "source_span_ms": descriptor.get("source_span_ms"),
        "sample_interval_ms": descriptor.get("sample_interval_ms"),
        "reference_complete": descriptor.get("reference_complete"),
        "completeness": descriptor.get("completeness"),
        "timing_basis": descriptor.get("timing_basis"),
        "include_training_results": result.get("include_training_results", False),
        "typed_training_predictions": result.get("typed_training_predictions", 0),
        "prediction_or_report_exposed": descriptor.get("prediction_or_report_exposed"),
        "expected": result.get("expected"),
        "predicted": result.get("predicted"),
        "matched": result.get("matched"),
        "precision": result.get("precision"),
        "recall": result.get("recall"),
        "passed": result.get("passed"),
        "reviewed_samples": result.get("reviewed_samples"),
        "evidence_hashes_checked": result.get("evidence_hashes_checked"),
        "source_onset_windows": result.get("source_onset_windows"),
        "reference_observability": result.get("reference_observability"),
        "missing_count": sum(issue["kind"] == "missing" for issue in issues),
        "extra_count": sum(issue["kind"] == "extra" for issue in issues),
        "issues": issues,
        "evidence_errors": result.get("evidence_errors", []),
        "timing_errors": result.get("timing_errors", []),
        "metadata_blockers": metadata_blockers,
        "score_blockers": sorted(set(score_blockers)),
        # A clean reference is necessary but insufficient for a holdout claim.
        # This index has no recording-level split or untouched-run provenance,
        # so per-reference diagnostics never establish independent eligibility.
        "independent_test_eligible": False,
        "status": "passed" if result.get("passed") else "failed",
    }


def score_reference_index(
    index: Mapping[str, Any] | Path | str,
    report: Mapping[str, Any] | Path | str,
    run_root: Path | str,
    *,
    evidence_root: Path | str | None = None,
    evaluator: Callable[[Mapping[str, Any], Mapping[str, Any], Path], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score every registered effect reference and mapped correction.

    Per-reference scores are useful diagnostics.  They are intentionally not
    aggregated across overlapping references or emitted as whole-recording
    recall.  Hash/source mismatches raise ``ReferenceIndexError`` before any
    score is returned.
    """

    index_data = _load_index(index)
    if index_data.get("schema") != SCHEMA:
        raise ReferenceIndexError("Unsupported reference index schema.")
    run_root = Path(run_root).resolve()
    evidence_root = Path(evidence_root or run_root).resolve()
    if isinstance(report, (Path, str)):
        report_path = Path(report)
        report_data = _read_json(report_path, label="report")
        report_digest = sha256_file(report_path)
    elif isinstance(report, Mapping):
        report_data = dict(report)
        report_digest = None
    else:
        raise ReferenceIndexError("Report must be an object or JSON path.")
    source_sha = index_data.get("source", {}).get("sha256")
    report_source = report_data.get("source", {}).get("sha256")
    if not isinstance(source_sha, str) or report_source != source_sha:
        raise ReferenceIndexError("Report source hash does not match reference index.")
    if evaluator is None:
        from .effect_evaluate import evaluate as evaluator

    descriptors = index_data.get("references")
    if not isinstance(descriptors, list):
        raise ReferenceIndexError("Reference index has no reference descriptors.")
    mappings = index_data.get("version_mappings", [])
    if not isinstance(mappings, list):
        raise ReferenceIndexError("Reference index version_mappings is invalid.")

    # The index stores mapped variants in ``references`` and keeps the base
    # registry path in canonical_path.  Score every unique registered/effective
    # path exactly once; partition rows point to these diagnostics.
    targets: list[tuple[str, Mapping[str, Any], str]] = []
    seen: set[str] = set()
    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            raise ReferenceIndexError("Reference descriptor is invalid.")
        path = descriptor.get("path")
        if not isinstance(path, str):
            raise ReferenceIndexError("Reference descriptor path is missing.")
        if path in seen:
            continue
        seen.add(path)
        role = "explicit_correction" if descriptor.get("selected_as_correction") else "registered_reference"
        targets.append((path, descriptor, role))
    # A mapped variant may be absent from the references list in hand-written
    # indexes.  Include it from the mapping table when possible.
    descriptor_by_path = {d.get("path"): d for d in descriptors if isinstance(d, Mapping)}
    for mapping in mappings:
        path = mapping.get("path") if isinstance(mapping, Mapping) else None
        if not isinstance(path, str) or path in seen:
            continue
        canonical = mapping.get("canonical_path")
        base_descriptor = descriptor_by_path.get(canonical)
        if base_descriptor is None:
            raise ReferenceIndexError(f"Version mapping canonical path is absent: {canonical}")
        variant_descriptor = dict(base_descriptor)
        variant_descriptor.update(
            {
                "path": path,
                "sha256": mapping.get("sha256"),
                "canonical_path": canonical,
                "variant_of": canonical,
                "selected_as_correction": True,
                "mapping": dict(mapping),
            }
        )
        targets.append((path, variant_descriptor, "explicit_correction"))
        seen.add(path)

    per_reference: list[dict[str, Any]] = []
    score_by_path: dict[str, dict[str, Any]] = {}
    for path_text, descriptor, role in targets:
        path = _safe_path(run_root, path_text, label="Indexed reference")
        actual_hash = sha256_file(path)
        expected_hash = descriptor.get("sha256")
        if not isinstance(expected_hash, str) or actual_hash.lower() != expected_hash.lower():
            raise ReferenceIndexError(f"Indexed reference hash mismatch: {path_text}")
        reference_data = _read_json(path, label=f"indexed reference {path_text}")
        if reference_data.get("source_sha256") != source_sha:
            raise ReferenceIndexError(f"Indexed reference source mismatch: {path_text}")
        source_span = descriptor.get("source_span_ms")
        if (
            not isinstance(source_span, list)
            or len(source_span) != 2
            or not all(_is_int(value) for value in source_span)
        ):
            raise ReferenceIndexError(f"Indexed reference span is invalid: {path_text}")
        score_descriptor = _descriptor(
            path=path_text,
            digest=actual_hash,
            registry={
                "start_ms": source_span[0],
                "end_ms": source_span[1],
                "sample_interval_ms": descriptor.get("sample_interval_ms"),
                "samples": descriptor.get("sample_count"),
            },
            data=reference_data,
            registry_position=descriptor.get("registry_position"),
            canonical_path=descriptor.get("canonical_path")
            or descriptor.get("variant_of"),
            mapping=descriptor.get("mapping"),
        )
        if descriptor.get("selected_correction") is not None:
            score_descriptor["selected_correction"] = descriptor["selected_correction"]
        try:
            result = evaluator(reference_data, report_data, evidence_root)
        except ValueError as exc:  # Known evaluator validation failure.
            if "source" in str(exc).lower() or "recording" in str(exc).lower():
                raise ReferenceIndexError(f"Reference scoring source failure: {path_text}") from exc
            summary = {
                "path": path_text,
                "role": role,
                "status": "blocked",
                "scope": descriptor.get("scope"),
                "source_span_ms": descriptor.get("source_span_ms"),
                "sample_interval_ms": descriptor.get("sample_interval_ms"),
                "reference_complete": descriptor.get("reference_complete"),
                "completeness": descriptor.get("completeness"),
                "timing_basis": descriptor.get("timing_basis"),
                "prediction_or_report_exposed": descriptor.get("prediction_or_report_exposed"),
                "missing_count": 0,
                "extra_count": 0,
                "issues": [],
                "metadata_blockers": descriptor.get("metadata_blockers", []),
                "score_blockers": ["evaluator_error", str(exc)],
                "error_type": type(exc).__name__,
                "error": str(exc),
                "independent_test_eligible": False,
            }
        else:
            summary = _score_summary(
                result, score_descriptor, path=path_text, role=role
            )
        per_reference.append(summary)
        score_by_path[path_text] = summary

    partition_diagnostics: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    for partition in index_data.get("partitions", []):
        selected_path = partition.get("selected_path")
        selected_score = score_by_path.get(selected_path)
        blockers = list(partition.get("blockers", []))
        if selected_score is None:
            blockers.append("selected_reference_has_no_score")
            score_summary = None
        else:
            blockers.extend(selected_score.get("score_blockers", []))
            score_summary = {
                key: selected_score.get(key)
                for key in (
                    "status",
                    "passed",
                    "expected",
                    "predicted",
                    "matched",
                    "precision",
                    "recall",
                    "missing_count",
                    "extra_count",
                    "reference_complete",
                    "timing_basis",
                    "evidence_hashes_checked",
                    "independent_test_eligible",
                )
            }
            for issue in selected_score.get("issues", []):
                all_issues.append(
                    {
                        "partition_id": partition.get("partition_id"),
                        "reference_path": selected_path,
                        **issue,
                    }
                )
        partition_diagnostics.append(
            {
                "partition_id": partition.get("partition_id"),
                "partition_ms": partition.get("partition_ms"),
                "selected_path": selected_path,
                "selected_canonical_path": partition.get("selected_canonical_path"),
                "candidate_paths": partition.get("candidate_paths", []),
                "candidate_count": partition.get("candidate_count"),
                "overlap": partition.get("overlap"),
                "tie": partition.get("tie"),
                "boundary_crossing": "selected_reference_crosses_partition_boundary"
                in partition.get("blockers", []),
                "scope": partition.get("selected", {}).get("scope"),
                "timing_basis": partition.get("selected", {}).get("timing_basis"),
                "completeness": partition.get("selected", {}).get("completeness"),
                "score_basis": "selected_reference_full_interval_diagnostic",
                "partition_accuracy_claimed": False,
                "score": score_summary,
                "blockers": sorted(set(blockers)),
            }
        )

    score_blockers = sorted(
        {
            blocker
            for score in per_reference
            for blocker in score.get("score_blockers", [])
            if blocker
        }
    )
    return {
        "schema": "tracen-replay/reference-index-score-v1",
        "index_schema": index_data.get("schema"),
        "source_sha256": source_sha,
        "report_sha256": report_digest,
        "report_source_sha256": report_source,
        "evidence_root": _portable(evidence_root),
        "per_reference": per_reference,
        "partitions": partition_diagnostics,
        "missing_extra_issues": all_issues,
        "coverage": {
            **dict(index_data.get("coverage", {})),
            "scored_reference_count": len(per_reference),
            "failed_reference_count": sum(
                score.get("status") != "passed" for score in per_reference
            ),
            "full_recording_effect_recall_measured": False,
        },
        "blockers": sorted(set(index_data.get("blockers", [])) | set(score_blockers)),
        "claims": {
            "full_recording_effect_recall_measured": False,
            "aggregate_accuracy_claimed": False,
            "independent_test_established": False,
            "partition_scores_additive": False,
        },
        "interpretation": (
            "Per-reference and partition diagnostics retain missing/extra issues. "
            "Overlapping references, revisions, incomplete scopes and timing bases "
            "are not summed into accuracy or whole-recording recall."
        ),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="build a reference index")
    build_parser.add_argument("run_root", type=Path)
    build_parser.add_argument("--coverage", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument(
        "--mapping",
        type=Path,
        help="JSON object mapping registered reference paths to immutable variants",
    )
    score_parser = subparsers.add_parser("score", help="score references in an index")
    score_parser.add_argument("run_root", type=Path)
    score_parser.add_argument("--index", type=Path, required=True)
    score_parser.add_argument("--report", type=Path, required=True)
    score_parser.add_argument("--evidence-root", type=Path)
    score_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        version_map: Mapping[str, Any] | None = None
        if args.mapping is not None:
            mapping_data = _read_json(args.mapping, label="version mapping")
            version_map = mapping_data
        result = build_reference_index(args.coverage, args.run_root, version_map=version_map)
        _write_json(args.output, result)
        print(json.dumps({"output": _portable(args.output), "partitions": len(result["partitions"])}))
        return 0
    result = score_reference_index(
        args.index,
        args.report,
        args.run_root,
        evidence_root=args.evidence_root,
    )
    _write_json(args.output, result)
    print(
        json.dumps(
            {
                "output": _portable(args.output),
                "references": len(result["per_reference"]),
                "partitions": len(result["partitions"]),
                "full_recording_effect_recall_measured": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
