"""Quarantine source-backed same-slot receipt identity conflicts.

The receipt parser can observe the same hint sentence several times while an
animation or overlay changes the OCR spelling.  A circle proof establishes
the marker at the end of the line, but it does not establish the spelling of
the skill name before that marker.  This module therefore provides a narrow,
conservative identity guard: when distinct names have the same semantic
payload and are source-bound to one stable, titled outcome slot, all of them
are retained as unresolved candidates instead of being counted as awards.

This helper does not normalize names, consult a catalog, choose a winner, or
infer that the candidates are one award.  The caller should run it after all
symbol and spelling fallbacks have finished, so a later fallback cannot put a
quarantined spelling back into ``effects``.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping

from .source_clock import elapsed


_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_DEFAULT_SCREEN = "event_outcome"
_MAX_SOURCE_GAP_MS = 750
_MIN_LINE_CONFIDENCE = 95.0
_GAMEPLAY_PANE_X = 148
_IMAGE_HASHES = ("gameplay_sha256", "source_frame_sha256", "evidence_sha256")
_SEMANTIC_FIELDS = ("amount", "field", "direction", "value", "rank", "level", "tier")


def _valid_timestamp(value: Any) -> bool:
    return type(value) is int and value >= 0


def _valid_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _valid_confidence(value: Any) -> bool:
    return _valid_number(value) and _MIN_LINE_CONFIDENCE <= value <= 100


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _valid_box(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(_valid_number(item) for item in value)
            and 0 <= value[0] < value[2] <= 10000
            and 0 <= value[1] < value[3] <= 10000)


def _line_box_in_source_space(line: Mapping[str, Any]) -> list[Any] | None:
    box = line.get("box")
    if not _valid_box(box):
        return None
    space = line.get("coordinate_space", "source_frame")
    if space in ("source_frame", "full_frame"):
        return list(box)
    if space == "gameplay_crop":
        return [box[0] + _GAMEPLAY_PANE_X, box[1],
                box[2] + _GAMEPLAY_PANE_X, box[3]]
    return None


def _proof_box_in_line_space(proof: Mapping[str, Any], line: Mapping[str, Any]) -> list[Any] | None:
    box = proof.get("box")
    line_box = line.get("box")
    if not _valid_box(box) or not _valid_box(line_box):
        return None
    proof_space = proof.get("coordinate_space")
    line_space = line.get("coordinate_space", "source_frame")
    if proof_space == line_space:
        return list(box)
    if proof_space == "gameplay_crop" and line_space in ("source_frame", "full_frame"):
        return [box[0] + _GAMEPLAY_PANE_X, box[1], box[2] + _GAMEPLAY_PANE_X, box[3]]
    return None


def _contains(outer: Any, inner: Any) -> bool:
    return (_valid_box(outer) and _valid_box(inner)
            and outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def _same_slot(left: Any, right: Any) -> bool:
    if not _valid_box(left) or not _valid_box(right):
        return False
    intersection_width = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    union = ((left[2] - left[0]) * (left[3] - left[1])
             + (right[2] - right[0]) * (right[3] - right[1])
             - intersection)
    return (union > 0 and intersection / union >= 0.8
            and abs((left[1] + left[3] - right[1] - right[3]) / 2) <= 3)


def _effect_key(effect: Mapping[str, Any]) -> str:
    return f"{effect.get('kind', '')}||{effect.get('name', '')}"


def _semantic_signature(effect: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(effect.get(field) for field in _SEMANTIC_FIELDS)


def _valid_circle_proof(proof: Any) -> bool:
    if not isinstance(proof, Mapping):
        return False
    if (proof.get("kind") != "single_circle"
            or proof.get("symbol") != "○"
            or proof.get("method") != "strict_terminal_ring_geometry"
            or proof.get("coordinate_space") != "gameplay_crop"
            or not _valid_box(proof.get("box"))):
        return False
    if any(not _valid_hash(proof.get(field)) for field in _IMAGE_HASHES):
        return False
    if "source_sha256" in proof and not _valid_hash(proof.get("source_sha256")):
        return False
    return True


def _same_semantics(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _semantic_signature(left) == _semantic_signature(right)


def _event_context(event: Mapping[str, Any]) -> str | None:
    title = event.get("context_title")
    if not isinstance(title, str) or not title.strip():
        return None
    return title


def _event_bounds_valid(event: Mapping[str, Any]) -> bool:
    first = event.get("first_seen_ms")
    last = event.get("last_seen_ms")
    if "first_seen_ms" in event and not _valid_timestamp(first):
        return False
    if "last_seen_ms" in event and not _valid_timestamp(last):
        return False
    return not (first is not None and last is not None and last < first)


def _in_event(event: Mapping[str, Any], timestamp: int) -> bool:
    if not _event_bounds_valid(event):
        return False
    first = event.get("first_seen_ms")
    last = event.get("last_seen_ms")
    return ((first is None or timestamp >= first)
            and (last is None or timestamp <= last))


def _row_semantics_match(row_effect: Mapping[str, Any], effect: Mapping[str, Any],
                         effect_kind: str) -> bool:
    if row_effect.get("kind") != effect_kind or row_effect.get("name") != effect.get("name"):
        return False
    return _same_semantics(row_effect, effect)


def _proof_matches_row(proof: Mapping[str, Any], row: Mapping[str, Any], path: str) -> bool:
    if proof.get("evidence") != path or proof.get("source_timestamp_ms") != row.get("source_timestamp_ms"):
        return False
    if row.get("evidence") != path:
        return False
    for field in _IMAGE_HASHES + ("source_sha256",):
        if field in row and row.get(field) != proof.get(field):
            return False
    return True


def _row_observation(effect: Mapping[str, Any], effect_kind: str, path: str,
                     row: Mapping[str, Any], event: Mapping[str, Any],
                     context_title: str) -> dict[str, Any] | None:
    if (not isinstance(path, str) or not path
            or not isinstance(row, Mapping)
            or row.get("evidence") != path
            or row.get("screen") != _DEFAULT_SCREEN
            or row.get("context_title") != context_title):
        return None
    timestamp = row.get("source_timestamp_ms")
    if not _valid_timestamp(timestamp) or not _in_event(event, timestamp):
        return None

    row_effects = row.get("effects")
    if not isinstance(row_effects, list):
        return None
    matches = [candidate for candidate in row_effects
               if isinstance(candidate, Mapping)
               and _row_semantics_match(candidate, effect, effect_kind)]
    if len(matches) != 1:
        return None
    proof = matches[0].get("visual_symbol_observation")
    if not _valid_circle_proof(proof) or not _proof_matches_row(proof, row, path):
        return None

    ocr = row.get("ocr")
    if not isinstance(ocr, Mapping):
        return None
    neural = ocr.get("neural", [])
    if not isinstance(neural, list):
        return None
    expected_text = {value for value in (effect.get("raw_text"), effect.get("original_text"))
                     if isinstance(value, str) and value}
    lines = [line for line in neural
             if isinstance(line, Mapping)
             and line.get("text") in expected_text
             and _valid_confidence(line.get("confidence"))
             and not line.get("overlay_occluded")
             and line.get("visual_symbol_observation") == proof
             and _contains(line.get("box"), _proof_box_in_line_space(proof, line))]
    if len(lines) != 1:
        return None
    source_box = _line_box_in_source_space(lines[0])
    if source_box is None:
        return None
    return {
        "timestamp_ms": timestamp,
        "evidence": path,
        "line": deepcopy(dict(lines[0])),
        "line_box": source_box,
        "visual_symbol_observation": deepcopy(dict(proof)),
    }


def _observations(effect: Mapping[str, Any], effect_kind: str,
                  event: Mapping[str, Any], rows_by_evidence: Mapping[str, Any],
                  context_title: str) -> list[dict[str, Any]]:
    key = _effect_key(effect)
    field_evidence = event.get("field_evidence")
    paths = field_evidence.get(key) if isinstance(field_evidence, Mapping) else None
    if not isinstance(paths, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for path in paths:
        row = rows_by_evidence.get(path)
        observation = _row_observation(effect, effect_kind, path, row, event, context_title)
        if observation is None:
            continue
        identity = (observation["timestamp_ms"], observation["evidence"])
        if identity in seen:
            continue
        seen.add(identity)
        result.append(observation)
    result.sort(key=lambda item: (item["timestamp_ms"], item["evidence"]))
    return result


def _valid_hash_continuity(observations: list[dict[str, Any]]) -> bool:
    if not observations:
        return False
    recording_hashes = {
        item["visual_symbol_observation"].get("source_sha256")
        for item in observations
        if item["visual_symbol_observation"].get("source_sha256") is not None
    }
    if len(recording_hashes) > 1:
        return False
    source_frames = [item["visual_symbol_observation"].get("source_frame_sha256")
                     for item in observations]
    return len(source_frames) == len(set(source_frames))


def _same_known_rows_between(minimum: int, maximum: int,
                             rows_by_evidence: Mapping[str, Any],
                             context_title: str) -> bool:
    """Reject known navigation or title changes inside the proposed span."""

    for row in rows_by_evidence.values():
        if not isinstance(row, Mapping):
            continue
        timestamp = row.get("source_timestamp_ms")
        if not _valid_timestamp(timestamp) or not minimum < timestamp < maximum:
            continue
        if (row.get("screen") != _DEFAULT_SCREEN
                or row.get("context_title") != context_title):
            return False
    return True


def _compatible(left: dict[str, Any], right: dict[str, Any],
                left_effect: Mapping[str, Any], right_effect: Mapping[str, Any]) -> bool:
    if left_effect.get("name") == right_effect.get("name"):
        return False
    if not _same_semantics(left_effect, right_effect):
        return False
    gap = abs(elapsed(left["timestamp_ms"], right["timestamp_ms"]))
    return (0 < gap <= _MAX_SOURCE_GAP_MS
            and _same_slot(left["line_box"], right["line_box"]))


def _group_valid(group: list[int], effects: list[Mapping[str, Any]],
                 observations: dict[int, list[dict[str, Any]]],
                 rows_by_evidence: Mapping[str, Any], context_title: str) -> bool:
    all_observations = [item for index in group for item in observations[index]]
    if len({effects[index].get("name") for index in group}) != len(group):
        return False
    if not all(_valid_hash_continuity(observations[index]) for index in group):
        return False
    recording_hashes = {
        item["visual_symbol_observation"].get("source_sha256")
        for item in all_observations
        if item["visual_symbol_observation"].get("source_sha256") is not None
    }
    if len(recording_hashes) > 1:
        return False
    source_frames = [item["visual_symbol_observation"].get("source_frame_sha256")
                     for item in all_observations]
    if len(source_frames) != len(set(source_frames)):
        return False
    if len({item["timestamp_ms"] for item in all_observations}) < 2:
        return False
    by_timestamp: dict[int, set[int]] = {}
    for index in group:
        for item in observations[index]:
            by_timestamp.setdefault(item["timestamp_ms"], set()).add(index)
    if any(len(indices) > 1 for indices in by_timestamp.values()):
        return False
    ordered = sorted(all_observations, key=lambda item: (item["timestamp_ms"], item["evidence"]))
    if any(elapsed(left["timestamp_ms"], right["timestamp_ms"]) > _MAX_SOURCE_GAP_MS
           for left, right in zip(ordered, ordered[1:])):
        return False
    return _same_known_rows_between(ordered[0]["timestamp_ms"], ordered[-1]["timestamp_ms"],
                                    rows_by_evidence, context_title)


def _pairs(group: list[int], observations: dict[int, list[dict[str, Any]]]) -> list[list[str]]:
    best: dict[tuple[int, int], tuple[int, int, str, str]] = {}
    for position, left_index in enumerate(group):
        for right_index in group[position + 1:]:
            for left in observations[left_index]:
                for right in observations[right_index]:
                    gap = abs(elapsed(left["timestamp_ms"], right["timestamp_ms"]))
                    if not (0 < gap <= _MAX_SOURCE_GAP_MS
                            and _same_slot(left["line_box"], right["line_box"])):
                        continue
                    paths = tuple(sorted((left["evidence"], right["evidence"])))
                    candidate = (gap, min(left["timestamp_ms"], right["timestamp_ms"]),
                                 paths[0], paths[1])
                    pair_key = (left_index, right_index)
                    if pair_key not in best or candidate < best[pair_key]:
                        best[pair_key] = candidate
    return [[item[2], item[3]] for item in sorted(best.values())]


def _already_candidate(event: Mapping[str, Any], effect: Mapping[str, Any]) -> bool:
    candidates = event.get("ambiguous_effect_candidates", [])
    if not isinstance(candidates, list):
        return False
    key = _effect_key(effect)
    return any(isinstance(candidate, Mapping)
               and candidate.get("reason") == "unresolved_skill_hint_identity"
               and isinstance(candidate.get("effect"), Mapping)
               and _effect_key(candidate["effect"]) == key
               for candidate in candidates)


def _already_conflict(event: Mapping[str, Any], effect: Mapping[str, Any],
                      names: list[str]) -> bool:
    conflicts = event.get("conflicting_readings", [])
    key = _effect_key(effect)
    return any(isinstance(conflict, Mapping)
               and conflict.get("field") == key
               and conflict.get("reason") == "skill_hint_identity_conflict"
               and conflict.get("name_candidates") == names
               for conflict in conflicts)


def quarantine_receipt_identity_conflicts(
    event: Mapping[str, Any], rows_by_evidence: Mapping[str, Any], *,
    effect_kind: str = "skill_hint_change",
) -> Mapping[str, Any]:
    """Move source-continuous same-slot identity conflicts to uncertainty.

    ``effect_kind`` is explicit so the caller cannot accidentally apply this
    guard to a different receipt channel.  It is intentionally the only
    semantic selector: names, titles, timestamps, and expected labels are
    never whitelisted.  The function mutates and returns ``event`` like the
    other receipt reconciliation helpers.
    """

    if (not isinstance(event, dict) or not isinstance(rows_by_evidence, Mapping)
            or not isinstance(effect_kind, str) or not effect_kind.strip()
            or not _event_bounds_valid(event)):
        return event
    context_title = _event_context(event)
    if context_title is None:
        return event
    effects = event.get("effects")
    if not isinstance(effects, list):
        return event
    field_evidence = event.get("field_evidence")
    if not isinstance(field_evidence, Mapping):
        return event
    conflicts = event.get("conflicting_readings")
    if conflicts is None:
        conflicts = []
        event["conflicting_readings"] = conflicts
    if not isinstance(conflicts, list):
        return event

    def effect_has_conflict(effect: Mapping[str, Any]) -> bool:
        key = _effect_key(effect)
        return any(isinstance(conflict, Mapping) and conflict.get("field") == key
                   for conflict in conflicts)

    selected = [
        (index, effect) for index, effect in enumerate(effects)
        if isinstance(effect, Mapping)
        and effect.get("kind") == effect_kind
        and isinstance(effect.get("name"), str)
        and effect.get("name")
        and type(effect.get("amount")) is int
        and effect.get("amount") >= 0
        and not effect_has_conflict(effect)
        and not any(effect.get(field) is not None and not isinstance(effect.get(field), (str, int, float, bool))
                    for field in ("field", "direction", "value", "rank", "level", "tier"))
    ]
    if len(selected) < 2:
        return event

    observations = {
        index: _observations(effect, effect_kind, event, rows_by_evidence, context_title)
        for index, effect in selected
    }
    observations = {index: items for index, items in observations.items() if items}
    if len(observations) < 2:
        return event

    adjacency: dict[int, set[int]] = {index: set() for index in observations}
    index_to_effect = {index: effect for index, effect in selected}
    indices = sorted(observations)
    for position, left_index in enumerate(indices):
        for right_index in indices[position + 1:]:
            if any(_compatible(left, right, index_to_effect[left_index], index_to_effect[right_index])
                   for left in observations[left_index] for right in observations[right_index]):
                adjacency[left_index].add(right_index)
                adjacency[right_index].add(left_index)

    groups: list[list[int]] = []
    unseen = set(indices)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: list[int] = []
        unseen.remove(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in sorted(adjacency[current], reverse=True):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)
        component.sort()
        if len(component) >= 2 and _group_valid(component, effects, observations,
                                                rows_by_evidence, context_title):
            groups.append(component)

    remove_indices: set[int] = set()
    for group in groups:
        names = [effects[index].get("name") for index in group]
        evidence_pairs = _pairs(group, observations)
        if not evidence_pairs:
            continue
        for index in group:
            effect = effects[index]
            key = _effect_key(effect)
            if not _already_conflict(event, effect, names):
                conflicts.append({
                    "field": key,
                    "reason": "skill_hint_identity_conflict",
                    "name_candidates": list(names),
                    "evidence_pairs": deepcopy(evidence_pairs),
                    "screen": _DEFAULT_SCREEN,
                    "context_title": context_title,
                    "identity_status": "unresolved",
                })
            if not _already_candidate(event, effect):
                source_observations = [
                    {
                        "timestamp_ms": item["timestamp_ms"],
                        "evidence": item["evidence"],
                        "line": deepcopy(item["line"]),
                        "visual_symbol_observation": deepcopy(item["visual_symbol_observation"]),
                    }
                    for item in observations[index]
                ]
                candidate = {
                    "effect": deepcopy(dict(effect)),
                    "kind": effect_kind,
                    "field": key,
                    "reason": "unresolved_skill_hint_identity",
                    "evidence": list(dict.fromkeys(
                        path for path in field_evidence.get(key, []) if isinstance(path, str)
                    )),
                    "name_candidates": list(names),
                    "evidence_pairs": deepcopy(evidence_pairs),
                    "source_continuity": {
                        "screen": _DEFAULT_SCREEN,
                        "context_title": context_title,
                        "observations": source_observations,
                    },
                    "identity_status": "possible_duplicate_or_additional_effect",
                    "continuity_proven": False,
                    "accepted_award": False,
                    "occurrence_count": None,
                }
                event.setdefault("ambiguous_effect_candidates", []).append(candidate)
            remove_indices.add(index)

    if remove_indices:
        event["effects"] = [effect for index, effect in enumerate(effects)
                             if index not in remove_indices]
    return event


__all__ = ["quarantine_receipt_identity_conflicts"]
