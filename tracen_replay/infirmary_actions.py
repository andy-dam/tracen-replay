"""Conservatively reconstruct completed Infirmary turns from source readings.

The Infirmary is a turn-consuming recovery action, but it is a different UI
path from Rest.  This module only joins a source-visible confirmation to a
named ``At the Infirmary`` result and a later calendar observation.  It does
not infer recovery from a state residual, and it leaves ambiguous sequences
unresolved for the caller.
"""
from copy import deepcopy
import math
import re
from .calendar_coverage import date_key


_TITLE = 'at the infirmary'
_CONFIRMATION = 'visit the infirmary?'
_ENTIRE_TURN = 'this will take up the entire turn.'
_CANCEL_WORDS = re.compile(r'\b(?:cancel(?:led|ed)?|aborted|declined)\b', re.I)
_CANCEL_STATUS = re.compile(
    r'\b(?:cancelled|canceled|aborted|declined)\b'
    r'|\b(?:action|visit|infirmary|selection|request)\s+'
    r'(?:was\s+)?cancel(?:led|ed)?\b', re.I)
_ACTION_SCREENS = frozenset({
    'training_preview', 'training_result', 'training_result_candidate',
    'training_selection', 'rest_confirmation', 'outing_confirmation',
    'outing_selection', 'lesson_selection',
    'race_confirmation', 'race_result', 'lesson_confirmation',
    'skill_selection', 'skill_confirmation', 'skill_receipt', 'concert_confirmation',
    'concert_result', 'concert_result_candidate', 'event_outcome',
    'infirmary_confirmation', 'career_hub', 'main_hub', 'hub',
    'career_completion_hub',
})
_PHASE_LABELS = frozenset({'Junior Year Pre-Debut', 'Finale Underway'})
_MAX_RESULT_DELAY_MS = 30_000
_MAX_NEXT_DATE_DELAY_MS = 30_000


def _finite_time(value):
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 0)


def _time(row):
    value = row.get('source_timestamp_ms') if isinstance(row, dict) else None
    return value if _finite_time(value) else None


def _evidence(row):
    value = row.get('evidence') if isinstance(row, dict) else None
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item]
    return None


def _text_values(value):
    """Yield OCR text without changing the caller's raw reading."""
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, dict):
        text = value.get('text')
        if isinstance(text, str) and text.strip():
            yield text
        for nested in value.values():
            yield from _text_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _text_values(nested)


def _row_text(row):
    parts = []
    if not isinstance(row, dict):
        return ''
    for key in ('context_title', 'context_title_candidate', 'text', 'ocr'):
        parts.extend(_text_values(row.get(key)))
    return ' '.join(' '.join(parts).casefold().split())


def _normalized_text(value):
    return ' '.join(value.casefold().split()) if isinstance(value, str) else ''


def _ocr_text(row, minimum_confidence=90):
    """Return only high-confidence text from the retained OCR payload."""
    parts = []

    def visit(value):
        if isinstance(value, dict):
            text = value.get('text')
            confidence = value.get('confidence', value.get('conf'))
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = None
            if (isinstance(text, str) and text.strip()
                    and confidence is not None and math.isfinite(confidence)
                    and confidence >= minimum_confidence):
                parts.append(text)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    if isinstance(row, dict):
        visit(row.get('ocr'))
    return ' '.join(' '.join(parts).casefold().split())


def _calendar_text(row):
    stats = row.get('stats') if isinstance(row, dict) else None
    value = stats.get('calendar_text') if isinstance(stats, dict) else None
    return ' '.join(value.split()) if isinstance(value, str) and value.strip() else None


def _is_confirmation(row):
    text = _ocr_text(row)
    # The screen label is a parser result, not source evidence by itself.  The
    # two fixed prompt phrases must still be present in the retained OCR.
    return _CONFIRMATION in text and _ENTIRE_TURN in text


def _is_cancel_status(row):
    """Recognize a cancellation statement, excluding a lone Cancel button."""
    return bool(_CANCEL_STATUS.search(_ocr_text(row)))


def _is_named_result(row):
    if not isinstance(row, dict):
        return False
    # A parser-only context-title candidate or an unrelated known screen is
    # not source proof of an Infirmary result.  The retained row must carry
    # the exact source context title as well as the OCR title.
    if _normalized_text(row.get('context_title')) != _TITLE:
        return False
    candidate_title = row.get('context_title_candidate')
    if candidate_title is not None and _normalized_text(candidate_title) != _TITLE:
        return False
    screen = row.get('screen')
    if screen not in (None, '', 'unknown', 'event_outcome'):
        return False
    text = _ocr_text(row)
    return _TITLE in text


