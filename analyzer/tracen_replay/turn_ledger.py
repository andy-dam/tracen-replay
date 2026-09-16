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


# Some entries share an instant because one screen is the only evidence for
# both: a training result and the decision read off it, a purchase and the
# receipt that announced it. Ordering those by the id they happened to be
# built with is arbitrary, so equal timestamps fall back to the part each
# plays: the decision, then what it produced, then the records that refer to
# it, then what followed. This orders entries the recording cannot separate;
# it never claims one was observed before the other, and it never reorders
# entries whose timestamps differ.
_ENTRY_ROLE = {'committed_action': 0,
               'training': 1, 'races': 1, 'concerts': 1,
               'lesson_purchases': 2, 'skill_purchases': 2, 'skill_purchase_batch': 2,
               'song_acquisitions': 2,
               'outcome': 3}


def _timeline_order(entry):
    return (entry['first_seen_ms'], _ENTRY_ROLE.get(entry['kind'], 4), entry['id'])


def _changes(value, fields, label, *, complete=False):
    if not isinstance(value, dict) or any(k not in fields or type(v) is not int for k, v in value.items()):
        raise ValueError(f'{label}: expected integer resource changes')
    if complete and set(value) != set(fields):
        raise ValueError(f'{label}: incomplete resource fields')
    return deepcopy(value)


def _repeated_source_state(readings, fields, channel, start_ms, action_time, uncertain):
    """Find one repeated complete source reading before the committed action.

    A repeated reading is evidence of an observed state, not a new accounting
    checkpoint.  Requiring distinct source timestamps and one exact value tuple
    keeps singleton, duplicate-PTS and conflicting observations unresolved.
    """
    groups = {}
    for index, row in enumerate(readings):
        time = row.get('source_timestamp_ms')
        if type(time) is not int or not start_ms <= time < action_time or uncertain(time, time):
            continue
        stats = row.get('stats')
        if not isinstance(stats, dict):
            continue
        if channel == 'stats':
            values = stats.get('values')
            source_ref = f'/gameplay_tracking/readings/{index}/stats'
            values_ref = f'{source_ref}/values'
        else:
            facts = row.get('facts')
            values = facts.get('performance_points') if isinstance(facts, dict) else None
            source_ref = f'/gameplay_tracking/readings/{index}/facts'
            values_ref = f'{source_ref}/performance_points'
        if not isinstance(values, dict) or any(type(values.get(field)) is not int for field in fields):
            continue
        evidence = _proof(row.get('evidence'))
        if not evidence:
            continue
        key = tuple(values[field] for field in fields)
        groups.setdefault(key, []).append(dict(index=index, time=time, values=deepcopy(values),
                                               evidence=evidence, source_ref=source_ref,
                                               values_ref=values_ref))
    if len(groups) != 1:
        return None
    observations = next(iter(groups.values()))
    if len({item['time'] for item in observations}) < 2:
        return None
    observations.sort(key=lambda item: (item['time'], item['index']))
    first = observations[0]
    return dict(source_ref=first['source_ref'], values_ref=first['values_ref'],
                supporting_source_refs=[item['source_ref'] for item in observations],
                observed_at_ms=first['time'], values=first['values'],
                evidence=first['evidence'], basis='first_repeated_source_state_before_action',
                exact_turn_boundary=first['time'] == start_ms)


