"""Audit source-bound derived numeric accounting in the preserved reports.

The final-analyzer checklist defines this as a report and evidence audit.  It
does not rerun recognition and it does not infer a new effect from a residual.
The three preserved reports expose overlapping checkpoint and turn views, so
this script selects canonical contribution IDs through the 128 inventory
turns, then counts each contribution once per recording/source hash.

Run from the repository root with::

    python analyzer/lab/inventory_derived_reliability.py

Use ``--root`` when invoking the script from another directory.  The default
outputs are the two G5 artifacts under ``.local/final-reliability-v1``.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


REPORT_SPECS = (
    ("v1", "v1-report.json", "v1-report.inventory.json"),
    ("independent-01", "independent-01-report.json", "independent-01-report.inventory.json"),
    ("independent-02", "independent-02-report.json", "independent-02-report.inventory.json"),
)
REPORT_DIR = Path(".local/final-reliability-v1/before")
DEFAULT_OUTPUT = Path(".local/final-reliability-v1/derived-audit.json")
DEFAULT_MARKDOWN_OUTPUT = Path(".local/final-reliability-v1/derived-audit.md")

STATS_FIELDS = ("speed", "stamina", "power", "guts", "wit", "skill_points")
PERFORMANCE_FIELDS = ("dance", "passion", "vocal", "visual", "composure")
CHANNEL_FIELDS = {"stats": STATS_FIELDS, "performance": PERFORMANCE_FIELDS}

# This is the same direct set used by tracen_replay.causal_accounting when it
# computes ``direct_change``.  Everything else is retained as derived or
# summary accounting and must carry its own explicit basis.
DIRECT_BASES = {"observed_receipt", "observed_training_gain", "committed_skill_debit"}
PRICE_BASES = {"committed_offer_cost_derived"}
SUPPORTED_DERIVED_BASES = {
    "observed_balance_debit",
    "state_constrained",
    "state_derived",
    *PRICE_BASES,
}
UNSUPPORTED_BASES = {"projected_debit", "summary_only", "unresolved", "displayed_request"}

BASIS_CLASSIFICATION = {
    "observed_balance_debit": "observed_balance_debit",
    "committed_offer_cost_derived": "legitimate_price_derivation",
    "state_constrained": "state_constrained_visible_candidate",
    "state_derived": "state_derived_endpoint_difference",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gameplay_sha256(path: Path) -> str | None:
    """Hash the decoded RGB gameplay pane using the reader's fixed profile."""

    try:
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            if image.size != (810, 1080):
                return None
            return hashlib.sha256(image.tobytes()).hexdigest()
    except (OSError, ValueError):
        return None


def _source_digest_present(value: Any) -> bool:
    """Require a non-empty report/source digest before binding views."""

    return isinstance(value, str) and bool(value.strip())


def _turn_number(turn_id: str) -> int:
    try:
        return int(turn_id.rsplit("-", 1)[1])
    except (AttributeError, IndexError, ValueError):
        return 10**9


def _ordered_turn_ids(values: set[str] | list[str]) -> list[str]:
    return sorted(values, key=_turn_number)


def _unique(values: list[Any]) -> list[Any]:
    """Return first occurrences while preserving JSON values such as lists.

    Evidence pointers are normally strings, but a few report producers keep
    an aligned list of paths for a before/after proof.  ``dict.fromkeys``
    raises for those list values and prevents the bounded audit from running
    on an otherwise valid report.  Equality is sufficient here because this
    helper only handles the small proof-path lists, and it preserves the
    source order in the emitted audit.
    """

    result: list[Any] = []
    for value in values:
        if not any(value == existing for existing in result):
            result.append(value)
    return result


def _counter_dict(counter: Counter) -> dict[str, int]:
    return {str(key): int(value) for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}


def _window(values: list[int]) -> list[int] | None:
    return [min(values), max(values)] if values else None


def _integer(value: Any) -> bool:
    """Accept only real JSON integers for an audited operand."""

    return type(value) is int


def _purchase_reference_matches(
    contribution: dict[str, Any], purchase: dict[str, Any]
) -> bool:
    """Ensure a contribution points at its own lesson purchase row."""

    purchase_id = purchase.get("id")
    suffix = purchase_id.rsplit("-", 1)[-1] if isinstance(purchase_id, str) else ""
    if not suffix.isdigit() or not isinstance(contribution.get("event_ref"), str):
        return False
    return contribution["event_ref"] == (
        f"/gameplay_tracking/lesson_purchases/{int(suffix) - 1}"
    )


def _observation_identity(
    rows: list[dict[str, Any]],
    *,
    timestamp_key: str = "source_timestamp_ms",
    evidence_key: str = "evidence",
) -> dict[str, Any]:
    """Describe independent source observations without trusting row count.

    A repeated parser row can point to the same physical image and timestamp.
    Counts used for repeated proof therefore expose both dimensions, and the
    caller can require the number of distinct timestamps and paths it needs.
    """

    timestamps = [
        row.get(timestamp_key)
        for row in rows
        if _integer(row.get(timestamp_key))
    ]
    evidence = [
        row.get(evidence_key)
        for row in rows
        if isinstance(row.get(evidence_key), str) and row.get(evidence_key)
    ]
    return {
        "row_count": len(rows),
        "distinct_timestamp_count": len(set(timestamps)),
        "distinct_evidence_count": len(set(evidence)),
        "timestamps_ms": sorted(set(timestamps)),
        "evidence": list(dict.fromkeys(evidence)),
        "three_frame_proof": (
            len(set(timestamps)) >= 3 and len(set(evidence)) >= 3
        ),
    }


def _repeated_source_proof(
    rows: list[dict[str, Any]], minimum: int = 2
) -> bool:
    """Require independent timestamps and source paths for repeated proof."""

    identity = _observation_identity(rows)
    return (
        identity["distinct_timestamp_count"] >= minimum
        and identity["distinct_evidence_count"] >= minimum
    )


def _stable_result_suffix(
    event_rows: list[tuple[int, dict[str, Any]]], field: str
) -> list[tuple[int, dict[str, Any]]]:
    """Select the final repeated result value without using a claimed amount.

    This deliberately mirrors the source audit's amount-independent selector.
    The final contiguous suffix is selected first; only then may its value be
    subtracted from a before operand.  In particular, an intermediate counter
    that happens to equal ``before + claimed_amount`` cannot win selection.
    """

    ordered = sorted(
        event_rows,
        key=lambda item: (item[1].get("source_timestamp_ms", -1), item[0]),
    )
    suffix: list[tuple[int, dict[str, Any]]] = []
    for index, row in reversed(ordered):
        value = ((row.get("facts") or {}).get("result_values") or {}).get(field)
        if not _integer(value):
            if suffix:
                break
            continue
        if suffix:
            previous = (
                (suffix[-1][1].get("facts") or {}).get("result_values") or {}
            ).get(field)
            if value != previous:
                break
        suffix.append((index, row))
    suffix.reverse()
    if len(suffix) < 3:
        return []
    timestamps = [
        row.get("source_timestamp_ms")
        for _, row in suffix
        if _integer(row.get("source_timestamp_ms"))
    ]
    if len(set(timestamps)) < 3 or len(timestamps) != len(set(timestamps)):
        return []
    evidence = [
        row.get("evidence")
        for _, row in suffix
        if isinstance(row.get("evidence"), str) and row.get("evidence")
    ]
    if len(set(evidence)) < 3 or len(evidence) != len(set(evidence)):
        return []
    if max(timestamps) - min(timestamps) < 60:
        return []
    return suffix


def _legacy_after_matches(
    event_rows: list[tuple[int, dict[str, Any]]], field: str, expected: int | None
) -> list[tuple[int, dict[str, Any]]]:
    """Retain amount-driven matching only as a compatibility metric."""

    if not _integer(expected):
        return []
    return sorted(
        [
            (index, row)
            for index, row in event_rows
            if ((row.get("facts") or {}).get("result_values") or {}).get(field)
            == expected
        ],
        key=lambda item: (item[1].get("source_timestamp_ms", -1), item[0]),
    )