def _effects(row, event):
    result = []
    for source in (row.get('effects', []) if isinstance(row, dict) else [],
                   event.get('effects', []) if isinstance(event, dict) else []):
        if isinstance(source, dict):
            source = [source]
        if isinstance(source, (list, tuple)):
            result.extend(effect for effect in source if isinstance(effect, dict))
    return result


def _recovery_effects(row, event):
    """Return only explicit recovery effects visible in source readings."""
    result = []
    for effect in _effects(row, event):
        kind = effect.get('kind')
        amount = effect.get('amount')
        if kind == 'energy_change' and type(amount) is int and amount > 0:
            result.append(deepcopy(effect))
            continue
        if kind in {'condition_removed', 'condition_recovered', 'condition_change'}:
            name, raw = effect.get('name'), effect.get('raw_text')
            if (not isinstance(name, str) or not name.strip()
                    or not isinstance(raw, str) or not raw.strip()
                    or _normalized_text(name) not in _normalized_text(raw)
                    or _normalized_text(raw) not in _ocr_text(row)):
                continue
            direction = str(effect.get('direction', '')).casefold()
            if ((kind != 'condition_change' and not direction)
                    or direction in {'removed', 'recovered', 'cleared'}):
                result.append(deepcopy(effect))
                continue
    return result


def _event_time(event, key, fallback=None):
    value = event.get(key) if isinstance(event, dict) else None
    return value if _finite_time(value) else fallback


def _same_date_boundary(rows, result_end):
    """Find a repeated, contiguous next date after the result."""
    lookback_start = result_end - _MAX_NEXT_DATE_DELAY_MS
    dated = []
    invalid = []
    for row in rows:
        time = _time(row)
        if time is None or time < lookback_start or time > result_end:
            continue
        calendar = _calendar_text(row)
        if not calendar:
            continue
        key = date_key(calendar)
        if key is None:
            if calendar in _PHASE_LABELS:
                continue
            invalid.append((time, calendar))
            continue
        dated.append((time, calendar, key, row))
    if not dated:
        return None
    current_text, current_key = dated[-1][1], dated[-1][2]
    prior = [item for item in dated[:-1] if item[2] != current_key]
    current_start = prior[-1][0] if prior else lookback_start
    if any(time >= current_start for time, _ in invalid):
        return None
    current_rows = [item[3] for item in dated if item[2] == current_key and item[0] >= current_start]
    current_timestamps = {item[0] for item in dated if item[2] == current_key and item[0] >= current_start}
    current_evidence = set()
    for row in current_rows:
        value = _evidence(row)
        values = value if isinstance(value, list) else [value]
        current_evidence.update(item for item in values if item)
    if len(current_timestamps) < 2 or len(current_evidence) < 2:
        return None
    next_key = current_key + 1
    next_rows = []
    future_started = False
    for row in rows:
        time = _time(row)
        if time is None or time <= result_end:
            continue
        if time - result_end > _MAX_NEXT_DATE_DELAY_MS:
            break
        calendar = _calendar_text(row)
        if not calendar:
            continue
        key = date_key(calendar)
        if key is None:
            if len(next_rows) >= 2:
                break
            return None
        if not future_started:
            if key == current_key:
                continue
            if key != next_key:
                return None
            future_started = True
            next_rows.append(row)
            continue
        if key == next_key:
            next_rows.append(row)
            continue
        # A later date belongs to the following turn.  It no longer bears on
        # whether this action crossed exactly one calendar boundary.  An old
        # date reappearing immediately after the transition is contradictory.
        if key == current_key:
            return None
        if len(next_rows) < 2:
            return None
        break
    timestamps = {_time(row) for row in next_rows}
    evidence = set()
    for row in next_rows:
        value = _evidence(row)
        values = value if isinstance(value, list) else [value]
        evidence.update(item for item in values if item)
    if len(timestamps) < 2 or len(evidence) < 2:
        return None
    return dict(row=next_rows[0], rows=next_rows)


def _repeated_observation(rows):
    timestamps = {_time(row) for row in rows}
    evidence = set()
    for row in rows:
        value = _evidence(row)
        values = value if isinstance(value, list) else [value]
        evidence.update(item for item in values if item)
    return len(timestamps) >= 2 and len(evidence) >= 2


