"""Project source pointers for numeric receipt lines kept out of effects.

The vision parser intentionally rejects numeric receipt prefixes without a
visible sentence terminator.  That protects typewriter frames from becoming
awards, but it also means a source frame can be absent from an event's
field-level evidence after a later complete frame has established the same
receipt.  This module records that source pointer only when a complete,
same-event receipt independently confirms the exact field and amount.

The candidate remains ``accepted_as_effect=False``.  No effect, amount, or
state value is created from a residual, an expected label, or a neighboring
event.  Primary OCR and the frozen source cache are never changed.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy

from .gameplay import receipt_rows
from .layout import place_x
from .source_clock import elapsed


_ALLOWED_SCREENS = frozenset(("unknown", "event_outcome"))
_MIN_CANDIDATE_CONFIDENCE = 85.0
_MIN_COMPLETE_CONFIDENCE = 90.0
_MAX_PRE_EVENT_MS = 500
# Cached gameplay crops are 810 px wide; the auxiliary profile is outside
# this crop and must never contribute a receipt candidate.  The bounds are
# PC pane positions; the receipt box is centred and they follow it.
_GAMEPLAY_X_RANGE = (0, 810)

_LABELS = {
    "speed": "speed",
    "stamina": "stamina",
    "power": "power",
    "guts": "guts",
    "wit": "wit",
    "skill pts": "skill_points",
    "skill points": "skill_points",
}
_LABEL_PATTERN = r"Speed|Stamina|Power|Guts|Wit|Skill (?:Pts|Points)"
_CANONICAL = re.compile(
    rf"^(?P<label>{_LABEL_PATTERN})\s+went\s+(?P<direction>up|down)\s+by\s+"
    r"(?P<amount>\d+)(?:\s+to\s+new\s+heights)?(?P<terminal>[.!])?$",
    re.IGNORECASE,
)
_OMITTED_CONNECTIVE = re.compile(
    rf"^(?P<label>{_LABEL_PATTERN})\s+went\s+(?P<amount>\d+)(?P<terminal>[.!])?$",
    re.IGNORECASE,
)
_OCR_VERB = re.compile(
    rf"^(?P<label>{_LABEL_PATTERN})\s+wem\s+(?P<direction>up|down)\s+by\s+"
    r"(?P<amount>\d+)(?:\s+to\s+new\s+heights)?(?P<terminal>[.!])?$",
    re.IGNORECASE,
)
_MISSING_BY = re.compile(
    rf"^(?P<label>{_LABEL_PATTERN})\s+went\s+(?P<direction>up|down)\s*"
    r"(?P<amount>\d+)(?P<terminal>[.!])?$",
    re.IGNORECASE,
)


def _line_confidence(line):
    values = []
    for key in ("confidence", "pre_occlusion_confidence"):
        value = line.get(key) if isinstance(line, dict) else None
        if type(value) in (int, float) and math.isfinite(value):
            values.append(float(value))
    return max(values, default=0.0)


def _valid_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in box):
        return False
    left, top, right, bottom = box
    band_top, band_bottom = receipt_rows()
    return (_GAMEPLAY_X_RANGE[0] <= left < right <= place_x(_GAMEPLAY_X_RANGE[1])
            and band_top <= top < bottom <= band_bottom)


def _valid_evidence_path(value):
    """Accept only one normalized, root-relative gameplay artifact path."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        return False
    if "?" in value or "#" in value or "\\" in value:
        return False
    parts = value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _evidence_identity(value):
    """Return the Windows source-path identity used for duplicate rejection."""
    if not _valid_evidence_path(value):
        return None
    # The source artifacts are addressed on Windows.  Case-only spellings
    # therefore cannot be treated as independent physical frames.
    return value.casefold()


def _normal_text(value):
    return " ".join(str(value or "").split())