def _evidence_readings(
    contribution: dict[str, Any], readings_by_evidence: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the source rows for a contribution without changing its proof."""

    rows = []
    missing = []
    for evidence in contribution.get("evidence") or []:
        row = readings_by_evidence.get(evidence)
        if row is None:
            missing.append(evidence)
            continue
        rows.append(row)
    return rows, missing


def _supplemental_paths(value: Any) -> tuple[list[str], list[Any]]:
    """Split a typed supplemental-evidence value into paths and malformed items."""

    if value is None:
        return [], []
    if isinstance(value, str):
        return [value], []
    if isinstance(value, list):
        paths = [item for item in value if isinstance(item, str)]
        malformed = [item for item in value if not isinstance(item, str)]
        return paths, malformed
    return [], [value]


def _safe_relative_evidence_path(value: Any) -> bool:
    """Reject path aliases and escapes before joining an evidence root."""

    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    if "\\" in value or ":" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return "/".join(path.parts) == value


def _evidence_path_binding(declared: Any, sidecar: Any) -> str | None:
    """Match a sidecar path exactly or through its explicit source namespace."""

    if not _safe_relative_evidence_path(declared) or not _safe_relative_evidence_path(sidecar):
        return None
    if declared == sidecar:
        return "exact"
    declared_parts = PurePosixPath(declared).parts
    sidecar_parts = PurePosixPath(sidecar).parts
    if len(sidecar_parts) >= len(declared_parts):
        return None
    if declared_parts[-len(sidecar_parts) :] == sidecar_parts:
        return "namespace_suffix"
    return None


def _source_frame_hashes(value: Any) -> set[str]:
    """Collect source identity hashes from typed provenance without using values."""

    hashes: set[str] = set()
    if isinstance(value, dict):
        for key in ("source_frame_sha256", "source_frame_hash"):
            item = value.get(key)
            if isinstance(item, str) and item:
                hashes.add(item)
        for item in value.values():
            hashes.update(_source_frame_hashes(item))
    elif isinstance(value, list):
        for item in value:
            hashes.update(_source_frame_hashes(item))
    return hashes


def _source_frame_hashes_for_evidence(value: Any, evidence: Any) -> set[str]:
    """Collect identities only from provenance records bound to one path."""

    hashes: set[str] = set()
    if isinstance(value, dict):
        item_path = value.get("evidence", value.get("source_evidence"))
        if item_path is not None and _evidence_path_binding(evidence, item_path) is not None:
            hashes.update(_source_frame_hashes(value))
        for item in value.values():
            hashes.update(_source_frame_hashes_for_evidence(item, evidence))
    elif isinstance(value, list):
        for item in value:
            hashes.update(_source_frame_hashes_for_evidence(item, evidence))
    return hashes


def _provenance_timestamps_for_evidence(value: Any, evidence: Any) -> list[int]:
    """Return typed provenance times that point at one primary evidence path."""

    timestamps: list[int] = []
    if isinstance(value, dict):
        item_path = value.get("evidence", value.get("source_evidence"))
        timestamp = value.get("source_timestamp_ms")
        if (
            item_path is not None
            and _evidence_path_binding(evidence, item_path) is not None
            and _integer(timestamp)
        ):
            timestamps.append(timestamp)
        for item in value.values():
            timestamps.extend(_provenance_timestamps_for_evidence(item, evidence))
    elif isinstance(value, list):
        for item in value:
            timestamps.extend(_provenance_timestamps_for_evidence(item, evidence))
    return timestamps


def _supplemental_metadata(row: dict[str, Any], path: str) -> dict[str, Any]:
    """Read optional per-view integrity metadata without treating it as facts."""

    for key in ("supplemental_evidence_provenance", "supplemental_evidence_metadata"):
        value = row.get(key)
        if isinstance(value, dict):
            item = value.get(path)
            if isinstance(item, dict):
                return item
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                item_path = item.get("evidence", item.get("path"))
                if item_path == path:
                    return item
    return {}


def _main_source_identity(
    row: dict[str, Any], evidence_root: Path | None
) -> set[str]:
    """Find source-frame identities for the primary view when available."""

    hashes = set()
    if evidence_root is None:
        return hashes
    primary = row.get("evidence")
    if not _safe_relative_evidence_path(primary):
        return hashes
    root = evidence_root.resolve()
    image = (root / primary).resolve()
    try:
        image.relative_to(root)
    except ValueError:
        return hashes
    sidecar = image.with_suffix(".v2.json")
    if not sidecar.is_file():
        return hashes
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return hashes
    if _evidence_path_binding(primary, metadata.get("evidence")) is not None:
        item = metadata.get("source_frame_sha256")
        if isinstance(item, str) and item:
            hashes.add(item)
    return hashes


def _validate_supplemental_binding(
    row: dict[str, Any],
    path: str,
    *,
    report_source_sha256: str | None,
    evidence_root: Path | None,
) -> tuple[bool, dict[str, Any], str | None]:
    """Validate one supplemental view and return metadata for the index.

    A supplemental row is an availability pointer only.  It intentionally
    carries no facts from the primary row, so accepting the pointer cannot
    manufacture a result or endpoint operand.  When an evidence root is
    supplied, the adjacent v2 sidecar binds the path to its timestamp, source
    video, source-frame identity, and decoded RGB gameplay pixels.  The
    primary row's claimed source-frame hashes are deliberately not used as
    supplemental authorization.
    """

    binding: dict[str, Any] = {
        "supplemental_evidence": path,
        "main_evidence": row.get("evidence"),
        "main_source_timestamp_ms": row.get("source_timestamp_ms"),
        "inspection_conflicts_present": bool(row.get("inspection_conflicts")),
        "merge_provenance_present": isinstance(
            (row.get("facts") or {}).get("inspection_merge_provenance"), dict
        ),
        "physical_validation": "not_requested",
        "source_identity_match": "unavailable",
    }
    if not _safe_relative_evidence_path(path):
        return False, binding, "unsafe_or_noncanonical_supplemental_path"
    if not _source_digest_present(report_source_sha256):
        return False, binding, "report_source_sha256_missing_or_invalid"
    main_timestamp = row.get("source_timestamp_ms")
    if not _integer(main_timestamp):
        return False, binding, "main_timestamp_missing_or_non_integer"
    metadata = _supplemental_metadata(row, path)
    declared_timestamp = metadata.get("source_timestamp_ms", metadata.get("timestamp_ms"))
    if declared_timestamp is not None and declared_timestamp != main_timestamp:
        return False, binding, "declared_supplemental_timestamp_mismatch"
    declared_source = metadata.get("source_sha256")
    if (
        declared_source is not None
        and declared_source != report_source_sha256
    ):
        return False, binding, "declared_supplemental_source_mismatch"
    expected_physical = metadata.get(
        "physical_image_sha256", metadata.get("image_sha256")
    )
    merge_provenance = (row.get("facts") or {}).get("inspection_merge_provenance")
    merge_times = _provenance_timestamps_for_evidence(
        merge_provenance, row.get("evidence")
    )
    if merge_times:
        binding["merge_provenance_timestamps_ms"] = sorted(set(merge_times))
        if any(timestamp != main_timestamp for timestamp in merge_times):
            return False, binding, "main_merge_provenance_timestamp_mismatch"

    if evidence_root is None:
        binding["physical_validation"] = "not_requested"
        return True, binding, None

    root = evidence_root.resolve()
    image = (root / path).resolve()
    try:
        image.relative_to(root)
    except ValueError:
        return False, binding, "supplemental_path_escapes_evidence_root"
    if not image.is_file():
        return False, binding, "supplemental_image_missing"
    try:
        physical_hash = _sha256(image)
    except OSError:
        return False, binding, "supplemental_image_unreadable"
    binding["physical_image_sha256"] = physical_hash
    if expected_physical is not None and physical_hash != expected_physical:
        return False, binding, "supplemental_physical_hash_mismatch"

    sidecar = image.with_suffix(".v2.json")
    if not sidecar.is_file():
        return False, binding, "supplemental_source_sidecar_missing"
    try:
        sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, binding, "supplemental_source_sidecar_invalid"
    binding["sidecar"] = str(sidecar.relative_to(root).as_posix())
    sidecar_timestamp = sidecar_data.get("source_timestamp_ms")
    sidecar_source = sidecar_data.get("source_sha256")
    sidecar_path = sidecar_data.get("evidence")
    source_frame_hash = sidecar_data.get("source_frame_sha256")
    sidecar_path_binding = _evidence_path_binding(path, sidecar_path)
    if sidecar_path_binding is None:
        return False, binding, "supplemental_sidecar_path_mismatch"
    binding["sidecar_path_binding"] = sidecar_path_binding
    if sidecar_timestamp != main_timestamp:
        return False, binding, "supplemental_sidecar_timestamp_mismatch"
    if sidecar_source != report_source_sha256:
        return False, binding, "supplemental_sidecar_source_mismatch"
    if not isinstance(source_frame_hash, str) or not source_frame_hash:
        return False, binding, "supplemental_sidecar_source_identity_missing"
    binding["source_frame_sha256"] = source_frame_hash
    binding["source_timestamp_ms"] = sidecar_timestamp
    binding["source_sha256"] = sidecar_source
    sidecar_gameplay_hash = sidecar_data.get("gameplay_sha256")
    if not isinstance(sidecar_gameplay_hash, str) or not sidecar_gameplay_hash:
        return False, binding, "supplemental_sidecar_gameplay_hash_missing"
    actual_gameplay_hash = _gameplay_sha256(image)
    if actual_gameplay_hash is None:
        return False, binding, "supplemental_image_not_valid_gameplay_pane"
    binding["gameplay_sha256"] = actual_gameplay_hash
    binding["sidecar_gameplay_sha256"] = sidecar_gameplay_hash
    if actual_gameplay_hash != sidecar_gameplay_hash:
        return False, binding, "supplemental_gameplay_hash_mismatch"
    expected_identities = _main_source_identity(row, evidence_root)
    if expected_identities:
        binding["source_identity_match"] = source_frame_hash in expected_identities
        if source_frame_hash not in expected_identities:
            return False, binding, "supplemental_source_identity_mismatch"
    if isinstance(metadata.get("source_frame_sha256"), str) and metadata[
        "source_frame_sha256"
    ] != source_frame_hash:
        return False, binding, "declared_supplemental_source_identity_mismatch"
    binding["physical_validation"] = "validated_sidecar_and_image"
    return True, binding, None


def _index_report_readings(
    report: dict[str, Any],
    *,
    report_source_sha256: str | None = None,
    evidence_root: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Index primary and explicitly bound supplemental source views.

    Supplemental entries are wrappers containing only source identity and
    timestamp metadata.  Primary ``facts``, ``stats``, OCR, and effects are
    never copied onto the wrapper.  This keeps evidence availability separate
    from proof content while allowing the auditor to resolve a cited native-v2
    path when its physical source binding is valid.
    """

    readings = (report.get("gameplay_tracking") or {}).get("readings", [])
    index: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    primary_rows: list[dict[str, Any]] = []
    for row in readings:
        if not isinstance(row, dict):
            errors.append({"kind": "reading_not_mapping"})
            continue
        primary_rows.append(row)
        evidence = row.get("evidence")
        if not isinstance(evidence, str) or not evidence:
            errors.append({"kind": "reading_missing_primary_evidence"})
            continue
        if evidence in index:
            errors.append({"kind": "duplicate_reading_evidence_id", "evidence": evidence})
        index[evidence] = row

    declared_count = 0
    indexed_count = 0
    rejected_count = 0
    validated_physical_count = 0
    source_identity_crosschecked_count = 0
    supplemental_bindings: list[dict[str, Any]] = []
    for row in primary_rows:
        paths, malformed = _supplemental_paths(row.get("supplemental_evidence"))
        for item in malformed:
            errors.append(
                {
                    "kind": "malformed_supplemental_evidence",
                    "main_evidence": row.get("evidence"),
                    "value": item,
                }
            )
        for path in paths:
            declared_count += 1
            binding = {
                "supplemental_evidence": path,
                "main_evidence": row.get("evidence"),
            }
            if path in index:
                errors.append(
                    {
                        "kind": "supplemental_evidence_collides_with_primary",
                        "evidence": path,
                        "main_evidence": row.get("evidence"),
                    }
                )
                rejected_count += 1
                continue
            valid, binding, reason = _validate_supplemental_binding(
                row,
                path,
                report_source_sha256=report_source_sha256,
                evidence_root=evidence_root,
            )
            supplemental_bindings.append(binding)
            if not valid:
                rejected_count += 1
                errors.append(
                    {
                        "kind": "invalid_supplemental_evidence_binding",
                        "main_evidence": row.get("evidence"),
                        "evidence": path,
                        "reason": reason,
                        "binding": binding,
                    }
                )
                continue
            wrapper = {
                "evidence": path,
                "source_timestamp_ms": binding.get(
                    "source_timestamp_ms", row.get("source_timestamp_ms")
                ),
                "_source_view": "supplemental_bound",
                "_supplemental_binding": binding,
            }
            # No facts/stats/ocr/effects are copied from the primary row.
            index[path] = wrapper
            indexed_count += 1
            if binding.get("physical_validation") == "validated_sidecar_and_image":
                validated_physical_count += 1
            if binding.get("source_identity_match") is True:
                source_identity_crosschecked_count += 1

    stats = {
        "primary_reading_count": len(primary_rows),
        "supplemental_declared_count": declared_count,
        "supplemental_indexed_count": indexed_count,
        "supplemental_rejected_count": rejected_count,
        "supplemental_validated_physical_count": validated_physical_count,
        "supplemental_source_identity_crosschecked_count": source_identity_crosschecked_count,
        "bindings": supplemental_bindings,
    }
    return index, errors, stats


def _evidence_timestamp_alignment(
    evidence: list[Any], readings_by_evidence: dict[str, dict[str, Any]]
) -> list[int] | None:
    """Return source times aligned one-for-one with an evidence path list.

    ``evidence_source_timestamp_ms`` is consumed by the source-bundle builder
    as an aligned list when it accompanies an aggregate ``evidence`` field.
    The report's evidence order is not guaranteed to be chronological, so a
    separately sorted set of times would bind the wrong timestamp to a frame.
    Return ``None`` when any path lacks an integer source row; callers can
    retain the sorted set for a summary without presenting it as alignment.
    """

    if not evidence or not all(isinstance(path, str) for path in evidence):
        return None
    timestamps: list[int] = []
    for path in evidence:
        row = readings_by_evidence.get(path)
        timestamp = row.get("source_timestamp_ms") if row else None
        if not _integer(timestamp):
            return None
        timestamps.append(timestamp)
    return timestamps


def _decorate_aggregate_evidence(
    value: dict[str, Any] | None,
    readings_by_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Copy an aggregate observation and bind each cited frame to its time."""

    if not isinstance(value, dict):
        return value
    result = dict(value)
    paths = value.get("evidence")
    aligned = _evidence_timestamp_alignment(paths, readings_by_evidence) if isinstance(paths, list) else None
    if aligned is not None:
        result["evidence_source_timestamp_ms"] = aligned
        result["evidence_timestamp_order"] = "aligned_to_evidence_paths"
    return result


def _reading_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    times = sorted(
        {
            row.get("source_timestamp_ms")
            for row in rows
            if type(row.get("source_timestamp_ms")) is int
        }
    )
    screens = Counter(row.get("screen", "unknown") for row in rows)
    return {
        "source_timestamp_ms": times,
        "source_window_ms": _window(times),
        "screens": _counter_dict(screens),
        "row_count": len(rows),
    }


def _compact_transition(transition: dict[str, Any], field: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn_id": transition["turn_id"],
        "channel": transition["channel"],
        "field": field["field"],
        "status": field["status"],
        "start_ms": transition.get("start_ms"),
        "end_ms": transition.get("end_ms"),
        "before": field.get("before"),
        "after": field.get("after"),
        "observed_change": field.get("observed_change"),
        "direct_change": field.get("direct_change"),
        "derived_or_summary_change": field.get("derived_or_summary_change"),
        "unresolved_change": field.get("unresolved_change"),
        "before_state_ref": transition.get("before_state_ref"),
        "after_state_ref": transition.get("after_state_ref"),
        "accounting_role": transition.get("accounting_role"),
        "endpoint_basis": transition.get("endpoint_basis"),
    }


def _event_proof(
    contribution: dict[str, Any],
    event: dict[str, Any] | None,
    evidence_rows: list[dict[str, Any]],
    missing_evidence: list[str],
) -> dict[str, Any]:
    field = contribution["field"]
    field_evidence = (event or {}).get("field_evidence", {}).get(field, [])
    result_rows = []
    result_candidate_rows = []
    accepted_gain_rows = []
    for row in evidence_rows:
        facts = row.get("facts") or {}
        if field in (facts.get("result_values") or {}):
            result_rows.append(row)
        if field in (facts.get("result_value_candidates") or {}):
            result_candidate_rows.append(row)
        if field in (facts.get("training_gains") or {}):
            accepted_gain_rows.append(row)
    derived_fields = (event or {}).get("result_state_derived_fields", [])
    structural_basis_present = (
        event is not None
        and field in derived_fields
        and bool(field_evidence)
        and not missing_evidence
        and bool(result_rows or result_candidate_rows)
        and not accepted_gain_rows
        and field_evidence == contribution.get("evidence", [])
    )
    return {
        "event_id": (event or {}).get("id"),
        "event_kind": (event or {}).get("kind"),
        "event_result_state_derived_fields": list(derived_fields),
        "event_field_evidence": list(field_evidence),
        "event_field_evidence_matches_contribution": field_evidence == contribution.get("evidence", []),
        "result_value_reading_count": len(result_rows),
        "result_value_candidate_reading_count": len(result_candidate_rows),
        "accepted_training_gain_reading_count": len(accepted_gain_rows),
        "conflicting_readings": (event or {}).get("conflicting_readings", {}),
        "structural_basis_present": structural_basis_present,
        "structural_basis_definition": (
            "Event tag, field evidence, and result/candidate readings are present; "
            "this does not verify source operands or effect identity."
        ),
        "proof_limitation": (
            "Stable result counters and surrounding state support the amount; this is not an independent receipt."
        ),
    }


def _resolution_proof(
    contribution: dict[str, Any],
    event: dict[str, Any] | None,
    missing_evidence: list[str],
) -> dict[str, Any]:
    field = contribution["field"]
    resolutions = [
        resolution
        for resolution in (event or {}).get("state_supported_candidate_resolutions", [])
        if resolution.get("field") == field
    ]
    resolution = resolutions[0] if len(resolutions) == 1 else None
    gain_evidence = list((resolution or {}).get("gain_evidence") or [])
    before_evidence = (resolution or {}).get("before_evidence")
    after_evidence = (resolution or {}).get("after_evidence")
    proof_paths = gain_evidence + [path for path in (before_evidence, after_evidence) if path]
    structural_basis_present = (
        resolution is not None
        and not missing_evidence
        and resolution.get("basis") == "visible_complete_gain_and_surrounding_state_constraints"
        and type(resolution.get("amount")) is int
        and all(path in contribution.get("evidence", []) for path in gain_evidence)
        and len(set(proof_paths)) >= 1
    )
    return {
        "event_id": (event or {}).get("id"),
        "resolution_count_for_field": len(resolutions),
        "resolution": resolution,
        "resolution_amount": (resolution or {}).get("amount"),
        "contribution_amount_match": (
            resolution is not None
            and resolution.get("amount") == contribution.get("amount")
        ),
        "visual_candidate_count": len((resolution or {}).get("visual_candidates") or []),
        "visual_candidates": list((resolution or {}).get("visual_candidates") or []),
        "proof_paths": _unique(proof_paths),
        "event_conflicts": (event or {}).get("conflicting_readings", {}),
        "structural_basis_present": structural_basis_present,
        "structural_basis_definition": (
            "A field-only candidate resolution and its pointers are present; "
            "this does not verify the candidate's effect independently."
        ),
        "proof_limitation": (
            "A visible candidate is selected by surrounding state constraints; it is not independent effect verification."
        ),
    }


def _purchase_balance_proof(
    contribution: dict[str, Any],
    purchase: dict[str, Any] | None,
    evidence_rows: list[dict[str, Any]],
    missing_evidence: list[str],
    debit_observations: list[dict[str, Any]],
    receipt_events_by_id: dict[str, dict[str, Any]] | None = None,
    readings_by_evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Audit a stored lesson debit using source-pinned balance operands.

    Selection is based on the recorded purchase event time and evidence
    pointers.  The contribution amount is compared only after the before and
    after values have been chosen, so it cannot select a convenient counter.
    A separate debit observation is useful when present, but its absence is a
    source-availability limitation rather than evidence that the balance
    operands are absent.
    """

    field = contribution["field"]
    purchase = purchase or {}
    performance_cost = purchase.get("performance_cost") or {}
    action_time = purchase.get("source_timestamp_ms")
    selections = [
        row
        for row in evidence_rows
        if row.get("screen") == "lesson_selection"
        and isinstance((row.get("facts") or {}).get("performance_points"), dict)
        and _integer(
            (row.get("facts") or {}).get("performance_points", {}).get(field)
        )
        and _integer(row.get("source_timestamp_ms"))
    ]
    selections.sort(key=lambda row: row.get("source_timestamp_ms", 0))
    before_candidates = [
        row for row in selections if _integer(action_time) and row["source_timestamp_ms"] < action_time
    ]
    after_candidates = [
        row for row in selections if _integer(action_time) and row["source_timestamp_ms"] > action_time
    ]
    # The latest immediately pre-action menu and earliest post-action menu are
    # selected from the recorded proof paths.  No amount is consulted here.
    before = before_candidates[-1] if before_candidates else None
    after = after_candidates[0] if after_candidates else None
    before_points = (before or {}).get("facts", {}).get("performance_points", {})
    after_points = (after or {}).get("facts", {}).get("performance_points", {})
    before_value = before_points.get(field)
    after_value = after_points.get(field)
    difference = (
        before_value - after_value
        if _integer(before_value) and _integer(after_value)
        else None
    )
    source_delta = {
        name: before_points[name] - after_points[name]
        for name in PERFORMANCE_FIELDS
        if _integer(before_points.get(name)) and _integer(after_points.get(name))
    }
    unavailable_cost_fields = sorted(
        name for name in performance_cost if name not in source_delta
    )
    comparable_cost_fields = sorted(
        name for name in performance_cost if name in source_delta
    )
    source_cost_matches = bool(comparable_cost_fields) and all(
        source_delta[name] == performance_cost.get(name)
        for name in comparable_cost_fields
    )
    full_source_cost_match = (
        not unavailable_cost_fields
        and source_cost_matches
        and set(source_delta) >= set(performance_cost)
    )

    purchase_ref_matches = _purchase_reference_matches(contribution, purchase)

    names = {
        purchase.get("name"),
        purchase.get("requested_name"),
        purchase.get("receipt_name"),
    }
    names.update(purchase.get("observed_request_names") or [])
    names.discard(None)
    evidence_paths = set(contribution.get("evidence") or [])
    if before:
        evidence_paths.add(before.get("evidence"))
    if after:
        evidence_paths.add(after.get("evidence"))
    evidence_paths.discard(None)
    debit_candidates = [
        row
        for row in debit_observations
        if row.get("name") in names
        and set(row.get("evidence") or []).intersection(evidence_paths)
    ]
    debit_candidates.sort(
        key=lambda row: (
            abs(row.get("source_timestamp_ms", 0) - after.get("source_timestamp_ms", 0))
            if after and _integer(row.get("source_timestamp_ms"))
            else 10**18,
            row.get("source_timestamp_ms", 0),
        )
    )
    debit_observation = debit_candidates[0] if debit_candidates else None
    debit_observation_cost_match = bool(
        debit_observation
        and debit_observation.get("performance_cost") == performance_cost
    )
    debit_observation_order_ok = bool(
        debit_observation
        and before
        and after
        and _integer(debit_observation.get("source_timestamp_ms"))
        and before["source_timestamp_ms"]
        <= debit_observation["source_timestamp_ms"]
        <= after["source_timestamp_ms"]
    )
    debit_observation_output = _decorate_aggregate_evidence(
        debit_observation, readings_by_evidence or {}
    )

    receipt_events_by_id = receipt_events_by_id or {}
    receipt = receipt_events_by_id.get(purchase.get("receipt_event_id"))
    receipt_acquisitions = [
        effect
        for effect in (receipt or {}).get("effects", [])
        if effect.get("kind") in ("song_learned", "named_acquisition")
    ]
    receipt_owner_ok = bool(
        receipt
        and not receipt.get("conflicting_readings")
        and len(receipt_acquisitions) == 1
        and receipt_acquisitions[0].get("name") == purchase.get("name")
    )
    receipt_first_seen_ms = (receipt or {}).get("first_seen_ms")
    receipt_temporal_order_ok = bool(
        receipt
        and _integer(receipt_first_seen_ms)
        and before
        and after
        and _integer(action_time)
        and action_time <= receipt_first_seen_ms <= after["source_timestamp_ms"]
    )
    temporal_order_ok = bool(
        before
        and after
        and _integer(action_time)
        and before["source_timestamp_ms"] < action_time < after["source_timestamp_ms"]
    )
    source_selection_identity = _observation_identity(selections)
    arithmetic_verified = bool(
        purchase
        and purchase.get("cost_basis") == "observed_debit"
        and not missing_evidence
        and before
        and after
        and temporal_order_ok
        and receipt_temporal_order_ok
        and _integer(performance_cost.get(field))
        and difference == performance_cost.get(field)
        and source_cost_matches
        and all(value >= 0 for value in source_delta.values())
        and purchase_ref_matches
    )
    if arithmetic_verified and receipt_owner_ok:
        audit_status = "verified_source_balance"
    elif arithmetic_verified:
        audit_status = "compatible_ownership_conflict"
    else:
        audit_status = "unverified_source_operands"
    structural_basis_present = bool(
        purchase
        and purchase.get("cost_basis") == "observed_debit"
        and not missing_evidence
        and isinstance(performance_cost, dict)
        and field in performance_cost
    )
    return {
        "structural_basis_present": structural_basis_present,
        "structural_basis_definition": (
            "Recorded purchase cost basis, field cost, and source pointers are present; "
            "this does not verify the source operands or receipt ownership."
        ),
        "purchase_id": purchase.get("id"),
        "purchase_name": purchase.get("name"),
        "receipt_event_id": purchase.get("receipt_event_id"),
        "cost_basis": purchase.get("cost_basis"),
        "performance_cost": performance_cost,
        "requested_name": purchase.get("requested_name"),
        "receipt_name": purchase.get("receipt_name"),
        "after_balance_observed": purchase.get("after_balance_observed"),
        "complete_transaction_verified": purchase.get("complete_transaction_verified"),
        "before_balance": {
            "source_timestamp_ms": (before or {}).get("source_timestamp_ms"),
            "evidence": (before or {}).get("evidence"),
            "value": before_value,
        },
        "after_balance": {
            "source_timestamp_ms": (after or {}).get("source_timestamp_ms"),
            "evidence": (after or {}).get("evidence"),
            "value": after_value,
        },
        "observed_difference": difference,
        "stored_field_cost": performance_cost.get(field),
        "source_delta": source_delta,
        "source_cost_matches": source_cost_matches,
        "full_source_cost_match": full_source_cost_match,
        "comparable_cost_fields": comparable_cost_fields,
        "unavailable_cost_fields": unavailable_cost_fields,
        "selected_by": "latest_preaction_and_earliest_postaction_source_pointers",
        "selection_observation_identity": source_selection_identity,
        "temporal_order_ok": temporal_order_ok,
        "purchase_ref_matches": purchase_ref_matches,
        "receipt_owner_ok": receipt_owner_ok,
        "receipt_first_seen_ms": receipt_first_seen_ms,
        "receipt_temporal_order_ok": receipt_temporal_order_ok,
        "receipt_acquisition_names": [effect.get("name") for effect in receipt_acquisitions],
        "debit_observation": debit_observation_output,
        "debit_observation_count": len(debit_candidates),
        "debit_observation_cost_match": debit_observation_cost_match,
        "debit_observation_order_ok": debit_observation_order_ok,
        "source_operand_audit_status": audit_status,
        "source_arithmetic_verified": arithmetic_verified,
        "contribution_amount_match": (
            _integer(contribution.get("amount"))
            and _integer(performance_cost.get(field))
            and contribution.get("amount") == -performance_cost.get(field)
        ),
        "proof_limitation": (
            "The affected performance balance is observed before and after the purchase; "
            "a separate lesson_debit_observation is reported when its source path is available. "
            "A conflicted receipt name or missing uncharged fields remains explicit."
        ),
    }


def _offer_source_values(
    row: dict[str, Any] | None,
    field: str,
    card_index: Any,
    title: str | None,
) -> list[dict[str, Any]]:
    """Read one field's price from the source row named by a proof entry."""

    if not row:
        return []
    facts = row.get("facts") or {}
    offers = facts.get("lesson_offer_observations", row.get("lesson_offer_observations"))
    if not isinstance(offers, list):
        return []
    matches = []
    for offer in offers:
        if not isinstance(offer, dict) or offer.get("card_index") != card_index:
            continue
        offer_title = offer.get("title")
        if isinstance(offer_title, dict):
            offer_title = offer_title.get("text")
        if title is not None and offer_title != title:
            continue
        for price in offer.get("prices") or []:
            if isinstance(price, dict) and price.get("field") == field:
                matches.append(price)
    return matches


def _validate_offer_field_entries(
    entries: list[dict[str, Any]],
    field: str,
    title: str | None,
    card_index: Any,
    readings_by_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate field price entries against their pointed source frames."""

    checks = []
    for entry in entries:
        if not isinstance(entry, dict):
            checks.append({"valid": False, "reason": "entry_not_mapping"})
            continue
        path = entry.get("evidence")
        row = readings_by_evidence.get(path) if isinstance(path, str) else None
        source_prices = _offer_source_values(row, field, card_index, title)
        source_values = [price.get("value") for price in source_prices]
        valid = bool(
            row
            and row.get("screen") == "lesson_selection"
            and _integer(entry.get("timestamp_ms"))
            and row.get("source_timestamp_ms") == entry.get("timestamp_ms")
            and _integer(entry.get("value"))
            and source_values
            and all(value == entry.get("value") for value in source_values)
        )
        checks.append(
            {
                "evidence": path,
                "timestamp_ms": entry.get("timestamp_ms"),
                "recorded_value": entry.get("value"),
                "source_values": source_values,
                "valid": valid,
            }
        )
    identity = _observation_identity(
        [
            {"source_timestamp_ms": item.get("timestamp_ms"), "evidence": item.get("evidence")}
            for item in entries
            if isinstance(item, dict)
        ]
    )
    return {
        "entry_count": len(entries),
        "entries": checks,
        "identity": identity,
        "all_entries_source_bound": bool(checks) and all(item.get("valid") for item in checks),
        "repeated_source_bound": bool(checks)
        and all(item.get("valid") for item in checks)
        and _repeated_source_proof(
            [
                {"source_timestamp_ms": item.get("timestamp_ms"), "evidence": item.get("evidence")}
                for item in checks
            ]
        ),
    }


def _validate_projection_entries(
    entries: list[dict[str, Any]],
    field: str,
    readings_by_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate request projection operands without using contribution amount."""

    checks = []
    for entry in entries:
        if not isinstance(entry, dict):
            checks.append({"valid": False, "reason": "entry_not_mapping"})
            continue
        path = entry.get("evidence")
        row = readings_by_evidence.get(path) if isinstance(path, str) else None
        projected = (row or {}).get("facts", {}).get("projected_performance_points", {})
        valid = bool(
            row
            and row.get("screen") == "lesson_confirmation"
            and _integer(entry.get("timestamp_ms"))
            and row.get("source_timestamp_ms") == entry.get("timestamp_ms")
            and _integer(entry.get("value"))
            and projected.get(field) == entry.get("value")
        )
        checks.append(
            {
                "evidence": path,
                "timestamp_ms": entry.get("timestamp_ms"),
                "recorded_value": entry.get("value"),
                "source_value": projected.get(field),
                "valid": valid,
            }
        )
    identity = _observation_identity(
        [
            {"source_timestamp_ms": item.get("timestamp_ms"), "evidence": item.get("evidence")}
            for item in entries
            if isinstance(item, dict)
        ]
    )
    return {
        "entry_count": len(entries),
        "entries": checks,
        "identity": identity,
        "all_entries_source_bound": bool(checks) and all(item.get("valid") for item in checks),
        "repeated_source_bound": bool(checks)
        and all(item.get("valid") for item in checks)
        and _repeated_source_proof(
            [
                {"source_timestamp_ms": item.get("timestamp_ms"), "evidence": item.get("evidence")}
                for item in checks
            ]
        ),
    }


def _validate_initial_field(
    paths: list[str],
    field: str,
    expected_value: Any,
    readings_by_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate stored initial operands against pointed lesson menus."""

    checks = []
    for path in paths:
        row = readings_by_evidence.get(path) if isinstance(path, str) else None
        points = (row or {}).get("facts", {}).get("performance_points", {})
        valid = bool(
            row
            and row.get("screen") == "lesson_selection"
            and _integer(expected_value)
            and _integer(row.get("source_timestamp_ms"))
            and _integer(points.get(field))
            and points.get(field) == expected_value
        )
        checks.append(
            {
                "evidence": path,
                "timestamp_ms": (row or {}).get("source_timestamp_ms"),
                "recorded_value": expected_value,
                "source_value": points.get(field),
                "valid": valid,
            }
        )
    identity = _observation_identity(
        [
            {
                "source_timestamp_ms": item.get("timestamp_ms"),
                "evidence": item.get("evidence"),
            }
            for item in checks
        ]
    )
    return {
        "entry_count": len(paths),
        "entries": checks,
        "identity": identity,
        "all_entries_source_bound": bool(checks)
        and all(item.get("valid") for item in checks),
        "repeated_source_bound": bool(checks)
        and all(item.get("valid") for item in checks)
        and _repeated_source_proof(
            [
                {
                    "source_timestamp_ms": item.get("timestamp_ms"),
                    "evidence": item.get("evidence"),
                }
                for item in checks
            ]
        ),
    }


def _purchase_price_proof(
    contribution: dict[str, Any],
    purchase: dict[str, Any] | None,
    receipt_events_by_id: dict[str, dict[str, Any]],
    readings_by_evidence: dict[str, dict[str, Any]],
    missing_evidence: list[str],
) -> dict[str, Any]:
    field = contribution["field"]
    purchase = purchase or {}
    offer_proof = purchase.get("offer_cost_evidence") or {}
    field_proof = (offer_proof.get("fields") or {}).get(field) or {}
    receipt = receipt_events_by_id.get(purchase.get("receipt_event_id"))
    offer = offer_proof.get("offer") or {}
    request = offer_proof.get("request") or {}

    def proof_times(section: dict[str, Any]) -> list[int]:
        return sorted({value for value in section.get("timestamps_ms", []) if _integer(value)})

    offer_times = proof_times(offer)
    request_times = proof_times(request)
    initial = offer_proof.get("initial") or {}
    initial_times = proof_times(initial)
    offer_paths = list(offer.get("evidence") or [])
    request_paths = list(request.get("evidence") or [])
    initial_paths = list(initial.get("evidence") or [])
    receipt_name = purchase.get("receipt_name")

    def section_identity(paths: list[str]) -> dict[str, Any]:
        return _observation_identity(
            [
                {
                    "source_timestamp_ms": readings_by_evidence.get(path, {}).get(
                        "source_timestamp_ms"
                    ),
                    "evidence": path,
                }
                for path in paths
            ]
        )

    offer_identity = section_identity(offer_paths)
    request_identity = section_identity(request_paths)
    initial_identity = section_identity(initial_paths)

    def section_times_match(paths: list[str], expected_times: set[int]) -> bool:
        actual_times = [
            readings_by_evidence.get(path, {}).get("source_timestamp_ms")
            for path in paths
        ]
        return bool(paths) and (
            len(paths) == len(set(paths))
            and all(_integer(value) for value in actual_times)
            and set(actual_times) == expected_times
        )

    source_times_match = all(
        section_times_match(paths, expected_times)
        for paths, expected_times in (
            (offer_paths, set(offer_times)),
            (request_paths, set(request_times)),
            (initial_paths, set(initial_times)),
        )
    )
    offer_source_rows = [readings_by_evidence.get(path) for path in offer_paths]
    request_source_rows = [readings_by_evidence.get(path) for path in request_paths]
    # Title validation does not depend on the contribution amount: inspect
    # every pointed menu row and require the named card to be present.
    # ``offer`` does not carry card_index; derive it from any field proof.
    card_indices = {
        item.get("card_index")
        for proof in (offer_proof.get("fields") or {}).values()
        for item in (proof.get("offer_evidence") or [])
        if isinstance(item, dict)
    }
    offer_card_index = next(iter(card_indices), None) if len(card_indices) == 1 else None
    offer_titles_ok = bool(offer_source_rows) and all(
        row
        and row.get("screen") == "lesson_selection"
        and any(
            isinstance(item, dict)
            and item.get("card_index") == offer_card_index
            and (
                (item.get("title") or {}).get("text")
                if isinstance(item.get("title"), dict)
                else item.get("title")
            )
            == offer.get("title")
            for item in ((row.get("facts") or {}).get("lesson_offer_observations") or [])
        )
        for row in offer_source_rows
    )
    request_titles_ok = bool(request_source_rows) and all(
        row
        and row.get("screen") == "lesson_confirmation"
        and (
            not (row.get("facts") or {}).get("name_candidates")
            or (row.get("facts") or {}).get("name_candidates") == [request.get("title")]
        )
        for row in request_source_rows
    )

    field_audits = {}
    for name, proof in (offer_proof.get("fields") or {}).items():
        if not isinstance(proof, dict):
            field_audits[name] = {"status": "invalid_field_proof"}
            continue
        source_offer = _validate_offer_field_entries(
            list(proof.get("offer_evidence") or []),
            name,
            offer.get("title"),
            offer_card_index,
            readings_by_evidence,
        )
        source_projection = _validate_projection_entries(
            list(proof.get("projection_evidence") or []), name, readings_by_evidence
        )
        source_initial = _validate_initial_field(
            initial_paths,
            name,
            proof.get("initial_value"),
            readings_by_evidence,
        )
        cost = proof.get("cost")
        projected_delta = None
        if _integer(proof.get("initial_value")) and _integer(proof.get("projected_value")):
            projected_delta = proof["initial_value"] - proof["projected_value"]
        projection_matches = (
            projected_delta is not None and _integer(cost) and projected_delta == cost
        )
        offer_matches = (
            _integer(proof.get("offer_price"))
            and _integer(cost)
            and proof.get("offer_price") == cost
            and source_offer.get("repeated_source_bound") is True
            and source_initial.get("repeated_source_bound") is True
        )
        projection_supported = (
            projection_matches and source_projection.get("repeated_source_bound") is True
            and source_initial.get("repeated_source_bound") is True
        )
        field_audits[name] = {
            "stored_cost": cost,
            "initial_value": proof.get("initial_value"),
            "projected_value": proof.get("projected_value"),
            "projected_delta": projected_delta,
            "offer_price": proof.get("offer_price"),
            "offer_source": source_offer,
            "projection_source": source_projection,
            "initial_source": source_initial,
            "projection_matches_cost": projection_matches,
            "offer_matches_cost": offer_matches,
            "source_price_supported": bool(
                _integer(cost)
                and cost >= 0
                and (offer_matches or projection_supported)
            ),
        }
    stored_cost = offer_proof.get("cost") or {}
    field_costs_match = all(
        _integer(stored_cost.get(name))
        and field_audits.get(name, {}).get("stored_cost") == stored_cost.get(name)
        for name in stored_cost
    )
    source_price_fields = sorted(
        name
        for name, audit in field_audits.items()
        if audit.get("source_price_supported")
    )
    source_price_missing_fields = sorted(
        name for name in stored_cost if name not in source_price_fields
    )
    actual_price_operands_verified = bool(
        stored_cost
        and field_costs_match
        and not source_price_missing_fields
        and _integer(offer_proof.get("total_cost"))
        and offer_proof.get("total_cost") == sum(stored_cost.values())
    )
    receipt_acquisitions = [
        effect
        for effect in (receipt or {}).get("effects", [])
        if effect.get("kind") in ("song_learned", "named_acquisition")
    ]
    exact_receipt = bool(
        receipt
        and receipt.get("id") == purchase.get("receipt_event_id")
        and not receipt.get("conflicting_readings")
        and len(receipt_acquisitions) == 1
        and receipt_acquisitions[0].get("name") == purchase.get("name")
    )
    purchase_identity_ok = bool(
        purchase.get("name")
        and purchase.get("requested_name") == purchase.get("name")
        and purchase.get("receipt_name") == purchase.get("name")
        and offer_proof.get("receipt_name") == purchase.get("name")
        and offer.get("title") == purchase.get("name")
        and request.get("title") == purchase.get("name")
    )
    purchase_ref_matches = _purchase_reference_matches(contribution, purchase)
    temporal_order_ok = bool(
        offer_times
        and request_times
        and _integer(purchase.get("source_timestamp_ms"))
        and max(offer_times) < min(request_times) < purchase["source_timestamp_ms"]
        and receipt
        and max(request_times) < receipt.get("first_seen_ms", 0)
    )
    repeated_windows_ok = (
        offer_identity["distinct_timestamp_count"] >= 2
        and offer_identity["distinct_evidence_count"] >= 2
        and request_identity["distinct_timestamp_count"] >= 2
        and request_identity["distinct_evidence_count"] >= 2
    )
    source_operand_verified = bool(
        purchase.get("cost_basis") == "receipt_request_and_observed_offer_prices"
        and not missing_evidence
        and source_times_match
        and offer_titles_ok
        and request_titles_ok
        and repeated_windows_ok
        and temporal_order_ok
        and purchase_identity_ok
        and purchase_ref_matches
        and exact_receipt
        and actual_price_operands_verified
    )
    if source_operand_verified:
        audit_status = "verified_source_price"
    elif actual_price_operands_verified and temporal_order_ok and not exact_receipt:
        audit_status = "compatible_ownership_conflict"
    else:
        audit_status = "unverified_source_price"
    structural_basis_present = bool(
        purchase
        and purchase.get("cost_basis") == "receipt_request_and_observed_offer_prices"
        and not missing_evidence
        and isinstance(offer, dict)
        and isinstance(request, dict)
        and isinstance(offer_proof.get("fields"), dict)
        and field in offer_proof.get("fields", {})
    )
    return {
        "structural_basis_present": structural_basis_present,
        "structural_basis_definition": (
            "Recorded offer, request, field-cost, and receipt pointers are present; "
            "this does not verify the source operands or receipt ownership."
        ),
        "purchase_id": purchase.get("id"),
        "purchase_name": purchase.get("name"),
        "receipt_event_id": purchase.get("receipt_event_id"),
        "cost_basis": purchase.get("cost_basis"),
        "field_cost_proof": field_proof,
        "total_cost": offer_proof.get("total_cost"),
        "offer": {
            "title": offer.get("title"),
            "timestamps_ms": offer_times,
            "source_window_ms": _window(offer_times),
            "evidence": offer_paths,
        },
        "request": {
            "title": request.get("title"),
            "timestamps_ms": request_times,
            "source_window_ms": _window(request_times),
            "evidence": request_paths,
        },
        "receipt": {
            "event_id": purchase.get("receipt_event_id"),
            "first_seen_ms": (receipt or {}).get("first_seen_ms"),
            "last_seen_ms": (receipt or {}).get("last_seen_ms"),
            "evidence": (receipt or {}).get("evidence"),
            "name": receipt_name,
            "exact_unconflicted_acquisition": exact_receipt,
        },
        "offer_identity": offer_identity,
        "request_identity": request_identity,
        "initial_identity": initial_identity,
        "field_source_audits": field_audits,
        "source_price_fields": source_price_fields,
        "source_price_missing_fields": source_price_missing_fields,
        "stored_cost_vector": stored_cost,
        "actual_price_operands_verified": actual_price_operands_verified,
        "purchase_identity_ok": purchase_identity_ok,
        "purchase_ref_matches": purchase_ref_matches,
        "temporal_order_ok": temporal_order_ok,
        "repeated_windows_ok": repeated_windows_ok,
        "receipt_acquisition_names": [effect.get("name") for effect in receipt_acquisitions],
        "offer_and_request_source_times_match_readings": source_times_match,
        "source_operand_audit_status": audit_status,
        "source_arithmetic_verified": source_operand_verified,
        "contribution_amount_match": (
            _integer(contribution.get("amount"))
            and _integer(stored_cost.get(field))
            and contribution.get("amount") == -stored_cost.get(field)
        ),
        "proof_limitation": (
            "Repeated named offer and request prices support the committed price; no post-purchase balance is observed, so the debit remains explicitly derived."
        ),
    }


def _state_constrained_operand_audit(
    contribution: dict[str, Any],
    event: dict[str, Any] | None,
    resolution: dict[str, Any] | None,
    readings_by_evidence: dict[str, dict[str, Any]],
    missing_evidence: list[str],
) -> dict[str, Any]:
    """Audit candidate pointers and constraints without calling them direct proof."""

    field = contribution.get("field")
    event = event or {}
    resolution = resolution or {}
    event_start = event.get("first_seen_ms")
    event_end = event.get("last_seen_ms")

    def pointer_paths(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [path for path in value if isinstance(path, str)]
        return []

    gain_paths = pointer_paths(resolution.get("gain_evidence"))
    gain_rows = [readings_by_evidence.get(path) for path in gain_paths]
    gain_rows = [row for row in gain_rows if row is not None]
    gain_values = [
        (row.get("facts") or {}).get("training_gains", {}).get(field)
        for row in gain_rows
        if _integer((row.get("facts") or {}).get("training_gains", {}).get(field))
    ]
    visual_candidates = [
        value for value in resolution.get("visual_candidates") or [] if _integer(value)
    ]

    # ``before_evidence`` and ``after_evidence`` are source pointers, not
    # scalar values.  Some reports preserve the whole result window as a
    # list.  Pick the latest pre-event integer baseline and the earliest
    # in-event integer result by timestamp; neither choice consults the
    # claimed amount or an expected endpoint total.
    before_paths = pointer_paths(resolution.get("before_evidence"))
    after_paths = pointer_paths(resolution.get("after_evidence"))
    before_candidates = [
        (path, readings_by_evidence.get(path))
        for path in before_paths
        if readings_by_evidence.get(path) is not None
        and _integer(readings_by_evidence[path].get("source_timestamp_ms"))
        and (
            not _integer(event_start)
            or readings_by_evidence[path].get("source_timestamp_ms") < event_start
        )
        and _integer(
            ((readings_by_evidence[path].get("stats") or {}).get("values") or {}).get(field)
        )
    ]
    before_candidates.sort(key=lambda item: item[1].get("source_timestamp_ms", -1))
    before_path, before = before_candidates[-1] if before_candidates else (None, None)
    after_candidates = [
        (path, readings_by_evidence.get(path))
        for path in after_paths
        if readings_by_evidence.get(path) is not None
        and _integer(readings_by_evidence[path].get("source_timestamp_ms"))
        and (
            not _integer(event_start)
            or readings_by_evidence[path].get("source_timestamp_ms") >= event_start
        )
        and (
            not _integer(event_end)
            or readings_by_evidence[path].get("source_timestamp_ms") <= event_end
        )
        and _integer(
            ((readings_by_evidence[path].get("facts") or {}).get("result_values") or {}).get(field)
        )
    ]
    after_candidates.sort(key=lambda item: item[1].get("source_timestamp_ms", 10**18))
    after_path, after = after_candidates[0] if after_candidates else (None, None)
    gain_paths_in_contribution_evidence = all(
        path in contribution.get("evidence", []) for path in gain_paths
    )
    before_values = (before or {}).get("stats", {}).get("values", {})
    after_values = ((after or {}).get("facts") or {}).get("result_values", {})
    before_value = before_values.get(field)
    after_value = after_values.get(field)
    gain_times = [
        row.get("source_timestamp_ms")
        for row in gain_rows
        if _integer(row.get("source_timestamp_ms"))
    ]
    before_time = (before or {}).get("source_timestamp_ms")
    after_time = (after or {}).get("source_timestamp_ms")
    gain_identity = _observation_identity(
        [
            {"source_timestamp_ms": row.get("source_timestamp_ms"), "evidence": path}
            for path, row in zip(gain_paths, [readings_by_evidence.get(path) for path in gain_paths])
            if row is not None
        ]
    )
    gain_source_ok = bool(
        gain_rows
        and gain_values
        and all(row.get("screen") == "training_result" for row in gain_rows)
        and all(_integer(value) for value in gain_values)
        and any(value == resolution.get("amount") for value in gain_values)
        and all(
            _integer(row.get("source_timestamp_ms"))
            and _integer(event_start)
            and _integer(event_end)
            and event_start <= row.get("source_timestamp_ms") <= event_end
            for row in gain_rows
        )
    )
    endpoint_pointers_ok = bool(
        before
        and after
        and _integer(before_time)
        and _integer(after_time)
        and _integer(event_start)
        and _integer(event_end)
        and before_time < event_start <= after_time <= event_end
        and _integer(before_value)
        and _integer(after_value)
    )
    endpoint_delta = (
        after_value - before_value
        if _integer(before_value) and _integer(after_value)
        else None
    )
    candidate_constraint_ok = bool(
        _integer(resolution.get("amount"))
        and resolution.get("amount") in visual_candidates
        and visual_candidates
        and all(
            str(resolution.get("amount")).startswith(str(value))
            for value in visual_candidates
            if value > 0
        )
    )
    structural_resolution_ok = bool(
        resolution
        and resolution.get("basis") in {
            "visible_complete_gain_and_surrounding_state_constraints",
            "visible_gain_candidate_and_stable_result_suffix",
            "single_visible_gain_candidate_and_surrounding_state_constraints",
        }
        and resolution.get("field") == field
        and not missing_evidence
    )
    source_operand_verified = bool(
        structural_resolution_ok
        and gain_source_ok
        and endpoint_pointers_ok
        and candidate_constraint_ok
    )
    return {
        "source_operand_audit_status": (
            "constraint_supported_not_independent"
            if source_operand_verified
            else "unverified_constraint_operands"
        ),
        "source_arithmetic_verified": source_operand_verified,
        "resolution_amount": resolution.get("amount"),
        "contribution_amount_match": resolution.get("amount") == contribution.get("amount"),
        "gain_paths": gain_paths,
        "gain_values": gain_values,
        "gain_source_ok": gain_source_ok,
        "gain_paths_in_contribution_evidence": gain_paths_in_contribution_evidence,
        "gain_identity": gain_identity,
        "before": {
            "evidence": resolution.get("before_evidence"),
            "selected_evidence": before_path,
            "candidate_count": len(before_candidates),
            "source_timestamp_ms": before_time,
            "value": before_value,
        },
        "after": {
            "evidence": resolution.get("after_evidence"),
            "selected_evidence": after_path,
            "candidate_count": len(after_candidates),
            "source_timestamp_ms": after_time,
            "value": after_value,
        },
        "endpoint_delta": endpoint_delta,
        "endpoint_pointers_ok": endpoint_pointers_ok,
        "visual_candidates": visual_candidates,
        "candidate_constraint_ok": candidate_constraint_ok,
        "structural_resolution_ok": structural_resolution_ok,
        "selected_without_amount_filter": True,
        "proof_limitation": (
            "The candidate amount is source-bound to a visible gain and its constraints, "
            "but the endpoint delta may include other state changes and does not independently verify the effect."
        ),
    }


def _basis_proof(
    contribution: dict[str, Any],
    events_by_id: dict[str, dict[str, Any]],
    purchases_by_id: dict[str, dict[str, Any]],
    receipt_events_by_id: dict[str, dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    readings_by_evidence: dict[str, dict[str, Any]],
    missing_evidence: list[str],
    debit_observations: list[dict[str, Any]],
    reused_state_derived: dict[str, Any] | None = None,
) -> dict[str, Any]:
    basis = contribution.get("basis")
    if basis == "state_derived":
        proof = _event_proof(
            contribution,
            events_by_id.get(contribution.get("event_id")),
            evidence_rows,
            missing_evidence,
        )
        if reused_state_derived:
            proof["reused_compatibility_audit"] = reused_state_derived
            proof["source_operand_audit_status"] = "compatible_reused_unverified"
            proof["source_arithmetic_verified"] = False
        else:
            proof["source_operand_audit_status"] = "unverified_missing_reused_audit"
            proof["source_arithmetic_verified"] = False
        return proof
    if basis == "state_constrained":
        event = events_by_id.get(contribution.get("event_id"))
        structural = _resolution_proof(contribution, event, missing_evidence)
        resolutions = [
            resolution
            for resolution in (event or {}).get("state_supported_candidate_resolutions", [])
            if resolution.get("field") == contribution.get("field")
        ]
        operand = _state_constrained_operand_audit(
            contribution,
            event,
            resolutions[0] if len(resolutions) == 1 else None,
            readings_by_evidence,
            missing_evidence,
        )
        structural["source_operand_audit"] = operand
        structural["source_operand_audit_status"] = operand["source_operand_audit_status"]
        structural["source_arithmetic_verified"] = operand["source_arithmetic_verified"]
        return structural
    if basis == "observed_balance_debit":
        return _purchase_balance_proof(
            contribution,
            purchases_by_id.get(contribution.get("event_id")),
            evidence_rows,
            missing_evidence,
            debit_observations,
            receipt_events_by_id,
            readings_by_evidence,
        )
    if basis in PRICE_BASES:
        return _purchase_price_proof(
            contribution,
            purchases_by_id.get(contribution.get("event_id")),
            receipt_events_by_id,
            readings_by_evidence,
            missing_evidence,
        )
    return {
        "structural_basis_present": False,
        "source_operand_audit_status": "unverified_unsupported_basis",
        "source_arithmetic_verified": False,
        "proof_limitation": "The basis is outside the supported G5 derived categories and is retained as unsupported."
    }


def _cause_group(rows: list[dict[str, Any]], classification: str) -> dict[str, Any]:
    # Turn IDs and screenshot paths are only unique within one recording.
    # Scope both by recording so repeated ``turn-024``/``gameplay/...`` names
    # from independent recordings are not collapsed in global counts.
    turns = {(row["recording"], row["turn_id"]) for row in rows}
    recordings = Counter(row["recording"] for row in rows)
    fields = Counter(row["field"] for row in rows)
    source_paths = {
        (row["recording"], path)
        for row in rows
        for path in row.get("evidence", [])
    }
    source_times = {
        (row["recording"], time)
        for row in rows
        for time in row.get("evidence_source_timestamp_ms", [])
        if type(time) is int
    }
    return {
        "classification": classification,
        "contribution_count": len(rows),
        "turn_count": len(turns),
        "recording_counts": _counter_dict(recordings),
        "field_counts": _counter_dict(fields),
        "amount_sum": sum(row.get("amount", 0) for row in rows),
        "evidence_path_count": len(source_paths),
        "source_timestamp_count": len(source_times),
    }


def _channel_field_audit(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Summarize operand status by source channel and field for coordination."""

    grouped: defaultdict[str, defaultdict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        grouped[str(row.get("channel"))][str(row.get("field"))].append(row)
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for channel in sorted(grouped):
        result[channel] = {}
        for field in sorted(grouped[channel]):
            field_rows = grouped[channel][field]
            result[channel][field] = {
                "contribution_count": len(field_rows),
                "basis_counts": _counter_dict(Counter(row.get("basis") for row in field_rows)),
                "source_operand_audit_status_counts": _counter_dict(
                    Counter(row.get("source_operand_audit_status") or "missing_status" for row in field_rows)
                ),
                "source_arithmetic_verified_count": sum(
                    row.get("source_arithmetic_verified") is True for row in field_rows
                ),
                "source_operand_verified_count": sum(
                    row.get("source_operand_audit_status")
                    in {"verified_source_balance", "verified_source_price"}
                    for row in field_rows
                ),
            }
    return result


def _state_constrained_context(
    recording: str,
    source_sha256: str,
    contributions: list[dict[str, Any]],
    selected_ids: set[str],
    events_by_id: dict[str, dict[str, Any]],
    transitions: list[dict[str, Any]],
    eligible_turn_ids: set[str],
    readings_by_evidence: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose every state-constrained candidate, including out-of-scope ones.

    Only candidates referenced by a balanced derived field enter the 486-row
    G5 count.  The remaining candidates are useful frozen-evaluation context:
    they commonly remain unresolved because the visible alternatives conflict
    with one another or belong to a non-eligible turn.
    """

    rows = []
    for contribution in contributions:
        if contribution.get("basis") != "state_constrained":
            continue
        evidence_rows, missing_evidence = _evidence_readings(contribution, readings_by_evidence)
        event = events_by_id.get(contribution.get("event_id"))
        proof = _resolution_proof(contribution, event, missing_evidence)
        resolutions = [
            resolution
            for resolution in (event or {}).get("state_supported_candidate_resolutions", [])
            if resolution.get("field") == contribution.get("field")
        ]
        operand = _state_constrained_operand_audit(
            contribution,
            event,
            resolutions[0] if len(resolutions) == 1 else None,
            readings_by_evidence,
            missing_evidence,
        )
        proof["source_operand_audit"] = operand
        proof["source_operand_audit_status"] = operand["source_operand_audit_status"]
        proof["source_arithmetic_verified"] = operand["source_arithmetic_verified"]
        transition_hits = []
        for transition in transitions:
            for field in transition.get("fields", []):
                if contribution.get("id") in field.get("contribution_refs", []):
                    transition_hits.append(_compact_transition(transition, field))
                if any(
                    item.get("contribution_ref") == contribution.get("id")
                    for item in field.get("ambiguous_contributions", [])
                ):
                    transition_hits.append(_compact_transition(transition, field))
        statuses = sorted({hit.get("status") for hit in transition_hits})
        turn_ids = set(contribution.get("candidate_turn_ids") or [])
        turn_ids.add(contribution.get("turn_id"))
        turn_ids.discard(None)
        if contribution.get("id") in selected_ids:
            scope_reason = "included_in_128_derived_balanced_turns"
        elif any(status == "unresolved_attribution" for status in statuses):
            scope_reason = "eligible_or_observed_but_unresolved_attribution"
        elif not turn_ids.intersection(eligible_turn_ids):
            scope_reason = "outside_eligible_one_action_turns"
        else:
            scope_reason = "not_in_derived_balanced_turn_set"
        evidence_paths = list(contribution.get("evidence") or [])
        evidence_times = sorted(
            {
                row.get("source_timestamp_ms")
                for row in evidence_rows
                if type(row.get("source_timestamp_ms")) is int
            }
        )
        evidence_timestamp_alignment = _evidence_timestamp_alignment(
            evidence_paths, readings_by_evidence
        )
        rows.append(
            {
                "recording": recording,
                "source_sha256": source_sha256,
                "id": contribution.get("id"),
                "event_id": contribution.get("event_id"),
                "turn_id": contribution.get("turn_id"),
                "candidate_turn_ids": list(contribution.get("candidate_turn_ids") or []),
                "field": contribution.get("field"),
                "amount": contribution.get("amount"),
                "selected_in_g5": contribution.get("id") in selected_ids,
                "scope_reason": scope_reason,
                "transition_statuses": statuses,
                "transitions": transition_hits,
                "visual_candidates": proof.get("visual_candidates", []),
                "resolution_basis": (proof.get("resolution") or {}).get("basis"),
                "evidence": evidence_paths,
                "evidence_source_timestamp_ms": evidence_timestamp_alignment or [],
                "evidence_source_timestamp_set_ms": evidence_times,
                "evidence_timestamp_order": "aligned_to_evidence_paths",
                "basis_proof": proof,
                "source_operand_audit_status": proof.get("source_operand_audit_status"),
                "source_arithmetic_verified": proof.get("source_arithmetic_verified") is True,
            }
        )
    return rows


def _render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    scope = result.get("scope", {})
    input_directory = scope.get("input_directory", "the report directory")
    current_derived_count = summary.get("derived_contribution_count", 0)
    current_derived_turn_count = summary.get("derived_turn_count", 0)
    historical_summary = (
        summary.get("historical_state_derived_compatibility", {})
        .get("historical_summary", {})
    )
    historical_compatible_count = historical_summary.get("contribution_count", 0)
    historical_baseline = summary.get("historical_baseline", {})
    observed_balance_count = summary.get("basis_counts", {}).get(
        "observed_balance_debit", 0
    )
    historical_stability = (
        summary.get("historical_state_derived_compatibility", {})
        .get("historical_stability_checks", {})
    )
    historical_stability_line = (
        "- Historical suffix identity guard status: **{status}** for {rows} rows; "
        "duplicate timestamps={timestamps}, duplicate evidence paths={paths}, "
        "fewer than three distinct timestamps={few_timestamps}, "
        "fewer than three distinct evidence paths={few_paths}. This validates "
        "historical selector structure only; it does not promote the {historical_rows} rows "
        "to current independent effect proof."
    ).format(
        status=historical_stability.get("status", "unavailable"),
        rows=historical_stability.get("rows_checked", 0),
        historical_rows=historical_stability.get("rows_checked", 0),
        timestamps=historical_stability.get("rows_with_duplicate_stable_timestamps", 0),
        paths=historical_stability.get("rows_with_duplicate_stable_evidence_paths", 0),
        few_timestamps=historical_stability.get(
            "rows_with_fewer_than_3_distinct_stable_timestamps", 0
        ),
        few_paths=historical_stability.get(
            "rows_with_fewer_than_3_distinct_stable_evidence_paths", 0
        ),
    )
    lines = [
        "# Derived numeric reliability audit",
        "",
        f"Generated by `analyzer/lab/inventory_derived_reliability.py` from the three reports in `{input_directory}` and their inventories.",
        "The audit is source-bound and report-only; it does not rerun recognition or claim full-history accuracy.",
        "Every `evidence_source_timestamp_ms` array is aligned to the sibling `evidence` path order; sorted timestamp sets are used only for windows and counts. A scalar observation time therefore never gets copied onto an aggregate list of frames.",
        "",
        "## Coverage",
        "",
        "| Recording | Eligible one-action turns | Derived-balanced turns | Canonical contributions on those turns | Derived contributions | Source proof paths | Source timestamps |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in result["recordings"]:
        lines.append(
            "| {recording} | {eligible} | {turns} | {all_refs} | {derived} | {paths} | {times} |".format(
                recording=item["recording"],
                eligible=item["eligible_one_action_turns"],
                turns=item["derived_turn_count"],
                all_refs=item["canonical_contributions_on_derived_turns"],
                derived=item["derived_contribution_count"],
                paths=item["unique_source_proof_path_count"],
                times=item["unique_source_timestamp_count"],
            )
        )
    lines.extend(
        [
            "| **Total** | **{eligible}** | **{turns}** | **{all_refs}** | **{derived}** | **{paths}** | **{times}** |".format(
                eligible=summary["eligible_one_action_turns"],
                turns=summary["derived_turn_count"],
                all_refs=summary["canonical_contributions_on_derived_turns"],
                derived=summary["derived_contribution_count"],
                paths=summary["unique_source_proof_path_count"],
                times=summary["unique_source_timestamp_count"],
            ),
            "",
            f"This input contains **{current_derived_turn_count}** derived-balanced turns and **{current_derived_count}** selected derived contributions. Rows are selected from canonical `causal_accounting.contributions` references in `turn_transitions`; checkpoint and turn views are never added together.",
            "",
            "## Supplemental source-view index",
            "",
            "Supplemental evidence is indexed only as a source-identity/timestamp wrapper. Primary facts, stats, OCR, and effects are never copied onto that wrapper, so path availability cannot create an operand or effect proof. When an evidence root is supplied, the image and adjacent `.v2.json` sidecar must agree on path namespace, source timestamp, source video hash, and source-frame identity when the primary view exposes one.",
            "",
            "| Recording | Declared supplemental views | Indexed | Rejected | Physical image + sidecar validated | Primary source identity cross-checks |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in result["recordings"]:
        index = item.get("evidence_index", {})
        lines.append(
            "| {recording} | {declared} | {indexed} | {rejected} | {physical} | {identity} |".format(
                recording=item["recording"],
                declared=index.get("supplemental_declared_count", 0),
                indexed=index.get("supplemental_indexed_count", 0),
                rejected=index.get("supplemental_rejected_count", 0),
                physical=index.get("supplemental_validated_physical_count", 0),
                identity=index.get("supplemental_source_identity_crosschecked_count", 0),
            )
        )
    lines.extend(
        [
            "",
            "## Basis and recurring causes",
            "",
            f"Counts are contributions, with unique local turn counts in the second column. Categories can overlap within a turn, so their turn counts do not sum to {current_derived_turn_count}.",
            "",
            "| Cause group | Contributions | Turns | Amount sum | Evidence paths | Priority interpretation |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    priority_text = {
        "state_derived_endpoint_difference": "Largest recurring cause: stable result counters plus before/after state support the field when no accepted direct gain badge is available.",
        "observed_balance_debit": "Second recurring cause: lesson cost is obtained from observed performance balances around a named receipt; it is not an explicit price receipt.",
        "legitimate_price_derivation": "Rare and source-supported: repeated named offer/request prices precede an unconflicted acquisition, with no post-purchase balance.",
        "state_constrained_visible_candidate": "Rare constraint resolution: visible candidates are narrowed by surrounding states; keep it separate from direct evidence.",
        "unsupported_assumption": "Zero promoted rows. Projected/summary-only bases and missing proofs are excluded from this G5 selection.",
    }
    for classification, group in sorted(
        result["cause_groups"].items(), key=lambda item: (-item[1]["contribution_count"], item[0])
    ):
        lines.append(
            "| {name} | {count} | {turns} | {amount} | {paths} | {priority} |".format(
                name=classification,
                count=group["contribution_count"],
                turns=group["turn_count"],
                amount=group["amount_sum"],
                paths=group["evidence_path_count"],
                priority=priority_text.get(classification, "See the JSON proof rows."),
            )
        )
    lines.extend(
        [
            "",
            "## Independent operand audit",
            "",
            f"The JSON contains one audit row for each of the {current_derived_count} current derived contributions. The historical {historical_compatible_count} state-derived compatibility rows are retained separately and are not promoted to current proof. Structural basis presence is reported separately from source operands and effect identity.",
            "",
            "| Source operand status | Contributions | Meaning |",
            "| --- | ---: | --- |",
        ]
    )
    status_text = {
        "verified_source_balance": "Before/after performance-point operands and stored field cost recompute; receipt identity, timing, and ownership are clean.",
        "verified_source_price": "Stored offer/projection price recomputes from pointed source rows with clean request, offer, receipt, and timing identity.",
        "compatible_ownership_conflict": "Arithmetic recomputes, but the receipt contains competing acquisition text, so ownership is unresolved.",
        "constraint_supported_not_independent": "Visible candidate, gain pointer, and state constraints validate; this remains non-independent effect evidence.",
        "compatible_reused_unverified": "The prior 264-row amount-independent compatibility artifact is attached; this inventory does not promote it to proof.",
        "unverified_source_operands": "Required before/after or ownership operands are unavailable or inconsistent.",
        "unverified_source_price": "Required offer/projection, timing, or ownership operands are unavailable or inconsistent.",
        "unverified_constraint_operands": "Required candidate or endpoint pointers are unavailable or inconsistent.",
    }
    for status, count in sorted(
        summary.get("source_operand_audit_status_counts", {}).items(),
        key=lambda item: (-item[1], item[0]),
    ):
        lines.append(
            "| {status} | {count} | {meaning} |".format(
                status=status,
                count=count,
                meaning=status_text.get(status, "See the JSON row for the source limitation."),
            )
        )
    lines.extend(
        [
            "",
            f"Structural basis records present: **{summary['structural_basis_present_count']} / {summary['derived_contribution_count']}**. This structural count records report pointers only; it is not an arithmetic or effect-proof count.",
            f"Bounded source arithmetic recomputed for **{summary['source_arithmetic_verified_count']}** rows; fully verified source operand and ownership statuses cover **{summary['source_operand_verified_count']}** rows. For the {observed_balance_count} current observed-balance rows, a separate direct debit observation is available for **{summary['observed_balance_direct_debit_observation_count']}** and unavailable for **{summary['observed_balance_direct_debit_observation_missing_count']}**; unavailable means no source pointer was recorded, not that the debit was absent.",
            "",
            "### Channel and field coverage",
            "",
            "The JSON `summary.channel_field_audit` keeps each field contribution in one source-status bucket so numeric and performance channels can be coordinated without re-counting views.",
            "",
            "| Channel | Field | Contributions | Source arithmetic | Source operand | Status counts |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for channel, fields in summary.get("channel_field_audit", {}).items():
        for field, item in fields.items():
            statuses = ", ".join(
                f"{key}:{value}"
                for key, value in item["source_operand_audit_status_counts"].items()
            )
            lines.append(
                f"| {channel} | {field} | {item['contribution_count']} | {item['source_arithmetic_verified_count']} | {item['source_operand_verified_count']} | {statuses} |"
            )
    lines.extend(
        [
            "",
            "",
            "## State-constrained candidate context",
            "",
            f"All {summary['state_constrained_context_count']} `state_constrained` contexts are shown below. {summary['state_constrained_in_scope_count']} are selected in the current derived-turn scope; the remaining {summary['state_constrained_out_of_scope_count']} remain context because their transition is unresolved or outside the eligible set.",
            f"Source pointers and candidate constraints are internally consistent for **{summary['state_constrained_context_operand_consistent_count']} / {summary['state_constrained_context_count']}** contexts, including the selected row. These remain candidate resolutions rather than independent effect proofs.",
            "",
            "| Recording | Contribution | Turn / candidates | Field | Amount | Selected in G5 | Scope reason | Visible candidates | Transition statuses |",
            "| --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for recording in result["recordings"]:
        for candidate in recording.get("state_constrained_context", []):
            turn_id = candidate.get("turn_id") or "null"
            alternate_turns = [
                value
                for value in candidate.get("candidate_turn_ids", [])
                if value != candidate.get("turn_id")
            ]
            lines.append(
                "| {recording} | `{id}` | {turn} | {field} | {amount} | {selected} | {reason} | {candidates} | {statuses} |".format(
                    recording=recording["recording"],
                    id=candidate["id"],
                    turn=turn_id + (" / " + ",".join(alternate_turns) if alternate_turns else ""),
                    field=candidate.get("field"),
                    amount=candidate.get("amount"),
                    selected="yes" if candidate.get("selected_in_g5") else "no",
                    reason=candidate.get("scope_reason"),
                    candidates=", ".join(str(value) for value in candidate.get("visual_candidates", [])),
                    statuses=", ".join(candidate.get("transition_statuses", [])) or "none",
                )
            )
    lines.extend(
        [
            "",
            "## Source windows for frozen evaluation",
            "",
            "`turn_window_ms` is the ledger calendar window. `transition_window_ms` is the selected observed opening-to-opening comparison window. `proof_window_ms` is the min/max timestamp of the derived contribution evidence paths; use the individual JSON row evidence paths for a tighter crop when a turn contains multiple actions. State-constrained endpoint pointers and price initial/offer/request pointers are recorded under each row's `basis_proof`.",
            "",
            "| Recording | Turn | Label | Turn window ms | Transition window ms | Proof window ms | Derived contributions | Basis mix |",
            "| --- | --- | --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for item in result["recordings"]:
        for turn in item["turns"]:
            label = str(turn.get("label", "")).replace("|", "\\|")
            lines.append(
                "| {recording} | {turn_id} | {label} | {turn_window} | {transition_window} | {proof_window} | {count} | {basis} |".format(
                    recording=item["recording"],
                    turn_id=turn["turn_id"],
                    label=label,
                    turn_window=turn.get("turn_window_ms"),
                    transition_window=turn.get("transition_window_ms"),
                    proof_window=turn.get("proof_window_ms"),
                    count=turn["derived_contribution_count"],
                    basis=", ".join(f"{key}:{value}" for key, value in turn["basis_counts"].items()),
                )
            )
    lines.extend(
        [
            "",
            "## Evidence limits",
            "",
            "- All selected rows have integer amounts, canonical source paths, and a balanced derived field comparison. `source_arithmetic_verified` is true only for the bounded operand checks described above; no selected row is independently effect-verified, and arithmetic closure does not establish complete event history or exclude offsetting recognition errors.",
            f"- Current `state_derived` remains state-derived ({summary.get('basis_counts', {}).get('state_derived', 0)} rows). Its separate historical amount-independent compatibility artifact contains {historical_stability.get('rows_checked', 0)} rows and is retained as compatibility evidence only. `state_constrained` remains candidate resolution even when its pointers and prefix constraints validate. The two price rows are explicitly derived and have no observed post-purchase balance.",
            f"- Historical baseline status counts are retained separately: {historical_baseline.get('source_operand_audit_status_counts', {})}; historical cause groups include {historical_baseline.get('direct_cause_counts', {})}. These counts describe the pre-change 486-row audit, including the 17 ownership conflicts, 179 canonical direct collisions, and 76 prefix conflicts when those entries are present.",
            historical_stability_line,
            "- Three independent-02 contributions cross a calendar boundary and have `turn_id=null` with `candidate_turn_ids=[turn-039, turn-040]`; they are included once through the referenced turn-039 transition. This is why the script must follow transition references.",
            "- Full-history accuracy remains unmeasured (`null` in the preserved inventories).",
            "",
            f"Rows without a fully verified source operand and ownership result: **{summary['unsupported_or_unproven_selected_rows']}**. Structural audit errors: **{summary['structural_error_count']}**. Independent effect proofs: **{summary['independent_effect_verified_count']}**.",
        ]
    )
    return "\n".join(lines) + "\n"


def _historical_stability_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Check the prior selector's repeated-suffix identity without promoting it.

    The source-cause artifact is retained as historical compatibility evidence.
    This lightweight pass verifies that each stored suffix really contains
    independent timestamp/path observations and that its ``after`` pointer is
    the first suffix row.  It does not reselect operands or certify effect
    identity for the current 486-row audit.
    """

    state_rows = [row for row in rows if row.get("basis") == "state_derived"]
    counts = Counter()
    for row in state_rows:
        operands = row.get("source_bound_operands") or {}
        suffix = operands.get("stable_result_suffix") or []
        if not isinstance(suffix, list) or not suffix:
            counts["rows_without_stable_suffix"] += 1
            continue
        counts["rows_with_stable_suffix"] += 1
        timestamps = [
            item.get("source_timestamp_ms")
            for item in suffix
            if isinstance(item, dict) and _integer(item.get("source_timestamp_ms"))
        ]
        paths = [
            path
            for item in suffix
            if isinstance(item, dict)
            for path in (item.get("evidence") or [])
            if isinstance(path, str) and path
        ]
        identity = [(timestamp, path) for timestamp, path in zip(timestamps, paths)]
        if len(timestamps) != len(set(timestamps)):
            counts["rows_with_duplicate_stable_timestamps"] += 1
        if len(paths) != len(set(paths)):
            counts["rows_with_duplicate_stable_evidence_paths"] += 1
        if len(identity) != len(set(identity)):
            counts["rows_with_duplicate_stable_source_identity"] += 1
        if len(set(timestamps)) < 3:
            counts["rows_with_fewer_than_3_distinct_stable_timestamps"] += 1
        if len(set(paths)) < 3:
            counts["rows_with_fewer_than_3_distinct_stable_evidence_paths"] += 1
        if timestamps != sorted(timestamps):
            counts["rows_with_unsorted_stable_timestamps"] += 1
        after = operands.get("after") or {}
        first = suffix[0] if suffix else {}
        if (
            after.get("pointer") != first.get("pointer")
            or after.get("source_timestamp_ms") != first.get("source_timestamp_ms")
        ):
            counts["rows_with_after_not_first_suffix"] += 1
    for key in (
        "rows_without_stable_suffix",
        "rows_with_stable_suffix",
        "rows_with_duplicate_stable_timestamps",
        "rows_with_duplicate_stable_evidence_paths",
        "rows_with_duplicate_stable_source_identity",
        "rows_with_fewer_than_3_distinct_stable_timestamps",
        "rows_with_fewer_than_3_distinct_stable_evidence_paths",
        "rows_with_unsorted_stable_timestamps",
        "rows_with_after_not_first_suffix",
    ):
        counts.setdefault(key, 0)
    failure_keys = (
        "rows_without_stable_suffix",
        "rows_with_duplicate_stable_timestamps",
        "rows_with_duplicate_stable_evidence_paths",
        "rows_with_duplicate_stable_source_identity",
        "rows_with_fewer_than_3_distinct_stable_timestamps",
        "rows_with_fewer_than_3_distinct_stable_evidence_paths",
        "rows_with_unsorted_stable_timestamps",
        "rows_with_after_not_first_suffix",
    )
    return {
        "status": "passed" if not any(counts[key] for key in failure_keys) else "failed",
        "scope": "historical_suffix_identity_only_not_current_effect_proof",
        "rows_checked": len(state_rows),
        **{key: int(counts[key]) for key in counts},
    }


def _load_reused_state_derived_audit(
    root: Path,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, Any]]:
    """Load the bounded 264-row source audit as compatibility evidence.

    The prior artifact is intentionally not promoted to independent basis
    proof here.  Its amount-independent operand rows are preserved with their
    historical counts and attached to the matching source hash and canonical
    ID.  A missing or stale artifact leaves those rows explicitly unverified.
    """

    path = root / ".local/final-reliability-v1/derived-source-causes.json"
    metadata: dict[str, Any] = {
        "path": str(path.relative_to(root).as_posix()),
        "available": path.is_file(),
        "historical_compatible_row_count": 0,
        "matched_row_count": 0,
        "stale_or_mismatched_row_count": 0,
        "semantics": "historical_compatibility_only_not_current_proof",
        "historical_summary": {},
        "historical_stability_checks": {},
    }
    baseline_path = root / ".local/final-reliability-v1/derived-audit.json"
    baseline_metadata: dict[str, Any] = {
        "path": str(baseline_path.relative_to(root).as_posix()),
        "available": False,
        "contribution_count": 0,
        "basis_counts": {},
        "source_operand_audit_status_counts": {},
        "direct_cause_counts": {},
    }
    if baseline_path.is_file():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            baseline = None
        if isinstance(baseline, dict):
            baseline_summary = baseline.get("summary") or {}
            baseline_metadata.update(
                {
                    "available": True,
                    "contribution_count": baseline_summary.get(
                        "derived_contribution_count", 0
                    ),
                    "basis_counts": baseline_summary.get("basis_counts", {}),
                    "source_operand_audit_status_counts": baseline_summary.get(
                        "source_operand_audit_status_counts", {}
                    ),
                }
            )
    historical_source_path = root / ".local/final-reliability-v1/derived-source-causes.json"
    if historical_source_path.is_file():
        try:
            historical_source = json.loads(
                historical_source_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            historical_source = None
        if isinstance(historical_source, dict):
            baseline_metadata["direct_cause_counts"] = {
                cause: (group or {}).get("contribution_count", 0)
                for cause, group in (historical_source.get("cause_groups") or {}).items()
            }
            baseline_metadata["source_cause_artifact"] = str(
                historical_source_path.relative_to(root).as_posix()
            )
    metadata["historical_baseline"] = baseline_metadata
    if not path.is_file():
        return {}, metadata
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        metadata["available"] = False
        return {}, metadata
    metadata["historical_summary"] = {
        key: artifact.get("summary", {}).get(key)
        for key in (
            "contribution_count",
            "source_bound_amount_match_count",
            "legacy_expected_value_compatibility_match_count",
            "source_bound_verified_row_count",
            "unsupported_or_unproven_source_bound_rows",
            "focused_operand_tests",
        )
        if key in artifact.get("summary", {})
    }
    metadata["historical_stability_checks"] = _historical_stability_checks(
        artifact.get("contributions", [])
    )
    mapping: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in artifact.get("contributions", []):
        if row.get("basis") != "state_derived":
            continue
        metadata["historical_compatible_row_count"] += 1
        key = (row.get("recording"), row.get("source_sha256"), row.get("id"))
        if not all(isinstance(value, str) and value for value in key):
            metadata["stale_or_mismatched_row_count"] += 1
            continue
        mapping[key] = {
            "status": "compatible_operands_found_unverified",
            "source_bound_operands": row.get("source_bound_operands"),
            "historical_source_review": row.get("source_review"),
            "historical_direct_reader_observation": row.get("direct_reader_observation"),
            "historical_report_structural_flags": row.get("report_structural_flags"),
            "artifact_path": str(path.relative_to(root).as_posix()),
            "artifact_source_sha256": row.get("source_sha256"),
            "historical_amount": row.get("amount"),
        }
    return mapping, metadata


def audit(
    root: Path,
    report_dir: Path = REPORT_DIR,
    evidence_roots: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Audit one report/inventory directory without changing its contents.

    ``before/`` remains the default frozen input.  A caller may point the
    same bounded audit at a versioned after-replay directory; the report
    names and inventory names intentionally stay fixed so the input contract
    remains comparable across runs.
    """
    report_root = report_dir if report_dir.is_absolute() else root / report_dir
    try:
        report_directory_label = report_root.relative_to(root).as_posix()
    except ValueError:
        report_directory_label = report_root.as_posix()
    recordings = []
    all_rows: list[dict[str, Any]] = []
    structural_errors: list[dict[str, Any]] = []
    reused_state_derived, reused_metadata = _load_reused_state_derived_audit(root)
    for recording, report_name, inventory_name in REPORT_SPECS:
        report_path = report_root / report_name
        inventory_path = report_root / inventory_name
        report_bytes = report_path.read_bytes()
        inventory_bytes = inventory_path.read_bytes()
        report = json.loads(report_bytes.decode("utf-8"))
        inventory = json.loads(inventory_bytes.decode("utf-8"))
        source_sha256 = report.get("source", {}).get("sha256")
        if not _source_digest_present(source_sha256):
            structural_errors.append(
                {
                    "recording": recording,
                    "kind": "report_source_sha256_missing_or_invalid",
                }
            )
        if source_sha256 != inventory.get("source_sha256"):
            structural_errors.append(
                {"recording": recording, "kind": "inventory_source_hash_mismatch"}
            )
        if report.get("causal_accounting", {}).get("source_sha256") != source_sha256:
            structural_errors.append(
                {"recording": recording, "kind": "accounting_source_hash_mismatch"}
            )

        accounting = report.get("causal_accounting") or {}
        contributions = accounting.get("contributions") or []
        contribution_by_id = {}
        for contribution in contributions:
            contribution_id = contribution.get("id")
            if contribution_id in contribution_by_id:
                structural_errors.append(
                    {"recording": recording, "kind": "duplicate_canonical_contribution_id", "id": contribution_id}
                )
            contribution_by_id[contribution_id] = contribution

        evidence_root = None
        if evidence_roots and recording in evidence_roots:
            evidence_root = Path(evidence_roots[recording])
            if not evidence_root.is_absolute():
                evidence_root = root / evidence_root
        readings_by_evidence, evidence_index_errors, evidence_index_stats = _index_report_readings(
            report,
            report_source_sha256=source_sha256,
            evidence_root=evidence_root,
        )
        for index_error in evidence_index_errors:
            structural_errors.append({"recording": recording, **index_error})

        events = (report.get("gameplay_tracking") or {}).get("events", [])
        events_by_id = {event.get("id"): event for event in events}
        receipt_events_by_id = dict(events_by_id)
        purchases = (report.get("gameplay_tracking") or {}).get("lesson_purchases", [])
        purchases_by_id = {purchase.get("id"): purchase for purchase in purchases}
        debit_observations = (report.get("gameplay_tracking") or {}).get("lesson_debit_observations", [])

        derived_turn_ids = set(inventory.get("turn_refs", {}).get("numeric_accounting_with_derived_changes", []))
        eligible_turn_ids = set(
            turn.get("id")
            for turn in (report.get("turn_ledger") or {}).get("turns", [])
            if turn.get("window_kind") == "calendar_turn" and turn.get("action_status") == "one_action"
        )
        if len(derived_turn_ids) != inventory.get("summary", {}).get("numeric_accounting_with_derived_changes_turns"):
            structural_errors.append(
                {"recording": recording, "kind": "inventory_derived_turn_summary_mismatch"}
            )
        if not derived_turn_ids <= eligible_turn_ids:
            structural_errors.append(
                {
                    "recording": recording,
                    "kind": "derived_turn_not_eligible",
                    "turn_ids": _ordered_turn_ids(derived_turn_ids - eligible_turn_ids),
                }
            )
        ledger_turns = {turn.get("id"): turn for turn in (report.get("turn_ledger") or {}).get("turns", [])}
        transition_hits: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        transition_rows_by_turn: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        transition_reference_ids: list[str] = []

        for transition in accounting.get("turn_transitions", []):
            turn_id = transition.get("turn_id")
            if turn_id not in derived_turn_ids:
                continue
            transition_rows_by_turn[turn_id].append(transition)
            expected_fields = CHANNEL_FIELDS.get(transition.get("channel"), ())
            fields = transition.get("fields") or []
            if tuple(field.get("field") for field in fields) != expected_fields:
                structural_errors.append(
                    {
                        "recording": recording,
                        "kind": "derived_transition_field_shape_mismatch",
                        "turn_id": turn_id,
                        "channel": transition.get("channel"),
                    }
                )
            for field in fields:
                if field.get("status") == "balanced_with_derived_changes":
                    for contribution_id in field.get("contribution_refs", []):
                        contribution = contribution_by_id.get(contribution_id)
                        if contribution is None:
                            structural_errors.append(
                                {
                                    "recording": recording,
                                    "kind": "transition_references_unknown_contribution",
                                    "turn_id": turn_id,
                                    "id": contribution_id,
                                }
                            )
                            continue
                        transition_reference_ids.append(contribution_id)
                        if contribution.get("basis") not in DIRECT_BASES:
                            transition_hits[contribution_id].append(_compact_transition(transition, field))

        missing_turn_transitions = [
            turn_id
            for turn_id in derived_turn_ids
            if len(transition_rows_by_turn.get(turn_id, [])) != 2
        ]
        if missing_turn_transitions:
            structural_errors.append(
                {
                    "recording": recording,
                    "kind": "derived_turn_missing_stats_or_performance_transition",
                    "turn_ids": _ordered_turn_ids(missing_turn_transitions),
                }
            )

        selected_ids = list(transition_hits)
        selected_rows = []
        for contribution_id in selected_ids:
            contribution = contribution_by_id[contribution_id]
            basis = contribution.get("basis")
            evidence_rows, missing_evidence = _evidence_readings(contribution, readings_by_evidence)
            evidence_paths = list(contribution.get("evidence") or [])
            evidence_times = sorted(
                {
                    row.get("source_timestamp_ms")
                    for row in evidence_rows
                    if type(row.get("source_timestamp_ms")) is int
                }
            )
            evidence_timestamp_alignment = _evidence_timestamp_alignment(
                evidence_paths, readings_by_evidence
            )
            hits = transition_hits[contribution_id]
            if len(hits) != 1:
                structural_errors.append(
                    {
                        "recording": recording,
                        "kind": "canonical_contribution_repeated_across_selected_transition_views",
                        "id": contribution_id,
                        "reference_count": len(hits),
                    }
                )
            if basis in UNSUPPORTED_BASES or basis not in SUPPORTED_DERIVED_BASES:
                classification = "unsupported_assumption"
            else:
                classification = BASIS_CLASSIFICATION[basis]
            proof = _basis_proof(
                contribution,
                events_by_id,
                purchases_by_id,
                receipt_events_by_id,
                evidence_rows,
                readings_by_evidence,
                missing_evidence,
                debit_observations,
                reused_state_derived.get((recording, source_sha256, contribution_id)),
            )
            if basis == "state_derived" and (
                recording,
                source_sha256,
                contribution_id,
            ) in reused_state_derived:
                reused_metadata["matched_row_count"] += 1
            if not type(contribution.get("amount")) is int:
                structural_errors.append(
                    {"recording": recording, "kind": "derived_contribution_non_integer_amount", "id": contribution_id}
                )
            if not evidence_rows or missing_evidence:
                structural_errors.append(
                    {
                        "recording": recording,
                        "kind": "derived_contribution_missing_source_evidence",
                        "id": contribution_id,
                        "missing_evidence": missing_evidence,
                    }
                )
            if not hits or hits[0]["status"] != "balanced_with_derived_changes":
                structural_errors.append(
                    {"recording": recording, "kind": "derived_contribution_not_in_balanced_derived_field", "id": contribution_id}
                )
            row = {
                "recording": recording,
                "source_sha256": source_sha256,
                "id": contribution.get("id"),
                "source_ref": contribution.get("source_ref"),
                "event_ref": contribution.get("event_ref"),
                "event_id": contribution.get("event_id"),
                "turn_id": contribution.get("turn_id"),
                "candidate_turn_ids": list(contribution.get("candidate_turn_ids") or []),
                "turn_assignment_basis": contribution.get("turn_assignment_basis"),
                "channel": contribution.get("channel"),
                "field": contribution.get("field"),
                "amount": contribution.get("amount"),
                "basis": basis,
                "classification": classification,
                "conflicts_present": contribution.get("conflicts_present"),
                "independent_effect_verification": contribution.get("independent_effect_verification"),
                "timing_basis": contribution.get("timing_basis"),
                "contribution_observation_window_ms": [
                    contribution.get("observation_start_ms"),
                    contribution.get("observation_end_ms"),
                ],
                "evidence": evidence_paths,
                "evidence_source_timestamp_ms": evidence_timestamp_alignment or [],
                "evidence_source_timestamp_set_ms": evidence_times,
                "evidence_timestamp_order": "aligned_to_evidence_paths",
                "evidence_source_window_ms": _window(evidence_times),
                "evidence_reading_summary": _reading_summary(evidence_rows),
                "missing_evidence": missing_evidence,
                "transition_reference_count": len(hits),
                "transition": hits[0] if hits else None,
                "basis_proof": proof,
                "source_operand_audit_status": proof.get("source_operand_audit_status"),
                "source_arithmetic_verified": proof.get("source_arithmetic_verified") is True,
            }
            selected_rows.append(row)
            all_rows.append(row)

        constrained_context = _state_constrained_context(
            recording,
            source_sha256,
            contributions,
            set(selected_ids),
            events_by_id,
            accounting.get("turn_transitions", []),
            eligible_turn_ids,
            readings_by_evidence,
        )

        duplicate_selected_ids = len(transition_reference_ids) - len(set(transition_reference_ids))
        if duplicate_selected_ids:
            structural_errors.append(
                {
                    "recording": recording,
                    "kind": "duplicate_derived_transition_reference",
                    "count": duplicate_selected_ids,
                }
            )

        # Build one compact window row per derived turn.  Contribution rows are
        # retained in JSON for exact evidence; this view is for selecting a
        # bounded frozen source window.
        turns = []
        for turn_id in _ordered_turn_ids(derived_turn_ids):
            ledger_turn = ledger_turns.get(turn_id, {})
            turn_rows = [row for row in selected_rows if turn_id in set(row.get("candidate_turn_ids") or []) or row.get("turn_id") == turn_id]
            # Prefer the actual selected transition reference.  This catches a
            # cross-calendar contribution whose primary turn_id is null.
            turn_rows = [
                row
                for row in selected_rows
                if row.get("transition", {}).get("turn_id") == turn_id
            ]
            transition_windows = [
                [transition.get("start_ms"), transition.get("end_ms")]
                for transition in transition_rows_by_turn.get(turn_id, [])
                if type(transition.get("start_ms")) is int and type(transition.get("end_ms")) is int
            ]
            proof_times = [
                time
                for row in turn_rows
                for time in row.get("evidence_source_timestamp_ms", [])
                if type(time) is int
            ]
            basis_counts = Counter(row.get("basis") for row in turn_rows)
            if not turn_rows:
                structural_errors.append(
                    {"recording": recording, "kind": "derived_turn_has_no_selected_derived_contribution", "turn_id": turn_id}
                )
            turns.append(
                {
                    "turn_id": turn_id,
                    "label": ledger_turn.get("label"),
                    "phase": ledger_turn.get("phase"),
                    "calendar_value": ledger_turn.get("calendar_value"),
                    "turn_window_ms": [ledger_turn.get("start_ms"), ledger_turn.get("end_ms")],
                    "transition_window_ms": _window([value for pair in transition_windows for value in pair]),
                    "proof_window_ms": _window(proof_times),
                    "transition_count": len(transition_rows_by_turn.get(turn_id, [])),
                    "derived_contribution_count": len(turn_rows),
                    "basis_counts": _counter_dict(basis_counts),
                    "derived_contribution_ids": [row["id"] for row in turn_rows],
                }
            )
        basis_counts = Counter(row.get("basis") for row in selected_rows)
        classification_counts = Counter(row.get("classification") for row in selected_rows)
        observed_balance_rows = [
            row for row in selected_rows if row.get("basis") == "observed_balance_debit"
        ]
        direct_debit_observation_count = sum(
            row.get("basis_proof", {}).get("debit_observation") is not None
            for row in observed_balance_rows
        )
        unique_source_paths = {path for row in selected_rows for path in row.get("evidence", [])}
        unique_source_times = {
            time
            for row in selected_rows
            for time in row.get("evidence_source_timestamp_ms", [])
            if type(time) is int
        }
        recordings.append(
            {
                "recording": recording,
                "report": str(Path(report_directory_label, report_name).as_posix()),
                "inventory": str(Path(report_directory_label, inventory_name).as_posix()),
                "report_sha256": _sha256(report_path),
                "inventory_sha256": _sha256(inventory_path),
                "source_sha256": source_sha256,
                "evidence_root": (
                    str(evidence_root.resolve())
                    if evidence_root is not None
                    else (report.get("evaluation_context") or {}).get("evidence_root")
                ),
                "evidence_index": evidence_index_stats,
                "eligible_one_action_turns": len(eligible_turn_ids),
                "derived_turn_count": len(derived_turn_ids),
                "derived_turn_ids": _ordered_turn_ids(derived_turn_ids),
                "canonical_contributions_on_derived_turns": len(
                    {
                        contribution_id
                        for transition in accounting.get("turn_transitions", [])
                        if transition.get("turn_id") in derived_turn_ids
                        for field in transition.get("fields", [])
                        for contribution_id in field.get("contribution_refs", [])
                    }
                ),
                "derived_contribution_count": len(selected_rows),
                "basis_counts": _counter_dict(basis_counts),
                "classification_counts": _counter_dict(classification_counts),
                "observed_balance_direct_debit_observation_count": direct_debit_observation_count,
                "observed_balance_direct_debit_observation_missing_count": (
                    len(observed_balance_rows) - direct_debit_observation_count
                ),
                "unique_source_proof_path_count": len(unique_source_paths),
                "unique_source_timestamp_count": len(unique_source_times),
                "source_window_ms": _window(list(unique_source_times)),
                "state_constrained_context": constrained_context,
                "turns": turns,
            }
        )

    cause_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        cause_rows[row["classification"]].append(row)
    cause_groups = {
        classification: _cause_group(rows, classification)
        for classification, rows in cause_rows.items()
    }
    # Make the zero row explicit so absence of unsupported assumptions is an
    # auditable result, rather than a missing key.
    if "unsupported_assumption" not in cause_groups:
        cause_groups["unsupported_assumption"] = _cause_group([], "unsupported_assumption")

    basis_counts = Counter(row.get("basis") for row in all_rows)
    classification_counts = Counter(row.get("classification") for row in all_rows)
    source_operand_status_counts = Counter(
        row.get("source_operand_audit_status") or "missing_status" for row in all_rows
    )
    structural_basis_present_count = sum(
        row.get("basis_proof", {}).get("structural_basis_present") is True
        for row in all_rows
    )
    source_arithmetic_verified_count = sum(
        row.get("source_arithmetic_verified") is True for row in all_rows
    )
    source_operand_verified_count = sum(
        row.get("source_operand_audit_status")
        in {"verified_source_balance", "verified_source_price"}
        for row in all_rows
    )
    source_paths = {
        (row["recording"], path)
        for row in all_rows
        for path in row.get("evidence", [])
    }
    source_times = {
        (row["recording"], time)
        for row in all_rows
        for time in row.get("evidence_source_timestamp_ms", [])
        if type(time) is int
    }
    source_time_values = [
        time
        for row in all_rows
        for time in row.get("evidence_source_timestamp_ms", [])
        if type(time) is int
    ]
    unsupported_or_unproven = sum(
        row.get("classification") == "unsupported_assumption"
        or row.get("source_operand_audit_status")
        not in {"verified_source_balance", "verified_source_price"}
        for row in all_rows
    )
    constrained_context = [
        row
        for recording in recordings
        for row in recording.get("state_constrained_context", [])
    ]
    constrained_scope_counts = Counter(
        "selected_in_g5" if row.get("selected_in_g5") else row.get("scope_reason")
        for row in constrained_context
    )
    observed_balance_rows = [
        row for row in all_rows if row.get("basis") == "observed_balance_debit"
    ]
    direct_debit_observation_count = sum(
        row.get("basis_proof", {}).get("debit_observation") is not None
        for row in observed_balance_rows
    )
    summary = {
        "eligible_one_action_turns": sum(item["eligible_one_action_turns"] for item in recordings),
        "derived_turn_count": sum(item["derived_turn_count"] for item in recordings),
        "canonical_contributions_on_derived_turns": sum(
            item["canonical_contributions_on_derived_turns"] for item in recordings
        ),
        "derived_contribution_count": len(all_rows),
        "basis_counts": _counter_dict(basis_counts),
        "classification_counts": _counter_dict(classification_counts),
        "channel_field_audit": _channel_field_audit(all_rows),
        "unique_source_proof_path_count": len(source_paths),
        "unique_source_timestamp_count": len(source_times),
        "unique_source_window_ms": _window(source_time_values),
        "state_constrained_context_count": len(constrained_context),
        "state_constrained_in_scope_count": sum(
            row.get("selected_in_g5") is True for row in constrained_context
        ),
        "state_constrained_out_of_scope_count": sum(
            row.get("selected_in_g5") is not True for row in constrained_context
        ),
        "state_constrained_scope_counts": _counter_dict(constrained_scope_counts),
        "state_constrained_context_operand_consistent_count": sum(
            row.get("source_arithmetic_verified") is True for row in constrained_context
        ),
        "observed_balance_direct_debit_observation_count": direct_debit_observation_count,
        "observed_balance_direct_debit_observation_missing_count": (
            len(observed_balance_rows) - direct_debit_observation_count
        ),
        "unsupported_or_unproven_selected_rows": unsupported_or_unproven,
        "structural_basis_present_count": structural_basis_present_count,
        "source_operand_audit_status_counts": _counter_dict(source_operand_status_counts),
        "source_operand_verified_count": source_operand_verified_count,
        "source_arithmetic_verified_count": source_arithmetic_verified_count,
        "independent_effect_verified_count": 0,
        "historical_state_derived_compatibility": reused_metadata,
        "historical_baseline": reused_metadata.get("historical_baseline", {}),
        "structural_error_count": len(structural_errors),
        "full_history_accuracy_measured": None,
        "deduplication_key": "(recording source_sha256, canonical contribution id)",
    }
    result = {
        "schema_version": "tracen-replay/derived-reliability-audit-v1",
        "scope": {
            "checklist_gate": "G5",
            "input_directory": report_directory_label,
            "preserved_reports": [spec[1] for spec in REPORT_SPECS],
            "preserved_inventories": [spec[2] for spec in REPORT_SPECS],
            "direct_bases_excluded_from_derived_rows": sorted(DIRECT_BASES),
            "selected_from": "causal_accounting.turn_transitions -> canonical contribution refs",
            "overlapping_views_counted_once": True,
            "state_derived_operand_source": (
                "reused .local/final-reliability-v1/derived-source-causes.json as "
                "amount-independent compatibility evidence; never promoted to proof"
            ),
        },
        "summary": summary,
        "cause_groups": cause_groups,
        "recordings": recordings,
        "contributions": all_rows,
        "structural_errors": structural_errors,
        "limitations": [
            "The three supplied recordings are development data and have influenced analyzer development.",
            "A state-derived endpoint difference is retained from the prior amount-independent compatibility audit; it is not independently re-proved here and is not an independent receipt observation.",
            "A state-constrained candidate is retained separately from direct evidence even when its source pointers and constraints validate.",
            "Observed balance debits are source-observed before/after resource states. The audit recomputes the stored field cost from those pointers; a separate lesson_debit_observation is reported only when its source identity is available.",
            "Committed offer-price derivations recompute each stored field price from pointed offer/projection readings and validate ownership and temporal ordering, but do not establish a post-purchase balance.",
            "Structural basis presence is not proof of arithmetic or effect identity. Use source_operand_audit_status and source_arithmetic_verified for the bounded operand result.",
            "Full-history accuracy remains unmeasured; no accuracy percentage is inferred from these balanced changes.",
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="Repository root (default: current directory).")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR,
                        help="Directory containing the three report/inventory name pairs.")
    parser.add_argument(
        "--evidence-roots",
        type=Path,
        help=(
            "Optional JSON object mapping recording names to evidence roots. "
            "When supplied, supplemental paths are checked against their image "
            "and adjacent .v2.json source binding."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON output path.")
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT, help="Markdown output path.")
    args = parser.parse_args()
    root = args.root.resolve()
    evidence_roots = None
    if args.evidence_roots:
        evidence_roots_path = args.evidence_roots
        if not evidence_roots_path.is_absolute():
            evidence_roots_path = root / evidence_roots_path
        parsed_roots = json.loads(evidence_roots_path.read_text(encoding="utf-8"))
        if not isinstance(parsed_roots, dict):
            raise ValueError("--evidence-roots must contain a recording-to-path JSON object")
        evidence_roots = {}
        for name, value in parsed_roots.items():
            if isinstance(value, str):
                path = value
            elif isinstance(value, dict) and isinstance(value.get("evidence_root"), str):
                path = value["evidence_root"]
            else:
                raise ValueError(
                    "--evidence-roots values must be paths or objects with evidence_root"
                )
            evidence_roots[name] = Path(path)
    result = audit(root, args.report_dir, evidence_roots=evidence_roots)
    script_hash = _sha256(Path(__file__).resolve())
    result["generator"] = {
        "script": str(Path(__file__).resolve().relative_to(root).as_posix()),
        "script_sha256": script_hash,
    }
    output = args.output if args.output.is_absolute() else root / args.output
    markdown_output = args.markdown_output if args.markdown_output.is_absolute() else root / args.markdown_output
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_output.write_text(_render_markdown(result), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=True, sort_keys=True))
    if result["structural_errors"]:
        print(json.dumps({"structural_errors": result["structural_errors"]}, ensure_ascii=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
