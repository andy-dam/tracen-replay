"""Reconstruct completed Rest turns from source-visible evidence.

Rest is a turn-consuming action, so a positive energy delta alone is not
enough to identify it.  This module joins the source confirmation prompt, a
repeated result receipt, and the next observed date/countdown transition.
The event title is deliberately not used as an identity signal: scenario
events can appear between the result and the next turn boundary.
"""

from copy import deepcopy
import math
import re

from .calendar_coverage import date_key
from .turn_boundary import (
    _calendar_text,
    _evidence,
    _finite_time,
    _observation_record,
    _phase_turn_boundary,
    _repeated_observation,
    _rows_between,
    _same_date_boundary,
    _time,
    ordered_rows,
)


_CONFIRMATION = "take the day off to let your trainee recover energy?"
_ENTIRE_TURN = "this will take up the entire turn."
_CANCEL_STATUS = re.compile(
    r"\b(?:cancelled|canceled|aborted|declined)\b"
    r"|\b(?:action|rest|selection|request)\s+(?:was\s+)?cancel(?:led|ed)?\b",
    re.I,
)

# These screens denote another selectable/committed action.  ``event_outcome``
# is handled separately because scenario dialogue is allowed after a Rest
# result but another outcome before it would make the path ambiguous.
_ACTION_SCREENS = frozenset({
    "training_preview", "training_result", "training_result_candidate",
    "training_selection", "rest_confirmation", "outing_confirmation",
    "outing_selection", "lesson_selection", "lesson_confirmation",
    "race_confirmation", "race_result", "skill_selection",
    "skill_confirmation", "skill_receipt", "concert_confirmation",
    "concert_result", "concert_result_candidate", "infirmary_confirmation",
    "career_hub", "main_hub", "hub", "career_completion_hub",
})
# Lessons, the concert and skill purchases are paid with points, not with
# the turn.  A player who rests and then buys lessons or holds the concert is
# still on the same turn, and its next date only appears after those
# screens: they extend the wait for the boundary and are not competing
# actions once the recovery result has been read.
_INTERLUDE_SCREENS = frozenset({
    "lesson_selection", "lesson_confirmation", "skill_selection",
    "skill_confirmation", "skill_receipt", "concert_confirmation",
    "concert_result", "concert_result_candidate", "concert_bonus_update",
})
_MAX_CONFIRMATION_DELAY_MS = 10_000
_MAX_BOUNDARY_DELAY_MS = 30_000
_MAX_INTERLUDE_MS = 10 * 60_000
_PHASE_LABELS = frozenset({"Junior Year Pre-Debut", "Finale Underway"})


def _text_values(value):
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            yield text
        for nested in value.values():
            yield from _text_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _text_values(nested)


def _ocr_lines(row, minimum_confidence=95):
    """Return only bounded-confidence retained OCR lines from one row."""
    parts = []

    def visit(value):
        if isinstance(value, dict):
            text = value.get("text")
            confidence = value.get("confidence", value.get("conf"))
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = None
            if (isinstance(text, str) and text.strip()
                    and confidence is not None and math.isfinite(confidence)
                    and 0 <= confidence <= 100 and confidence >= minimum_confidence):
                parts.append(dict(value, confidence=confidence))
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    if isinstance(row, dict):
        visit(row.get("ocr"))
    return parts


def _ocr_text(row, minimum_confidence=95):
    return " ".join(" ".join(line['text'] for line in _ocr_lines(row, minimum_confidence)).casefold().split())


def _is_confirmation(row):
    return (isinstance(row, dict) and row.get("screen") == "rest_confirmation"
            and _CONFIRMATION in _ocr_text(row)
            and _ENTIRE_TURN in _ocr_text(row))


def _is_cancel_status(row):
    return bool(_CANCEL_STATUS.search(_ocr_text(row)))


def _effects(value):
    if isinstance(value, dict):
        value = [value]
    return [effect for effect in value
            if isinstance(effect, dict)] if isinstance(value, (list, tuple)) else []


def _positive_energy_effects(value):
    return [deepcopy(effect) for effect in _effects(value)
            if effect.get("kind") == "energy_change"
            and type(effect.get("amount")) is int and effect["amount"] > 0
            and isinstance(effect.get("raw_text"), str)
            and effect["raw_text"].strip()]