def _parse_candidate_text(value):
    """Parse only a bounded numeric receipt shape; preserve the raw spelling."""
    text = _normal_text(value)
    for pattern, variant in (
        (_CANONICAL, "canonical_receipt"),
        (_OMITTED_CONNECTIVE, "omitted_connective"),
        (_OCR_VERB, "ocr_verb_variant"),
        (_MISSING_BY, "omitted_by"),
    ):
        match = pattern.fullmatch(text)
        if match is None:
            continue
        label = match.group("label").casefold()
        field = _LABELS.get(label)
        if field is None:
            continue
        amount = int(match.group("amount"))
        direction = match.groupdict().get("direction")
        signed_amount = amount if direction != "down" else -amount
        return {
            "field": field,
            "magnitude": amount,
            "signed_amount": signed_amount,
            "direction": direction,
            "terminal": match.groupdict().get("terminal"),
            "variant": variant,
            "text": text,
        }
    return None


def _candidate_from_line(line, source, index):
    if not isinstance(line, dict) or not _valid_box(line.get("box")):
        return None
    if source == "facts_occluded":
        if line.get("recipient_name_occluded") is not False:
            return None
    confidence = _line_confidence(line)
    if confidence < _MIN_CANDIDATE_CONFIDENCE:
        return None
    parsed = _parse_candidate_text(line.get("text"))
    if parsed is None:
        return None
    return dict(parsed, source=source, line_index=index,
                confidence=confidence, box=list(line["box"]), raw=deepcopy(line))


def _candidate_from_pending(effect, index):
    if not isinstance(effect, dict) or effect.get("kind") != "stat_change":
        return None
    field = effect.get("field")
    amount = effect.get("amount")
    if field not in _LABELS.values() or type(amount) is not int:
        return None
    parsed = _parse_candidate_text(effect.get("raw_text"))
    if parsed is None or parsed["field"] != field or abs(parsed["signed_amount"]) != abs(amount):
        return None
    confidence = _line_confidence(effect)
    if confidence < _MIN_CANDIDATE_CONFIDENCE:
        return None
    return dict(parsed, source="pending_effect", line_index=index,
                confidence=confidence, box=None, raw=deepcopy(effect))


def _candidates(row):
    """Collect source lines from the gameplay reading without changing it."""
    result = []
    if row.get("screen") not in _ALLOWED_SCREENS:
        return result
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    for index, line in enumerate(facts.get("occluded_receipt_lines", [])):
        candidate = _candidate_from_line(line, "facts_occluded", index)
        if candidate is not None:
            result.append(candidate)
    neural = row.get("ocr", {}).get("neural", [])
    if isinstance(neural, list):
        for index, line in enumerate(neural):
            candidate = _candidate_from_line(line, "neural", index)
            if candidate is not None:
                result.append(candidate)
    for index, effect in enumerate(facts.get("effect_candidates", [])):
        candidate = _candidate_from_pending(effect, index)
        if candidate is not None:
            result.append(candidate)
    # A complete line can still be present in the neural sidecar after the
    # regular parser accepted it.  Such a row already carries its own effect
    # and must not be re-emitted as a projection.  Keep lines whose exact
    # field/value is absent from the row effects: those are the source-backed
    # parser gaps this module is meant to point at.
    observed = [effect for effect in row.get("effects", [])
                if isinstance(effect, dict) and effect.get("kind") == "stat_change"]
    filtered = []
    for candidate in result:
        # A fully terminated canonical neural line is the parser's normal
        # input.  If it has no row effect, that is usually a separate
        # animation/refinement concern and is deliberately left alone here.
        # Facts-side occlusion and pending candidates are already explicit
        # parser-gap signals; noncanonical OCR variants remain eligible when
        # a complete same-event proof corroborates them.
        if (candidate.get("source") == "neural"
                and candidate.get("variant") == "canonical_receipt"
                and candidate.get("terminal") is not None):
            continue
        exact = [effect for effect in observed
                 if effect.get("field") == candidate["field"]
                 and type(effect.get("amount")) is int
                 and ((candidate["direction"] is None
                       and abs(effect["amount"]) == candidate["magnitude"])
                      or effect["amount"] == candidate["signed_amount"])]
        if not exact:
            filtered.append(candidate)
    return filtered


def _candidate_preference(candidate):
    return (
        candidate.get("source") == "facts_occluded",
        candidate.get("source") == "neural",
        candidate.get("confidence", 0),
    )


