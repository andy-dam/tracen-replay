"""Audit turn-explanation reports without replaying the recordings.

The auditor compares a frozen report with a candidate report, checks the
turn-opening provenance embedded in the candidate, and verifies any bounded
source recovery caches that the candidate claims to use.  It intentionally
reports changes in accepted actions, numeric event amounts, and canonical
effects separately so an improvement cannot hide a regression.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover - only needed when recovery caches exist
    Image = None


REPO = Path(__file__).resolve().parents[2]
RUNS = ("v1", "independent-01", "independent-02")
CHANNEL_FIELDS = {
    "stats": ("speed", "stamina", "power", "guts", "wit", "skill_points"),
    "performance": ("dance", "passion", "vocal", "visual", "composure"),
}
DIRECT_BASES = {"observed_receipt", "observed_training_gain", "committed_skill_debit"}
RECOVERY_FACTS = ("training_gain_recovery", "numeric_receipt_recovery", "boundary_state_recovery")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path(value, base=None):
    """Resolve a repository-relative path from either slash convention."""

    path = Path(str(value).replace("\\", "/"))
    if path.is_absolute():
        return path
    return (base or REPO) / path


def _json_key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def json_pointer(document, pointer):
    """Resolve an RFC 6901 JSON pointer, raising a useful error on bad refs."""

    if pointer == "":
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"Invalid JSON pointer: {pointer!r}")
    value = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            try:
                value = value[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"JSON pointer list token is invalid: {pointer!r}") from exc
        elif isinstance(value, dict) and token in value:
            value = value[token]
        else:
            raise ValueError(f"JSON pointer does not resolve: {pointer!r}")
    return value


def _is_int(value):
    return type(value) is int


def _deep_diff(left, right, path="$", limit=32):
    """Return compact changed paths, retaining enough detail for diagnostics."""

    if left == right:
        return []
    if len(path) > 400:
        return [path]
    if isinstance(left, dict) and isinstance(right, dict):
        rows = []
        for key in sorted(set(left) | set(right), key=str):
            child = f"{path}.{key}"
            if key not in left or key not in right:
                rows.append(child)
            else:
                rows.extend(_deep_diff(left[key], right[key], child, limit))
            if len(rows) >= limit:
                return rows[:limit]
        return rows
    if isinstance(left, list) and isinstance(right, list):
        rows = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                rows.append(child)
            else:
                rows.extend(_deep_diff(left[index], right[index], child, limit))
            if len(rows) >= limit:
                return rows[:limit]
        return rows
    return [path]


def _object_digest(value):
    return hashlib.sha256(_json_key(value).encode("utf-8")).hexdigest()


def _row_enrichment_kind(old, new):
    """Classify the only additive changes allowed on a retained source row."""

    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    old_facts = old.get("facts") if isinstance(old.get("facts"), dict) else {}
    new_facts = new.get("facts") if isinstance(new.get("facts"), dict) else {}
    if any(new.get(key) != value for key, value in old.items() if key != "facts"):
        return None
    if any(new_facts.get(key) != value for key, value in old_facts.items()):
        return None
    added = set(new_facts) - set(old_facts)
    if added and added.issubset({"performance_points", "performance_panel_recovery", "boundary_state_recovery"}):
        return "additive_panel_provenance"
    return None


def _compact_row_change(old, new, evidence, before_index, after_index):
    return dict(
        evidence=evidence,
        before_index=before_index,
        after_index=after_index,
        changed_paths=_deep_diff(old, new),
        before_sha256=_object_digest(old),
        after_sha256=_object_digest(new),
        before_facts_keys=sorted((old.get("facts") or {}).keys()),
        after_facts_keys=sorted((new.get("facts") or {}).keys()),
        added_facts=sorted(set((new.get("facts") or {})) - set((old.get("facts") or {}))),
        change_kind=_row_enrichment_kind(old, new),
    )


def _report_readings(report):
    return (report.get("gameplay_tracking") or {}).get("readings") or []


def compare_old_readings(before, after):
    """Check that every frozen reading survives byte-for-byte in the candidate.

    New readings are allowed for bounded recovery.  Existing rows changed by a
    deliberate panel enrichment are reported separately, so callers can decide
    whether that provenance is acceptable instead of silently ignoring a
    mutation.
    """

    old = _report_readings(before)
    new = _report_readings(after)
    old_by_evidence = defaultdict(list)
    new_by_evidence = defaultdict(list)
    old_without_evidence = []
    new_without_evidence = []
    for index, row in enumerate(old):
        evidence = row.get("evidence") if isinstance(row, dict) else None
        if isinstance(evidence, str) and evidence:
            old_by_evidence[evidence].append((index, row))
        else:
            old_without_evidence.append((index, row))
    for index, row in enumerate(new):
        evidence = row.get("evidence") if isinstance(row, dict) else None
        if isinstance(evidence, str) and evidence:
            new_by_evidence[evidence].append((index, row))
        else:
            new_without_evidence.append((index, row))

    missing = []
    changed = []
    matched = []
    for evidence, rows in old_by_evidence.items():
        candidates = new_by_evidence.get(evidence, [])
        if not candidates:
            missing.append(dict(evidence=evidence, before_index=rows[0][0]))
            continue
        for before_index, old_row in rows:
            same = [item for item in candidates if item[1] == old_row]
            if same:
                matched.append(dict(evidence=evidence, before_index=before_index, after_index=same[0][0]))
                continue
            after_index, new_row = candidates[0]
            changed.append(_compact_row_change(old_row, new_row, evidence, before_index, after_index))
    for before_index, old_row in old_without_evidence:
        candidates = [
            (index, row)
            for index, row in new_without_evidence
            if row.get("source_timestamp_ms") == old_row.get("source_timestamp_ms")
        ]
        if not candidates:
            missing.append(dict(before_index=before_index, reason="missing_evidence_and_timestamp_match"))
        elif candidates[0][1] == old_row:
            matched.append(dict(before_index=before_index, after_index=candidates[0][0]))
        else:
            changed.append(_compact_row_change(old_row, candidates[0][1], None, before_index, candidates[0][0]))
    duplicate_before = {key: len(rows) for key, rows in old_by_evidence.items() if len(rows) > 1}
    duplicate_after = {key: len(rows) for key, rows in new_by_evidence.items() if len(rows) > 1}
    old_evidence = set(old_by_evidence)
    new_evidence = set(new_by_evidence)
    new_rows = [
        dict(after_index=index, evidence=row.get("evidence"), source_timestamp_ms=row.get("source_timestamp_ms"))
        for index, row in enumerate(new)
        if row.get("evidence") not in old_evidence
    ]
    return dict(
        before_count=len(old),
        after_count=len(new),
        matched_count=len(matched),
        missing=missing,
        changed=changed,
        new_rows=new_rows,
        duplicate_evidence_before=duplicate_before,
        duplicate_evidence_after=duplicate_after,
        old_rows_unchanged=not missing and not changed,
        allowed_new_rows=len(new_rows),
    )


def _action_projection(row):
    return {key: row[key] for key in ("kind", "training_option", "source_timestamp_ms", "event_id", "race_id") if key in row}


def _action_key(row, index=0):
    if row.get("event_id") is not None:
        return f"event:{row['event_id']}"
    if row.get("race_id") is not None:
        return f"race:{row['race_id']}"
    return f"row:{index}:{_json_key(_action_projection(row))}"


def diff_accepted_actions(before, after):
    old = (before.get("gameplay_tracking") or {}).get("turn_action_receipts") or []
    new = (after.get("gameplay_tracking") or {}).get("turn_action_receipts") or []
    old_map = {_action_key(row, index): row for index, row in enumerate(old)}
    new_map = {_action_key(row, index): row for index, row in enumerate(new)}
    identity_changes = []
    outcome_changes = []
    for key in sorted(set(old_map) & set(new_map)):
        old_identity = _action_projection(old_map[key])
        new_identity = _action_projection(new_map[key])
        if old_identity != new_identity:
            identity_changes.append(dict(key=key, before=old_identity, after=new_identity))
        old_outcome = old_map[key].get("training_outcome")
        new_outcome = new_map[key].get("training_outcome")
        if old_outcome != new_outcome:
            outcome_changes.append(dict(key=key, before=old_outcome, after=new_outcome))
    added = [dict(key=key, action=_action_projection(new_map[key])) for key in sorted(set(new_map) - set(old_map))]
    removed = [dict(key=key, action=_action_projection(old_map[key])) for key in sorted(set(old_map) - set(new_map))]
    kind_option_changes = [
        row for row in identity_changes
        if any(row["before"].get(key) != row["after"].get(key) for key in ("kind", "training_option"))
    ]
    return dict(
        before_count=len(old),
        after_count=len(new),
        identity_unchanged=not identity_changes and not added and not removed,
        kind_option_unchanged=not kind_option_changes,
        success_metadata_unchanged=not outcome_changes,
        added=added,
        removed=removed,
        changed=identity_changes,
        kind_or_option_changes=kind_option_changes,
        success_changes=outcome_changes,
        explanation_required=bool(identity_changes or added or removed or outcome_changes),
    )


def _numeric_projection(event):
    return {key: deepcopy(event.get(key)) for key in ("id", "kind", "first_seen_ms", "last_seen_ms", "deltas", "performance_deltas")}


def diff_numeric_events(before, after):
    old_events = (before.get("gameplay_tracking") or {}).get("events") or []
    new_events = (after.get("gameplay_tracking") or {}).get("events") or []
    old_map = {event.get("id", f"index-{index}"): event for index, event in enumerate(old_events)}
    new_map = {event.get("id", f"index-{index}"): event for index, event in enumerate(new_events)}
    changed = []
    for key in sorted(set(old_map) & set(new_map), key=str):
        old_projection = _numeric_projection(old_map[key])
        new_projection = _numeric_projection(new_map[key])
        if old_projection != new_projection:
            changed.append(
                dict(
                    event_id=key,
                    changed_fields=[field for field in old_projection if old_projection[field] != new_projection[field]],
                    before=old_projection,
                    after=new_projection,
                )
            )
    added = [dict(event_id=key, event=_numeric_projection(new_map[key])) for key in sorted(set(new_map) - set(old_map), key=str)]
    removed = [dict(event_id=key, event=_numeric_projection(old_map[key])) for key in sorted(set(old_map) - set(new_map), key=str)]
    amount_changes = [
        row for row in changed
        if any(field in row["changed_fields"] for field in ("deltas", "performance_deltas"))
    ]
    return dict(
        before_count=len(old_events),
        after_count=len(new_events),
        summaries_unchanged=not changed and not added and not removed,
        added=added,
        removed=removed,
        changed=changed,
        amount_changes=amount_changes,
        explanation_required=bool(changed or added or removed),
    )


def _canonical_effect_rows(report):
    rows = []
    for event in (report.get("gameplay_tracking") or {}).get("events", []):
        for effect in event.get("effects", []):
            payload = {
                key: effect[key]
                for key in ("kind", "field", "name", "amount", "direction", "value")
                if key in effect
            }
            rows.append(dict(event_id=event.get("id"), event_kind=event.get("kind"), effect=payload))
    return rows


def diff_canonical_effects(before, after):
    old_rows = _canonical_effect_rows(before)
    new_rows = _canonical_effect_rows(after)
    old_counter = Counter(_json_key(row) for row in old_rows)
    new_counter = Counter(_json_key(row) for row in new_rows)
    added = [json.loads(key) for key in (new_counter - old_counter).elements()]
    removed = [json.loads(key) for key in (old_counter - new_counter).elements()]

    def semantic(row):
        effect = row["effect"]
        return _json_key(
            dict(
                event_id=row.get("event_id"),
                event_kind=row.get("event_kind"),
                kind=effect.get("kind"),
                field=effect.get("field"),
                name=effect.get("name"),
                direction=effect.get("direction"),
                value=effect.get("value"),
            )
        )

    old_semantic = defaultdict(list)
    new_semantic = defaultdict(list)
    for row in old_rows:
        old_semantic[semantic(row)].append(row)
    for row in new_rows:
        new_semantic[semantic(row)].append(row)
    amount_changes = []
    for key in sorted(set(old_semantic) & set(new_semantic)):
        old_amounts = sorted(row["effect"].get("amount") for row in old_semantic[key])
        new_amounts = sorted(row["effect"].get("amount") for row in new_semantic[key])
        if old_amounts != new_amounts:
            amount_changes.append(dict(identity=json.loads(key), before=old_amounts, after=new_amounts))
    return dict(
        before_count=len(old_rows),
        after_count=len(new_rows),
        retained=sum((old_counter & new_counter).values()),
        added=added,
        removed=removed,
        amount_changes=amount_changes,
        unchanged=not added and not removed,
        explanation_required=bool(added or removed or amount_changes),
    )


def _pointer_timestamp(report, pointer):
    """Return the source timestamp owning a values/evidence pointer."""

    tokens = pointer.lstrip("/").split("/")
    try:
        index = tokens.index("readings") + 1
        row = json_pointer(report, "/" + "/".join(tokens[: index + 1]))
    except (ValueError, IndexError, KeyError):
        return None
    return row.get("source_timestamp_ms") if isinstance(row, dict) else None


def _source_refs_for_opening(opening):
    refs = []
    if isinstance(opening, dict):
        if isinstance(opening.get("source_ref"), str):
            refs.append(opening["source_ref"])
        refs.extend(ref for ref in opening.get("supporting_source_refs", []) if isinstance(ref, str))
    return list(dict.fromkeys(refs))


def audit_openings(report):
    """Validate source pointers, partial openings and corroboration semantics."""

    findings = []
    openings = []
    partial_count = 0
    full_count = 0
    unobserved_count = 0
    corroborated_fields = 0
    duplicate_view_count = 0
    turns = (report.get("turn_ledger") or {}).get("turns") or []
    for turn in turns:
        states = turn.get("states") or {}
        for channel, fields in CHANNEL_FIELDS.items():
            state = states.get(channel) or {}
            status = state.get("opening_status")
            opening = state.get("opening")
            if opening is None:
                unobserved_count += 1
                if status not in (None, "not_observed_before_action"):
                    findings.append(
                        dict(turn_id=turn.get("id"), channel=channel, kind="opening_status_without_opening", status=status)
                    )
                continue
            if not isinstance(opening, dict):
                findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="opening_not_object"))
                continue
            values = opening.get("values")
            if not isinstance(values, dict):
                findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="opening_values_not_object"))
                values = {}
            missing = [field for field in fields if type(values.get(field)) is not int]
            unknown = [field for field in values if field not in fields]
            if unknown:
                findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="opening_unknown_fields", fields=unknown))
            values_ref = opening.get("values_ref")
            if not isinstance(values_ref, str):
                findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="opening_missing_values_ref"))
            else:
                try:
                    resolved = json_pointer(report, values_ref)
                    if resolved != values:
                        findings.append(
                            dict(turn_id=turn.get("id"), channel=channel, kind="opening_values_ref_mismatch", values_ref=values_ref)
                        )
                except ValueError as exc:
                    findings.append(
                        dict(turn_id=turn.get("id"), channel=channel, kind="opening_values_ref_unresolved", values_ref=values_ref, error=str(exc))
                    )
            if status == "partially_observed" or missing:
                partial_count += 1
                if status not in (None, "partially_observed"):
                    findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="partial_status_mismatch", status=status, missing_fields=missing))
            else:
                full_count += 1
                if status not in (None, "observed"):
                    findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="complete_status_mismatch", status=status))
            observed = [field for field in fields if field not in missing]
            sources = _source_refs_for_opening(opening)
            for source_ref in sources:
                try:
                    json_pointer(report, source_ref)
                except ValueError as exc:
                    findings.append(
                        dict(turn_id=turn.get("id"), channel=channel, kind="supporting_source_ref_unresolved", source_ref=source_ref, error=str(exc))
                    )
            evidence = opening.get("evidence")
            if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
                findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="opening_evidence_missing"))

            field_proofs = opening.get("field_corroboration") or {}
            if not isinstance(field_proofs, dict):
                findings.append(dict(turn_id=turn.get("id"), channel=channel, kind="field_corroboration_not_object"))
                field_proofs = {}
            for field in field_proofs:
                if field not in observed:
                    findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_for_missing_field"))
                    continue
                proofs = field_proofs[field]
                if not isinstance(proofs, list) or not proofs:
                    findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="empty_field_corroboration"))
                    continue
                timestamps = set()
                for proof in proofs:
                    if not isinstance(proof, dict):
                        findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_not_object"))
                        continue
                    value_ref = proof.get("value_ref")
                    proof_value = None
                    if not isinstance(value_ref, str):
                        findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_missing_value_ref"))
                    else:
                        try:
                            proof_value = json_pointer(report, value_ref)
                            if proof_value != values.get(field):
                                findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_value_mismatch", value_ref=value_ref, expected=values.get(field), actual=proof_value))
                        except ValueError as exc:
                            findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_value_ref_unresolved", value_ref=value_ref, error=str(exc)))
                    observed_at = proof.get("observed_at_ms")
                    if not _is_int(observed_at):
                        findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_timestamp_invalid"))
                    else:
                        timestamps.add(observed_at)
                        source_time = _pointer_timestamp(report, value_ref) if isinstance(value_ref, str) else None
                        if source_time is not None and source_time != observed_at:
                            findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_timestamp_mismatch", value_ref=value_ref, expected=source_time, actual=observed_at))
                    proof_evidence = proof.get("evidence")
                    if not isinstance(proof_evidence, list) or not all(isinstance(item, str) and item for item in proof_evidence):
                        findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="corroboration_evidence_missing"))
                corroborated_fields += 1
                if "repeated_field_corroboration" in str(opening.get("basis", "")) and len(timestamps) < 2:
                    findings.append(dict(turn_id=turn.get("id"), channel=channel, field=field, kind="insufficient_distinct_corroboration_timestamps", count=len(timestamps)))
            supporting_times = []
            for source_ref in sources:
                try:
                    source = json_pointer(report, source_ref)
                    if isinstance(source, dict) and _is_int(source.get("source_timestamp_ms")):
                        supporting_times.append(source["source_timestamp_ms"])
                except ValueError:
                    pass
            duplicate_view_count += max(0, len(supporting_times) - len(set(supporting_times)))
            openings.append(
                dict(
                    turn_id=turn.get("id"),
                    channel=channel,
                    opening_status=status or ("partially_observed" if missing else "observed"),
                    observed_fields=observed,
                    missing_fields=missing,
                    values_ref=values_ref,
                    source_refs=sources,
                    evidence=evidence if isinstance(evidence, list) else [],
                    basis=opening.get("basis"),
                )
            )

    accounting = report.get("causal_accounting") or {}
    contributions = {item.get("id"): item for item in accounting.get("contributions", []) if isinstance(item, dict)}
    ambiguous_claims = 0
    propagation_violations = []
    for comparison in accounting.get("turn_transitions", []):
        for field in comparison.get("fields", []):
            for claim in field.get("ambiguous_contributions", []) or []:
                ambiguous_claims += 1
                reference = claim.get("contribution_ref")
                contribution = contributions.get(reference)
                if not contribution or contribution.get("conflicts_present") is not True:
                    propagation_violations.append(
                        dict(turn_id=comparison.get("turn_id"), channel=comparison.get("channel"), field=field.get("field"), contribution_ref=reference, kind="ambiguous_claim_without_conflict_flag")
                    )

    basis_counts = Counter()
    derived_without_basis = []
    independently_verified = []
    for contribution in contributions.values():
        basis = contribution.get("basis")
        if not basis:
            derived_without_basis.append(contribution.get("id"))
        basis_counts[basis or "missing"] += 1
        if contribution.get("independent_effect_verification") is True:
            independently_verified.append(contribution.get("id"))
    conflict_result = dict(
        ambiguous_claims=ambiguous_claims,
        propagation_violations=propagation_violations,
        verified=not propagation_violations,
    )
    observed_vs_derived = dict(
        basis_counts=dict(basis_counts),
        direct_observed_contributions=sum(basis in DIRECT_BASES for basis in basis_counts for _ in range(basis_counts[basis])),
        derived_or_summary_contributions=sum(count for basis, count in basis_counts.items() if basis not in DIRECT_BASES),
        missing_basis=derived_without_basis,
        independent_effect_verification_flags=independently_verified,
        verified=not derived_without_basis and not independently_verified,
    )
    return dict(
        status="failed" if findings or propagation_violations else "verified",
        checks=dict(values_refs=not any(item["kind"].startswith("opening_") for item in findings),
                    corroboration=not any("corroboration" in item["kind"] for item in findings),
                    conflict_propagation=conflict_result["verified"],
                    observed_vs_derived=observed_vs_derived["verified"]),
        counts=dict(full_openings=full_count, partial_openings=partial_count, unobserved_openings=unobserved_count,
                    corroborated_fields=corroborated_fields, duplicate_view_count=duplicate_view_count),
        openings=openings,
        conflict_propagation=conflict_result,
        observed_vs_derived=observed_vs_derived,
        findings=findings,
    )


def _proof_path(cache_root, evidence):
    if not isinstance(evidence, str) or not evidence:
        return None
    relative = evidence.replace("\\", "/")
    prefix = f"{Path(cache_root).name.replace('\\', '/')}/"
    if relative.startswith(prefix):
        relative = relative[len(prefix):]
    path = _path(relative, cache_root)
    try:
        path.relative_to(cache_root.resolve())
    except ValueError:
        return None
    return path


def _source_frame_pixels(path):
    if Image is None:
        raise RuntimeError("Pillow is required to inspect recovery proof images")
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        if rgb.size == (810, 1080):
            return rgb.tobytes()
        return rgb.crop((148, 0, 958, 1080)).tobytes()


def _audit_recovery_cache(cache_root, expected_source_sha, kind):
    """Verify cached proof frames and sidecars for one bounded recovery pass."""

    cache_root = Path(cache_root)
    manifest_path = cache_root / "receipt-inspection.json"
    if not manifest_path.exists():
        return dict(kind=kind, status="not_present", verified_frames=0, verified_readings=0, findings=[], files_sha256={})
    findings = []
    files = {}
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError) as exc:
        return dict(kind=kind, status="failed", verified_frames=0, verified_readings=0,
                    findings=[dict(kind="manifest_unreadable", error=str(exc))], files_sha256={})
    if manifest.get("source_sha256") != expected_source_sha:
        findings.append(dict(kind="manifest_source_mismatch", expected=expected_source_sha, actual=manifest.get("source_sha256")))
    capture_path = cache_root / "capture.json"
    capture = None
    if capture_path.exists():
        try:
            capture = read_json(capture_path)
            source = capture.get("source") if isinstance(capture, dict) else None
            if isinstance(source, dict) and source.get("sha256") not in (None, expected_source_sha):
                findings.append(dict(kind="capture_source_mismatch", expected=expected_source_sha, actual=source.get("sha256")))
        except (OSError, ValueError) as exc:
            findings.append(dict(kind="capture_unreadable", error=str(exc)))
    else:
        findings.append(dict(kind="capture_missing"))
    windows = manifest.get("windows") or []
    for window in windows:
        if not isinstance(window, dict):
            findings.append(dict(kind="window_not_object"))
            continue
        start, end, fps = window.get("start_ms"), window.get("end_ms"), window.get("fps")
        if not (_is_int(start) and _is_int(end) and start < end):
            findings.append(dict(kind="window_bounds_invalid", window=window))
        if not _is_int(fps) or fps <= 0:
            findings.append(dict(kind="window_fps_invalid", window=window))
    verified = 0
    for row in manifest.get("readings", []) or []:
        if not isinstance(row, dict):
            findings.append(dict(kind="reading_not_object"))
            continue
        evidence = row.get("evidence")
        proof = _proof_path(cache_root, evidence)
        if proof is None or not proof.is_file():
            findings.append(dict(kind="proof_missing", evidence=evidence, source_timestamp_ms=row.get("source_timestamp_ms")))
            continue
        sidecar_path = proof.with_suffix(".v2.json")
        frames_path = proof.parent / "frames.json"
        if not sidecar_path.is_file() or not frames_path.is_file():
            findings.append(dict(kind="proof_metadata_missing", evidence=evidence))
            continue
        try:
            raw = read_json(sidecar_path)
            frames = read_json(frames_path)
            frame = next(item for item in frames if item.get("id") == proof.stem)
        except (OSError, ValueError, StopIteration) as exc:
            findings.append(dict(kind="proof_metadata_unreadable", evidence=evidence, error=str(exc)))
            continue
        timestamp = row.get("source_timestamp_ms")
        source_timestamp = frame.get("source_timestamp_ms")
        raw_timestamp = raw.get("source_timestamp_ms")
        if timestamp != source_timestamp or raw_timestamp != source_timestamp:
            findings.append(dict(kind="source_timestamp_mismatch", evidence=evidence, row=timestamp, frame=source_timestamp, sidecar=raw_timestamp))
        if raw.get("source_sha256") != expected_source_sha:
            findings.append(dict(kind="proof_source_mismatch", evidence=evidence, actual=raw.get("source_sha256")))
        try:
            origin_seconds = ((capture or {}).get("source") or {}).get("timeline_origin_seconds", 0)
            pts_ms = round((float(frame["source_pts"] * Fraction(frame["time_base"])) - float(origin_seconds)) * 1000)
            if _is_int(source_timestamp) and abs(pts_ms - source_timestamp) > 1:
                findings.append(dict(kind="source_pts_timestamp_mismatch", evidence=evidence, expected=source_timestamp, actual=pts_ms))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            findings.append(dict(kind="source_pts_unreadable", evidence=evidence))
        original = _proof_path(proof.parent, frame.get("evidence"))
        if original is None or not original.is_file():
            findings.append(dict(kind="original_frame_missing", evidence=evidence, original=frame.get("evidence")))
            continue
        try:
            source_frame_sha = sha256(original)
            if source_frame_sha != raw.get("source_frame_sha256"):
                findings.append(dict(kind="source_frame_hash_mismatch", evidence=evidence, expected=raw.get("source_frame_sha256"), actual=source_frame_sha))
            pixels = _source_frame_pixels(original)
            with Image.open(proof) as image:
                proof_pixels = image.convert("RGB").tobytes()
                if image.size != (810, 1080) or proof_pixels != pixels:
                    findings.append(dict(kind="proof_pixels_mismatch", evidence=evidence))
            gameplay_hash = hashlib.sha256(pixels).hexdigest()
            if gameplay_hash != raw.get("gameplay_sha256"):
                findings.append(dict(kind="gameplay_hash_mismatch", evidence=evidence, expected=raw.get("gameplay_sha256"), actual=gameplay_hash))
        except (OSError, RuntimeError) as exc:
            findings.append(dict(kind="proof_pixels_unreadable", evidence=evidence, error=str(exc)))
        if not raw.get("engine_fingerprint") or not raw.get("model_sha256"):
            findings.append(dict(kind="ocr_provenance_missing", evidence=evidence))
        if raw.get("evidence") and str(raw.get("evidence")).replace("\\", "/") != str(evidence).replace("\\", "/"):
            findings.append(dict(kind="proof_evidence_mismatch", evidence=evidence, sidecar=raw.get("evidence")))
        for path in (proof, sidecar_path, original, frames_path):
            try:
                files[str(path.relative_to(REPO))] = sha256(path)
            except ValueError:
                files[str(path)] = sha256(path)
        verified += 1
    files[str(manifest_path.relative_to(REPO))] = sha256(manifest_path)
    if capture_path.exists():
        files[str(capture_path.relative_to(REPO))] = sha256(capture_path)
    return dict(
        kind=kind,
        status="failed" if findings else "verified",
        verified_frames=verified,
        verified_readings=len(manifest.get("readings", []) or []),
        window_count=len(windows),
        findings=findings,
        files_sha256=files,
        manifest=manifest,
    )


def _promoted_rows(report, kind):
    rows = []
    for index, row in enumerate(_report_readings(report)):
        facts = row.get("facts") if isinstance(row, dict) else None
        if isinstance(facts, dict) and facts.get(kind):
            rows.append((index, row, facts[kind]))
    return rows


def _metadata_checks(metadata, manifest, before, promoted_rows, kind):
    findings = []
    if not isinstance(metadata, dict):
        return [dict(kind="recovery_metadata_not_object")]
    expected_source = before.get("source", {}).get("sha256")
    if metadata.get("source_sha256") not in (None, expected_source):
        findings.append(dict(kind="metadata_source_mismatch", expected=expected_source, actual=metadata.get("source_sha256")))
    pending = metadata.get("pending_windows")
    if pending:
        findings.append(dict(kind="recovery_windows_pending", count=len(pending)))
    if _is_int(metadata.get("promoted_source_timestamps")):
        source_times = {row.get("source_timestamp_ms") for _, row, _ in promoted_rows}
        if metadata["promoted_source_timestamps"] != len(source_times):
            findings.append(dict(kind="promoted_timestamp_count_mismatch", metadata=metadata["promoted_source_timestamps"], report=len(source_times)))
    if _is_int(metadata.get("source_samples")) and manifest is not None:
        sample_count = len(manifest.get("readings", []) or [])
        if metadata["source_samples"] != sample_count:
            findings.append(dict(kind="source_sample_count_mismatch", metadata=metadata["source_samples"], manifest=sample_count))
    processed = metadata.get("processed_windows")
    requested = metadata.get("requested_windows")
    if isinstance(processed, list) and isinstance(requested, list):
        processed_keys = {(_item.get("start_ms"), _item.get("end_ms")) for _item in processed if isinstance(_item, dict)}
        requested_keys = {(_item.get("start_ms"), _item.get("end_ms")) for _item in requested if isinstance(_item, dict)}
        if not processed_keys.issubset(requested_keys):
            findings.append(dict(kind="processed_window_not_requested"))
    return findings


def _audit_promoted_rows(report, before, recovery, kind):
    """Check that promoted rows are new, source-backed, and field-scoped."""

    findings = []
    before_times = {row.get("source_timestamp_ms") for row in _report_readings(before)}
    promoted = _promoted_rows(report, kind)
    manifest = recovery.get("manifest") if isinstance(recovery, dict) else None
    manifest_rows = {row.get("source_timestamp_ms"): row for row in (manifest or {}).get("readings", []) if isinstance(row, dict)}
    windows = (manifest or {}).get("windows", [])
    for index, row, metadata in promoted:
        time = row.get("source_timestamp_ms")
        if time in before_times:
            findings.append(dict(kind="promoted_row_reuses_old_timestamp", row_index=index, source_timestamp_ms=time))
        if not isinstance(metadata, (dict, list)):
            findings.append(dict(kind="promoted_provenance_not_object", row_index=index))
            continue
        entries = metadata if isinstance(metadata, list) else [metadata]
        evidence_values = []
        for entry in entries:
            if not isinstance(entry, dict):
                findings.append(dict(kind="promoted_provenance_entry_not_object", row_index=index))
                continue
            evidence = entry.get("evidence") or entry.get("source_evidence")
            if isinstance(evidence, list):
                evidence_values.extend(evidence)
            elif isinstance(evidence, str):
                evidence_values.append(evidence)
            requested = entry.get("requested_fields") or entry.get("fields") or []
            observed = entry.get("observed_fields")
            if observed is not None and not set(observed).issubset(set(requested)):
                findings.append(dict(kind="promoted_field_outside_request", row_index=index, observed=observed, requested=requested))
            if entry.get("source_timestamp_ms") not in (None, time):
                findings.append(dict(kind="promoted_metadata_timestamp_mismatch", row_index=index, source_timestamp_ms=time, metadata=entry.get("source_timestamp_ms")))
        if time not in manifest_rows:
            # Training caches sometimes have the same physical timestamp in a
            # repeated manifest row; a missing timestamp is still a provenance
            # failure because the report cannot be tied to a proof frame.
            findings.append(dict(kind="promoted_timestamp_absent_from_manifest", row_index=index, source_timestamp_ms=time))
        manifest_evidence = manifest_rows.get(time, {}).get("evidence") if time in manifest_rows else None
        normalise_evidence = lambda value: str(value).replace("\\", "/").split("/", 1)[1] if "/" in str(value) and str(value).replace("\\", "/").split("/", 1)[0] in ("training-gain-recovery", "boundary-state-recovery") else str(value).replace("\\", "/")
        if manifest_evidence and evidence_values and not any(normalise_evidence(item) == normalise_evidence(manifest_evidence) for item in evidence_values):
            findings.append(dict(kind="promoted_evidence_not_manifest_row", row_index=index, source_timestamp_ms=time, evidence=evidence_values, manifest_evidence=manifest_evidence))
        if kind == "training_gain_recovery":
            facts = row.get("facts") or {}
            allowed = {"training_gains", "observed_training_gain_fields", kind}
            unexpected = sorted(set(facts) - allowed)
            if unexpected:
                findings.append(dict(kind="training_recovery_imported_unrequested_fields", row_index=index, fields=unexpected))
            gains = facts.get("training_gains") or {}
            requested = set()
            for entry in entries:
                requested.update(entry.get("requested_fields", []))
            if not set(gains).issubset(requested):
                findings.append(dict(kind="training_gain_outside_requested_fields", row_index=index, gains=sorted(gains), requested=sorted(requested)))
            if any(key in facts for key in ("performance_points", "stats", "effects", "completed_action")):
                findings.append(dict(kind="training_recovery_imported_state_or_action", row_index=index))
        elif kind == "boundary_state_recovery":
            if row.get("effects") or row.get("completed_action") or row.get("training_option"):
                findings.append(dict(kind="boundary_recovery_imported_action_or_effect", row_index=index))
    return dict(promoted_rows=len(promoted), promoted_timestamps=sorted({row.get("source_timestamp_ms") for _, row, _ in promoted}), findings=findings)


def audit_recovery(run, before, after, repo=REPO, *, before_path=None):
    """Audit training and boundary recovery caches plus promoted report rows."""

    repo = Path(repo)
    results = []
    for kind, directory_name in (("training_gain_recovery", "training-gain-recovery"), ("boundary_state_recovery", "boundary-state-recovery")):
        cache_root = repo / ".local" / "full-recording" / run / directory_name
        result = _audit_recovery_cache(cache_root, before.get("source", {}).get("sha256"), kind)
        metadata = after.get(kind)
        combined_path = repo / ".local" / "turn-explanations-v1" / "combined-v1" / f"{run}-observations.json"
        native_path = repo / ".local" / "turn-explanations-v1" / "native-v1" / f"{run}-observations.json"
        if metadata is None and combined_path.exists():
            try:
                metadata = (read_json(combined_path).get("metadata") or {}).get(kind)
            except (OSError, ValueError):
                metadata = None
        if metadata is None and kind == "training_gain_recovery" and native_path.exists():
            try:
                metadata = read_json(native_path).get("metadata")
            except (OSError, ValueError):
                metadata = None
        promoted = _audit_promoted_rows(after, before, result, kind)
        result["promoted"] = promoted
        if metadata is not None:
            result["metadata_findings"] = _metadata_checks(metadata, result.get("manifest"), before, _promoted_rows(after, kind), kind)
            result["metadata"] = metadata
        else:
            result["metadata_findings"] = []
        result["findings"] = result.get("findings", []) + promoted["findings"] + result["metadata_findings"]
        if result["status"] == "not_present" and promoted["promoted_rows"]:
            result["status"] = "failed"
            result["findings"].append(dict(kind="promoted_rows_without_recovery_cache"))
        elif result["status"] != "failed" and result["findings"]:
            result["status"] = "failed"
        results.append(result)
    combined = repo / ".local" / "turn-explanations-v1" / "combined-v1" / f"{run}-observations.json"
    native = repo / ".local" / "turn-explanations-v1" / "native-v1" / f"{run}-observations.json"
    supplemental_path = combined if combined.exists() else native
    supplemental = audit_supplemental_observations(
        supplemental_path,
        before,
        after,
        repo=repo,
        before_path=before_path,
        recovery_roots={
            "training_gain_recovery": repo / ".local" / "full-recording" / run / "training-gain-recovery",
            "boundary_state_recovery": repo / ".local" / "full-recording" / run / "boundary-state-recovery",
        },
    ) if supplemental_path.exists() else dict(status="not_present", findings=[])
    return dict(
        status="failed" if any(result["status"] == "failed" for result in results) or supplemental["status"] == "failed" else "verified",
        training_gain_recovery=results[0],
        boundary_state_recovery=results[1],
        supplemental_observations=supplemental,
    )


def audit_supplemental_observations(path, before, after=None, *, repo=REPO, before_path=None, recovery_roots=None):
    """Check a frozen supplemental observation file without accepting values.

    The supplemental file is an input to replay.  Its original readings must
    remain unchanged, while every extra row must carry a bounded recovery
    provenance object and a timestamp not present in the starting report.
    """

    path = Path(path)
    if not path.exists():
        return dict(status="not_present", findings=[])
    findings = []
    try:
        supplemental = read_json(path)
    except (OSError, ValueError) as exc:
        return dict(status="failed", findings=[dict(kind="supplemental_unreadable", error=str(exc))])
    source_sha = before.get("source", {}).get("sha256")
    if supplemental.get("source_sha256") != source_sha:
        findings.append(dict(kind="supplemental_source_mismatch", expected=source_sha, actual=supplemental.get("source_sha256")))
    if before_path is not None and supplemental.get("input_report_sha256") != sha256(before_path):
        findings.append(dict(kind="supplemental_input_report_mismatch", expected=sha256(before_path), actual=supplemental.get("input_report_sha256")))
    old_rows = _report_readings(before)
    new_rows = supplemental.get("readings") or []
    before_evidence = {row.get("evidence") for row in old_rows if isinstance(row, dict) and row.get("evidence")}
    old_times = {row.get("source_timestamp_ms") for row in old_rows if isinstance(row, dict)}
    extra = []
    for index, row in enumerate(new_rows):
        if not isinstance(row, dict):
            findings.append(dict(kind="supplemental_row_not_object", index=index))
            continue
        evidence = row.get("evidence")
        if evidence in before_evidence:
            continue
        extra.append(row)
    old_report = {"gameplay_tracking": {"readings": old_rows}}
    new_report = {"gameplay_tracking": {"readings": new_rows}}
    row_check = compare_old_readings(old_report, new_report)
    old_row_enrichment = _allowed_enrichment(row_check["changed"])
    findings.extend(dict(kind="supplemental_old_row_mutation", **change) for change in old_row_enrichment["unauthorized"])
    findings.extend(dict(kind="supplemental_old_row_missing", **missing) for missing in row_check["missing"])
    promoted_times = {row.get("source_timestamp_ms") for row in extra}
    if promoted_times & old_times:
        findings.append(dict(kind="supplemental_new_row_reuses_old_timestamp", timestamps=sorted(promoted_times & old_times)))
    metadata = supplemental.get("metadata") or {}
    metadata_by_kind = metadata if any(key in metadata for key in RECOVERY_FACTS) else {"training_gain_recovery": metadata}
    counts_by_kind = Counter()
    for row in extra:
        facts = row.get("facts") or {}
        for kind in RECOVERY_FACTS:
            if facts.get(kind):
                counts_by_kind[kind] += 1
    for kind, kind_metadata in metadata_by_kind.items():
        if not isinstance(kind_metadata, dict):
            findings.append(dict(kind="supplemental_metadata_not_object", recovery_kind=kind))
            continue
        if _is_int(kind_metadata.get("promoted_source_timestamps")) and kind_metadata["promoted_source_timestamps"] != counts_by_kind.get(kind, 0):
            findings.append(dict(kind="supplemental_promoted_count_mismatch", recovery_kind=kind, metadata=kind_metadata["promoted_source_timestamps"], actual=counts_by_kind.get(kind, 0)))
    for index, row in enumerate(extra):
        facts = row.get("facts") or {}
        provenance = facts.get("training_gain_recovery") or facts.get("boundary_state_recovery")
        if not provenance:
            findings.append(dict(kind="supplemental_extra_without_recovery_provenance", index=index, source_timestamp_ms=row.get("source_timestamp_ms")))
        if not isinstance(row.get("source_timestamp_ms"), int) or not isinstance(row.get("evidence"), str):
            findings.append(dict(kind="supplemental_extra_missing_identity", index=index))
        recovery_kind = "boundary_state_recovery" if facts.get("boundary_state_recovery") else "training_gain_recovery"
        root = (recovery_roots or {}).get(recovery_kind)
        evidence = None
        if isinstance(provenance, dict):
            evidence = provenance.get("evidence") or provenance.get("source_evidence")
        elif isinstance(provenance, list):
            evidence = next((item.get("source_evidence") or item.get("evidence") for item in provenance if isinstance(item, dict)), None)
        if isinstance(evidence, list):
            evidence = evidence[0] if evidence else None
        if root is not None and evidence:
            proof = _proof_path(root, evidence)
            if proof is None or not proof.is_file():
                findings.append(dict(kind="supplemental_proof_missing", index=index, recovery_kind=recovery_kind, evidence=evidence))
    return dict(
        status="failed" if findings else "verified",
        path=str(path),
        source_sha256=source_sha,
        original_readings=len(old_rows),
        supplemental_readings=len(new_rows),
        promoted_rows=len(extra),
        promoted_timestamps=sorted(promoted_times),
        metadata=metadata,
        old_rows_unchanged=not old_row_enrichment["unauthorized"] and not row_check["missing"],
        allowed_old_row_enrichment=len(old_row_enrichment["allowed"]),
        findings=findings,
    )


def _normalised_hash_map(values):
    return {str(key).replace("\\", "/"): value for key, value in (values or {}).items()}


def audit_historical_references(repo=REPO, prior_audit=None, baseline_manifest=None):
    """Verify frozen labels/references and the baseline artifact hashes."""

    repo = Path(repo)
    prior_path = Path(prior_audit or repo / ".local" / "evaluation-hardening-v1" / "final" / "integrity-audit.json")
    manifest_path = Path(baseline_manifest or repo / ".local" / "turn-explanations-v1" / "baseline-manifest.json")
    findings = []
    prior = read_json(prior_path) if prior_path.exists() else {}
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    prior_hashes = _normalised_hash_map(prior.get("artifacts_sha256"))
    artifact_checks = []
    for name, expected in _normalised_hash_map(manifest.get("artifacts")).items():
        path = _path(name, repo)
        actual = sha256(path) if path.is_file() else None
        row = dict(path=name, expected=expected, actual=actual, unchanged=actual == expected)
        artifact_checks.append(row)
        if not row["unchanged"]:
            findings.append(dict(kind="baseline_artifact_changed_or_missing", **row))

    references = []
    amendments = []
    corpus_checks = []
    corpus_root = repo / ".local" / "evaluation-hardening-v1"
    for run in RUNS:
        corpus_path = corpus_root / f"{run}-corpus.json"
        if not corpus_path.exists():
            findings.append(dict(kind="corpus_missing", run=run, path=str(corpus_path)))
            continue
        corpus_sha = sha256(corpus_path)
        corpus_checks.append(dict(run=run, path=str(corpus_path.relative_to(repo)), sha256=corpus_sha,
                                  prior_sha256=prior_hashes.get(str(corpus_path.relative_to(repo)).replace("\\", "/")),
                                  unchanged=prior_hashes.get(str(corpus_path.relative_to(repo)).replace("\\", "/")) == corpus_sha))
        for config in (read_json(corpus_path).get("references") or []):
            reference = config.get("reference")
            if reference:
                references.append(reference)
            amendment = config.get("amendments")
            if amendment:
                amendments.append(amendment)
    source_files = []
    for kind, values in (("reference", references), ("amendment", amendments)):
        for value in dict.fromkeys(values):
            name = str(value).replace("\\", "/")
            path = _path(name, repo)
            actual = sha256(path) if path.is_file() else None
            expected = prior_hashes.get(name)
            row = dict(kind=kind, path=name, expected=expected, actual=actual,
                       unchanged=expected is not None and actual == expected)
            source_files.append(row)
            if not row["unchanged"]:
                findings.append(dict(kind="historical_source_file_changed_or_missing", **row))

    # Keep the active implementation changes visible to reviewers without
    # treating them as frozen source-label regressions.  The manifest hashes
    # are a baseline fingerprint, not a prohibition on implementing the goal.
    worktree_checks = []
    for name, expected in _normalised_hash_map(manifest.get("worktree")).items():
        path = _path(name, repo)
        actual = sha256(path) if path.is_file() else None
        worktree_checks.append(dict(path=name, expected=expected, actual=actual, unchanged=actual == expected))
    return dict(
        status="failed" if findings else "verified",
        baseline_artifacts=dict(expected=len(artifact_checks), unchanged=sum(item["unchanged"] for item in artifact_checks), checks=artifact_checks),
        corpus_checks=corpus_checks,
        source_references=dict(expected=20, actual=len(dict.fromkeys(references)), unchanged=sum(item["unchanged"] for item in source_files if item["kind"] == "reference")),
        historical_source_artifacts=dict(expected=22, actual=len(dict.fromkeys(references + amendments)), unchanged=sum(item["unchanged"] for item in source_files)),
        source_files=source_files,
        worktree=dict(expected=len(worktree_checks), unchanged=sum(item["unchanged"] for item in worktree_checks), changed=[item for item in worktree_checks if not item["unchanged"]]),
        findings=findings,
    )


def _allowed_enrichment(changes):
    """Separate additive panel provenance from an actual old-row mutation."""

    allowed = []
    unauthorized = []
    for change in changes:
        if change.get("change_kind") == "additive_panel_provenance":
            allowed.append(change)
            continue
        old = change.get("before")
        new = change.get("after")
        if not isinstance(old, dict) or not isinstance(new, dict):
            unauthorized.append(change)
            continue
        valid = True
        old_facts = old.get("facts") if isinstance(old.get("facts"), dict) else {}
        new_facts = new.get("facts") if isinstance(new.get("facts"), dict) else {}
        # Enrichment is allowed only when the old row did not contain the
        # corresponding object and all other old fields remain identical.
        for key, value in old.items():
            if key == "facts":
                continue
            if new.get(key) != value:
                valid = False
        for key, value in old_facts.items():
            if new_facts.get(key) != value:
                valid = False
        added_fact_keys = set(new_facts) - set(old_facts)
        if not added_fact_keys or not added_fact_keys.issubset({"performance_points", "performance_panel_recovery", "boundary_state_recovery"}):
            valid = False
        target = allowed if valid else unauthorized
        target.append(change)
    return dict(allowed=allowed, unauthorized=unauthorized)


def _load_explanations(path, run):
    if path is None or not Path(path).exists():
        return {}
    data = read_json(path)
    if isinstance(data, dict) and isinstance(data.get(run), dict):
        return data[run]
    return data if isinstance(data, dict) else {}


def _explanation_for(explanations, category, item):
    if not isinstance(explanations, dict):
        return None
    values = explanations.get(category)
    if isinstance(values, str):
        return values
    if isinstance(values, dict):
        key = item.get("event_id") or item.get("key") or item.get("evidence")
        return values.get(str(key)) or values.get("default")
    if isinstance(values, list):
        return values[0] if values else None
    return None


def _candidate_contract(report):
    try:
        sys.path.insert(0, str(REPO))
        from tracen_replay.report_contract import validate
        validate(report, require_gameplay=True)
        return dict(valid=True, error=None)
    except Exception as exc:  # report integrity is a data result, not a traceback
        return dict(valid=False, error=f"{type(exc).__name__}: {exc}")


def _baseline_envelope(report):
    """Validate frozen envelope fields without requiring current ledger code."""

    required = ("source", "gameplay_tracking", "turn_ledger", "causal_accounting")
    missing = [key for key in required if key not in report]
    source = report.get("source") if isinstance(report, dict) else None
    if not isinstance(source, dict) or not isinstance(source.get("sha256"), str):
        missing.append("source.sha256")
    return dict(valid=not missing, missing=missing)


def _reproduce_accounting(report):
    try:
        sys.path.insert(0, str(REPO))
        from tracen_replay.causal_accounting import build
        reproduced = build(report)
        return dict(reproduced=reproduced == report.get("causal_accounting"), error=None,
                    summary=reproduced.get("summary") if isinstance(reproduced, dict) else None)
    except Exception as exc:
        return dict(reproduced=False, error=f"{type(exc).__name__}: {exc}", summary=None)


def _shared_comparison(before, after, corpus_path):
    try:
        sys.path.insert(0, str(REPO / "analyzer" / "lab"))
        from compare_hardening_reports import compare
        configs = read_json(corpus_path).get("references") or []
        return dict(status="verified", result=compare(before, after, configs), error=None)
    except Exception as exc:
        return dict(status="failed", result=None, error=f"{type(exc).__name__}: {exc}")


def _regressions(run_result, explanations):
    regressions = []
    contract = run_result["contract"]
    if not contract["valid"]:
        regressions.append(dict(category="report_contract", reason=contract["error"]))
    accounting = run_result["accounting"]
    if not accounting["reproduced"]:
        regressions.append(dict(category="causal_accounting", reason=accounting["error"] or "embedded accounting differs from build"))
    opening = run_result["openings"]
    for finding in opening.get("findings", []):
        regressions.append(dict(category="opening_provenance", **finding))
    recovery = run_result["recovery"]
    for name in ("training_gain_recovery", "boundary_state_recovery"):
        for finding in recovery[name].get("findings", []):
            regressions.append(dict(category=f"{name}.provenance", **finding))
    supplemental = recovery.get("supplemental_observations", {})
    for finding in supplemental.get("findings", []):
        regressions.append(dict(category="supplemental_observations", **finding))
    old_rows = run_result["old_rows"]
    for change in old_rows.get("unauthorized", []):
        regressions.append(dict(category="old_reading_mutation", **change))
    action = run_result["actions"]
    for item in action["changed"] + action["added"] + action["removed"]:
        regressions.append(dict(category="accepted_action_identity", **item))
    numeric = run_result["numeric_events"]
    for item in numeric["changed"] + numeric["added"] + numeric["removed"]:
        regressions.append(dict(category="numeric_event_amount", **item))
    effects = run_result["canonical_effects"]
    for item in effects["removed"] + effects["amount_changes"]:
        regressions.append(dict(category="canonical_effect", **item))
    shared_info = run_result.get("shared_comparison", {})
    if shared_info.get("status") == "failed":
        regressions.append(
            dict(
                category="shared_comparison",
                reason=shared_info.get("error") or "comparison failed",
            )
        )
    shared = shared_info.get("result")
    if isinstance(shared, dict):
        for key in ("selected_actions_unchanged", "numeric_event_changes_unchanged", "stat_interval_totals_unchanged"):
            if shared.get(key) is False:
                regressions.append(dict(category="shared_comparison", reason=f"{key}=false"))
        for collection, unchanged in (shared.get("unchanged_collections") or {}).items():
            if unchanged is False:
                if collection == "performance_accounting" and run_result.get("recovery", {}).get("status") == "verified":
                    run_result.setdefault("expected_recovery_changes", []).append(
                        dict(category="shared_comparison", reason="collection performance_accounting changed", expected=True)
                    )
                    continue
                regressions.append(dict(category="shared_comparison", reason=f"collection {collection} changed"))
        for grade in shared.get("shared_reference_grades", []):
            for change in grade.get("changes", []):
                if change.get("before", {}).get("status") == "correct" and change.get("after", {}).get("status") != "correct":
                    regressions.append(dict(category="source_reference_grade", **change))
    for item in regressions:
        category = item.get("category", "unknown")
        item["explanation"] = _explanation_for(explanations, category, item)
        item["requires_explanation"] = True
        item["explained"] = bool(item["explanation"])
    return regressions


def audit_run(run, before_path, after_path, repo=REPO, explanations_path=None):
    """Audit one frozen/candidate report pair."""

    repo = Path(repo)
    before = read_json(before_path)
    after = read_json(after_path)
    source_before = before.get("source", {}).get("sha256")
    source_after = after.get("source", {}).get("sha256")
    source_match = source_before == source_after
    baseline_envelope = _baseline_envelope(before)
    contract = _candidate_contract(after)
    accounting = _reproduce_accounting(after)
    openings = audit_openings(after)
    raw_old_rows = compare_old_readings(before, after)
    old_rows = dict(raw_old_rows, **_allowed_enrichment(raw_old_rows["changed"]))
    old_rows["old_rows_unchanged"] = not old_rows["missing"] and not old_rows["unauthorized"]
    actions = diff_accepted_actions(before, after)
    numeric_events = diff_numeric_events(before, after)
    canonical_effects = diff_canonical_effects(before, after)
    recovery = audit_recovery(run, before, after, repo, before_path=before_path)
    corpus_path = repo / ".local" / "evaluation-hardening-v1" / f"{run}-corpus.json"
    shared = _shared_comparison(before, after, corpus_path) if corpus_path.exists() else dict(status="failed", result=None, error="corpus missing")
    result = dict(
        run=run,
        before_path=str(Path(before_path)),
        after_path=str(Path(after_path)),
        before_sha256=sha256(before_path),
        after_sha256=sha256(after_path),
        source_sha256=source_after,
        source_match=source_match,
        baseline_envelope=baseline_envelope,
        contract=contract,
        accounting=accounting,
        openings=openings,
        old_rows=old_rows,
        actions=actions,
        numeric_events=numeric_events,
        canonical_effects=canonical_effects,
        recovery=recovery,
        shared_comparison=shared,
    )
    result["expected_recovery_changes"] = []
    result["regressions"] = _regressions(result, _load_explanations(explanations_path, run))
    hard_fail = (
        not source_match
        or not contract["valid"]
        or not accounting["reproduced"]
        or recovery["status"] == "failed"
        or shared.get("status") == "failed"
        or bool(old_rows["missing"])
        or bool(old_rows["unauthorized"])
        or openings["status"] == "failed"
    )
    result["status"] = "failed" if hard_fail else "needs_explanation" if result["regressions"] else "verified"
    if result["status"] == "verified" and any(
        value.get("explanation_required") for value in (actions, numeric_events, canonical_effects)
    ):
        result["status"] = "needs_explanation"
    return result


def audit_all(after_dir, *, repo=REPO, baseline_dir=None, prior_audit=None, baseline_manifest=None, explanations=None):
    """Audit all three runs in an output directory."""

    repo = Path(repo)
    baseline_dir = Path(baseline_dir or repo / ".local" / "turn-explanations-v1" / "before")
    after_dir = Path(after_dir)
    results = []
    for run in RUNS:
        before_path = baseline_dir / f"{run}-report.json"
        after_path = after_dir / f"{run}-report.json"
        if not before_path.exists() or not after_path.exists():
            results.append(dict(run=run, status="failed", regressions=[dict(category="report_missing", before=str(before_path), after=str(after_path))]))
            continue
        try:
            results.append(audit_run(run, before_path, after_path, repo, explanations))
        except Exception as exc:
            results.append(dict(run=run, status="failed", regressions=[dict(category="auditor_error", reason=f"{type(exc).__name__}: {exc}")]))
    historical = audit_historical_references(repo, prior_audit, baseline_manifest)
    statuses = [result.get("status") for result in results]
    status = "failed" if historical["status"] == "failed" or "failed" in statuses else "needs_explanation" if "needs_explanation" in statuses else "verified"
    return dict(
        schema_version="turn-explanations-audit-v1",
        status=status,
        baseline=dict(path=str(baseline_dir), runs=list(RUNS)),
        after_dir=str(after_dir),
        results=results,
        historical_references=historical,
        limitations=[
            "This audit reads candidate reports and bounded proof caches; it does not replay a full recording.",
            "A changed action outcome is reported separately from action kind and option identity.",
            "A source reference grade regression requires explicit explanation before the candidate is accepted.",
            "Independent complete-history verification remains unmeasured and is represented as null by inventory.",
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("after_dir", nargs="?", type=Path)
    parser.add_argument("--after-dir", dest="after_dir_option", type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--prior-audit", type=Path)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--explanations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    after_dir = args.after_dir_option or args.after_dir
    if after_dir is None:
        parser.error("an after report directory is required")
    result = audit_all(
        after_dir,
        repo=args.repo,
        baseline_dir=args.baseline_dir,
        prior_audit=args.prior_audit,
        baseline_manifest=args.baseline_manifest,
        explanations=args.explanations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"status": result["status"], "runs": len(result["results"])}))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