def _observation_record(row):
    """Keep the source fields used by a boundary proof, without raw mutation."""
    stats = row.get('stats') if isinstance(row, dict) else None
    record = {
        'source_timestamp_ms': _time(row),
        'evidence': deepcopy(_evidence(row)),
        'calendar_text': _calendar_text(row),
    }
    if isinstance(stats, dict) and type(stats.get('turns_remaining_to_goal')) is int:
        record['turns_remaining_to_goal'] = stats['turns_remaining_to_goal']
    for key in ('source_frame_sha256', 'gameplay_sha256', 'evidence_sha256',
                'source_sha256'):
        value = row.get(key)
        if isinstance(value, str) and value:
            record[key] = value
    return record


def _phase_turn_boundary(rows, confirmation_time, result_end):
    """Use a repeated phase countdown when no dated calendar exists."""
    lookback_start = confirmation_time - _MAX_RESULT_DELAY_MS
    phase_rows = []
    for row in rows:
        time = _time(row)
        if time is None or time < lookback_start or time > result_end:
            continue
        phase = _calendar_text(row)
        if phase in _PHASE_LABELS:
            phase_rows.append((time, phase, row))
    if not phase_rows:
        return None
    phase = phase_rows[-1][1]
    before = []
    for time, observed_phase, row in phase_rows:
        if time <= confirmation_time and observed_phase == phase:
            value = row.get('stats', {}).get('turns_remaining_to_goal')
            if type(value) is int and value > 0:
                before.append((time, value, row))
    if not before:
        return None
    before_value = before[-1][1]
    # Only the trailing run immediately before the confirmation establishes
    # the current countdown.  Earlier values belong to earlier turns and are
    # expected in a long lookback window (for example 5 -> 4 before this
    # visit); treating them as contradictions would reject a valid action.
    before_start = len(before) - 1
    while before_start > 0 and before[before_start - 1][1] == before_value:
        before_start -= 1
    before_rows = [row for _, _, row in before[before_start:]]
    if not _repeated_observation(before_rows):
        return None
    after = []
    target_started = False
    for row in rows:
        time = _time(row)
        if time is None or time <= result_end or time - result_end > _MAX_NEXT_DATE_DELAY_MS:
            if time is not None and time - result_end > _MAX_NEXT_DATE_DELAY_MS:
                break
            continue
        observed_phase = _calendar_text(row)
        if observed_phase is None:
            continue
        if observed_phase != phase:
            return None
        value = row.get('stats', {}).get('turns_remaining_to_goal')
        if type(value) is not int or value < 0:
            continue
        if not target_started:
            if value == before_value:
                continue
            if value != before_value - 1:
                return None
            target_started = True
        elif value != before_value - 1:
            # A lower value is a later completed turn.  The already repeated
            # target state is sufficient for this action, so stop the proof
            # at that next transition.  Reappearance of the old value or an
            # increase is contradictory and fails closed.
            if value < before_value - 1:
                break
            return None
        after.append((time, value, row))
    target = before_value - 1
    target_rows = [row for _, value, row in after if value == target]
    if not target_rows or not _repeated_observation(target_rows):
        return None
    if any(value not in (before_value, target) for _, value, _ in after):
        return None
    return dict(row=target_rows[0], rows=target_rows,
                before_rows=before_rows, kind='phase_countdown', phase=phase,
                turns_before=before_value, turns_after=target)


def _blocked_between(rows, start, end, *, allow_confirmation=True):
    for row in rows:
        time = _time(row)
        if time is None or not start < time < end:
            continue
        screen = row.get('screen')
        if screen in _ACTION_SCREENS - {'infirmary_confirmation'}:
            return True
        if row.get('completed_action') not in (None, '', 'infirmary'):
            return True
        if _is_cancel_status(row):
            return True
        if _is_confirmation(row):
            if not allow_confirmation:
                return True
            continue
        if screen == 'infirmary_confirmation' and not allow_confirmation:
            return True
        text = _ocr_text(row)
        if _CANCEL_WORDS.search(text):
            return True
    return False


