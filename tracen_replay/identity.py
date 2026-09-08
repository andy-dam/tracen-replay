"""Track visible log occurrences using ordered text and surrounding context.

Identity is inferred from sampled UI continuity, never from stat arithmetic.
Observation time is not the time the player clicked an action.
"""

import re
from difflib import SequenceMatcher


def canonical(text):
    heading = re.search(r'\bTraining\s+(Speed|Stamina|Power|Guts|Wit)\s+Lv[l1]\s*(\d+)',text,re.I)
    if heading:
        text = heading[0]
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


def compatible(left, right):
    return all(left[k] == right[k] for k in left.keys() & right.keys())


def unique_slice(sequence, fragment):
    return sum(sequence[i:i+len(fragment)] == fragment
               for i in range(len(sequence)-len(fragment)+1)) == 1


def track_occurrences(snapshots, parse, maximum_gap_ms=1250):
    occurrences, previous, previous_blocks = [], None, []
    decisions = []
    for snapshot in sorted(snapshots, key=lambda s: s['source_timestamp_ms']):
        blocks = parse(snapshot['lines'])
        normalized = [canonical(line['text']) for line in snapshot['lines']]
        aligned = {}
        gap = snapshot['source_timestamp_ms']-previous['source_timestamp_ms'] if previous else None
        if previous and gap > 0:
            old = [canonical(line['text']) for line in previous['lines']]
            old_stat_indices = {i for b in previous_blocks for i in b['line_indices']}
            matches = SequenceMatcher(None, old, normalized, autojunk=False).get_matching_blocks()
            anchors = []
            for match in matches:
                fragment = old[match.a:match.a+match.size]
                # Equal gains alone cannot establish identity. Require surrounding
                # non-stat text and an unambiguous ordered match in both frames.
                context = [old[i] for i in range(match.a,match.a+match.size) if i not in old_stat_indices
                           and (old[i].startswith('friendship with ') or not re.search(r'\b(?:went up|went down|recovered) by\b',old[i]))]
                if match.size < 2 or sum(len(s) for s in context) < 20:
                    continue
                if not unique_slice(old,fragment) or not unique_slice(normalized,fragment):
                    continue
                anchors.append(match)
                for offset in range(match.size):
                    aligned[match.b+offset] = match.a+offset
            # OCR can omit a heading between a stable preceding card and its
            # delta rows. Preserve order on both sides of that small text gap.
            for match in matches:
                fragment = old[match.a:match.a+match.size]
                if match.size < 1 or not unique_slice(old,fragment) or not unique_slice(normalized,fragment):
                    continue
                if any(0 <= match.a-(a.a+a.size) <= 3 and 0 <= match.b-(a.b+a.size) <= 3 for a in anchors):
                    for offset in range(match.size):
                        aligned[match.b+offset] = match.a+offset
        claimed = set()
        for block in blocks:
            candidates = set()
            for prior in previous_blocks:
                if any(aligned.get(i) in prior['line_indices'] for i in block['line_indices']):
                    occurrence = occurrences[prior['occurrence_index']]
                    if compatible(occurrence['deltas'],block['deltas']):
                        candidates.add(prior['occurrence_index'])
            available = candidates-claimed
            if len(available)==1:
                index = available.pop()
                occurrence = occurrences[index]
                occurrence['deltas'].update(block['deltas'])
                reappearance = gap > maximum_gap_ms
                if occurrence['identity_status'] != 'context_reappearance':
                    occurrence['identity_status'] = 'context_reappearance' if reappearance else 'context_continuity'
                decision = 'match_context_after_gap' if reappearance else 'match_ordered_context'
                if block.get('training_option'):
                    occurrence['training_option'] = block['training_option']
                    occurrence['event_type'] = block['event_type']
                if len(block['deltas']) >= occurrence['evidence_field_count']:
                    occurrence.update(text=block['text'],evidence=snapshot['evidence'],observed_at_ms=snapshot['source_timestamp_ms'],evidence_field_count=len(block['deltas']))
            else:
                index = len(occurrences)
                occurrence = dict(block, deltas=dict(block['deltas']), id=f'log-{index+1:04d}',
                                  first_seen_ms=snapshot['source_timestamp_ms'], last_seen_ms=snapshot['source_timestamp_ms'],
                                  observed_at_ms=snapshot['source_timestamp_ms'], evidence=snapshot['evidence'],
                                  origin='ocr_log_occurrence', event_time_ms=None, identity_verified=False,
                                  identity_status='unanchored', evidence_field_count=len(block['deltas']), observations=[])
                occurrence.pop('line_indices',None)
                occurrences.append(occurrence)
                decision = 'new_unanchored_occurrence'
            occurrence['last_seen_ms'] = snapshot['source_timestamp_ms']
            context = ' '.join(normalized[max(0,min(block['line_indices'])-8):min(block['line_indices'])])
            occurrence['observations'].append(dict(observed_at_ms=snapshot['source_timestamp_ms'],evidence=snapshot['evidence'],
                                                   deltas=dict(block['deltas']),text=block['text'],training_option=block.get('training_option'),context_before=context))
            block['occurrence_index'] = index
            claimed.add(index)
            decisions.append(dict(observed_at_ms=snapshot['source_timestamp_ms'],evidence=snapshot['evidence'],
                                  occurrence_id=occurrence['id'],decision=decision,
                                  previous_evidence=previous['evidence'] if decision.startswith('match_') else None,
                                  observation_gap_ms=gap if decision.startswith('match_') else None))
        if blocks:
            previous, previous_blocks = snapshot, blocks
    for occurrence in occurrences:
        occurrence.pop('evidence_field_count')
        occurrence['outcome_observations'] = []
    return dict(occurrences=occurrences,decisions=decisions,
                method='unique_ordered_context_v1',maximum_observation_gap_ms=maximum_gap_ms)


