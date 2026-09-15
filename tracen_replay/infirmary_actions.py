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
from .turn_boundary import (
    _rows_between, ordered_rows,
    _finite_time, _time, _evidence, _calendar_text, _same_date_boundary,
    _repeated_observation, _observation_record, _phase_turn_boundary,
)


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


def _blocked_between(rows, start, end, *, allow_confirmation=True):
    for row in _rows_between(rows, start, end):
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
    for row in _rows_between(rows, first, last):
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
    for row in _rows_between(rows, confirmation_time-_MAX_RESULT_DELAY_MS, confirmation_time):
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
    for row in _rows_between(rows, confirmation_time, result_end):
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
    ordered = ordered_rows(readings)
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
        candidates = [row for row in _rows_between(ordered, confirmation_time, confirmation_time + _MAX_RESULT_DELAY_MS)
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