def _recovery_receipt(row):
    """Check source support with the shared effect grammar, without new OCR."""
    from .gameplay import effects_from_lines
    observed = [effect for effect in effects_from_lines(_ocr_lines(row))
                if effect.get('kind') == 'energy_change']
    amounts = {effect['amount'] for effect in observed}
    effects = _positive_energy_effects(row.get("effects"))
    if len(effects) != 1 or {effects[0]["amount"]} != amounts:
        return []
    return effects


def _evidence_list(rows):
    result = []
    for row in rows:
        value = _evidence(row)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item and item not in result:
                result.append(item)
    return result


def _event_times(event):
    if not isinstance(event, dict):
        return None
    first = event.get("first_seen_ms")
    last = event.get("last_seen_ms", first)
    if not _finite_time(first) or not _finite_time(last) or last < first:
        return None
    return first, last


def _confirmation_group(ordered, event_first):
    candidates = [row for row in _rows_between(ordered, event_first - _MAX_CONFIRMATION_DELAY_MS, event_first)
                  if _time(row) is not None
                  and event_first - _MAX_CONFIRMATION_DELAY_MS <= _time(row) < event_first
                  and _is_confirmation(row)]
    if not candidates:
        return []
    group = [candidates[-1]]
    for row in reversed(candidates[:-1]):
        if _time(group[0]) - _time(row) > 500:
            break
        group.insert(0, row)
    return group


def _blocked_between(ordered, start, end, *, allow_event_dialogue=False,
                     allow_action_screens=(), own_title=None):
    """Reject cancellation, navigation, or another action in a source path.

    The result dialogue's own frames, titled like the Rest event, can precede
    the frame on which its receipt text was read; they are part of the path.
    """
    allowed = frozenset(allow_action_screens)
    own = " ".join(str(own_title).split()).casefold() if isinstance(own_title, str) and own_title.strip() else None
    for row in _rows_between(ordered, start, end):
        time = _time(row)
        if time is None or not start < time < end:
            continue
        if _is_cancel_status(row):
            return True
        if row.get("completed_action") not in (None, "", "rest"):
            return True
        screen = row.get("screen")
        if screen == "event_outcome":
            title = row.get("context_title")
            if own and isinstance(title, str) and " ".join(title.split()).casefold() == own:
                continue
            if not allow_event_dialogue:
                return True
            # Event dialogue after a Rest result is allowed.  A non-empty
            # completed_action still fails above, so this remains dialogue-only.
            continue
        if screen in _ACTION_SCREENS:
            # A repeated, source-confirmed Rest prompt is part of this path;
            # all other action screens are competing turns or navigation.
            if screen == "rest_confirmation" and _is_confirmation(row):
                continue
            if screen in allowed:
                continue
            return True
    return False


def _goal_countdown_before_result(ordered, confirmation_time, result_first):
    """Return repeated final-goal countdown rows after Rest is accepted.

    A Rest can be followed by a mandatory goal race before the next calendar
    label.  The countdown is retained as source evidence that the later race
    is a phase transition after this Rest, rather than evidence from an
    unrelated later turn.  It is intentionally read only between the source
    confirmation and the recovery result.
    """
    rows = []
    for row in _rows_between(ordered, confirmation_time, result_first):
        time = _time(row)
        if time is None or not confirmation_time < time < result_first:
            continue
        if _calendar_text(row) not in _PHASE_LABELS:
            continue
        stats = row.get("stats")
        value = stats.get("turns_remaining_to_goal") if isinstance(stats, dict) else None
        if type(value) is int and value == 1:
            rows.append(row)
    return rows if _repeated_observation(rows) else []


def _goal_race_followup(ordered, result_end, boundary_time,
                        confirmation_time, result_first):
    """Return a narrow allowlist for a mandatory race after a completed Rest.

    The race is a later action, so it must not invalidate the already visible
    Rest receipt.  This exception is source-gated by repeated final-goal
    countdown readings, repeated race-result readings, and the existing
    repeated next-calendar boundary.  Other action screens remain blockers.
    """
    countdown = _goal_countdown_before_result(
        ordered, confirmation_time, result_first)
    if not countdown:
        return None
    race_rows = []
    for row in _rows_between(ordered, result_end, boundary_time):
        time = _time(row)
        if time is None or not result_end < time < boundary_time:
            continue
        if row.get("screen") == "race_result":
            race_rows.append(row)
    if not _repeated_observation(race_rows):
        return None
    return dict(countdown_rows=countdown, race_rows=race_rows)