def interval_occurrences(ledger, before, after):
    events, excluded = [], []
    start, end = before['last_seen_ms'], after['first_seen_ms']
    for occurrence in ledger['occurrences']:
        visible = [o for o in occurrence['observations'] if start <= o['observed_at_ms'] <= end]
        if not visible:
            continue
        if occurrence['first_seen_ms'] <= start:
            excluded.append(dict(occurrence_id=occurrence['id'],reason='observed_at_or_before_starting_checkpoint',
                                 evidence=occurrence['observations'][0]['evidence'],observed_at_ms=occurrence['first_seen_ms']))
        else:
            # Do not borrow fuller readings or action headings from beyond the
            # interval's end. Keep inference and observation timestamps separate.
            deltas = {}
            for observation in visible:
                deltas.update(observation['deltas'])
            best = max(visible,key=lambda o:len(o['deltas']))
            options = {o['training_option'] for o in visible if o.get('training_option')}
            option = next(iter(options)) if len(options)==1 else None
            events.append(dict(id=occurrence['id'],deltas=deltas,text=best['text'],evidence=best['evidence'],
                               observed_at_ms=best['observed_at_ms'],event_time_ms=None,
                               training_option=option,event_type='logged_training_result' if option else 'logged_change',
                               origin='ocr_log_occurrence',identity_status=occurrence['identity_status'],
                               identity_verified=False,outcome_observations=[],
                               context_before=[o['context_before'] for o in visible],
                               observation_start_ms=visible[0]['observed_at_ms'],observation_end_ms=visible[-1]['observed_at_ms']))
    return events, excluded


def track_outcomes(snapshots, parse, maximum_gap_ms=500):
    episodes, current, context = [], None, None
    for snapshot in sorted(snapshots,key=lambda s:s['source_timestamp_ms']):
        blocks = parse(snapshot['lines'])
        if not blocks:
            current = None
            text = ' '.join(canonical(line['text']) for line in snapshot['lines'] if line['confidence']>=80 and len(canonical(line['text']))>=5)
            if len(text)>=30:
                context = dict(text=text,observed_at_ms=snapshot['source_timestamp_ms'],evidence=snapshot['evidence'])
        for block in blocks:
            timestamp = snapshot['source_timestamp_ms']
            overlap = current and any(current['deltas'].get(k)==v for k,v in block['deltas'].items())
            if not current or timestamp-current['last_seen_ms']>maximum_gap_ms or not overlap or not compatible(current['deltas'],block['deltas']):
                current = dict(id=f'outcome-{len(episodes)+1:04d}',deltas={},first_seen_ms=timestamp,
                               last_seen_ms=timestamp,observations=[],origin='ocr_main_outcome',training_option=None,
                               event_type='observed_outcome',event_time_ms=None,identity_verified=False)
                current['preceding_context'] = context if context and 0 < timestamp-context['observed_at_ms']<=1500 else None
                episodes.append(current)
            current['last_seen_ms'] = timestamp
            current['deltas'].update(block['deltas'])
            current['observations'].append(dict(observed_at_ms=timestamp,evidence=snapshot['evidence'],text=block['text'],deltas=block['deltas']))
    return episodes


