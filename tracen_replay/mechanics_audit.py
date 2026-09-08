"""Additional ledgers that do not turn missing observations into zeroes."""
from collections import Counter
import re


def fan_accounting(races, events):
    states = [r for r in races if type(r.get('fans')) is int]
    intervals = []
    for before, after in zip(states, states[1:]):
        receipts = [e for e in events if before['last_seen_ms'] < e['first_seen_ms'] <= after['first_seen_ms']]
        gains = [r for r in races if before['last_seen_ms'] < r['first_seen_ms'] <= after['first_seen_ms']]
        unknown = [r['id'] for r in gains if type(r.get('fans_gained')) is not int]
        supported = None if unknown else sum(r['fans_gained'] for r in gains) + sum(
            f['amount'] for e in receipts for f in e.get('effects', []) if f['kind'] == 'fan_change')
        observed = after['fans'] - before['fans']
        residual = None if supported is None else observed - supported
        intervals.append(dict(start_ms=before['last_seen_ms'], end_ms=after['first_seen_ms'],
            observed_change=observed, supported_change=supported, unexplained_change=residual,
            status='unknown' if unknown else 'balanced' if residual == 0 else 'unresolved',
            unknown_race_gains=unknown, evidence=before['evidence'] + after['evidence'] +
            [e['evidence'] for e in receipts if any(f['kind'] == 'fan_change' for f in e.get('effects', []))]))
    return dict(intervals=intervals, statuses=dict(Counter(i['status'] for i in intervals)),
        scope='Between observed race-result fan totals; the initial fan total and changes after the final race are not independently checked.')


def song_acquisitions(events, lessons):
    result = []
    for event in events:
        names = list(dict.fromkeys(f['name'] for f in event.get('effects', []) if f['kind'] == 'song_learned'))
        if not names:
            continue
        purchases = [p for p in lessons if p['receipt_event_id'] == event['id']]
        purchase = purchases[0] if len(purchases) == 1 else None
        # A fragmented receipt is one acquisition with alternative OCR names.
        # Never silently correct a song title using a game catalog.
        name = purchase['name'] if purchase and purchase['name'] in names else names[0] if len(names) == 1 else None
        result.append(dict(event_id=event['id'], source_timestamp_ms=event['first_seen_ms'],
            name=name, observed_name_candidates=names, name_conflicted=len(names) > 1,
            acquisition='paid_lesson' if purchase else 'story_event_receipt' if event.get('context_title') else 'unknown',
            lesson_id=purchase['id'] if purchase else None,
            performance_cost=purchase['performance_cost'] if purchase else None,
            context_title=event.get('context_title'), evidence=event['evidence'],
            bonus_values_complete=False))
    return result


def unparsed_receipt_candidates(readings):
    """Expose plausible missed receipts without guessing their meaning or value."""
    from .vision import within
    from .gameplay import effects_from_lines
    result=[];latest={}
    prefix=re.compile(r'^(?:Gained \d|Learned |Acquired |(?:Max )?Energy |Friendship with |'
                      r'(?:Speed|Stamina|Power|Guts|Wit|Skill Pts|Dance|Passion|Vocals?|Visuals?|Composure) '
                      r'(?:cap |Bonus |went )|(?:Front Runner|Pace Chaser|Sprint|Mile|Medium|Long) Aptitude )',re.I)
    for row in readings:
        if row['screen'] not in ('unknown','event_outcome'):continue
        for line in row.get('ocr',{}).get('neural',[]):
            if line['confidence']<95 or not within(line,(250,770,850,1000)) or not prefix.match(line['text']):continue
            if effects_from_lines([line]):continue
            if any(line['text'] in e.get('raw_text','') or line['text']==e.get('original_text') for e in row.get('effects',[])):continue
            key=line['text'];time=row['source_timestamp_ms'];entry=latest.get(key)
            if not entry or time-entry['last_seen_ms']>750:
                entry=dict(first_seen_ms=time,last_seen_ms=time,raw_text=key,observations=0,evidence=[],
                           status='needs_review',scope='Possible unparsed receipt or OCR fragment; not an asserted missed effect.')
                result.append(entry);latest[key]=entry
            entry['last_seen_ms']=time;entry['observations']+=1
            entry['evidence'].append(row['evidence'])
    return result