def _corroborated_source_state(readings, fields, channel, start_ms, action_time, uncertain, timeline, *, partial=False):
    """Corroborate one complete source snapshot with nearby partial readings.

    Every value still comes from the same actual snapshot. Each field must
    repeat at another distinct source timestamp within one second of it.
    Conflicting values, intervening events and calendar uncertainty prevent
    this fallback; a tuple assembled from different frames is never emitted.
    """
    observations = []
    for index, row in enumerate(readings):
        time = row.get('source_timestamp_ms')
        if type(time) is not int or not start_ms <= time < action_time:
            continue
        if channel == 'stats':
            values = row.get('stats', {}).get('values')
            source_ref = f'/gameplay_tracking/readings/{index}/stats'
            values_ref = source_ref+'/values'
        else:
            values = row.get('facts', {}).get('performance_points')
            source_ref = f'/gameplay_tracking/readings/{index}/facts'
            values_ref = source_ref+'/performance_points'
        if not isinstance(values, dict) or not _proof(row.get('evidence')):
            continue
        if any(value is not None and type(value) is not int for field, value in values.items() if field in fields):
            continue
        observations.append(dict(time=time, values=values, source_ref=source_ref,
                                 values_ref=values_ref, evidence=_proof(row['evidence'])))
    candidates=sorted(observations,key=lambda r:(-sum(type(r['values'].get(f)) is int for f in fields),r['time'])) if partial else observations
    for snapshot in candidates:
        observed_fields=[f for f in fields if type(snapshot['values'].get(f)) is int]
        if (not observed_fields or (not partial and len(observed_fields)!=len(fields))
            or (partial and len(observed_fields)==len(fields))):
            continue
        witnesses = [r for r in observations if abs(r['time']-snapshot['time']) <= 1000]
        first, last = min(r['time'] for r in witnesses), max(r['time'] for r in witnesses)
        if uncertain(first, last) or any(e['first_seen_ms'] <= last and e['last_seen_ms'] >= first for e in timeline):
            continue
        evidence = {}
        for field in observed_fields:
            seen = [r for r in witnesses if type(r['values'].get(field)) is int]
            if (len({r['time'] for r in seen}) < 2
                    or any(r['values'][field] != snapshot['values'][field] for r in seen)):
                break
            evidence[field] = [dict(value_ref=r['values_ref']+'/'+field,
                                    observed_at_ms=r['time'], evidence=r['evidence']) for r in seen]
        if len(evidence) != len(observed_fields):
            continue
        return dict(source_ref=snapshot['source_ref'], values_ref=snapshot['values_ref'],
                    supporting_source_refs=[r['source_ref'] for r in witnesses],
                    field_corroboration=evidence, observed_at_ms=snapshot['time'],
                    values=deepcopy(snapshot['values']), evidence=snapshot['evidence'],
                    basis=('partial' if partial else 'complete')+'_source_snapshot_with_repeated_field_corroboration',
                    exact_turn_boundary=snapshot['time'] == start_ms)
    return None


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


_FINALE = 'Finale Underway'
# Screens that show a turn was played (a training menu or result); the
# screens after the Finals carry the finale label but none of these.
_ACTION_SCREENS = frozenset({'training_preview', 'training_result', 'training_result_candidate'})