def _matching_events(events, result_rows, confirmation_time):
    matches = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('kind') != 'outcome':
            continue
        event_id = event.get('id')
        if not isinstance(event_id, str) or not event_id.strip():
            continue
        first = _event_time(event, 'first_seen_ms')
        last = _event_time(event, 'last_seen_ms', first)
        if (first is None or last is None or last < first
                or first < confirmation_time):
            continue
        # ``context_title_candidate`` is diagnostic metadata and cannot
        # authorize an event by itself.  The event's resolved source context
        # must be the exact Infirmary title.
        if _normalized_text(event.get('context_title')) != _TITLE:
            continue
        candidate_title = event.get('context_title_candidate')
        if (candidate_title is not None
                and _normalized_text(candidate_title) != _TITLE):
            continue
        if not any(first <= _time(row) <= last for row in result_rows if _time(row) is not None):
            continue
        matches.append(event)
    return matches


def _result_interval_clean(rows, first, last):
    """Reject a result interval containing another recognized action screen."""
    for row in rows:
        time = _time(row)
        if time is None or not first <= time <= last:
            continue
        screen = row.get('screen')
        if screen in _ACTION_SCREENS - {'event_outcome'}:
            return False
        if row.get('completed_action') not in (None, '', 'infirmary'):
            return False
        if _is_cancel_status(row):
            return False
        # A second confirmation inside the result interval means the first
        # candidate did not have a clean result boundary.
        if _is_confirmation(row):
            return False
        for key in ('context_title', 'context_title_candidate'):
            value = row.get(key)
            if isinstance(value, str) and value.strip() and _normalized_text(value) != _TITLE:
                return False
    return True


def _turns_stable_through_result(rows, confirmation_time, result_end):
    """Reject calendar, phase, or counter changes before the visit's result."""
    known_before = []
    calendar_before = None
    for row in rows:
        time = _time(row)
        if time is None or not confirmation_time-_MAX_RESULT_DELAY_MS <= time <= confirmation_time:
            continue
        calendar = _calendar_text(row)
        if calendar:
            calendar_before = calendar
        stats = row.get('stats')
        value = stats.get('turns_remaining_to_goal') if isinstance(stats, dict) else None
        if type(value) is int and value >= 0:
            known_before.append(value)
    current = known_before[-1] if known_before else None
    for row in rows:
        time = _time(row)
        if time is None or not confirmation_time < time <= result_end:
            continue
        calendar = _calendar_text(row)
        if calendar:
            if date_key(calendar) is None and calendar not in _PHASE_LABELS:
                return False
            if calendar_before is not None and calendar != calendar_before:
                return False
            calendar_before = calendar
        stats = row.get('stats')
        value = stats.get('turns_remaining_to_goal') if isinstance(stats, dict) else None
        if type(value) is int and value >= 0:
            if current is not None and value != current:
                return False
            current = value
    return True