def _deduplicate_candidates(row, candidates):
    """Keep one best source line per field/value/physical slot."""
    groups = []
    for candidate in candidates:
        box = tuple(candidate["box"]) if candidate.get("box") is not None else None
        key = (candidate["field"], candidate["magnitude"], box)
        group = next((group for group in groups if group[0] == key), None)
        if group is None:
            groups.append((key, candidate))
        elif _candidate_preference(candidate) > _candidate_preference(group[1]):
            groups[groups.index(group)] = (key, candidate)
    return [candidate for _, candidate in groups]


def _effect_key(effect):
    return "|".join(str(effect.get(part) or "")
                     for part in ("kind", "field", "name"))


def _effect_signature(effect):
    return tuple(effect.get(part) for part in
                 ("kind", "field", "name", "amount", "direction", "value"))


def _is_pre_event_boundary(row, title, candidate_calendar=None,
                           candidate_turns=None):
    """Reject an observed modal, narrative, or turn boundary in a link gap."""
    if not isinstance(row, dict) or row.get("screen") not in _ALLOWED_SCREENS:
        return True
    if _row_has_boundary(row):
        return True
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    if facts.get("boundary_state_recovery") or facts.get("new_turn"):
        return True
    calendar = stats.get("calendar_text") or facts.get("calendar_text")
    if calendar and (candidate_calendar is None or calendar != candidate_calendar):
        return True
    turns = stats.get("turns_remaining_to_goal")
    if (type(turns) is int
            and (type(candidate_turns) is not int or turns != candidate_turns)):
        return True
    observed_titles = [row.get("context_title"), row.get("context_title_candidate")]
    if any(value and value != title for value in observed_titles):
        return True
    # A title candidate without an accepted title is not sufficient evidence
    # to bridge a modal or narrative gap, even when its spelling happens to
    # equal the destination event title.
    if row.get("context_title") is None and row.get("context_title_candidate"):
        return True
    top, bottom = receipt_rows(790, 950)
    for line in (row.get("ocr") or {}).get("neural", []):
        if not isinstance(line, dict) or line.get("overlay_occluded") is True:
            continue
        confidence = _line_confidence(line)
        box = line.get("box")
        if (confidence < _MIN_COMPLETE_CONFIDENCE
                or not _valid_box(box)
                or not top < (box[1] + box[3]) / 2 < bottom
                or len(str(line.get("text") or "")) <= 25):
            continue
        if _parse_candidate_text(line.get("text")) is None:
            return True
    return False


def _row_has_boundary(row):
    if not isinstance(row, dict):
        return True
    if row.get("screen_boundary") or row.get("completed_action"):
        return True
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    return bool(
        facts.get("boundary_state_recovery")
        or facts.get("new_turn")
        or facts.get("completed_action")
        or stats.get("completed_action")
    )


def _proof_context_matches(event, row):
    """Require proof ownership metadata to agree with the accepted event."""
    event_title = event.get("context_title")
    row_title = row.get("context_title")
    row_title_candidate = row.get("context_title_candidate")
    if event_title:
        if row_title and row_title != event_title:
            return False
        if row_title_candidate and row_title_candidate != event_title:
            return False
    elif row_title is None and row_title_candidate:
        # A diagnostic title without an accepted title cannot establish proof
        # ownership for an event whose title is itself unavailable.
        return False
    return not _row_has_boundary(row)


def _proof_source_lines(row):
    """Yield gameplay OCR lines that can independently support a proof."""
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    for line in facts.get("occluded_receipt_lines", []):
        if (isinstance(line, dict)
                and line.get("recipient_name_occluded") is False):
            yield line
    ocr = row.get("ocr") if isinstance(row.get("ocr"), dict) else {}
    neural = ocr.get("neural", [])
    if isinstance(neural, list):
        yield from (line for line in neural if isinstance(line, dict))


