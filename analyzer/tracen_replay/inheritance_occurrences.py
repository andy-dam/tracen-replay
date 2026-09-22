"""Summarize distinct source evidence for inheritance receipts.

The normal transaction stream contains one effect for a receipt, while a
receipt can visibly contain several simultaneous sparks and can be redisplayed
while the list scrolls.  This helper keeps those two facts separate: the
maximum number of disjoint lines in one source frame is evidence of concurrent
occurrences, while the number of later frames is intentionally unknown.
"""

from __future__ import annotations

import copy
import math


MIN_CONFIDENCE = 95.0
RECEIPT_LEFT = 250
RECEIPT_TOP = 770
RECEIPT_RIGHT = 850
RECEIPT_BOTTOM = 970
_KINDS = frozenset(("inheritance_spark", "inheritance_inspiration"))
_PAYLOAD_FIELDS = ("kind", "field", "name", "amount", "direction", "value")


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _valid_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    if not all(_finite(value) for value in box):
        return False
    left, top, right, bottom = box
    return (
        RECEIPT_LEFT <= left < right <= RECEIPT_RIGHT
        and RECEIPT_TOP <= top < bottom <= RECEIPT_BOTTOM
    )


def _overlap(first, second):
    """Return whether two same-text boxes are duplicate views of one line."""

    if not _valid_box(first) or not _valid_box(second):
        return False
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    # A partial overlap is still ambiguous evidence for one physical line.
    # Counting it as disjoint would inflate the lower bound when OCR boxes
    # come from slightly different crops or channels.
    return intersection > 0


def _key(effect):
    return "|".join(str(effect.get(part) or "") for part in ("kind", "field", "name"))


def _payload(effect):
    return {field: copy.deepcopy(effect.get(field)) for field in _PAYLOAD_FIELDS}


def _payload_signature(effect):
    return tuple(effect.get(field) for field in _PAYLOAD_FIELDS)


def _expected_raw_text(effect):
    kind = effect.get("kind")
    name = effect.get("name")
    if kind not in _KINDS or not isinstance(name, str) or not name:
        return None
    if kind == "inheritance_spark":
        return f"{name} spark activated!"
    return f"Inspired by {name}!"


def _event_bounds(event):
    """Return validated inclusive event bounds, or ``False`` if malformed."""

    first = event.get("first_seen_ms")
    last = event.get("last_seen_ms")
    if first is None and last is None:
        return None
    if first is not None and (type(first) is not int or first < 0):
        return False
    if last is not None and (type(last) is not int or last < 0):
        return False
    if first is not None and last is not None and last < first:
        return False
    return first, last


def _in_event_bounds(timestamp, bounds):
    if bounds is False:
        return False
    if bounds is None:
        return True
    first, last = bounds
    return ((first is None or timestamp >= first)
            and (last is None or timestamp <= last))


def _effect_variants(event):
    variants = {}
    effects = event.get("effects", ()) if isinstance(event, dict) else ()
    if not isinstance(effects, (list, tuple)):
        return variants
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("kind") not in _KINDS:
            continue
        if not isinstance(effect.get("name"), str) or not effect.get("name"):
            continue
        raw_text = effect.get("raw_text")
        expected = _expected_raw_text(effect)
        # The grammar prevents a stat/cap receipt from being reclassified as a
        # spark, and rejects an effect whose accepted text is already truncated.
        if not isinstance(raw_text, str) or expected is None or raw_text != expected:
            continue
        base_key = _key(effect)
        signature = (_payload_signature(effect), raw_text)
        variants.setdefault(base_key, {})[signature] = {
            "effect": copy.deepcopy(effect),
            "payload": _payload(effect),
            "raw_text": raw_text,
        }
    return variants


