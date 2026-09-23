"""Arithmetic and evidence accounting, independent of OCR."""
from .source_clock import elapsed

FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points')


def stable_checkpoints(readings, minimum_samples=3, maximum_gap_ms=500):
    checkpoints, group = [], []

    def finish():
        if len(group) < minimum_samples or group[-1]['source_timestamp_ms']-group[0]['source_timestamp_ms'] < 500:
            return
        first, last = group[0], group[-1]
        checkpoint = dict(id=f'checkpoint-{len(checkpoints)+1:03d}',
                          first_seen_ms=first['source_timestamp_ms'], last_seen_ms=last['source_timestamp_ms'],
                          values=first['values'], evidence=first['evidence'],
                          supporting_frames=[r['evidence'] for r in group],
                          status='ocr_consensus', human_verified=False,
                          calendar_text=first.get('calendar_text'),
                          turns_remaining_to_goal=first.get('turns_remaining_to_goal'))
        # Matching values across an unreadable interval do not prove continuity:
        # a turn or offsetting changes may have occurred while totals were hidden.
        checkpoints.append(checkpoint)

    for reading in readings:
        valid = all(type((reading.get('values') or {}).get(f)) is int for f in FIELDS)
        if not valid:
            finish()
            group = []
            continue
        if group and (reading['values'] != group[-1]['values'] or
                      reading.get('turns_remaining_to_goal') != group[-1].get('turns_remaining_to_goal') or
                      reading.get('calendar_text') != group[-1].get('calendar_text') or
                      elapsed(group[-1]['source_timestamp_ms'], reading['source_timestamp_ms']) > maximum_gap_ms):
            finish()
            group = []
        group.append(reading)
    finish()
    return _without_transient_misreads(checkpoints)


# A stat never moves by this much between two consecutive stable states and
# back again; a checkpoint that does is a misread the frames repeated.
TRANSIENT_STEP = 100


def _without_transient_misreads(checkpoints):
    """Drop a checkpoint that contradicts both of its neighbours on a field.

    Values can be hidden or cut for several frames (a cursor over the first
    digit reads 876 as 76) and still form a repeated group. When the
    checkpoints before and after agree within a plausible step and this one
    is far from both, it is the misread, not the stats.
    """
    kept = []
    for index, checkpoint in enumerate(checkpoints):
        previous = kept[-1] if kept else None
        following = checkpoints[index + 1] if index + 1 < len(checkpoints) else None
        if previous is not None and following is not None:
            transient = False
            for field in FIELDS:
                a, b, c = previous['values'].get(field), checkpoint['values'].get(field), following['values'].get(field)
                if not all(type(v) is int for v in (a, b, c)):
                    continue
                if abs(b - a) > TRANSIENT_STEP and abs(b - c) > TRANSIENT_STEP and abs(c - a) <= TRANSIENT_STEP:
                    transient = True
                    break
            if transient:
                continue
        kept.append(checkpoint)
    for index, checkpoint in enumerate(kept, 1):
        checkpoint['id'] = f'checkpoint-{index:03d}'
    return kept


def account(before, after, events):
    observed = {f: after['values'][f]-before['values'][f] for f in FIELDS}
    supported = {f: sum(e['deltas'].get(f, 0) for e in events) for f in FIELDS}
    residual = {f: observed[f]-supported[f] for f in FIELDS}
    return dict(before_id=before['id'], after_id=after['id'],
                start_ms=before['last_seen_ms'], end_ms=after['first_seen_ms'],
                observed_change=observed, supported_change=supported, unexplained_change=residual,
                status='balanced' if not any(residual.values()) else 'unresolved',
                events=events, complete_event_history=False,
                turn_boundary_verified=False, event_assignment_verified=False)


def preview_segments(readings, maximum_gap_ms=500):
    """Observed option browsing only; even the last preview is not a selection."""
    segments, current = [], None
    for reading in readings:
        if not reading.get('training_preview'):
            current = None
            continue
        option, timestamp = reading.get('preview_option'), reading['source_timestamp_ms']
        if current is None or current['option'] != option or elapsed(current['last_seen_ms'], timestamp)>maximum_gap_ms:
            current = dict(option=option, first_seen_ms=timestamp, last_seen_ms=timestamp,
                           evidence=reading['evidence'], supporting_frames=[],
                           kind='training_preview', completed_action=None)
            segments.append(current)
        current['last_seen_ms'] = timestamp
        current['supporting_frames'].append(reading['evidence'])
    return segments
