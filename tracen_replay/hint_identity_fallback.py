"""Keep a source-verified hint circle from being downgraded by a fallback.

The normal receipt-name passes can prove that an unmarked OCR line is the
same sentence as a pixel-corrected circle line when their source observations
are contiguous.  A later fallback handles the remaining ``Name``/``Name ○``
pair so it does not count one visible receipt twice.  That fallback must not
discard a circle proof when the name observations are separated by an
unproven OCR gap.

This module handles only that narrow case.  It does not repair names, compare
spelling variants, or infer that two readings are the same award.  When a
repeated, source-bound ``single_circle`` proof exists, the proven circle
effect remains accepted and the unmarked effect is moved to an explicit
possible-duplicate/additional-effect candidate.  Its occurrence count stays
unknown until a continuity rule proves the relationship.

The three image hashes are required.  Some older validated reading
collections do not repeat the recording-level ``source_sha256`` in each
proof, so that field is optional here and is preserved when absent.  The
caller remains responsible for supplying rows from the already validated
recording collection; this helper never invents a recording identity.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CIRCLE_NAME = re.compile(r"^(?P<base>.+?)\s+○$")
_STRICT_METHOD = "strict_terminal_ring_geometry"
_GAMEPLAY_PANE_X = 148
_IMAGE_PROOF_HASHES = ("gameplay_sha256", "source_frame_sha256", "evidence_sha256")


def _field_key(effect: Mapping[str, Any]) -> str:
    return "skill_hint_change||" + str(effect.get("name") or "")


def _valid_timestamp(value: Any) -> bool:
    return type(value) is int and value >= 0


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _valid_box(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(_finite_number(item) for item in value)
            and value[2] > value[0] and value[3] > value[1])


def _box_contains(outer: Any, inner: Any) -> bool:
    """Require a symbol proof to be inside its associated OCR line."""

    if not _valid_box(outer) or not _valid_box(inner):
        return False
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def _same_context(event: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    event_title = event.get("context_title")
    row_title = row.get("context_title")
    if isinstance(event_title, str) and event_title and row_title != event_title:
        return False
    return row.get("screen") == "event_outcome"


def _proof_shape(proof: Any) -> bool:
    if not isinstance(proof, Mapping):
        return False
    if proof.get("method") != _STRICT_METHOD:
        return False
    if proof.get("kind") != "single_circle" or proof.get("symbol") != "○":
        return False
    if proof.get("coordinate_space") != "gameplay_crop":
        return False
    if not _valid_box(proof.get("box")):
        return False
    if any(not _valid_hash(proof.get(key)) for key in _IMAGE_PROOF_HASHES):
        return False
    # Older validated source rows can lack the recording-level hash.  Do not
    # fabricate one here; if it is present, it must still be well formed.
    if "source_sha256" in proof and not _valid_hash(proof.get("source_sha256")):
        return False
    return True


def _proof_matches_row(proof: Any, row: Mapping[str, Any]) -> bool:
    if not _proof_shape(proof):
        return False
    if not _valid_timestamp(row.get("source_timestamp_ms")):
        return False
    if proof.get("source_timestamp_ms") != row.get("source_timestamp_ms"):
        return False
    if proof.get("evidence") != row.get("evidence"):
        return False
    if not isinstance(row.get("evidence"), str) or not row["evidence"]:
        return False
    return True


def _proof_box_in_line_space(proof: Mapping[str, Any], line: Mapping[str, Any]) -> list[Any] | None:
    """Translate the symbol box into the coordinate space used by OCR lines."""

    proof_box = proof.get("box")
    line_box = line.get("box")
    if not _valid_box(proof_box) or not _valid_box(line_box):
        return None
    proof_space = proof.get("coordinate_space")
    line_space = line.get("coordinate_space", "source_frame")
    if proof_space == line_space:
        return list(proof_box)
    if proof_space == "gameplay_crop" and line_space in ("source_frame", "full_frame"):
        return [proof_box[0] + _GAMEPLAY_PANE_X, proof_box[1],
                proof_box[2] + _GAMEPLAY_PANE_X, proof_box[3]]
    return None


def _event_time_contains(event: Mapping[str, Any], timestamp: int) -> bool:
    first = event.get("first_seen_ms") if "first_seen_ms" in event else None
    last = event.get("last_seen_ms") if "last_seen_ms" in event else None
    if "first_seen_ms" in event and not _valid_timestamp(first):
        return False
    if "last_seen_ms" in event and not _valid_timestamp(last):
        return False
    if first is not None and last is not None and last < first:
        return False
    if first is not None and timestamp < first:
        return False
    if last is not None and timestamp > last:
        return False
    return True


def _valid_visual_rows(event: Mapping[str, Any], effect: Mapping[str, Any],
                       rows_by_evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return distinct source rows carrying a bound visual proof for effect."""

    key = _field_key(effect)
    evidence = event.get("field_evidence", {}).get(key, [])
    if not isinstance(evidence, list):
        return []
    expected_text = {value for value in (effect.get("raw_text"), effect.get("original_text"))
                     if isinstance(value, str) and value}
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for path in dict.fromkeys(item for item in evidence if isinstance(item, str)):
        row = rows_by_evidence.get(path)
        if (not isinstance(row, Mapping) or row.get("evidence") != path
                or not _same_context(event, row)):
            continue
        timestamp = row.get("source_timestamp_ms")
        if not _valid_timestamp(timestamp) or not _event_time_contains(event, timestamp):
            continue

        row_effects = row.get("effects", [])
        if not isinstance(row_effects, list):
            continue
        candidates = [candidate for candidate in row_effects
                      if isinstance(candidate, Mapping)
                      and candidate.get("kind") == "skill_hint_change"
                      and candidate.get("name") == effect.get("name")
                      and candidate.get("amount") == effect.get("amount")
                      and _proof_shape(candidate.get("visual_symbol_observation"))]
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        proof = candidate.get("visual_symbol_observation")
        if not _proof_matches_row(proof, row):
            continue

        neural = row.get("ocr", {}).get("neural", [])
        if not isinstance(neural, list):
            continue
        matching_lines = [line for line in neural
                          if isinstance(line, Mapping)
                          and line.get("text") in expected_text
                          and _finite_number(line.get("confidence"))
                          and 95 <= line.get("confidence") <= 100
                          and _valid_box(line.get("box"))
                          and _box_contains(line.get("box"),
                                            _proof_box_in_line_space(proof, line))
                          and line.get("visual_symbol_observation") == proof]
        if len(matching_lines) != 1:
            continue

        identity = (timestamp, path)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(dict(timestamp=timestamp, evidence=path, proof=deepcopy(proof),
                           line=deepcopy(matching_lines[0])))
    # Different present recording hashes cannot establish repeated evidence
    # for one source.  A missing recording hash remains absent and is owned by
    # the already-validated reading collection; this helper never invents it.
    source_hashes = {item["proof"].get("source_sha256")
                     for item in result
                     if item["proof"].get("source_sha256") is not None}
    if len(source_hashes) > 1:
        return []
    source_frame_hashes = [item["proof"].get("source_frame_sha256")
                           for item in result
                           if item["proof"].get("source_frame_sha256") is not None]
    if source_frame_hashes and (len(source_frame_hashes) != len(result)
                                or len(set(source_frame_hashes)) != len(source_frame_hashes)):
        return []
    return sorted(result, key=lambda item: (item["timestamp"], item["evidence"]))