def _line_candidates(effect, event, rows_by_evidence):
    field_evidence = event.get("field_evidence", {})
    if not isinstance(field_evidence, dict):
        return []
    proofs = field_evidence.get(_key(effect), ())
    if not isinstance(proofs, (list, tuple)):
        return []
    bounds = _event_bounds(event)
    if bounds is False:
        return []

    candidates = []
    seen_proofs = set()
    for proof in proofs:
        if not isinstance(proof, str) or proof in seen_proofs:
            continue
        seen_proofs.add(proof)
        row = rows_by_evidence.get(proof)
        if not isinstance(row, dict) or row.get("evidence") != proof:
            continue
        if row.get("screen") != "event_outcome":
            continue
        timestamp = row.get("source_timestamp_ms")
        if type(timestamp) is not int or timestamp < 0:
            continue
        if not _in_event_bounds(timestamp, bounds):
            continue
        ocr = row.get("ocr")
        lines = ocr.get("neural") if isinstance(ocr, dict) else None
        if not isinstance(lines, (list, tuple)):
            continue
        for line_index, line in enumerate(lines):
            if not isinstance(line, dict):
                continue
            confidence = line.get("confidence")
            raw_text = line.get("text")
            if (
                raw_text != effect.get("raw_text")
                or not _finite(confidence)
                or float(confidence) < MIN_CONFIDENCE
                or float(confidence) > 100
                or line.get("overlay_occluded") is True
                or not _valid_box(line.get("box"))
            ):
                continue
            row_effects = row.get("effects")
            if not isinstance(row_effects, (list, tuple)) or not any(
                isinstance(observed, dict)
                and _payload_signature(observed) == _payload_signature(effect)
                and observed.get("raw_text") == raw_text
                for observed in row_effects
            ):
                continue
            candidates.append(
                {
                    "source_timestamp_ms": timestamp,
                    "evidence": proof,
                    "box": list(line["box"]),
                    "line_index": line_index,
                    "confidence": float(confidence),
                    "payload": _payload(effect),
                    "raw_text": raw_text,
                }
            )
    return candidates


def _deduplicate_snapshot(candidates):
    """Collapse duplicate OCR boxes within one source/evidence snapshot."""

    selected = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-item["confidence"], item["line_index"], item["evidence"]),
    ):
        if any(
            prior["raw_text"] == candidate["raw_text"]
            and _overlap(prior["box"], candidate["box"])
            for prior in selected
        ):
            continue
        selected.append(candidate)
    return sorted(
        selected,
        key=lambda item: (item["box"][1], item["box"][0], item["line_index"]),
    )


def _snapshot_count(observations):
    """Count only mutually non-overlapping lines in one source snapshot."""

    frame = sorted(
        observations,
        key=lambda item: (item["box"][3], item["box"][1], item["box"][0]),
    )
    chosen = []
    for item in frame:
        if all(not _overlap(item["box"], prior["box"]) for prior in chosen):
            chosen.append(item)
    return len(chosen)


def _select_snapshots(candidates):
    """Select one evidence crop per source timestamp without unioning crops.

    Distinct evidence paths at the same timestamp may be alternate crops of
    the same frame.  Their boxes are never combined into a larger occurrence
    count.  ``frame_counts`` retains the per-crop measurements for audit.
    """

    by_timestamp = {}
    for candidate in candidates:
        by_timestamp.setdefault(candidate["source_timestamp_ms"], {}).setdefault(
            candidate["evidence"], []
        ).append(candidate)

    selected = []
    frame_counts = []
    for timestamp in sorted(by_timestamp):
        snapshots = []
        for evidence, items in by_timestamp[timestamp].items():
            snapshot = _deduplicate_snapshot(items)
            count = _snapshot_count(snapshot)
            frame_counts.append(
                {
                    "source_timestamp_ms": timestamp,
                    "evidence": evidence,
                    "minimum_observed_count": count,
                }
            )
            snapshots.append((evidence, snapshot, count))
        # Prefer the source crop with the strongest independently visible
        # multiplicity, then confidence.  This chooses one snapshot only.
        _, snapshot, _ = min(
            snapshots,
            key=lambda item: (
                -item[2],
                -sum(observation["confidence"] for observation in item[1]),
                item[0],
            ),
        )
        selected.extend(snapshot)
    selected.sort(
        key=lambda item: (
            item["source_timestamp_ms"],
            item["box"][1],
            item["box"][0],
            item["line_index"],
            item["evidence"],
        )
    )
    frame_counts.sort(key=lambda item: (item["source_timestamp_ms"], item["evidence"]))
    return selected, frame_counts


def _max_disjoint_count(observations):
    maximum = 0
    for timestamp in sorted({item["source_timestamp_ms"] for item in observations}):
        frame = [item for item in observations if item["source_timestamp_ms"] == timestamp]
        maximum = max(maximum, _snapshot_count(frame))
    return maximum