def attach_outcomes(events, episodes, start, end):
    """Keep visible outcome timing separate from inferred cross-pane identity."""
    associations = []
    for episode in episodes:
        if not start < episode['first_seen_ms'] <= end:
            continue
        observations = [o for o in episode['observations'] if start < o['observed_at_ms'] <= end]
        deltas = {}
        for observation in observations:
            deltas.update(observation['deltas'])
        candidates = [event for event in events if event['origin']=='ocr_log_occurrence'
                      and all(event['deltas'].get(k)==v for k,v in deltas.items())]
        best = max(observations,key=lambda o:len(o['deltas']))
        association = dict(outcome_id=episode['id'],candidate_log_ids=[e['id'] for e in candidates],
                           first_seen_ms=observations[0]['observed_at_ms'],last_seen_ms=observations[-1]['observed_at_ms'],
                           evidence=best['evidence'],evidence_timestamp_ms=best['observed_at_ms'],
                           identity_verified=False,action_time_ms=None)
        if len(candidates)==1:
            association['status'] = 'unique_delta_match_unverified'
            candidates[0]['outcome_observations'].append(association)
        elif candidates:
            # Do not arbitrarily assign the same outcome to one of several equal
            # logged results, or count it again as a separate awarded change.
            association['status'] = 'ambiguous_multiple_log_matches'
        else:
            association['status'] = 'main_outcome_only'
            events.append(dict(id=episode['id'],deltas=deltas,text=best['text'],evidence=best['evidence'],
                               observed_at_ms=best['observed_at_ms'],event_time_ms=None,training_option=None,
                               event_type='observed_outcome',origin='ocr_main_outcome',identity_verified=False,
                               identity_status='visible_outcome_episode',outcome_observations=[association],
                               observation_start_ms=observations[0]['observed_at_ms'],observation_end_ms=observations[-1]['observed_at_ms']))
        associations.append(association)
    events.sort(key=lambda e:e['observed_at_ms'])
    return associations


def refine_intervals(checkpoints, log_readings, outcome_readings, provisional, parse):
    from .reconcile import account
    ledger = track_occurrences(log_readings,parse)
    episodes = track_outcomes(outcome_readings,parse)
    intervals = []
    for before, after, original in zip(checkpoints,checkpoints[1:],provisional):
        events, excluded = interval_occurrences(ledger,before,after)
        retained = []
        for event in events:
            historical = [episode for episode in episodes
                          if episode['last_seen_ms']<=before['last_seen_ms'] and episode['deltas']==event['deltas']
                          and 0 < event['observation_start_ms']-episode['last_seen_ms']<=10000
                          and episode.get('preceding_context') and any(episode['preceding_context']['text'] in c for c in event['context_before'])]
            if len(historical)==1:
                episode = historical[0]
                excluded.append(dict(occurrence_id=event['id'],reason='earlier_main_outcome_with_matching_narrative_and_deltas',
                                     evidence=episode['observations'][0]['evidence'],observed_at_ms=episode['first_seen_ms'],
                                     context_evidence=episode['preceding_context']['evidence'],log_evidence=event['evidence'],
                                     outcome_id=episode['id'],identity_verified=False))
            else:
                retained.append(event)
        events = retained
        associations = attach_outcomes(events,episodes,before['last_seen_ms'],after['first_seen_ms'])
        interval = dict(original)
        interval.update(account(before,after,events))
        interval.update(excluded_historical_occurrences=excluded,outcome_associations=associations,
                        accounting_method='context_occurrences_and_visible_outcomes',
                        provisional_delta_accounting=original['unexplained_change'],
                        deduplication_decisions=[],
                        log_identity_warning='Ordered context provides provisional identity. Reappearance after a gap and matching cross-pane deltas do not verify event identity or click time.')
        intervals.append(interval)
    return intervals, ledger, episodes