def _effect_conflicted(event: Mapping[str, Any], effect: Mapping[str, Any]) -> bool:
    key = _field_key(effect)
    conflicts = event.get("conflicting_readings", [])
    if not isinstance(conflicts, list):
        return True
    return any(isinstance(item, Mapping) and item.get("field") == key for item in conflicts)


def _already_unresolved(event: Mapping[str, Any], weak_key: str) -> bool:
    candidates = event.get("ambiguous_effect_candidates", [])
    if not isinstance(candidates, list):
        return False
    return any(isinstance(item, Mapping)
               and isinstance(item.get("effect"), Mapping)
               and _field_key(item["effect"]) == weak_key
               and item.get("reason") == "unresolved_circle_base_variant"
               for item in candidates)


def _unresolved_candidate(weak: Mapping[str, Any], strong: Mapping[str, Any],
                          weak_evidence: list[str], strong_evidence: list[str],
                          proof_evidence: list[str]) -> dict[str, Any]:
    """Keep the weak payload while explicitly declining identity resolution."""

    return dict(
        effect=deepcopy(dict(weak)),
        reason="unresolved_circle_base_variant",
        field=_field_key(weak),
        evidence=list(weak_evidence),
        possible_duplicate_of=dict(
            field=_field_key(strong),
            name=strong.get("name"),
            amount=strong.get("amount"),
            evidence=list(strong_evidence),
            circle_proof_evidence=list(proof_evidence),
        ),
        identity_status="possible_duplicate_or_additional_effect",
        continuity_proven=False,
        occurrence_count=None,
        basis="repeated_strict_terminal_circle_proof_without_base_continuity",
    )