def _result_rows(ordered, first, last):
    rows = []
    for row in _rows_between(ordered, first, last):
        time = _time(row)
        if time is None or not first <= time <= last:
            continue
        if row.get("screen") not in (None, "", "unknown", "event_outcome"):
            continue
        effects = _recovery_receipt(row)
        if effects:
            rows.append((row, effects[0]))
    return rows


def _clean_result_interval(ordered, first, last):
    for row in _rows_between(ordered, first, last):
        time = _time(row)
        if time is None or not first <= time <= last:
            continue
        if row.get("completed_action") not in (None, "", "rest"):
            return False
        if _is_cancel_status(row):
            return False
        screen = row.get("screen")
        if screen in _ACTION_SCREENS or screen == "event_outcome":
            if screen != "event_outcome":
                return False
    return True


def _interlude_end(ordered, result_end):
    """When the wait for the next date starts: the result, or the last of
    the point-spending screens that follow it without a long gap.

    The walk stops at the first dated calendar that differs from the date
    read before the result (that is the boundary itself) and at any other
    action screen, which the caller rejects as a competing action.
    """
    current_key = None
    for row in _rows_between(ordered, result_end - _MAX_BOUNDARY_DELAY_MS, result_end):
        time = _time(row)
        calendar = _calendar_text(row)
        if time is None or time > result_end or not calendar:
            continue
        key = date_key(calendar)
        if key is not None:
            current_key = key
    end = result_end
    for row in _rows_between(ordered, result_end, result_end + _MAX_INTERLUDE_MS):
        time = _time(row)
        if time is None or time <= result_end:
            continue
        if time - end > _MAX_BOUNDARY_DELAY_MS:
            break
        calendar = _calendar_text(row)
        key = date_key(calendar) if calendar else None
        if key is not None and current_key is not None and key != current_key:
            break
        screen = row.get("screen")
        if screen in _INTERLUDE_SCREENS:
            end = time
        elif screen in _ACTION_SCREENS:
            break
    return end


def _boundary(ordered, confirmation_time, result_end, deadline=None):
    if deadline is None:
        deadline = result_end + _MAX_BOUNDARY_DELAY_MS
    boundary = _same_date_boundary(ordered, result_end, deadline)
    if boundary is not None:
        return dict(boundary, kind="calendar_date")
    boundary = _phase_turn_boundary(ordered, confirmation_time, result_end, deadline)
    if boundary is not None:
        return boundary
    # Some recordings expose a phase label before a turn and then the next
    # concrete calendar date, without exposing a numeric countdown.  Treat
    # that as a boundary only when both sides are repeated source readings.
    phase_rows = [row for row in _rows_between(ordered, result_end - _MAX_BOUNDARY_DELAY_MS, result_end)
                  if _time(row) is not None
                  and result_end - _MAX_BOUNDARY_DELAY_MS <= _time(row) <= result_end
                  and _calendar_text(row) in _PHASE_LABELS]
    if not _repeated_observation(phase_rows):
        return None
    next_rows = []
    next_key = None
    for row in _rows_between(ordered, result_end + 1, deadline):
        time = _time(row)
        if time is None or time <= result_end:
            continue
        if time > deadline:
            break
        calendar = _calendar_text(row)
        if not calendar or calendar in _PHASE_LABELS:
            continue
        key = date_key(calendar)
        if key is None:
            return None
        if next_key is None:
            next_key = key
        if key != next_key:
            return None
        next_rows.append(row)
    if len(next_rows) < 2 or not _repeated_observation(next_rows):
        return None
    return dict(row=next_rows[0], rows=next_rows, kind="phase_date")


