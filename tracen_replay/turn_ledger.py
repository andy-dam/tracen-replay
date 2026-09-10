"""Join observed turns and evidence without inventing boundary states or awards.

Times describe observations. A calendar transition is not a proven click time,
and a resource comparison may span several turns. Existing report collections
remain authoritative; JSON pointers identify them without duplicating charges.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from .calendar_coverage import date_key
from .reconcile import FIELDS


SCHEMA = 'tracen-replay/turn-ledger-v1'
PERFORMANCE_FIELDS = ('dance', 'passion', 'vocal', 'visual', 'composure')
TRANSACTIONS = ('lesson_purchases', 'skill_purchases', 'races', 'concerts', 'song_acquisitions')


def _time(value, duration, label):
    if type(value) is not int or not 0 <= value <= duration:
        raise ValueError(f'{label}: expected a source timestamp within the recording')
    return value


def _proof(value):
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and all(isinstance(p, str) and p for p in value):
        return list(dict.fromkeys(value))
    return []


def _ref(collection, index):
    return f'/gameplay_tracking/{collection}/{index}'


def _changes(value, fields, label, *, complete=False):
    if not isinstance(value, dict) or any(k not in fields or type(v) is not int for k, v in value.items()):
        raise ValueError(f'{label}: expected integer resource changes')
    if complete and set(value) != set(fields):
        raise ValueError(f'{label}: incomplete resource fields')
    return deepcopy(value)


def _calendar_identity(row):
    stats = row.get('stats', {})
    text = stats.get('calendar_text')
    ordinal = date_key(text)
    if ordinal is not None:
        return ('dated', ordinal), text
    remaining = stats.get('turns_remaining_to_goal')
    if text in ('Junior Year Pre-Debut', 'Finale Underway'):
        if type(remaining) is int and remaining >= 0:
            return (text, remaining), f'{text} · {remaining} turns to goal'
        return (text, None), text
    return None, None


def _windows(readings, duration):
    """Use successive repeated calendar/countdown observations, not action count."""
    groups = []
    for row in readings:
        key, label = _calendar_identity(row)
        if key is None:
            continue
        # A newly observed phase closes the previous dated turn even before
        # its countdown is readable. Missing countdown text within an already
        # numbered phase is not another turn or a reset.
        if key[1] is None and groups and groups[-1]['key'][0] == key[0] and groups[-1]['key'][1] is not None:
            continue
        if not groups or groups[-1]['key'] != key:
            groups.append(dict(key=key, label=label, rows=[]))
        groups[-1]['rows'].append(row)
    confirmed, rejected = [], []
    for group in groups:
        rows = group['rows']
        if len({r['source_timestamp_ms'] for r in rows}) < 2:
            rejected.append(dict(label=group['label'], first_seen_ms=rows[0]['source_timestamp_ms'],
                                 reason='single_calendar_observation', evidence=_proof(rows[0].get('evidence'))))
        else:
            confirmed.append(group)
    windows = []
    for issue in rejected:
        issue['start_ms'] = issue['first_seen_ms']
        issue['end_ms'] = next((g['rows'][0]['source_timestamp_ms'] for g in confirmed
                                if g['rows'][0]['source_timestamp_ms'] > issue['start_ms']), duration)
    for index, group in enumerate(confirmed):
        start = group['rows'][0]['source_timestamp_ms']
        end = confirmed[index + 1]['rows'][0]['source_timestamp_ms'] if index + 1 < len(confirmed) else duration
        if end <= start:
            raise ValueError('Calendar windows overlap or have zero duration')
        previous = confirmed[index - 1]['key'] if index else None
        key = group['key']
        issues = []
        if previous and key[0] == previous[0] and previous[1] is not None and key[1] is not None:
            expected = previous[1] + (1 if key[0] == 'dated' else -1)
            if key[1] != expected:
                issues.append('calendar_gap_or_reset')
                gap_start = confirmed[index - 1]['rows'][-1]['source_timestamp_ms'] + 1
                if gap_start < start:
                    rejected.append(dict(label='Unobserved calendar/countdown transition',
                        first_seen_ms=gap_start, start_ms=gap_start, end_ms=start,
                        reason='calendar_gap_or_reset',
                        evidence=_proof(confirmed[index - 1]['rows'][-1].get('evidence')) +
                                 _proof(group['rows'][0].get('evidence'))))
        if any(start <= r['first_seen_ms'] < end for r in rejected):
            issues.append('unconfirmed_calendar_transition')
        if index + 1 == len(confirmed):
            issues.append('next_turn_boundary_unobserved')
        windows.append(dict(id=f'turn-{index + 1:03d}', label=group['label'],
                            phase=key[0], calendar_value=key[1], start_ms=start, end_ms=end,
                            window_kind='calendar_turn' if key[0] == 'dated' else
                            'countdown_segment' if key[1] is not None else 'unresolved_phase',
                            end_exclusive=index + 1 < len(confirmed),
                            boundary_basis='repeated_calendar_observations',
                            evidence=list(dict.fromkeys(p for r in group['rows'][:2] for p in _proof(r.get('evidence')))),
                            issues=issues, timeline_refs=[], ambiguous_timeline_refs=[], comparisons=dict(stats=[], performance=[])))
    return windows, rejected


def _contains(turn, time):
    return turn['start_ms'] <= time < turn['end_ms'] or (
        not turn['end_exclusive'] and time == turn['end_ms'])


def _overlap(turn, start, end):
    return start < turn['end_ms'] and end >= turn['start_ms'] or start == end and _contains(turn, start)


def build(report):
    """Return a reproducible ledger projection; never modify the input report."""
    duration = report['source']['duration_ms']
    if type(duration) is not int or duration <= 0:
        raise ValueError('source.duration_ms: expected a positive integer')
    data = report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:
        raise ValueError('Turn ledger requires gameplay-only observations')
    readings = data['readings']
    previous = -1
    evidence_times = {}
    evidence_rows = {}
    for row in readings:
        time = _time(row.get('source_timestamp_ms'), duration, 'reading')
        if time < previous:
            raise ValueError('Readings must be in source order')
        previous = time
        for proof in _proof(row.get('evidence')):
            if proof in evidence_times and evidence_times[proof] != time:
                raise ValueError('One evidence path cannot identify different source times')
            evidence_times[proof] = time
            evidence_rows[proof] = row
    turns, calendar_issues = _windows(readings, duration)
    timeline = []

    def uncertain(start, end):
        return [i for i in calendar_issues if start < i['end_ms'] and end >= i['start_ms']
                or start == end == duration == i['end_ms']]

    def add(source_ref, kind, start, end, evidence, **details):
        start = _time(start, duration, source_ref)
        end = _time(end, duration, source_ref)
        if end < start:
            raise ValueError(f'{source_ref}: reversed observation bounds')
        overlaps = [t['id'] for t in turns if _overlap(t, start, end)]
        owner = next((t for t in turns if _contains(t, start)), None)
        uncertainty = uncertain(start, end)
        # An observation spanning a calendar transition has no proven single owner.
        assigned = owner if len(overlaps) == 1 and not uncertainty else None
        entry = dict(source_ref=source_ref, kind=kind, first_seen_ms=start, last_seen_ms=end,
                     evidence=_proof(evidence), turn_id=assigned['id'] if assigned else None,
                     candidate_turn_ids=overlaps, unconfirmed_calendar_labels=[i['label'] for i in uncertainty],
                     assignment_basis='unconfirmed_calendar_transition' if uncertainty else
                     'observed_within_calendar_window' if assigned else
                     'crosses_calendar_boundary' if len(overlaps) > 1 else 'outside_observed_turns', **details)
        entry['id'] = f'entry-{len(timeline) + 1:04d}'
        timeline.append(entry)
        if assigned:
            assigned['timeline_refs'].append(entry['id'])
        else:
            for turn in turns:
                if turn['id'] in overlaps:
                    turn['ambiguous_timeline_refs'].append(entry['id'])
        return entry

    event_ids = {}
    for index, event in enumerate(data['events']):
        identity = event.get('id')
        if not isinstance(identity, str) or not identity or identity in event_ids:
            raise ValueError('Event IDs must be unique nonempty strings')
        event_ids[identity] = _ref('events', index)
        start, end = event['first_seen_ms'], event['last_seen_ms']
        add(_ref('events', index), event['kind'], start, end, event.get('evidence'),
            event_id=identity, context_title=event.get('context_title'),
            stat_changes=_changes(event.get('deltas', {}), FIELDS, identity),
            performance_changes=_changes(event.get('performance_deltas', {}), PERFORMANCE_FIELDS, identity),
            field_evidence=deepcopy(event.get('field_evidence', {})),
            changes_are_additional_to_effects=False,
            conflicts_present=bool(event.get('conflicting_readings') or event.get('ambiguous_effect_candidates')))
        for effect_index, effect in enumerate(event.get('effects', [])):
            # Hint awards need their own entries; numeric effects remain on the
            # event reference so consumers cannot sum both forms of one award.
            if effect.get('kind') != 'skill_hint_change':
                continue
            key = '|'.join(str(effect.get(f) or '') for f in ('kind', 'field', 'name'))
            proofs = event.get('field_evidence', {}).get(key, [])
            times = [evidence_times[p] for p in proofs if p in evidence_times and start <= evidence_times[p] <= end]
            first = min(times) if times else start
            add(f'{_ref("events", index)}/effects/{effect_index}', 'skill_hint_change',
                first, first if times else end, proofs,
                event_id=identity, effect=deepcopy(effect),
                timing_basis='first_linked_effect_observation' if times else 'event_window')
        for candidate_index, candidate in enumerate(event.get('ambiguous_effect_candidates', [])):
            # Retain uncertain identities as visible alternatives, never as
            # additional accepted awards or as silently missing timeline data.
            proofs = _proof(candidate.get('evidence'))
            times = [evidence_times[p] for p in proofs if p in evidence_times and start <= evidence_times[p] <= end]
            add(f'{_ref("events", index)}/ambiguous_effect_candidates/{candidate_index}', 'ambiguous_effect',
                min(times) if times else start, max(times) if times else end, proofs,
                event_id=identity, candidate=deepcopy(candidate), accepted_award=False,
                occurrence_count=None,
                timing_basis='linked_candidate_observations' if times else 'event_window')
    for index, action in enumerate(data['turn_action_receipts']):
        if action.get('kind') not in ('training', 'race', 'rest', 'outing', 'infirmary'):
            raise ValueError('Unknown committed action kind')
        event_id = action.get('event_id')
        if event_id is not None and event_id not in event_ids:
            raise ValueError(f'Action refers to missing event {event_id}')
        time = action['source_timestamp_ms']
        add(_ref('turn_action_receipts', index), 'committed_action', time, time, action.get('evidence'),
            action_kind=action['kind'], training_option=action.get('training_option'),
            event_ref=event_ids.get(event_id), reward_link_status='linked_event' if event_id else 'separate_receipt',
            click_timestamp_ms=action.get('click_timestamp_ms'))
    for collection in TRANSACTIONS:
        for index, transaction in enumerate(data.get(collection, [])):
            start = transaction.get('source_timestamp_ms', transaction.get('first_seen_ms'))
            end = transaction.get('last_seen_ms', start)
            add(_ref(collection, index), collection, start, end, transaction.get('evidence'),
                transaction_id=transaction.get('id'), event_id=transaction.get('receipt_event_id', transaction.get('event_id')),
                accounting_role='reference_only_not_an_additional_award')
    for index, candidate in enumerate(data.get('unparsed_receipt_candidates', [])):
        add(_ref('unparsed_receipt_candidates', index), 'unparsed_receipt',
            candidate['first_seen_ms'], candidate['last_seen_ms'], candidate.get('evidence'),
            raw_text=candidate.get('raw_text'), accepted_award=False)

    comparisons = dict(stats=[], performance=[])
    for channel, checkpoints, intervals, fields, prefix in (
        ('stats', data['checkpoints'], data['intervals'], FIELDS, 'checkpoints'),
        ('performance', data['performance_accounting']['checkpoints'],
         data['performance_accounting']['intervals'], PERFORMANCE_FIELDS, 'performance_accounting/checkpoints'),
    ):
        for index, checkpoint in enumerate(checkpoints):
            _time(checkpoint['first_seen_ms'], duration, 'checkpoint')
            _time(checkpoint['last_seen_ms'], duration, 'checkpoint')
            if checkpoint['last_seen_ms'] < checkpoint['first_seen_ms']:
                raise ValueError('Checkpoint bounds reversed')
            if any(type(checkpoint.get('values', {}).get(field)) is not int for field in fields):
                raise ValueError(f'{channel} checkpoint has incomplete numeric values')
        checkpoint_ids = {c.get('id'): i for i, c in enumerate(checkpoints)} if channel == 'stats' else {}
        if channel == 'stats' and (None in checkpoint_ids or len(checkpoint_ids) != len(checkpoints)):
            raise ValueError('Stat checkpoint IDs must be present and unique')
        for index, interval in enumerate(intervals):
            start = _time(interval['start_ms'], duration, 'interval')
            end = _time(interval['end_ms'], duration, 'interval')
            if end < start:
                raise ValueError('Interval bounds reversed')
            owners = [t for t in turns if _overlap(t, start, end)]
            path = _ref('intervals' if channel == 'stats' else 'performance_accounting/intervals', index)
            pairs = [(i, i + 1) for i in range(len(checkpoints) - 1)
                     if checkpoints[i]['last_seen_ms'] == start and checkpoints[i + 1]['first_seen_ms'] == end]
            if channel == 'stats' and 'before_id' in interval:
                pairs = [p for p in pairs if checkpoints[p[0]]['id'] == interval['before_id']
                         and checkpoints[p[1]]['id'] == interval.get('after_id')]
            if len(pairs) != 1:
                raise ValueError(f'{path}: comparison does not link one adjacent checkpoint pair')
            before, after = pairs[0]
            observed = _changes(interval['observed_change'], fields, path, complete=True)
            supported = _changes(interval['supported_change'], fields, path, complete=True)
            residual = _changes(interval['unexplained_change'], fields, path, complete=True)
            if any(observed[f] != checkpoints[after]['values'][f] - checkpoints[before]['values'][f]
                   or residual[f] != observed[f] - supported[f] for f in fields):
                raise ValueError(f'{path}: resource comparison contradicts its states or changes')
            related_events = []
            for event in interval.get('events', []):
                if event.get('id') not in event_ids:
                    raise ValueError(f'{path}: missing supporting event')
                related_events.append(event_ids[event['id']])
            comparison = dict(source_ref=path, start_ms=start, end_ms=end, turn_ids=[t['id'] for t in owners],
                              unconfirmed_calendar_labels=[i['label'] for i in uncertain(start, end)],
                              turn_assignment_complete=bool(owners) and start >= turns[0]['start_ms']
                              and end <= turns[-1]['end_ms'] and not uncertain(start, end),
                              before_state_ref=_ref(prefix, before), after_state_ref=_ref(prefix, after),
                              spans_multiple_turns=len(owners) > 1,
                              observed_change=observed, supported_change=supported, unexplained_change=residual,
                              event_refs=list(dict.fromkeys(related_events)),
                              transaction_refs=[f'{path}/transactions/{i}' for i in range(len(interval.get('transactions', [])))],
                              status=interval['status'], attribution_verified=interval.get('event_assignment_verified') is True,
                              complete_event_history=interval.get('complete_event_history') is True)
            # Keep comparisons intact across turns. Never divide or allocate a
            # residual to a turn just because its action happened in this span.
            comparisons[channel].append(comparison)
            for turn in owners:
                turn['comparisons'][channel].append(path)
        for turn_index, turn in enumerate(turns):
            action_times = [e['first_seen_ms'] for e in timeline if e['turn_id'] == turn['id'] and e['kind'] == 'committed_action']
            action_time = min(action_times, default=turn['end_ms'])
            candidates = [(i, c, c['first_seen_ms'], c.get('evidence')) for i, c in enumerate(checkpoints)
                          if turn['start_ms'] <= c['first_seen_ms'] < action_time and
                          not uncertain(c['first_seen_ms'], c['first_seen_ms'])]
            if channel == 'stats':
                for i, c in enumerate(checkpoints):
                    for proof in c.get('supporting_frames', []):
                        row = evidence_rows.get(proof)
                        if row is not None and row.get('stats', {}).get('values') == c['values']:
                            time = row['source_timestamp_ms']
                            if (turn['start_ms'] <= time < action_time and c['first_seen_ms'] <= time <= c['last_seen_ms']
                                    and not uncertain(time, time)):
                                candidates.append((i, c, time, proof))
            chosen = min(candidates, key=lambda p: p[2]) if candidates else None
            opening = None if chosen is None else dict(source_ref=_ref(prefix, chosen[0]),
                observed_at_ms=chosen[2], values=deepcopy(chosen[1]['values']),
                evidence=_proof(chosen[3]), basis='first_observed_state_before_action',
                exact_turn_boundary=chosen[2] == turn['start_ms'])
            turn.setdefault('states', {})[channel] = dict(opening=opening, closing=None,
                opening_status='observed' if opening else 'not_observed_before_action')
        for index, turn in enumerate(turns[:-1]):
            following = turns[index + 1]['states'][channel]['opening']
            turn['states'][channel]['closing'] = deepcopy(following)
            turn['states'][channel]['closing_basis'] = 'next_turn_first_observed_state' if following else 'unavailable'
            # Events before the next first state are still shown on that next
            # turn. This link is a later observation, not an invented end value.
        if turns:
            last = turns[-1]
            action_time = max((e['first_seen_ms'] for e in timeline if e['turn_id'] == last['id']
                               and e['kind'] == 'committed_action'), default=last['start_ms'])
            candidates = [(i, c) for i, c in enumerate(checkpoints)
                          if action_time <= c['first_seen_ms'] <= duration
                          and not uncertain(c['first_seen_ms'], c['first_seen_ms'])]
            if candidates:
                i, checkpoint = max(candidates, key=lambda p: p[1]['first_seen_ms'])
                last['states'][channel]['closing'] = dict(source_ref=_ref(prefix, i),
                    observed_at_ms=checkpoint['first_seen_ms'], values=deepcopy(checkpoint['values']),
                    evidence=_proof(checkpoint.get('evidence')), basis='last_observed_state_after_action',
                    exact_turn_boundary=False)
                last['states'][channel]['closing_basis'] = 'last_observed_state_not_inferred_completion'
            else:
                last['states'][channel]['closing_basis'] = 'unavailable'
    for turn in turns:
        actions = [e for e in timeline if e['turn_id'] == turn['id'] and e['kind'] == 'committed_action']
        turn['action_status'] = 'one_action' if len(actions) == 1 else 'missing_action' if not actions else 'multiple_actions'
        turn['action_count'] = len(actions)
        turn['expects_one_action'] = turn['window_kind'] == 'calendar_turn'
        turn['complete_event_history'] = False
    return dict(schema_version=SCHEMA, source_sha256=report['source']['sha256'], source_duration_ms=duration,
                turns=turns, timeline=sorted(timeline, key=lambda e: (e['first_seen_ms'], e['id'])),
                comparisons=comparisons, calendar_issues=calendar_issues,
                unassigned_entry_refs=[e['id'] for e in timeline if e['turn_id'] is None],
                summary=dict(observed_turn_windows=len(turns), action_statuses=dict(Counter(t['action_status'] for t in turns)),
                             unassigned_entries=sum(e['turn_id'] is None for e in timeline),
                             fully_verified=False),
                limitations=['Calendar windows identify observed turn labels, not exact action times.',
                             'First observed state may follow unobserved changes at the start of a turn.',
                             'Shared resource comparisons must be counted once by source_ref, not once per turn.',
                             'Timeline proximity does not prove an event was caused by the committed action.',
                             'Missing, ambiguous and unparsed effects are not converted into zero changes.'])