def _source_proves_effect(row, effect):
    """Bind an accepted effect back to a valid, gameplay-bounded OCR line."""
    for line in _proof_source_lines(row):
        if line.get("overlay_occluded") is True:
            continue
        if _line_confidence(line) < _MIN_COMPLETE_CONFIDENCE:
            continue
        if not _valid_box(line.get("box")):
            continue
        parsed = _parse_candidate_text(line.get("text"))
        if (parsed is None
                or parsed.get("direction") not in ("up", "down")
                or parsed.get("terminal") is None
                or parsed.get("field") != effect.get("field")
                or parsed.get("signed_amount") != effect.get("amount")):
            continue
        return True
    return False


def _event_for_candidate(row, events, readings):
    time = row.get("source_timestamp_ms")
    if type(time) not in (int, float) or not math.isfinite(time):
        return None
    if _row_has_boundary(row):
        return None
    candidate_title = row.get("context_title")
    candidate_title_candidate = row.get("context_title_candidate")
    if (candidate_title and candidate_title_candidate
            and candidate_title != candidate_title_candidate):
        return None
    inside = [event for event in events
              if type(event.get("first_seen_ms")) in (int, float)
              and type(event.get("last_seen_ms")) in (int, float)
              and event.get("first_seen_ms") <= time <= event.get("last_seen_ms")]
    if len(inside) == 1:
        return inside[0]
    if inside:
        return None
    # The candidate title is diagnostic OCR, not an ownership witness.  A
    # pre-event link requires the accepted title on both sides.
    title = row.get("context_title")
    if not title:
        return None
    before = [event for event in events
              if type(event.get("first_seen_ms")) in (int, float)
              and type(event.get("last_seen_ms")) in (int, float)
              and 0 <= elapsed(time, event.get("first_seen_ms", 0)) <= _MAX_PRE_EVENT_MS
              and event.get("context_title") == title]
    if len(before) != 1:
        return None
    event = before[0]
    candidate_facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    candidate_stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    candidate_calendar = (candidate_stats.get("calendar_text")
                          or candidate_facts.get("calendar_text"))
    candidate_turns = candidate_stats.get("turns_remaining_to_goal")
    intervening = [candidate for candidate in readings
                   if isinstance(candidate, dict)
                   and type(candidate.get("source_timestamp_ms")) in (int, float)
                   and time < candidate.get("source_timestamp_ms") < event["first_seen_ms"]]
    if any(_is_pre_event_boundary(candidate, title, candidate_calendar,
                                  candidate_turns)
           for candidate in intervening):
        return None
    # A different outcome occupying the same gap makes ownership ambiguous.
    for other in events:
        if other is event:
            continue
        if (type(other.get("first_seen_ms")) in (int, float)
                and type(other.get("last_seen_ms")) in (int, float)
                and other.get("last_seen_ms", -1) >= time
                and other.get("first_seen_ms", 0) <= event.get("first_seen_ms", 0)):
            return None
    return event


def _complete_proofs(event, effect, readings, candidate_row):
    key = _effect_key(effect)
    proofs = []
    candidate_time = candidate_row.get("source_timestamp_ms")
    candidate_evidence = candidate_row.get("evidence")
    candidate_identity = _evidence_identity(candidate_evidence)
    if candidate_identity is None:
        return proofs
    for reading_index, row in enumerate(readings):
        if row is candidate_row:
            continue
        time = row.get("source_timestamp_ms")
        first_seen = event.get("first_seen_ms")
        last_seen = event.get("last_seen_ms")
        if (type(time) not in (int, float)
                or type(first_seen) not in (int, float)
                or type(last_seen) not in (int, float)
                or not math.isfinite(time)
                or not first_seen <= time <= last_seen):
            continue
        # One physical source frame cannot independently corroborate itself,
        # even if it appears twice as separate row objects.  A proof must be
        # at a different timestamp and have a different normalized artifact
        # path; query/traversal aliases are rejected below.
        if time == candidate_time or row.get("evidence") == candidate_evidence:
            continue
        proof_identity = _evidence_identity(row.get("evidence"))
        if proof_identity is None or proof_identity == candidate_identity:
            continue
        if row.get("screen") not in _ALLOWED_SCREENS:
            continue
        if not _proof_context_matches(event, row):
            continue
        for observed in row.get("effects", []):
            if (_effect_key(observed) != key
                    or _effect_signature(observed) != _effect_signature(effect)):
                continue
            raw_text = str(observed.get("raw_text") or "").strip()
            confidence = observed.get("confidence", 0)
            parsed = _parse_candidate_text(raw_text)
            if (parsed is None
                    or parsed["field"] != effect.get("field")
                    or parsed["signed_amount"] != effect.get("amount")
                    or parsed.get("terminal") is None
                    or not re.search(r"[.!]$", raw_text)
                    or type(confidence) not in (int, float)
                    or not math.isfinite(confidence)
                    or confidence < _MIN_COMPLETE_CONFIDENCE):
                continue
            if not _source_proves_effect(row, effect):
                continue
            proofs.append(dict(
                reading_index=reading_index,
                source_timestamp_ms=time,
                evidence=row.get("evidence"),
                raw_text=raw_text,
                confidence=confidence,
            ))
            break
    return proofs