def preserve_valid_circle_effect(event: Mapping[str, Any],
                                 rows_by_evidence: Mapping[str, Any]) -> Mapping[str, Any]:
    """Protect a repeated pixel-verified ``Name ○`` effect from fallback loss.

    The function mutates the supplied event in the same style as the existing
    receipt reconciliation helpers and returns it for convenient composition.
    Only a strict ``single_circle`` proof repeated at two distinct source
    timestamps qualifies.  A matching unmarked base effect is removed from
    active effects and retained as an unresolved possible duplicate/additional
    effect.  No name spelling is changed and no continuity is inferred.
    """

    if not isinstance(event, dict) or not isinstance(rows_by_evidence, Mapping):
        return event
    effects = event.get("effects")
    field_evidence = event.get("field_evidence")
    if not isinstance(effects, list) or not isinstance(field_evidence, Mapping):
        return event

    active = [effect for effect in effects
              if isinstance(effect, dict)
              and effect.get("kind") == "skill_hint_change"
              and isinstance(effect.get("name"), str)]
    if not active:
        return event

    removed: set[int] = set()
    for strong in active:
        match = _CIRCLE_NAME.fullmatch(strong["name"])
        if (match is None or type(strong.get("amount")) is not int
                or strong["amount"] < 0):
            continue
        if _effect_conflicted(event, strong):
            continue
        strong_proofs = _valid_visual_rows(event, strong, rows_by_evidence)
        timestamps = {item["timestamp"] for item in strong_proofs}
        if len(timestamps) < 2:
            continue
        effect_proof = strong.get("visual_symbol_observation")
        if not any(item["proof"] == effect_proof for item in strong_proofs):
            continue

        base_name = match.group("base")
        strong_key = _field_key(strong)
        strong_evidence = list(dict.fromkeys(field_evidence.get(strong_key, [])
                                             if isinstance(field_evidence.get(strong_key), list) else []))

        # First require the exact unmarked base.  A terminal OCR ``O`` alone
        # is not enough: a legitimate skill may happen to end in that letter.
        # Once the base anchor is present, stage both weak spellings as
        # unresolved candidates so the later legacy suffix fallback cannot
        # leave an unverified ``Name O`` as a second active award.
        weak_candidates = [weak for weak in active
                           if weak is not strong and id(weak) not in removed
                           and weak.get("name") == base_name]
        weak_candidates = [*weak_candidates,
                           *[weak for weak in active
                             if weak is not strong and id(weak) not in removed
                             and weak.get("name") == f"{base_name} O"]]
        base_anchor = next((weak for weak in weak_candidates
                            if weak.get("name") == base_name
                            and weak.get("amount") == strong.get("amount")
                            and not any(weak.get(field) != strong.get(field)
                                        for field in ("field", "direction", "value"))), None)
        if base_anchor is None:
            continue
        for weak in weak_candidates:
            if weak.get("amount") != strong.get("amount"):
                continue
            if any(weak.get(field) != strong.get(field)
                   for field in ("field", "direction", "value")):
                continue

            weak_key = _field_key(weak)
            weak_evidence = list(dict.fromkeys(field_evidence.get(weak_key, [])
                                               if isinstance(field_evidence.get(weak_key), list) else []))
            if not _already_unresolved(event, weak_key):
                event.setdefault("ambiguous_effect_candidates", []).append(
                    _unresolved_candidate(
                        weak, strong, weak_evidence, strong_evidence,
                        [item["evidence"] for item in strong_proofs]))
            removed.add(id(weak))

    if removed:
        event["effects"] = [effect for effect in effects if id(effect) not in removed]
    return event


__all__ = ["preserve_valid_circle_effect"]