def _summary_variant(effect, event, rows_by_evidence):
    observations, frame_counts = _select_snapshots(
        _line_candidates(effect, event, rows_by_evidence)
    )
    proof = []
    for observation in observations:
        item = {
            "source_timestamp_ms": observation["source_timestamp_ms"],
            "evidence": observation["evidence"],
            "box": observation["box"],
            "line_index": observation["line_index"],
            "payload": copy.deepcopy(observation["payload"]),
            "raw_text": observation["raw_text"],
            "confidence": observation["confidence"],
        }
        proof.append(item)
    return {
        "payload": _payload(effect),
        "raw_text": effect["raw_text"],
        "minimum_observed_count": _max_disjoint_count(observations),
        "total_count": None,
        "count_complete": False,
        "frame_counts": frame_counts,
        "observations": proof,
    }


_OCCUPANCY_ONLY_REASON = "multiple_same_field_lines"


def _same_field(detail, semantic_key):
    return isinstance(detail, dict) and detail.get("field") == semantic_key


def _event_conflict_reasons(event, semantic_key):
    """Return identity/value uncertainty retained on this event field."""

    reasons = []
    active = event.get("conflicting_readings", [])
    if isinstance(active, (list, tuple)):
        for detail in active:
            if not _same_field(detail, semantic_key):
                continue
            reason = detail.get("reason") or "active_conflict"
            if reason != _OCCUPANCY_ONLY_REASON and reason not in reasons:
                reasons.append(reason)

    candidates = event.get("ambiguous_effect_candidates", [])
    if isinstance(candidates, (list, tuple)):
        for candidate in candidates:
            effect = candidate.get("effect") if isinstance(candidate, dict) else None
            if isinstance(effect, dict) and _key(effect) == semantic_key:
                reason = candidate.get("reason") or "ambiguous_effect_candidate"
                if reason not in reasons:
                    reasons.append(reason)

    # A resolver may retain one winner while archiving the disagreement.  The
    # occurrence summary must keep that audit state visible instead of making
    # a single retained payload look unconditionally certain.
    for collection_name in (
        "resolved_reading_conflicts",
        "resolved_reading_conflict_details",
    ):
        collection = event.get(collection_name, [])
        if not isinstance(collection, (list, tuple)):
            continue
        for detail in collection:
            if _same_field(detail, semantic_key):
                reason = detail.get("basis") or collection_name
                marker = f"{collection_name}:{reason}"
                if marker not in reasons:
                    reasons.append(marker)
    return reasons


def summarize(event, rows_by_evidence):
    """Return source-bound occurrence counts without mutating either input.

    ``minimum_observed_count`` is the maximum number of disjoint accepted lines
    visible in one source frame for an exact payload variant.  Repeated frames
    add observations and provenance but do not increase that count.  A missing
    or ambiguous source proof remains represented by zero or separate uncertain
    variants; no numeric ledger value is used to fill it.
    """

    if not isinstance(event, dict) or not isinstance(rows_by_evidence, dict):
        return {"event_id": event.get("id") if isinstance(event, dict) else None, "by_key": {}}
    by_key = {}
    for base_key, variants in _effect_variants(event).items():
        summaries = [
            _summary_variant(variant["effect"], event, rows_by_evidence)
            for variant in variants.values()
        ]
        conflict_reasons = _event_conflict_reasons(event, base_key)
        item = {
            "semantic_key": base_key,
            "uncertain": len(summaries) > 1 or bool(conflict_reasons),
            "conflicting_payloads": len(summaries) > 1,
            "variants": summaries,
        }
        if conflict_reasons:
            item["uncertainty_reasons"] = conflict_reasons
        # Keep the common single-payload case convenient for consumers while
        # retaining every conflicting payload in ``variants``.
        if len(summaries) == 1:
            item.update(summaries[0])
        else:
            item["minimum_observed_count"] = max(
                (summary["minimum_observed_count"] for summary in summaries),
                default=0,
            )
            item["total_count"] = None
            item["count_complete"] = False
        by_key[base_key] = item
    return {"event_id": event.get("id"), "by_key": by_key}


__all__ = [
    "MIN_CONFIDENCE",
    "RECEIPT_BOTTOM",
    "RECEIPT_LEFT",
    "RECEIPT_RIGHT",
    "RECEIPT_TOP",
    "summarize",
]
