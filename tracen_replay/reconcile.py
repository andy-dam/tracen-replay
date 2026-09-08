"""Arithmetic and evidence accounting, independent of OCR."""

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
                      reading['source_timestamp_ms']-group[-1]['source_timestamp_ms'] > maximum_gap_ms):
            finish()
            group = []
        group.append(reading)
    finish()
    return checkpoints


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


def distinct_changes(events, baseline):
    return reconcile_changes(events, baseline)['events']


def reconcile_changes(events, baseline):
    """Conservatively merge partial views; preserve raw candidates elsewhere.

    Subset matching is an ambiguity policy, not proof that two events are equal.
    """
    def subset(a, b):
        return all(b.get(k) == v for k,v in a.items())
    kept, decisions = [], []
    for index, event in enumerate(events):
        if any(subset(event['deltas'], b['deltas']) for b in baseline):
            decisions.append(dict(candidate_index=index, decision='exclude_baseline_match', identity_verified=False))
            continue
        if any(subset(event['deltas'], k[1]['deltas']) for k in kept):
            decisions.append(dict(candidate_index=index, decision='merge_partial_or_identical', identity_verified=False))
            continue
        for old_index, old in kept:
            if subset(old['deltas'],event['deltas']):
                decisions.append(dict(candidate_index=old_index, decision='replace_with_fuller_block', replacement_index=index, identity_verified=False))
        kept = [k for k in kept if not subset(k[1]['deltas'], event['deltas'])]
        kept.append((index,event))
    return dict(events=[e for _,e in kept], decisions=decisions, identity_verified=False)


def preview_segments(readings, maximum_gap_ms=500):
    """Observed option browsing only; even the last preview is not a selection."""
    segments, current = [], None
    for reading in readings:
        if not reading.get('training_preview'):
            current = None
            continue
        option, timestamp = reading.get('preview_option'), reading['source_timestamp_ms']
        if current is None or current['option'] != option or timestamp-current['last_seen_ms']>maximum_gap_ms:
            current = dict(option=option, first_seen_ms=timestamp, last_seen_ms=timestamp,
                           evidence=reading['evidence'], supporting_frames=[],
                           kind='training_preview', completed_action=None)
            segments.append(current)
        current['last_seen_ms'] = timestamp
        current['supporting_frames'].append(reading['evidence'])
    return segments