def reconstruct(readings, events):
    """Return source-supported completed Infirmary actions.

    A result is accepted only when the confirmation and result are independently
    evidenced at distinct timestamps, the named result repeats, an explicit
    recovery effect is visible, and the next calendar label is observed.  A
    Cancel button appearing in the confirmation itself is expected; a later
    cancellation or competing action rejects the candidate.
    """
    if not isinstance(readings, (list, tuple)) or not isinstance(events, (list, tuple)):
        return []
    ordered = sorted((row for row in readings if isinstance(row, dict) and _time(row) is not None),
                     key=_time)
    confirmations = [row for row in ordered if _is_confirmation(row)]
    if not confirmations:
        return []
    event_ids = [event.get('id').strip() for event in events
                 if isinstance(event, dict) and isinstance(event.get('id'), str)
                 and event.get('id').strip()]
    duplicate_event_ids = {event_id for event_id in event_ids
                           if event_ids.count(event_id) > 1}
    result = []
    used_events = set()
    used_result_spans = []
    for confirmation in confirmations:
        confirmation_time = _time(confirmation)
        if confirmation_time is None:
            continue
        if _is_cancel_status(confirmation):
            continue
        candidates = [row for row in ordered
                      if _time(row) is not None and confirmation_time < _time(row) <= confirmation_time + _MAX_RESULT_DELAY_MS
                      and _is_named_result(row)]
        candidate_events = _matching_events(events, candidates, confirmation_time)
        for event in candidate_events:
            event_id = event.get('id')
            event_key = event_id.strip() if isinstance(event_id, str) else None
            if event_key in duplicate_event_ids or event_key in used_events:
                continue
            event_first = _event_time(event, 'first_seen_ms')
            event_last = _event_time(event, 'last_seen_ms', event_first)
            if event_first is None or event_last is None:
                continue
            if any(event_first <= last and first <= event_last for first,last in used_result_spans):
                continue
            if not _result_interval_clean(ordered, event_first, event_last):
                continue
            if not _turns_stable_through_result(ordered, confirmation_time, event_last):
                continue
            named = [row for row in candidates
                     if event_first <= _time(row) <= event_last and _is_named_result(row)]
            named_times = {_time(row) for row in named}
            named_evidence = []
            for row in named:
                evidence = _evidence(row)
                values = evidence if isinstance(evidence, list) else [evidence]
                for item in values:
                    if item and item not in named_evidence:
                        named_evidence.append(item)
            if len(named_times) < 2 or len(named_evidence) < 2:
                continue
            if _blocked_between(ordered, confirmation_time, event_first):
                continue
            # Event effects alone are not enough: this action needs a source
            # row carrying the explicit recovery receipt.  The event remains
            # useful for joining the result interval and corroborating the
            # same effect, but an anonymous event cannot create a recovery.
            recovery = []
            for row in named:
                recovery.extend(_recovery_effects(row, None))
            unique_recovery = []
            seen_recovery = set()
            for effect in recovery:
                key = (effect.get('kind'), effect.get('field'), effect.get('amount'), effect.get('raw_text'))
                if key not in seen_recovery:
                    seen_recovery.add(key)
                    unique_recovery.append(effect)
            if not unique_recovery:
                continue
            boundary_info = _same_date_boundary(ordered, event_last)
            if boundary_info is not None:
                boundary_info = dict(boundary_info, kind='calendar_date')
            else:
                boundary_info = _phase_turn_boundary(ordered, confirmation_time, event_last)
            if boundary_info is None:
                continue
            boundary = boundary_info['row']
            boundary_time = _time(boundary)
            if boundary_time is None or boundary_time - event_last > _MAX_NEXT_DATE_DELAY_MS:
                continue
            if _blocked_between(ordered, event_last, boundary_time, allow_confirmation=False):
                continue
            confirmation_evidence = _evidence(confirmation)
            boundary_rows = boundary_info.get('rows', [boundary])
            boundary_evidence = []
            for boundary_row in boundary_rows:
                evidence = _evidence(boundary_row)
                values = evidence if isinstance(evidence, list) else [evidence]
                for item in values:
                    if item and item not in boundary_evidence:
                        boundary_evidence.append(item)
            all_evidence = []
            for item in (([confirmation_evidence] if isinstance(confirmation_evidence, str) else confirmation_evidence or [])
                         + named_evidence + boundary_evidence):
                if isinstance(item, str) and item and item not in all_evidence:
                    all_evidence.append(item)
            if len(all_evidence) < 4:
                continue
            action = dict(kind='infirmary', source_timestamp_ms=event_first,
                          confirmation_timestamp_ms=confirmation_time,
                          result_first_seen_ms=event_first,
                          result_last_seen_ms=event_last,
                          next_date_timestamp_ms=boundary_time,
                          event_id=event_id, click_timestamp_ms=None,
                          evidence=all_evidence,
                          confirmation_evidence=([confirmation_evidence] if isinstance(confirmation_evidence, str) else confirmation_evidence or []),
                          result_evidence=named_evidence,
                          next_date_evidence=boundary_evidence,
                          next_boundary_observations=[_observation_record(row)
                                                      for row in boundary_rows],
                          next_calendar=_calendar_text(boundary),
                          boundary_kind=boundary_info['kind'],
                          recovery_effects=unique_recovery,
                          basis='infirmary_confirmation_result_and_next_date')
            if boundary_info['kind'] == 'phase_countdown':
                before_rows = boundary_info.get('before_rows', [])
                transition_evidence = []
                for transition_row in before_rows + boundary_rows:
                    evidence = _evidence(transition_row)
                    values = evidence if isinstance(evidence, list) else [evidence]
                    for item in values:
                        if item and item not in transition_evidence:
                            transition_evidence.append(item)
                action.update(turns_before=boundary_info['turns_before'],
                              turns_after=boundary_info['turns_after'],
                              turn_transition_evidence=transition_evidence,
                              turn_transition_observations=[
                                  dict(state='before', **_observation_record(row))
                                  for row in before_rows
                              ] + [
                                  dict(state='after', **_observation_record(row))
                                  for row in boundary_rows
                              ])
            result.append(action)
            used_result_spans.append((event_first,event_last))
            if event_id is not None:
                used_events.add(event_key)
            break
    return sorted(result, key=lambda action: action['source_timestamp_ms'])


__all__ = ['reconstruct']