def _race_name(row):
    facts = row.get('facts') if isinstance(row, dict) else None
    name = facts.get('race_name') if isinstance(facts, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _windows(readings, duration, action_times=()):
    """Use successive repeated calendar/countdown observations, not action count.

    The finale keeps one calendar label and one countdown for its three turns
    (train, then the Qualifier; train, then the Semifinal; train, then the
    Finals), so there the finale race ends the turn: the next home observation
    after a finale race result opens the following turn. The screens after the
    Finals still carry the label but offer no training menu; they stay in the
    last turn's window instead of becoming a turn of their own.
    """
    groups = []
    pending_race = None
    for row in readings:
        if row.get('screen') == 'race_result' and groups and groups[-1]['key'][0] == _FINALE:
            if pending_race is None and _race_name(row):
                pending_race = row
        key, label = _calendar_identity(row)
        if key is None:
            continue
        if (key[0] == _FINALE and groups and groups[-1]['key'][0] == _FINALE
                and (pending_race is not None or groups[-1].get('race_advance') is not None)):
            # From the first finale race on, the calendar text and countdown
            # no longer separate turns; the race advance does.
            if pending_race is not None:
                groups.append(dict(key=(_FINALE, None, len(groups)), label=label, rows=[], race_advance=pending_race))
                pending_race = None
            groups[-1]['rows'].append(row)
            continue
        # A newly observed phase closes the previous dated turn even before
        # its countdown is readable. Missing countdown text within an already
        # numbered phase is not another turn or a reset.
        if key[1] is None and groups and groups[-1]['key'][0] == key[0] and groups[-1]['key'][1] is not None:
            continue
        if not groups or groups[-1]['key'] != key:
            groups.append(dict(key=key, label=label, rows=[]))
        groups[-1]['rows'].append(row)
    merged = []
    for group in groups:
        acted = any(r.get('completed_action') or r.get('screen') in _ACTION_SCREENS for r in group['rows'])
        if group.get('race_advance') is not None and merged and not acted:
            merged[-1]['rows'].extend(group['rows'])
            continue
        merged.append(group)
    groups = merged
    race_rows = [r for r in readings if r.get('screen') == 'race_result' and _race_name(r)]
    confirmed, rejected = [], []
    for group in groups:
        rows = group['rows']
        if len({r['source_timestamp_ms'] for r in rows}) < 2:
            rejected.append(dict(label=group['label'], first_seen_ms=rows[0]['source_timestamp_ms'],
                                 reason='single_calendar_observation', evidence=_proof(rows[0].get('evidence'))))
        else:
            confirmed.append(group)
    # A countdown or date read for a moment that contradicts both of its
    # neighbours (11, then 0, then 10) is the transition animation misread,
    # not a window of its own. It stays visible as noise; the neighbours it
    # separated are joined again when they carry the same value.
    kept = []
    for index, group in enumerate(confirmed):
        key = group['key']
        prev_key = kept[-1]['key'] if kept else None
        next_key = confirmed[index + 1]['key'] if index + 1 < len(confirmed) else None
        if (key[1] is not None and prev_key and next_key and prev_key[0] == key[0] == next_key[0]
                and prev_key[1] is not None and next_key[1] is not None):
            low, high = sorted((prev_key[1], next_key[1]))
            if not low <= key[1] <= high:
                rows = group['rows']
                rejected.append(dict(label=group['label'], first_seen_ms=rows[0]['source_timestamp_ms'],
                                     start_ms=rows[0]['source_timestamp_ms'],
                                     end_ms=confirmed[index + 1]['rows'][0]['source_timestamp_ms'],
                                     reason='calendar_transient_misread',
                                     evidence=_proof(rows[0].get('evidence')) + _proof(rows[-1].get('evidence'))))
                continue
        if kept and kept[-1]['key'] == key and kept[-1].get('race_advance') is None and group.get('race_advance') is None:
            kept[-1]['rows'].extend(group['rows'])
            continue
        kept.append(group)
    # A phase read before its countdown (the first Pre-Debut frames, before
    # "11 turns to goal" is legible) is the start of that first countdown
    # turn, not a turn of its own, as long as nothing was played in it.
    # A phase window that holds an action stays separate.
    folded = []
    for index, group in enumerate(kept):
        key = group['key']
        following = kept[index + 1] if index + 1 < len(kept) else None
        if (key[1] is None and group.get('race_advance') is None and following is not None
                and following['key'][0] == key[0] and following['key'][1] is not None
                and following.get('race_advance') is None):
            start = group['rows'][0]['source_timestamp_ms']
            end = following['rows'][0]['source_timestamp_ms']
            acted = (any(r.get('completed_action') or r.get('screen') in _ACTION_SCREENS for r in group['rows'])
                     or any(start <= t < end for t in action_times))
            if not acted:
                following['rows'] = group['rows'] + following['rows']
                continue
        folded.append(group)
    confirmed = folded
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
        label = group['label']
        kind = ('calendar_turn' if key[0] == 'dated' else
                'countdown_segment' if key[1] is not None else 'unresolved_phase')
        advance = group.get('race_advance')
        evidence_rows = ([advance] if advance is not None else []) + group['rows'][:2]
        window = dict(id=f'turn-{index + 1:03d}', label=label,
                      phase=key[0], calendar_value=key[1], start_ms=start, end_ms=end,
                      window_kind=kind, end_exclusive=index + 1 < len(confirmed),
                      boundary_basis='finale_race_advance' if advance is not None else 'repeated_calendar_observations',
                      evidence=list(dict.fromkeys(p for r in evidence_rows for p in _proof(r.get('evidence')))),
                      issues=issues, timeline_refs=[], ambiguous_timeline_refs=[], comparisons=dict(stats=[], performance=[]))
        if key[0] == _FINALE:
            # A finale turn is named by the race that ends it; its training is
            # the decision, the race is scheduled by the game.
            scheduled = next((_race_name(r) for r in race_rows if start <= r['source_timestamp_ms'] < end), None)
            if scheduled:
                window.update(label=f'{_FINALE} · {scheduled}', window_kind='phase_race_turn', scheduled_race=scheduled)
        elif kind == 'countdown_segment' and key[1] == 1:
            # The last countdown turn ends with the goal race. Whether the
            # player's decision shares this window with the race depends on
            # when the countdown was confirmed, so the race is only marked as
            # scheduled once the actions are known (below).
            scheduled = next((_race_name(r) for r in race_rows if start <= r['source_timestamp_ms'] < end), None)
            if scheduled:
                window['goal_race'] = scheduled
        windows.append(window)
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
    turns, calendar_issues = _windows(readings, duration, action_times=[
        _time(a.get('source_timestamp_ms'), duration, 'action') for a in data.get('turn_action_receipts', [])
        if type(a.get('source_timestamp_ms')) is int])
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

    # Acquisition identities can be disputed even when the underlying event
    # has no numeric conflict. Keep those source objects visible to consumers.
    acquisition_conflicts = {}
    for collection in TRANSACTIONS:
        for index, transaction in enumerate(data.get(collection, [])):
            if transaction.get('name_conflicted'):
                event_id = transaction.get('receipt_event_id', transaction.get('event_id'))
                if event_id:
                    acquisition_conflicts.setdefault(event_id, []).append(dict(
                        source_ref=_ref(collection, index), kind='acquisition_name_conflict',
                        observed_name_candidates=deepcopy(transaction.get('observed_name_candidates', [])),
                        evidence=_proof(transaction.get('evidence'))))
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
            conflicts_present=bool(event.get('conflicting_readings') or event.get('ambiguous_effect_candidates')
                                   or event.get('performance_reading_conflicts') or acquisition_conflicts.get(identity)),
            acquisition_conflicts=deepcopy(acquisition_conflicts.get(identity, [])))
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
    # A training result card seen on one frame, or without its training name,
    # proves no committed action on its own.  In a window that expects one
    # decision and holds no other, that lone result is what the player did:
    # the turn takes it as its action and says the commit was not seen.
    for turn in turns:
        if turn['window_kind'] not in ('calendar_turn', 'phase_race_turn'):
            continue
        own = [e for e in timeline if e['turn_id'] == turn['id']]
        if any(e['kind'] == 'committed_action' and e.get('action_kind') != 'race' for e in own):
            continue
        trainings = [e for e in own if e['kind'] == 'training']
        if len(trainings) != 1:
            continue
        result = trainings[0]
        event = next((e for e in data['events'] if e.get('id') == result.get('event_id')), {})
        add(result['source_ref'], 'committed_action', result['first_seen_ms'], result['first_seen_ms'],
            result.get('evidence'), action_kind='training', training_option=event.get('training_option'),
            event_ref=event_ids.get(result.get('event_id')), reward_link_status='linked_event',
            click_timestamp_ms=None, identity_basis='result_card_only')
    for collection in TRANSACTIONS:
        for index, transaction in enumerate(data.get(collection, [])):
            start = transaction.get('source_timestamp_ms', transaction.get('first_seen_ms'))
            end = transaction.get('last_seen_ms', start)
            # A purchase is dated at its debit, from the request to the balance
            # that matched it, not at the receipt that follows it. The accounting
            # already charges it over that window (causal_accounting._debit_timed);
            # dating the entry the same way keeps the two consistent and puts a
            # purchase before the receipt it produced rather than after it.
            window = transaction.get('debit_window_ms')
            if (isinstance(window, list) and len(window) == 2
                    and all(type(value) is int for value in window) and window[0] <= window[1]):
                start, end = window
            event_id = transaction.get('receipt_event_id', transaction.get('event_id'))
            add(_ref(collection, index), collection, start, end, transaction.get('evidence'),
                transaction_id=transaction.get('id'), event_id=event_id,
                conflicts_present=bool(transaction.get('name_conflicted') or transaction.get('conflicting_readings')
                                       or acquisition_conflicts.get(event_id)),
                acquisition_conflicts=deepcopy(acquisition_conflicts.get(event_id, [])),
                accounting_role='reference_only_not_an_additional_award')
    for index, candidate in enumerate(data.get('unparsed_receipt_candidates', [])):
        if candidate.get('status') in ('ocr_fragment', 'out_of_scope'):
            continue  # a repeat of a receipt already in the log, or a line the ledger does not account for
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
            candidates = [dict(kind='checkpoint', index=i, values=deepcopy(c['values']),
                               observed_at_ms=c['first_seen_ms'], evidence=c.get('evidence'))
                          for i, c in enumerate(checkpoints)
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
                                candidates.append(dict(kind='checkpoint', index=i, values=deepcopy(c['values']),
                                                       observed_at_ms=time, evidence=proof))
            source_state = _repeated_source_state(readings, fields, channel, turn['start_ms'],
                                                  action_time, uncertain)
            if source_state is None and not candidates:
                source_state = _corroborated_source_state(readings, fields, channel, turn['start_ms'],
                                                          action_time, uncertain, timeline)
            if source_state is None and not candidates:
                source_state = _corroborated_source_state(readings, fields, channel, turn['start_ms'],
                                                          action_time, uncertain, timeline, partial=True)
            if source_state is not None:
                candidates.append(dict(kind='source', **source_state))
            chosen = min(candidates, key=lambda p: p['observed_at_ms']) if candidates else None
            if chosen is None:
                opening = None
            elif chosen['kind'] == 'source':
                opening = dict(source_ref=chosen['source_ref'], values_ref=chosen['values_ref'],
                               supporting_source_refs=chosen['supporting_source_refs'],
                               observed_at_ms=chosen['observed_at_ms'],
                               values=deepcopy(chosen['values']), evidence=_proof(chosen['evidence']),
                               basis=chosen['basis'], exact_turn_boundary=chosen['exact_turn_boundary'])
                if 'field_corroboration' in chosen:
                    opening['field_corroboration'] = deepcopy(chosen['field_corroboration'])
            else:
                source_ref = _ref(prefix, chosen['index'])
                opening = dict(source_ref=source_ref, values_ref=f'{source_ref}/values',
                               observed_at_ms=chosen['observed_at_ms'],
                               values=deepcopy(chosen['values']), evidence=_proof(chosen['evidence']),
                               basis='first_observed_state_before_action',
                               exact_turn_boundary=chosen['observed_at_ms'] == turn['start_ms'])
            turn.setdefault('states', {})[channel] = dict(opening=opening, closing=None,
                opening_status=('observed' if all(type(opening['values'].get(f)) is int for f in fields)
                                else 'partially_observed') if opening else 'not_observed_before_action')
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
                source_ref = _ref(prefix, i)
                last['states'][channel]['closing'] = dict(source_ref=source_ref,
                    values_ref=f'{source_ref}/values', observed_at_ms=checkpoint['first_seen_ms'],
                    values=deepcopy(checkpoint['values']),
                    evidence=_proof(checkpoint.get('evidence')), basis='last_observed_state_after_action',
                    exact_turn_boundary=False)
                last['states'][channel]['closing_basis'] = 'last_observed_state_not_inferred_completion'
            else:
                last['states'][channel]['closing_basis'] = 'unavailable'
    for turn in turns:
        actions = [e for e in timeline if e['turn_id'] == turn['id'] and e['kind'] == 'committed_action']
        if (turn.get('goal_race') and any(e.get('action_kind') == 'race' for e in actions)
                and any(e.get('action_kind') != 'race' for e in actions)):
            # The goal race shares the last countdown window with the turn's
            # own decision: like a finale race it is scheduled, not chosen.
            turn.update(window_kind='phase_race_turn', scheduled_race=turn['goal_race'],
                        label=f"{turn['label']} · {turn['goal_race']}")
        if turn['window_kind'] == 'phase_race_turn':
            # The finale race is scheduled by the game, not chosen; the turn's
            # decision is what the player did before it.
            decisions = [e for e in actions if e.get('action_kind') != 'race']
            turn['scheduled_race_actions'] = len(actions) - len(decisions)
        else:
            decisions = actions
        turn['action_status'] = 'one_action' if len(decisions) == 1 else 'missing_action' if not decisions else 'multiple_actions'
        turn['action_count'] = len(decisions)
        turn['expects_one_action'] = turn['window_kind'] in ('calendar_turn', 'phase_race_turn')
        turn['complete_event_history'] = False
    result = dict(schema_version=SCHEMA, source_sha256=report['source']['sha256'], source_duration_ms=duration,
                turns=turns, timeline=sorted(timeline, key=_timeline_order),
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
    # Resolve openings inside the canonical builder so worker validation and
    # causal accounting rebuild exactly the same source-backed ledger.
    from .boundary_state_recovery import promote_existing_endpoints, apply_opening_endpoint_projections
    projections = promote_existing_endpoints(dict(report, turn_ledger=result), readings)
    result, accepted, rejected = apply_opening_endpoint_projections(
        result, projections, source_sha256=report['source']['sha256'])
    if accepted or rejected:
        result['opening_endpoint_projection'] = dict(accepted=accepted, rejected=rejected)
    return result