def _action(event, confirmations, result_rows, boundary, followup=None):
    first, last = _event_times(event)
    result_observations = [row for row, _ in result_rows]
    recovery_effects = []
    seen = set()
    for _, effect in result_rows:
        key = (effect.get("kind"), effect.get("amount"), effect.get("raw_text"))
        if key not in seen:
            seen.add(key)
            recovery_effects.append(deepcopy(effect))
    boundary_rows = boundary.get("rows", [boundary["row"]])
    confirmation_time = _time(confirmations[0])
    result_evidence = _evidence_list(result_observations)
    confirmation_evidence = _evidence_list(confirmations)
    boundary_evidence = _evidence_list(boundary_rows)
    followup_evidence = []
    if followup is not None:
        followup_evidence = _evidence_list(
            followup["countdown_rows"] + followup["race_rows"])
    action = dict(
        kind="rest",
        source_timestamp_ms=first,
        confirmation_timestamp_ms=confirmation_time,
        result_first_seen_ms=first,
        result_last_seen_ms=last,
        next_date_timestamp_ms=_time(boundary["row"]),
        event_id=event.get("id"),
        click_timestamp_ms=None,
        evidence=list(dict.fromkeys(confirmation_evidence + result_evidence
                                    + boundary_evidence + followup_evidence)),
        confirmation_evidence=confirmation_evidence,
        result_evidence=result_evidence,
        next_date_evidence=boundary_evidence,
        next_boundary_observations=[_observation_record(row)
                                    for row in boundary_rows],
        next_calendar=_calendar_text(boundary["row"]),
        boundary_kind=boundary["kind"],
        recovery_effects=recovery_effects,
        basis="rest_confirmation_repeated_recovery_and_turn_boundary",
    )
    if followup is not None:
        action.update(
            post_result_action_kind="goal_race",
            post_result_action_evidence=followup_evidence,
            post_result_action_observations=[
                dict(kind="goal_countdown", **_observation_record(row))
                for row in followup["countdown_rows"]
            ] + [
                dict(kind="race_result", **_observation_record(row))
                for row in followup["race_rows"]
            ],
        )
    if boundary["kind"] == "phase_countdown":
        before_rows = boundary.get("before_rows", [])
        transition_rows = before_rows + boundary_rows
        action.update(
            turns_before=boundary["turns_before"],
            turns_after=boundary["turns_after"],
            turn_transition_evidence=_evidence_list(transition_rows),
            turn_transition_observations=(
                [dict(state="before", **_observation_record(row))
                 for row in before_rows]
                + [dict(state="after", **_observation_record(row))
                   for row in boundary_rows]
            ),
        )
    return action


def reconstruct(readings, events):
    """Return completed Rest actions proved by source readings."""
    if not isinstance(readings, (list, tuple)) or not isinstance(events, (list, tuple)):
        return []
    ordered = ordered_rows(readings)
    result = []
    used_events = set()
    used_spans = []
    for event in sorted((event for event in events if isinstance(event, dict)),
                        key=lambda value: (_event_times(value) or (float("inf"),))[0]):
        times = _event_times(event)
        if times is None or event.get("kind") != "outcome":
            continue
        first, last = times
        event_id = event.get("id")
        if isinstance(event_id, str):
            event_id = event_id.strip() or None
        if event_id is not None and event_id in used_events:
            continue
        if any(first <= prior_last and prior_first <= last
               for prior_first, prior_last in used_spans):
            continue
        confirmations = _confirmation_group(ordered, first)
        if not confirmations or _is_cancel_status(confirmations[-1]):
            continue
        confirmation_time = _time(confirmations[0])
        if confirmation_time is None:
            continue
        # Event-level effects nominate the expected amount; source rows must
        # independently show that amount in the recovery receipt.
        event_effects = _positive_energy_effects(event.get("effects"))
        if len(event_effects) != 1:
            continue
        rows = _result_rows(ordered, first, last)
        if (len(rows) < 2 or not _repeated_observation([row for row, _ in rows])
                or {effect["amount"] for _, effect in rows} != {event_effects[0]["amount"]}):
            continue
        if _is_cancel_status(event) or not _clean_result_interval(ordered, first, last):
            continue
        if _blocked_between(ordered, confirmation_time, first, own_title=event.get("context_title")):
            continue
        interlude_end = _interlude_end(ordered, last)
        boundary = _boundary(ordered, confirmation_time, last,
                             interlude_end + _MAX_BOUNDARY_DELAY_MS)
        if boundary is None:
            continue
        boundary_time = _time(boundary.get("row"))
        if boundary_time is None or boundary_time <= last or boundary_time - interlude_end > _MAX_BOUNDARY_DELAY_MS:
            continue
        followup = _goal_race_followup(
            ordered, last, boundary_time, confirmation_time, first)
        allowed_screens = {"race_result"} if followup is not None else set()
        if _blocked_between(ordered, last, boundary_time,
                            allow_event_dialogue=True,
                            allow_action_screens=allowed_screens | _INTERLUDE_SCREENS):
            continue
        action = _action(event, confirmations, rows, boundary, followup)
        if interlude_end > last:
            action["post_result_interlude_end_ms"] = interlude_end
        result.append(action)
        used_spans.append((first, last))
        if event_id is not None:
            used_events.add(event_id)
    return sorted(result, key=lambda action: action["source_timestamp_ms"])


__all__ = ["reconstruct"]
