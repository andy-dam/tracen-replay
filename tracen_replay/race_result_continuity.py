"""Keep a result panel resumed after a playback dialog in the same race.

Matching rewards alone cannot identify a race. Continuation requires repeated
complete result identities on both sides and an observed, uninterrupted dialog
sequence. The dialog interrupts item visibility even when race identity persists.
"""

from copy import deepcopy


IDENTITY_FIELDS = ('race_name', 'placing', 'fans', 'fans_gained', 'course')
MAX_SOURCE_STEP_MS = 1000
MAX_TRANSITION_MS = 500
MAX_DIALOG_SPAN_MS = 10000


def _identity(rows):
    complete = []
    for row in rows:
        facts = row.get('facts', {})
        if any(facts.get(field) is None for field in IDENTITY_FIELDS):
            continue
        if not isinstance(facts['race_name'], str) or not facts['race_name'].strip():
            return None
        if any(type(facts[field]) is not int for field in ('placing', 'fans', 'fans_gained')):
            return None
        if facts['placing'] < 1 or facts['fans'] < 0 or facts['fans_gained'] < 0:
            return None
        course = facts['course']
        if not isinstance(course, dict) or any(course.get(k) is None for k in
                ('venue', 'surface', 'distance_m', 'distance_category', 'direction')):
            continue
        complete.append(row)
    if len({r['source_timestamp_ms'] for r in complete}) < 2 or len({r['evidence'] for r in complete}) < 2:
        return None
    identity = {field: complete[0]['facts'][field] for field in IDENTITY_FIELDS}
    for row in rows:
        facts = row.get('facts', {})
        if any(facts.get(field) is not None and facts[field] != identity[field]
               for field in IDENTITY_FIELDS if field != 'course'):
            return None
        course = facts.get('course')
        if course is not None and (not isinstance(course, dict) or any(
                value is not None and value != identity['course'].get(key) for key, value in course.items())):
            return None
    conditions = {r['facts']['course_condition'] for r in rows if r.get('facts', {}).get('course_condition')}
    if len(conditions) > 1:
        return None
    if conditions:
        identity['course_condition'] = next(iter(conditions))
    return identity


def _bridge(previous, following, readings):
    before, after = previous['last_seen_ms'], following['first_seen_ms']
    if not 0 < after - before <= MAX_DIALOG_SPAN_MS:
        return None
    identity = _identity(previous['rows'])
    if identity is None or identity != _identity(following['rows']):
        return None
    gap = [r for r in readings if before < r['source_timestamp_ms'] < after]
    if not gap or any(r['screen'] not in ('unknown', 'playback_confirmation') for r in gap):
        return None
    times = sorted({before, after, *(r['source_timestamp_ms'] for r in gap)})
    if any(b - a > MAX_SOURCE_STEP_MS for a, b in zip(times, times[1:])):
        return None
    modal = [r for r in gap if r['screen'] == 'playback_confirmation']
    if len({r['source_timestamp_ms'] for r in modal}) < 2 or len({r['evidence'] for r in modal}) < 2:
        return None
    first = min(r['source_timestamp_ms'] for r in modal)
    last = max(r['source_timestamp_ms'] for r in modal)
    if first - before > MAX_TRANSITION_MS or after - last > MAX_TRANSITION_MS:
        return None
    # An unknown screen inside the confirmed dialog could be playback starting;
    # only short transition frames at its edges are permitted.
    if any(first <= r['source_timestamp_ms'] <= last and r['screen'] != 'playback_confirmation' for r in gap):
        return None
    return dict(
        basis='matching_result_identity_across_observed_playback_dialog',
        previous_result_ms=before, resumed_result_ms=after,
        identity=deepcopy(identity),
        observations=[dict(source_timestamp_ms=r['source_timestamp_ms'],
                           evidence=r['evidence'], screen=r['screen']) for r in gap],
    ), gap


def join_dialog_returns(groups, readings):
    """Join existing raw result groups; never create a race from a dialog."""
    result = []
    for group in groups:
        bridge = _bridge(result[-1], group, readings) if result else None
        if bridge:
            proof, gap = bridge
            previous = result[-1]
            previous.setdefault('result_panel_continuations', []).append(proof)
            previous.setdefault('_visibility_interruptions', []).extend(gap)
            previous['rows'].extend(group['rows'])
            previous['last_seen_ms'] = group['last_seen_ms']
        else:
            result.append(group)
    for index, group in enumerate(result, 1):
        group['id'] = f'race-{index:03d}'
    return result
