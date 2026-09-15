"""Shared source observations for dated and countdown-based turn advancement."""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from .calendar_coverage import date_key

_PHASE_LABELS = frozenset({"Junior Year Pre-Debut", "Finale Underway"})
_MAX_RESULT_DELAY_MS = 30_000
_MAX_NEXT_DATE_DELAY_MS = 30_000


def _finite_time(value):
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 0)


def _time(row):
    value = row.get('source_timestamp_ms') if isinstance(row, dict) else None
    return value if _finite_time(value) else None


class TimeOrdered(list):
    """Rows sorted by finite ``source_timestamp_ms`` with a bisect index.

    It is the plain sorted list callers already iterate.  ``window(lo, hi)``
    returns the contiguous slice whose timestamps lie in ``[lo, hi]``, in the
    same order, so a loop over the slice visits exactly the rows a full scan
    with that time filter would have kept.
    """

    def __init__(self, rows):
        super().__init__(rows)
        self.times = [_time(row) for row in self]

    def window(self, lo, hi):
        if hi < lo:
            return []
        return self[bisect_left(self.times, lo):bisect_right(self.times, hi)]


def ordered_rows(readings):
    """Sort the finite-timestamp readings once, with a window index."""
    return TimeOrdered(sorted((row for row in readings if isinstance(row, dict) and _time(row) is not None),
                              key=_time))


def _rows_between(rows, lo, hi):
    """The rows a scan limited to ``lo <= time <= hi`` visits, in order.

    An indexed list answers with its window; a plain list is returned whole so
    the caller's own time condition keeps doing the filtering.
    """
    window = getattr(rows, 'window', None)
    return window(lo, hi) if window is not None else rows


def _evidence(row):
    value = row.get('evidence') if isinstance(row, dict) else None
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item]
    return None


def _calendar_text(row):
    stats = row.get('stats') if isinstance(row, dict) else None
    value = stats.get('calendar_text') if isinstance(stats, dict) else None
    return ' '.join(value.split()) if isinstance(value, str) and value.strip() else None


def _same_date_boundary(rows, result_end):
    """Find a repeated, contiguous next date after the result."""
    lookback_start = result_end - _MAX_NEXT_DATE_DELAY_MS
    dated = []
    invalid = []
    for row in _rows_between(rows, lookback_start, result_end):
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
    for row in _rows_between(rows, result_end + 1, result_end + _MAX_NEXT_DATE_DELAY_MS):
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
    for row in _rows_between(rows, lookback_start, result_end):
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
    for row in _rows_between(rows, result_end + 1, result_end + _MAX_NEXT_DATE_DELAY_MS):
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

