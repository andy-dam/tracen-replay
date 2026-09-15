"""Audit baseline-v1 causal rows that disappeared from intermediate-worker-v2.

This is a read-only, source-bound comparison.  It does not rerun recognition,
rewrite a frozen report, or infer a correction from the worker's current
absence.  Each old-only row is emitted once, with the baseline and worker
event summaries, the exact source paths/timestamps, deterministic before/after
pointers, and an explicit verdict about what the output change means.

Run from the repository root with::

    .\\.venv\\Scripts\\python.exe scripts\\audit_intermediate_worker_old_only.py
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


WORKER_REPORT = Path(".local/final-reliability-v1/intermediate-worker-v2/report.json")
BASELINE_REPORT = Path(".local/final-reliability-v1/before/v1-report.json")
PRIOR_AUDIT = Path(
    ".local/final-reliability-v1/intermediate-worker-v2/source-basis-audit.json"
)
SOURCE_PHASE_REVIEW = Path(
    ".local/final-reliability-v1/source-phase-verification-v1.json"
)
STRICT_GRADE = Path(
    ".local/final-reliability-v1/intermediate-worker-v2/strict-grade.json"
)
SOURCE_ROOT = Path(".local/full-recording/v1")
OUTPUT_JSON = Path(
    ".local/final-reliability-v1/intermediate-worker-v2/old-only-source-audit.json"
)
OUTPUT_MD = Path(
    ".local/final-reliability-v1/intermediate-worker-v2/old-only-source-audit.md"
)


# These are source-review anchors, rather than recognition inputs.  The
# receipt frames were visually checked during this bounded audit.  Other rows
# retain their machine-recorded source facts and exact source paths; the
# independent phase artifact is used when it covers the same event/field.
MANUAL_SOURCE_ANCHORS: dict[tuple[str, str], dict[str, Any]] = {
    ("outcome-0177", "skill_points"): {
        "status": "manual_source_visual_positive",
        "path": "gameplay/part-009-frame-000133.png",
        "visual": "The outcome receipt visibly says Skill Pts went up by 57.",
        "dependency": "T055raceSP / after-race receipt parser gap",
    },
    ("outcome-0254", "skill_points"): {
        "status": "manual_source_visual_positive",
        "path": "gameplay/part-013-frame-000298.png",
        "visual": "The outcome receipt visibly shows Skill Pts +51 and says Skill Pts went up by 51.",
        "dependency": "receipt overlay closure",
    },
}


MANUAL_SOURCE_REPRESENTATIVES: list[dict[str, str]] = [
    {
        "event_id": "training-0028",
        "field": "composure",
        "path": "training-inspection/453000/frame-000018.png",
        "visual": "Applied performance panel visibly shows Composure +15.",
    },
    {
        "event_id": "training-0029",
        "field": "wit",
        "path": "training-inspection/465000/frame-000018.png",
        "visual": "Applied training receipt visibly shows Wit +17.",
    },
    {
        "event_id": "training-0033",
        "field": "wit",
        "path": "training-inspection/542500/frame-000015.png",
        "visual": "Applied training receipt visibly shows Wit +26.",
    },
    {
        "event_id": "training-0048",
        "field": "speed",
        "path": "training-inspection/845250/frame-000015.png",
        "visual": "Applied training receipt visibly shows Speed +36.",
    },
    {
        "event_id": "training-0049",
        "field": "wit",
        "path": "training-inspection/915750/frame-000017.png",
        "visual": "Applied training receipt visibly shows Wit +35.",
    },
    {
        "event_id": "training-0062",
        "field": "speed",
        "path": "training-inspection/1320750/frame-000015.png",
        "visual": "Complete applied training badge visibly shows Speed +46; the tight crop also exposes the competing +4 reading.",
    },
    {
        "event_id": "training-0063",
        "field": "speed",
        "path": "training-inspection/1328150-native-v2/frame-000008.png",
        "visual": "Applied training receipt visibly shows Speed +14 while neighboring crops produce one-digit alternatives.",
    },
    {
        "event_id": "training-0067",
        "field": "speed",
        "path": "training-inspection/1363500/frame-000020.png",
        "visual": "The recorded training window contains the Speed applied overlay; the preserved reading facts record +8, but this representative frame is partially occluded.",
    },
    {
        "event_id": "training-0071",
        "field": "speed",
        "path": "training-inspection/1420750/frame-000015.png",
        "visual": "Applied training receipt visibly shows Speed +32.",
    },
    {
        "event_id": "training-0035",
        "field": "passion",
        "path": "training-inspection/571000-573250/frame-000014.png",
        "visual": "Applied performance panel visibly shows Passion +15.",
    },
    {
        "event_id": "training-0037",
        "field": "dance",
        "path": "training-inspection/588250/frame-000009.png",
        "visual": "Applied performance panel visibly shows Dance +15.",
    },
    {
        "event_id": "training-0041",
        "field": "composure",
        "path": "training-inspection/732750/frame-000011.png",
        "visual": "Applied performance panel visibly shows Composure +19.",
    },
    {
        "event_id": "training-0051",
        "field": "vocal",
        "path": "training-inspection/973750/frame-000007.png",
        "visual": "Applied performance panel visibly shows Vocal +18.",
    },
    {
        "event_id": "training-0065",
        "field": "visual",
        "path": "training-inspection/1337000/frame-000013.png",
        "visual": "Applied performance panel visibly shows Visual +20.",
    },
]


KNOWN_DEPENDENCIES: dict[tuple[str, str], list[str]] = {
    ("training-0031", "wit"): ["T028appliedwit"],
    ("training-0031", "skill_points"): ["T028appliedSP"],
    ("outcome-0177", "skill_points"): ["T055raceSP"],
    ("training-0066", "speed"): ["turn-064 component/full phase review"],
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integer(value: Any) -> bool:
    return type(value) is int


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def source_file(root: Path, evidence: str) -> Path:
    clean = evidence.replace("\\", "/")
    if clean.startswith(".local/"):
        return root / clean
    return root / SOURCE_ROOT / clean


def source_identity(row: dict[str, Any], image_hash: str | None = None) -> str | None:
    path = row.get("evidence")
    timestamp = row.get("source_timestamp_ms")
    if not isinstance(path, str) or not integer(timestamp):
        return None
    return f"{image_hash or path}@{timestamp}"


def value_from_facts(facts: dict[str, Any], container: str, field: str) -> Any:
    values = facts.get(container)
    return values.get(field) if isinstance(values, dict) else None


def filtered_effects(row: dict[str, Any], field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for effect in row.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        if effect.get("field") != field:
            continue
        result.append(
            {
                key: effect.get(key)
                for key in (
                    "kind",
                    "field",
                    "amount",
                    "value",
                    "raw_text",
                    "confidence",
                    "confirmation",
                )
                if key in effect
            }
        )
    return result


def crop_summary(facts: dict[str, Any], field: str) -> dict[str, Any] | None:
    crops = facts.get("training_gain_crop_provenance")
    crop = crops.get(field) if isinstance(crops, dict) else None
    if not isinstance(crop, dict):
        return None
    candidates = []
    for item in crop.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append(
            {
                key: item.get(key)
                for key in (
                    "amount",
                    "canonical_eligible",
                    "confidence",
                    "crop_family",
                    "normalization",
                    "raw_text",
                    "region",
                    "source_role",
                )
                if key in item
            }
        )
    return {
        key: crop.get(key)
        for key in (
            "candidate_amounts",
            "canonical_amount",
            "canonical_basis",
            "conflict_state",
            "crop_region",
        )
        if key in crop
    } | {"candidates": candidates}


def ocr_text(row: dict[str, Any]) -> list[str]:
    ocr = row.get("ocr")
    if not isinstance(ocr, dict):
        return []
    result: list[str] = []
    for key in ("neural", "tesseract", "raw"):
        values = ocr.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    result.append(item["text"])
                elif isinstance(item, str):
                    result.append(item)
        elif isinstance(values, str):
            result.append(values)
    return result[:12]


def reading_ref(root: Path, row: dict[str, Any] | None, field: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {"present": False}
    evidence = row.get("evidence")
    image_path = source_file(root, evidence) if isinstance(evidence, str) else None
    exists = bool(image_path and image_path.is_file())
    image_hash = sha256(image_path) if exists and image_path else None
    facts = row.get("facts") or {}
    if not isinstance(facts, dict):
        facts = {}
    return {
        "present": True,
        "evidence": evidence,
        "source_file": (
            relative(root, image_path) if image_path is not None else None
        ),
        "source_file_exists": exists,
        "source_file_sha256": image_hash,
        "source_timestamp_ms": row.get("source_timestamp_ms"),
        "screen": row.get("screen"),
        "training_option": row.get("training_option"),
        "completed_action": row.get("completed_action"),
        "source_identity": source_identity(row, image_hash),
        "training_gain": value_from_facts(facts, "training_gains", field),
        "training_gain_candidates": value_from_facts(
            facts, "training_gain_candidates", field
        ),
        "awarded_performance_gain": value_from_facts(
            facts, "awarded_performance_gains", field
        ),
        "performance_gain": value_from_facts(facts, "performance_gains", field),
        "performance_gain_candidates": value_from_facts(
            facts, "performance_gain_candidates", field
        ),
        "result_value": value_from_facts(facts, "result_values", field),
        "result_value_candidate": value_from_facts(
            facts, "result_value_candidates", field
        ),
        "crop_provenance": crop_summary(facts, field),
        "effects": filtered_effects(row, field),
        "ocr_text": ocr_text(row),
    }


def event_ref(event: dict[str, Any] | None, field: str) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"present": False}
    field_evidence = {
        key: value
        for key, value in (event.get("field_evidence") or {}).items()
        if field in key
    }
    effects = [
        {
            key: effect.get(key)
            for key in (
                "kind",
                "field",
                "amount",
                "value",
                "raw_text",
                "confidence",
                "confirmation",
            )
            if key in effect
        }
        for effect in (event.get("effects") or [])
        if isinstance(effect, dict) and effect.get("field") == field
    ]
    return {
        "present": True,
        "id": event.get("id"),
        "kind": event.get("kind"),
        "first_seen_ms": event.get("first_seen_ms"),
        "last_seen_ms": event.get("last_seen_ms"),
        "evidence": event.get("evidence"),
        "context_title": event.get("context_title"),
        "deltas": (event.get("deltas") or {}).get(field),
        "performance_deltas": (event.get("performance_deltas") or {}).get(field),
        "effects": effects,
        "field_evidence": field_evidence,
        "conflicting_readings": (event.get("conflicting_readings") or {}).get(
            field
        ),
    }


def unique_readings(
    report: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in (report.get("gameplay_tracking") or {}).get("readings") or []:
        evidence = row.get("evidence")
        if isinstance(evidence, str) and evidence:
            grouped[evidence].append(row)
    chosen: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for evidence, rows in grouped.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                row.get("source_timestamp_ms")
                if integer(row.get("source_timestamp_ms"))
                else -1,
                json.dumps(row, sort_keys=True, ensure_ascii=False),
            ),
        )
        chosen[evidence] = ordered[0]
        if len(rows) > 1:
            duplicates.append(
                {
                    "evidence": evidence,
                    "row_count": len(rows),
                    "timestamps_ms": [row.get("source_timestamp_ms") for row in ordered],
                }
            )
    return chosen, duplicates


def relevant_int(row: dict[str, Any], field: str, keys: tuple[str, ...]) -> list[int]:
    facts = row.get("facts") or {}
    if not isinstance(facts, dict):
        return []
    values: list[int] = []
    for key in keys:
        value = value_from_facts(facts, key, field)
        if integer(value):
            values.append(value)
    return values


def pointer(row: dict[str, Any] | None, field: str, root: Path) -> dict[str, Any]:
    if not row:
        return {"present": False}
    return reading_ref(root, row, field)


def before_after_operands(
    root: Path,
    rows: list[dict[str, Any]],
    proof_evidence: list[str],
    event: dict[str, Any] | None,
    field: str,
) -> dict[str, Any]:
    event = event or {}
    start = event.get("first_seen_ms")
    end = event.get("last_seen_ms")
    proof_set = set(proof_evidence)
    timed = [
        row
        for row in rows
        if integer(row.get("source_timestamp_ms"))
        and row.get("evidence") in proof_set
    ]

    def stat_value(row: dict[str, Any]) -> Any:
        stats = row.get("stats") or {}
        values = stats.get("values") or {}
        return values.get(field) if isinstance(values, dict) else None

    before_candidates = [
        row
        for row in timed
        if integer(start)
        and row["source_timestamp_ms"] < start
        and integer(stat_value(row))
    ]
    before_candidates.sort(key=lambda row: (row["source_timestamp_ms"], row.get("evidence", "")))
    before = before_candidates[-1] if before_candidates else None

    result_candidates = [
        row
        for row in timed
        if (not integer(start) or row["source_timestamp_ms"] >= start)
        and (not integer(end) or row["source_timestamp_ms"] <= end)
        and integer(value_from_facts(row.get("facts") or {}, "result_values", field))
    ]
    result_candidates.sort(key=lambda row: (row["source_timestamp_ms"], row.get("evidence", "")))
    after = result_candidates[-1] if result_candidates else None
    after_selection = "latest_integer_result_value_in_event_window"

    # Direct badges/effects are the fallback when a result counter is not part
    # of this event.  The choice is chronological and never filtered by the
    # claimed contribution amount.
    if after is None:
        evidence_rows = [
            row
            for row in timed
            if row.get("evidence") in proof_set
            and (not integer(start) or row["source_timestamp_ms"] >= start)
            and (not integer(end) or row["source_timestamp_ms"] <= end)
        ]
        evidence_rows.sort(
            key=lambda row: (row["source_timestamp_ms"], row.get("evidence", ""))
        )
        if evidence_rows:
            after = evidence_rows[-1]
            after_selection = "latest_worker_baseline_evidence_in_event_window"

    before_value = (
        stat_value(before)
        if before
        else None
    )
    after_value = (
        value_from_facts(after.get("facts") or {}, "result_values", field)
        if after
        else None
    )
    if not integer(after_value) and after:
        direct = relevant_int(
            after,
            field,
            (
                "training_gains",
                "awarded_performance_gains",
                "performance_gains",
            ),
        )
        after_value = direct[-1] if direct else None
    recomputed = (
        after_value - before_value
        if integer(before_value) and integer(after_value)
        else None
    )
    return {
        "selection_without_amount_filter": True,
        "event_window_ms": [start, end],
        "before": {
            **pointer(before, field, root),
            "value": before_value,
            "selected_by": "latest_integer_stats_value_before_event_start",
            "candidate_count": len(before_candidates),
        },
        "after": {
            **pointer(after, field, root),
            "value": after_value,
            "selected_by": after_selection,
            "candidate_count": len(result_candidates),
        },
        "recomputed_endpoint_delta": recomputed,
        "after_is_result_value": bool(after in result_candidates),
        "proof_status": (
            "endpoint_compatible_unverified"
            if integer(recomputed)
            else "no_independent_endpoint_operands"
        ),
    }


def source_phase_index(review: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for section in ("target_comparisons", "historical_regressions"):
        for group in review.get(section) or []:
            # Target groups wrap contributions; historical regression entries
            # are already one contribution each.
            items = (
                group.get("contributions")
                if isinstance(group, dict) and isinstance(group.get("contributions"), list)
                else [group]
            )
            for item in items:
                if not isinstance(item, dict):
                    continue
                # The old-only rows are from the v1 source.  Event ids repeat
                # across recordings, so never let an independent run overwrite
                # a v1 proof with a same-named event.
                if item.get("recording") != "v1":
                    continue
                event_id = item.get("event_id")
                field = item.get("field")
                if isinstance(event_id, str) and isinstance(field, str):
                    index[(event_id, field)] = {
                        "source_id": item.get("source_id") or group.get("id"),
                        "source_amount": item.get("source_amount"),
                        "accepted_value": item.get("accepted_value"),
                        "source_proof_status": item.get("source_proof_status"),
                        "primary_cause": item.get("primary_cause"),
                        "root_causes": item.get("root_causes"),
                        "source_interval_ms": item.get("source_interval_ms"),
                        "source_evidence": item.get("source_review_evidence"),
                        "producer_source_proof": item.get("producer_source_proof"),
                        "negative_evidence": item.get("negative_evidence"),
                    }
    return index


def compact_phase_proof(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    direct = value.get("direct_provenance") or {}
    observations = []
    for observation in direct.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        evidence = observation.get("evidence") or {}
        observations.append(
            {
                "role": observation.get("role"),
                "source_timestamp_ms": observation.get("source_timestamp_ms"),
                "value": observation.get("value"),
                "evidence": evidence.get("path"),
                "sha256": evidence.get("sha256"),
            }
        )
    return {
        key: value.get(key)
        for key in (
            "status",
            "accepted_value",
            "basis",
            "phase_order",
            "component_first_seen_ms",
            "full_last_seen_ms",
            "observed_values",
        )
        if key in value
    } | {
        "direct_observations": observations,
        "independent_effect_verification": False,
    }


def strict_reference_index(grade: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for case in grade.get("cases") or []:
        for item in case.get("results") or []:
            source_id = item.get("source_id")
            if not isinstance(source_id, str):
                continue
            # Source ids use v1-turn-055-after-race-skill-points.  Keeping the
            # complete id in the artifact avoids relying on a prediction path.
            if source_id == "v1-turn-055-after-race-skill-points":
                result[("outcome-0177", "skill_points")] = {
                    "source_id": source_id,
                    "status": item.get("status"),
                    "source_interval_ms": item.get("source_interval_ms"),
                    "source_evidence": item.get("source_evidence"),
                    "fields": item.get("fields"),
                    "reference_case": case.get("case_id"),
                }
    return result


def source_anchor(
    root: Path,
    event_id: str,
    field: str,
    phase: dict[tuple[str, str], dict[str, Any]],
    strict: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    anchor = MANUAL_SOURCE_ANCHORS.get((event_id, field))
    phase_item = phase.get((event_id, field))
    strict_item = strict.get((event_id, field))
    if anchor:
        path = anchor.get("path")
        image = source_file(root, path) if isinstance(path, str) else None
        exists = bool(image and image.is_file())
        return {
            **anchor,
            "source_file": relative(root, image) if image else None,
            "source_file_exists": exists,
            "source_file_sha256": sha256(image) if exists and image else None,
            "strict_reference": strict_item,
        }
    if phase_item:
        return {
            "status": "independent_source_phase_positive",
            "source_id": phase_item.get("source_id"),
            "source_amount": phase_item.get("source_amount"),
            "accepted_value": phase_item.get("accepted_value"),
            "source_proof_status": phase_item.get("source_proof_status"),
            "primary_cause": phase_item.get("primary_cause"),
            "root_causes": phase_item.get("root_causes"),
            "source_interval_ms": phase_item.get("source_interval_ms"),
            "source_evidence": phase_item.get("source_evidence"),
            "producer_source_proof": compact_phase_proof(
                phase_item.get("producer_source_proof")
            ),
            "negative_evidence": phase_item.get("negative_evidence"),
        }
    return None


def manual_representative(
    root: Path, event_id: str, field: str
) -> dict[str, Any] | None:
    for item in MANUAL_SOURCE_REPRESENTATIVES:
        if item.get("event_id") != event_id or item.get("field") != field:
            continue
        path = item.get("path")
        image = source_file(root, path) if isinstance(path, str) else None
        exists = bool(image and image.is_file())
        return {
            **item,
            "source_file": relative(root, image) if image else None,
            "source_file_exists": exists,
            "source_file_sha256": sha256(image) if exists and image else None,
        }
    return None


def classify(
    contribution: dict[str, Any],
    baseline_event: dict[str, Any] | None,
    worker_event: dict[str, Any] | None,
    baseline_rows: list[dict[str, Any]],
    worker_rows: list[dict[str, Any]],
    phase_anchor: dict[str, Any] | None,
) -> tuple[str, str, str, str]:
    basis = contribution.get("old_basis")
    channel = contribution.get("channel")
    field = contribution.get("field")
    amount = contribution.get("amount")
    worker_event = worker_event or {}
    old_event = baseline_event or {}
    worker_direct = [
        value
        for row in worker_rows
        for value in relevant_int(row, field, ("training_gains",))
    ]
    worker_candidates = [
        value
        for row in worker_rows
        for value in (
            value_from_facts(row.get("facts") or {}, "training_gain_candidates", field)
            if isinstance(value_from_facts(row.get("facts") or {}, "training_gain_candidates", field), list)
            else []
        )
        if integer(value)
    ]
    worker_conflicts = (worker_event.get("conflicting_readings") or {}).get(field) or []
    old_direct = [
        value
        for row in baseline_rows
        for value in relevant_int(row, field, ("training_gains", "awarded_performance_gains"))
    ]
    old_candidates = [
        value
        for row in baseline_rows
        for value in (
            value_from_facts(row.get("facts") or {}, "training_gain_candidates", field)
            if isinstance(value_from_facts(row.get("facts") or {}, "training_gain_candidates", field), list)
            else []
        )
        if integer(value)
    ]

    if channel == "performance":
        return (
            "worker_performance_fact_scope_gap",
            "The worker keeps the same performance_deltas summary but has no accepted performance gain fact or causal row on the retained source window; the visible old performance badge is therefore a worker evidence-scope gap, not an error correction.",
            "source_supported_old_value_missing_in_worker",
            "performance applied overlay parser / field-evidence ownership",
        )
    if basis == "observed_receipt":
        return (
            "receipt_effect_scope_gap",
            "The old outcome effect is source-visible at the recorded receipt frame, while the worker keeps the outcome event but drops this effect; current absence cannot certify the old value as wrong.",
            "source_supported_old_value_missing_in_worker",
            "receipt overlay/effect parser ownership",
        )
    if basis == "state_derived":
        return (
            "state_endpoint_only_scope_change",
            "The worker retains the before/after counters in the same event window but does not retain this state-derived causal row. Endpoint compatibility is recorded separately and is not direct effect proof.",
            "endpoint_compatible_unverified",
            "state-derived causal promotion policy",
        )
    if basis == "state_constrained":
        if phase_anchor:
            return (
                "full_component_phase_conflict_rejection",
                "The source-phase review accepts the complete +11 badge before later component +1 evidence; the worker's same-window conflict is a rejection/interface gap, not a source-supported correction.",
                "source_supported_old_value_rejected_by_phase_conflict",
                "full-before-component phase resolver",
            )
        return (
            "source_conflict_unresolved",
            "The worker retains conflicting source candidates and drops the old constrained row; no independent correction was established.",
            "unresolved_source_conflict",
            "source crop conflict resolver",
        )
    if worker_conflicts and (amount in worker_direct or amount in worker_candidates):
        return (
            "source_value_rejected_by_conflict",
            "The worker still reads the old amount in at least one source frame, but a conflicting candidate remains attached to the same event/field, so the row was rejected rather than corrected.",
            "source_supported_old_value_rejected_by_conflict",
            "training full/component conflict resolver",
        )
    if amount in old_direct or amount in old_candidates:
        return (
            "worker_direct_fact_scope_gap",
            "The baseline source window records the old amount, while the worker has no accepted direct amount for this field; disappearance is a readable-evidence scope gap, not proof that the old value was erroneous.",
            "source_supported_old_value_missing_in_worker",
            "training direct-gain provenance/parser scope",
        )
    return (
        "source_conflict_unresolved",
        "The old row is absent and the retained worker source window does not provide an independently adjudicated replacement.",
        "unresolved_source_conflict",
        "source crop conflict resolver",
    )


def build_audit(root: Path) -> dict[str, Any]:
    worker = read_json(root / WORKER_REPORT)
    baseline = read_json(root / BASELINE_REPORT)
    prior = read_json(root / PRIOR_AUDIT)
    phase_review = read_json(root / SOURCE_PHASE_REVIEW)
    strict_grade = read_json(root / STRICT_GRADE)

    baseline_contributions = {
        item.get("id"): item
        for item in (baseline.get("causal_accounting") or {}).get("contributions") or []
        if isinstance(item.get("id"), str)
    }
    worker_contributions = {
        item.get("id"): item
        for item in (worker.get("causal_accounting") or {}).get("contributions") or []
        if isinstance(item.get("id"), str)
    }
    baseline_events = {
        item.get("id"): item
        for item in (baseline.get("gameplay_tracking") or {}).get("events") or []
        if isinstance(item.get("id"), str)
    }
    worker_events = {
        item.get("id"): item
        for item in (worker.get("gameplay_tracking") or {}).get("events") or []
        if isinstance(item.get("id"), str)
    }
    baseline_readings, baseline_duplicates = unique_readings(baseline)
    worker_readings, worker_duplicates = unique_readings(worker)

    old_only_from_prior = (
        prior.get("before_after_comparison", {}).get("old_only_contributions") or []
    )
    old_only_ids = [item.get("id") for item in old_only_from_prior]
    recomputed_old_only = sorted(set(baseline_contributions) - set(worker_contributions))
    if sorted(old_only_ids) != recomputed_old_only:
        raise ValueError("prior old-only list does not match baseline/worker contribution sets")

    phase_index = source_phase_index(phase_review)
    strict_index = strict_reference_index(strict_grade)
    cases: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    basis_counts: Counter[str] = Counter()
    channel_counts: Counter[str] = Counter()
    source_status_counts: Counter[str] = Counter()
    same_event_count = 0
    alternate_field_count = 0
    missing_worker_source_path_count = 0
    source_file_missing_count = 0

    for item in sorted(old_only_from_prior, key=lambda value: value.get("id", "")):
        contribution_id = item["id"]
        event_id = item.get("event_id")
        field = item.get("field")
        channel = item.get("channel")
        amount = item.get("amount")
        baseline_contribution = baseline_contributions[contribution_id]
        old_event = baseline_events.get(event_id)
        worker_event = worker_events.get(event_id)
        old_paths = [path for path in item.get("evidence") or [] if isinstance(path, str)]
        baseline_field_paths = [
            path
            for key, values in (old_event or {}).get("field_evidence", {}).items()
            if field in key
            for path in (values if isinstance(values, list) else [])
            if isinstance(path, str)
        ]
        proof_paths = list(dict.fromkeys(old_paths + baseline_field_paths))
        baseline_rows = [baseline_readings[path] for path in old_paths if path in baseline_readings]
        worker_rows = [worker_readings[path] for path in old_paths if path in worker_readings]
        missing_paths = [path for path in old_paths if path not in worker_readings]
        missing_worker_source_path_count += len(missing_paths)
        worker_field_paths = []
        if worker_event:
            worker_field_paths = [
                path
                for key, values in (worker_event.get("field_evidence") or {}).items()
                if field in key
                for path in (values if isinstance(values, list) else [])
            ]
        same_event_fields = [
            {
                "id": candidate.get("id"),
                "basis": candidate.get("basis"),
                "amount": candidate.get("amount"),
                "channel": candidate.get("channel"),
            }
            for candidate in worker_contributions.values()
            if candidate.get("event_id") == event_id and candidate.get("field") == field
        ]
        if worker_event:
            same_event_count += 1
        alternate_field_count += len(same_event_fields)
        old_refs = [reading_ref(root, baseline_readings.get(path), field) for path in old_paths]
        worker_refs = [reading_ref(root, worker_readings.get(path), field) for path in old_paths]
        source_file_missing_count += sum(
            1 for ref in old_refs + worker_refs if ref.get("present") and not ref.get("source_file_exists")
        )
        phase_anchor = source_anchor(root, event_id, field, phase_index, strict_index)
        category, reason, source_status, ownership = classify(
            item,
            old_event,
            worker_event,
            baseline_rows,
            worker_rows,
            phase_anchor,
        )
        category_counts[category] += 1
        verdict_counts[source_status] += 1
        basis_counts[item.get("old_basis")] += 1
        channel_counts[channel] += 1
        source_status_counts[
            "worker_field_evidence_present" if worker_field_paths else "worker_field_evidence_absent"
        ] += 1
        if phase_anchor and phase_anchor.get("status") == "manual_source_visual_positive":
            source_status_counts["manual_source_visual_positive"] += 1
        elif phase_anchor and phase_anchor.get("status") == "independent_source_phase_positive":
            source_status_counts["independent_source_phase_positive"] += 1

        endpoint = before_after_operands(
            root,
            list(worker_readings.values()),
            proof_paths,
            worker_event,
            field,
        )
        baseline_endpoint = before_after_operands(
            root,
            list(baseline_readings.values()),
            proof_paths,
            old_event,
            field,
        )
        worker_direct_values = sorted(
            {
                value
                for row in worker_rows
                for value in relevant_int(row, field, ("training_gains",))
            }
        )
        worker_candidate_values = sorted(
            {
                value
                for row in worker_rows
                for value in (
                    value_from_facts(row.get("facts") or {}, "training_gain_candidates", field)
                    if isinstance(value_from_facts(row.get("facts") or {}, "training_gain_candidates", field), list)
                    else []
                )
                if integer(value)
            }
        )
        baseline_direct_values = sorted(
            {
                value
                for row in baseline_rows
                for value in relevant_int(
                    row, field, ("training_gains", "awarded_performance_gains")
                )
            }
        )
        baseline_candidate_values = sorted(
            {
                value
                for row in baseline_rows
                for value in (
                    value_from_facts(row.get("facts") or {}, "training_gain_candidates", field)
                    if isinstance(value_from_facts(row.get("facts") or {}, "training_gain_candidates", field), list)
                    else []
                )
                if integer(value)
            }
        )
        source_review = {
            "status": (
                phase_anchor.get("status")
                if phase_anchor
                else "baseline_recorded_source_fact_only"
            ),
            "anchor": phase_anchor,
            "manual_visual_representative": manual_representative(
                root, event_id, field
            ),
            "baseline_old_event_field": event_ref(old_event, field),
            "baseline_relevant_readings": old_refs,
            "worker_same_paths": worker_refs,
            "worker_field_evidence": [
                reading_ref(root, worker_readings.get(path), field)
                for path in worker_field_paths
            ],
            "worker_conflicting_readings": (
                (worker_event or {}).get("conflicting_readings") or {}
            ).get(field),
            "worker_direct_values": worker_direct_values,
            "worker_candidate_values": worker_candidate_values,
            "baseline_direct_values": baseline_direct_values,
            "baseline_candidate_values": baseline_candidate_values,
            "selection_note": "Source rows are listed by recorded evidence path and time. No row is selected by comparing against the claimed amount.",
        }
        dependencies = list(KNOWN_DEPENDENCIES.get((event_id, field), []))
        if phase_anchor and phase_anchor.get("dependency"):
            dependencies.append(phase_anchor["dependency"])
        if category == "worker_performance_fact_scope_gap":
            dependencies.append("performance applied overlay/parser shared cause")
        if category == "receipt_effect_scope_gap" and event_id == "outcome-0254":
            dependencies.append("post-race/URA receipt overlay closure")
        if category == "worker_direct_fact_scope_gap":
            dependencies.append("training direct-gain provenance/parser shared cause")
        case = {
            "id": contribution_id,
            "old_basis": item.get("old_basis"),
            "channel": channel,
            "field": field,
            "amount": amount,
            "event_id": event_id,
            "turn_id": item.get("turn_id"),
            "classification": category,
            "source_verdict": source_status,
            "change_reason": reason,
            "suggested_shared_ownership": ownership,
            "dependencies": sorted(set(dependencies)),
            "baseline": {
                "contribution": {
                    key: baseline_contribution.get(key)
                    for key in (
                        "id",
                        "basis",
                        "channel",
                        "field",
                        "amount",
                        "event_id",
                        "turn_id",
                        "evidence",
                        "provenance",
                    )
                    if key in baseline_contribution
                },
                "event": event_ref(old_event, field),
                "evidence": old_refs,
                "proof_paths": proof_paths,
                "before_after": baseline_endpoint,
            },
            "worker": {
                "event": event_ref(worker_event, field),
                "same_event_same_field_contributions": same_event_fields,
                "field_evidence": worker_field_paths,
                "evidence_at_baseline_paths": worker_refs,
                "missing_baseline_paths": missing_paths,
                "proof_paths": proof_paths,
                "before_after": endpoint,
            },
            "source_review": source_review,
        }
        cases.append(case)

    endpoint_status_counts = Counter(
        case["worker"]["before_after"]["proof_status"] for case in cases
    )
    old_only_basis_counts = Counter(case["old_basis"] for case in cases)
    old_only_channel_counts = Counter(case["channel"] for case in cases)
    return {
        "schema_version": "tracen-replay/intermediate-worker-old-only-source-audit-v1",
        "scope": {
            "baseline": relative(root, root / BASELINE_REPORT),
            "worker": relative(root, root / WORKER_REPORT),
            "prior_source_basis_audit": relative(root, root / PRIOR_AUDIT),
            "source_root": relative(root, root / SOURCE_ROOT),
            "baseline_worker_only": True,
            "fresh_parser_rerun": False,
            "all_three_run_acceptance": False,
            "full_history_accuracy": None,
            "frozen_inputs_modified": False,
            "recognizer_modified": False,
        },
        "integrity": {
            "baseline_report_sha256": sha256(root / BASELINE_REPORT),
            "worker_report_sha256": sha256(root / WORKER_REPORT),
            "source_sha256_baseline": (baseline.get("source") or {}).get("sha256"),
            "source_sha256_worker": (worker.get("source") or {}).get("sha256"),
            "source_sha256_matches": (baseline.get("source") or {}).get("sha256")
            == (worker.get("source") or {}).get("sha256"),
            "prior_source_basis_audit_sha256": sha256(root / PRIOR_AUDIT),
            "source_phase_review_sha256": sha256(root / SOURCE_PHASE_REVIEW),
            "strict_grade_sha256": sha256(root / STRICT_GRADE),
            "script_sha256": sha256(Path(__file__).resolve()),
        },
        "coverage": {
            "prior_old_only_rows": len(old_only_from_prior),
            "recomputed_old_only_rows": len(recomputed_old_only),
            "audited_rows": len(cases),
            "coverage_exact": len(cases) == 30
            and len(old_only_from_prior) == 30
            and len(recomputed_old_only) == 30,
            "old_only_ids_unique": len(old_only_ids) == len(set(old_only_ids)),
            "worker_same_event_present_count": same_event_count,
            "worker_alternate_same_event_field_count": alternate_field_count,
            "worker_field_evidence_present_count": sum(
                1
                for case in cases
                if case["worker"]["field_evidence"]
            ),
            "worker_baseline_evidence_paths_missing_count": missing_worker_source_path_count,
            "source_files_missing_count": source_file_missing_count,
            "duplicate_baseline_evidence_groups": len(baseline_duplicates),
            "duplicate_worker_evidence_groups": len(worker_duplicates),
        },
        "counts": {
            "old_only_basis": dict(sorted(old_only_basis_counts.items())),
            "old_only_channel": dict(sorted(old_only_channel_counts.items())),
            "classification": dict(sorted(category_counts.items())),
            "source_verdict": dict(sorted(verdict_counts.items())),
            "source_review": dict(sorted(source_status_counts.items())),
            "endpoint_proof_status": dict(sorted(endpoint_status_counts.items())),
            "same_contribution_not_double_counted": True,
        },
        "recurring_causes": [
            {
                "cause": "performance applied overlay/parser scope gap",
                "count": category_counts.get("worker_performance_fact_scope_gap", 0),
                "rows": [
                    case["id"]
                    for case in cases
                    if case["classification"] == "worker_performance_fact_scope_gap"
                ],
                "priority": 1,
                "action": "Restore source-bound performance gain field evidence/provenance for the retained applied overlay; keep performance_deltas summary separate from accepted proof.",
            },
            {
                "cause": "full/component crop conflict rejection",
                "count": category_counts.get("source_value_rejected_by_conflict", 0)
                + category_counts.get("full_component_phase_conflict_rejection", 0),
                "rows": [
                    case["id"]
                    for case in cases
                    if case["classification"]
                    in {
                        "source_value_rejected_by_conflict",
                        "full_component_phase_conflict_rejection",
                    }
                ],
                "priority": 2,
                "action": "Use bounded chronology and source geometry to adjudicate full badges before later component crops; preserve explicit conflicts when the source cannot resolve them.",
            },
            {
                "cause": "direct training-gain provenance scope gap",
                "count": category_counts.get("worker_direct_fact_scope_gap", 0),
                "rows": [
                    case["id"]
                    for case in cases
                    if case["classification"] == "worker_direct_fact_scope_gap"
                ],
                "priority": 3,
                "action": "Carry source-bound direct training badge observations into the event without replacing them with endpoint arithmetic.",
            },
            {
                "cause": "receipt effect/overlay scope gap",
                "count": category_counts.get("receipt_effect_scope_gap", 0),
                "rows": [
                    case["id"]
                    for case in cases
                    if case["classification"] == "receipt_effect_scope_gap"
                ],
                "priority": 4,
                "action": "Retain the source-visible receipt effect in the outcome event and bind it to its receipt frame; do not infer it from balance alone.",
            },
            {
                "cause": "state-derived endpoint-only row dropped from causal scope",
                "count": category_counts.get("state_endpoint_only_scope_change", 0),
                "rows": [
                    case["id"]
                    for case in cases
                    if case["classification"] == "state_endpoint_only_scope_change"
                ],
                "priority": 5,
                "action": "Keep these endpoint-compatible rows labeled state-derived/unverified unless a direct source badge is independently bound.",
            },
        ],
        "source_review_notes": {
            "source_supported_correction_count": 0,
            "source_supported_correction_note": "No disappearance is certified as an erroneous old value. The worker either drops source-readable evidence, rejects a conflicting candidate, or retains only endpoint-compatible state readings.",
            "manual_source_visual_positive_cases": [
                {
                    "event_id": event_id,
                    "field": field,
                    **source_anchor(root, event_id, field, phase_index, strict_index),
                }
                for event_id, field in MANUAL_SOURCE_ANCHORS
            ],
            "manual_source_visual_representatives": [
                {
                    **item,
                    "source_file": relative(
                        root, source_file(root, item["path"])
                    ),
                    "source_file_exists": source_file(root, item["path"]).is_file(),
                    "source_file_sha256": (
                        sha256(source_file(root, item["path"]))
                        if source_file(root, item["path"]).is_file()
                        else None
                    ),
                }
                for item in MANUAL_SOURCE_REPRESENTATIVES
            ],
            "independent_phase_positive_cases": [
                {
                    "event_id": event_id,
                    "field": field,
                    "source_id": anchor.get("source_id"),
                    "accepted_value": anchor.get("accepted_value"),
                    "source_proof_status": anchor.get("source_proof_status"),
                }
                for (event_id, field), anchor in phase_index.items()
                if any(
                    case["event_id"] == event_id and case["field"] == field
                    for case in cases
                )
            ],
            "state_endpoint_limit": "Endpoint before/after pointers are chosen by chronology and stable source values without amount filtering. A matching difference remains compatibility evidence, not independent effect proof.",
        },
        "cases": cases,
        "limitations": [
            "This artifact compares only baseline v1 with intermediate-worker-v2; it is not a fresh all-three-run replay.",
            "The source video hash is shared by both reports, but a source-file presence/hash check does not replace visual adjudication.",
            "Baseline recorded facts and event effects are source pointers from the preserved report; independent phase proof is included only where source-phase-verification-v1 covers the same event/field.",
            "The two receipt anchors and fourteen representative training frames were visually checked in this audit. Other rows retain exact recorded source facts and are grouped by mechanism; they are not silently promoted to independent proof.",
            "Full-history recall and accuracy remain unmeasured.",
        ],
    }


def markdown(audit: dict[str, Any]) -> str:
    counts = audit["counts"]
    coverage = audit["coverage"]
    lines = [
        "# Intermediate worker old-only source audit",
        "",
        "This bounded audit traces every baseline-v1 contribution absent from intermediate-worker-v2. It preserves the original reports and source labels, and does not rerun recognition.",
        "",
        "## Integrity and coverage",
        "",
        f"- Baseline: `{audit['scope']['baseline']}` (`{audit['integrity']['baseline_report_sha256']}`).",
        f"- Worker: `{audit['scope']['worker']}` (`{audit['integrity']['worker_report_sha256']}`).",
        f"- Shared source SHA: `{audit['integrity']['source_sha256_baseline']}`; reports match: **{audit['integrity']['source_sha256_matches']}**.",
        f"- Exact old-only coverage: **{coverage['audited_rows']}/{coverage['prior_old_only_rows']}**; recomputed set agrees: **{coverage['recomputed_old_only_rows']}**; exact: **{coverage['coverage_exact']}**.",
        f"- All **{coverage['worker_same_event_present_count']}** old event IDs remain present in the worker; alternate same-event/field causal rows: **{coverage['worker_alternate_same_event_field_count']}**; worker field-evidence paths for the old fields: **{coverage['worker_field_evidence_present_count']}**.",
        f"- Worker source paths missing relative to the baseline evidence lists: **{coverage['worker_baseline_evidence_paths_missing_count']}**; missing source files: **{coverage['source_files_missing_count']}**.",
        "",
        "## Counts",
        "",
        "| Old basis | Count |",
        "| --- | ---: |",
    ]
    for key, value in counts["old_only_basis"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "| Old-only verdict | Count | Meaning |",
            "| --- | ---: | --- |",
            f"| `source_supported_old_value_missing_in_worker` | {counts['source_verdict'].get('source_supported_old_value_missing_in_worker', 0)} | Source evidence remains readable/recorded, but the worker drops the direct performance, training, or receipt field. |",
            f"| `source_supported_old_value_rejected_by_conflict` | {counts['source_verdict'].get('source_supported_old_value_rejected_by_conflict', 0)} | The worker retains a conflicting source candidate and does not choose the old amount. |",
            f"| `source_supported_old_value_rejected_by_phase_conflict` | {counts['source_verdict'].get('source_supported_old_value_rejected_by_phase_conflict', 0)} | Independent phase review accepts the complete badge before a later component crop. |",
            f"| `endpoint_compatible_unverified` | {counts['source_verdict'].get('endpoint_compatible_unverified', 0)} | Source counters recompute the old difference, but no direct effect badge is established. |",
            f"| `unresolved_source_conflict` | {counts['source_verdict'].get('unresolved_source_conflict', 0)} | No independently adjudicated replacement is available in the retained worker source window. |",
            "",
            "No old-only row is certified as a source-supported correction. The worker's absence is reported as a scope gap, conflict rejection, or endpoint-only compatibility.",
            "",
            "## Recurring causes and ownership",
            "",
        ]
    )
    for cause in audit["recurring_causes"]:
        rows = ", ".join(f"`{row}`" for row in cause["rows"])
        lines.append(
            f"- **P{cause['priority']} {cause['cause']} ({cause['count']})** — {cause['action']} Rows: {rows}."
        )
    lines.extend(
        [
            "",
            "## Per-row evidence",
            "",
            "Each row below is emitted once. The JSON artifact contains all source pointers, timestamps, image hashes, worker conflicts, and deterministic before/after selections.",
            "",
            "| ID | Basis | Field | Amount | Classification | Dependencies |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for case in audit["cases"]:
        deps = ", ".join(case["dependencies"]) or "—"
        lines.append(
            f"| `{case['id']}` | `{case['old_basis']}` | `{case['field']}` | {case['amount']} | `{case['classification']}` | {deps} |"
        )
    lines.extend(
        [
            "",
            "## Source-bound limits",
            "",
        ]
    )
    lines.extend(f"- {limit}" for limit in audit["limitations"])
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
    print(
        json.dumps(
            {
                "output_json": relative(root, output_json),
                "output_markdown": relative(root, output_md),
                "audited": audit["coverage"]["audited_rows"],
                "classification": audit["counts"]["classification"],
                "source_verdict": audit["counts"]["source_verdict"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