def project_numeric_receipt_evidence(events, readings):
    """Attach source evidence for safely corroborated numeric candidates.

    The function mutates the event objects in place and returns ``events`` for
    pipeline convenience.  It never mutates ``readings``.  A candidate is
    eligible only when its strict label and numeric token agree with one
    accepted event effect and a different source timestamp in that event has
    a complete, high-confidence receipt for the same effect.
    """
    if not isinstance(events, list) or not isinstance(readings, list):
        return events
    for reading_index, row in enumerate(readings):
        if (not isinstance(row, dict)
                or not _valid_evidence_path(row.get("evidence"))):
            continue
        candidates = _deduplicate_candidates(row, _candidates(row))
        for candidate in candidates:
            event = _event_for_candidate(row, events, readings)
            if event is None:
                continue
            matches = [effect for effect in event.get("effects", [])
                       if effect.get("kind") == "stat_change"
                       and effect.get("field") == candidate["field"]
                       and type(effect.get("amount")) is int
                       and effect.get("amount") == candidate["signed_amount"]]
            if not matches:
                # A direction-less OCR omission such as ``Power went 4.``
                # carries a magnitude only.  The independently complete
                # receipt supplies the observed sign; it does not supply a
                # different amount.
                matches = [effect for effect in event.get("effects", [])
                           if effect.get("kind") == "stat_change"
                           and effect.get("field") == candidate["field"]
                           and type(effect.get("amount")) is int
                           and candidate["direction"] is None
                           and abs(effect.get("amount")) == candidate["magnitude"]]
            if len(matches) != 1:
                continue
            effect = matches[0]
            key = _effect_key(effect)
            if any(conflict.get("field") == key
                   for conflict in event.get("conflicting_readings", [])
                   if isinstance(conflict, dict)):
                continue
            proofs = _complete_proofs(event, effect, readings, row)
            if not proofs:
                continue
            evidence = row["evidence"]
            existing = event.setdefault("receipt_evidence_projections", [])
            identity = (row.get("source_timestamp_ms"), evidence,
                        candidate["field"], candidate["magnitude"])
            if any((item.get("source_timestamp_ms"), item.get("evidence"),
                    item.get("field"), item.get("observed_magnitude")) == identity
                   for item in existing):
                continue
            field_evidence = event.setdefault("field_evidence", {}).setdefault(key, [])
            if evidence not in field_evidence:
                field_evidence.append(evidence)
            existing.append(dict(
                reading_index=reading_index,
                source_timestamp_ms=row.get("source_timestamp_ms"),
                evidence=evidence,
                field=candidate["field"],
                observed_magnitude=candidate["magnitude"],
                accepted_amount=effect.get("amount"),
                raw_text=candidate["text"],
                candidate_variant=candidate["variant"],
                candidate_confidence=candidate["confidence"],
                candidate_source=candidate["source"],
                candidate_line_index=candidate["line_index"],
                candidate_box=candidate.get("box"),
                complete_proofs=proofs,
                accepted_as_effect=False,
                basis="same_event_complete_receipt_confirms_strict_numeric_candidate",
            ))
    return events
