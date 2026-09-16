"""Additional ledgers that do not turn missing observations into zeroes."""
from collections import Counter
import re


_RECEIPT_PREFIX=re.compile(r'^(?:Gained \d|Learned |Acquired |(?:Max )?Energy |Friendship with |'
                          r'(?:Speed|Stamina|Power|Guts|Wit|Skill Pts|Dance|Passion|Vocals?|Visuals?|Composure) '
                          r'(?:cap |Bonus |went )|(?:Front Runner|Pace Chaser|Sprint|Mile|Medium|Long) Aptitude )',re.I)
_SUPPORTER_PREFIX=re.compile(r'^.+? joined your\b',re.I)


def plausible_receipt_line(line):
    """Recognize receipt-shaped uncertainty, without accepting an effect."""
    from .vision import within
    return (line['confidence']>=95 and within(line,(250,770,850,1000))
            and bool(_RECEIPT_PREFIX.match(line['text']) or _SUPPORTER_PREFIX.match(line['text'])))


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


_FRAGMENT_WINDOW_MS=5000


def _receipt_key(text):
    """A receipt line as compared for fragments: case-folded, one space, no trailing punctuation."""
    return ' '.join(text.split()).rstrip(' .,;:').casefold()


def fragment_of(text,parsed):
    """The parsed receipt this unparsed line is a fragment of, or None.

    The log panel scrolls and fades: a receipt caught mid-motion is read cut
    short (``Friendship with Light Hello is maxed``), with a letter or two
    wrong (``is mald`` for ``is maxed``), or with its number garbled
    (``Skill Pts went un hv 57``). Such a line is a fragment when it is a
    prefix of a receipt that was parsed nearby, or within a few edits of one
    of about the same length. The bound grows slowly with the length so a
    long line tolerates a couple of misread glyphs while a short one does
    not turn into a different receipt.
    """
    import re
    from .gameplay import _edit_distance
    key=_receipt_key(text)
    if len(key)<8:return None
    digits=re.findall(r'\d+',key)
    for full in parsed:
        other=_receipt_key(full)
        if other==key:return full
        if len(key)<len(other) and other.startswith(key):return full
        # A variant must keep the receipt's subject and every number it shows;
        # "Guts went up by 3" is not a misread "Wit went up by 3", and a
        # different amount is a different receipt.
        if key.split(' ',1)[0]!=other.split(' ',1)[0] or (digits and digits!=re.findall(r'\d+',other)):continue
        bound=2+len(other)//12
        if abs(len(other)-len(key))<=3 and _edit_distance(key,other)<=bound:return full
        # Cut short and misread at once: the line matches the head of the receipt within the bound.
        if len(key)<len(other) and _edit_distance(key,other[:len(key)])<=bound:return full
    return None


def unparsed_receipt_candidates(readings):
    """Expose plausible missed receipts without guessing their meaning or value.

    A line that is a fragment of a receipt parsed within a few seconds is
    kept for the record but marked ``ocr_fragment`` with the receipt it
    repeats; it is not a missed effect and does not need a review.
    """
    from .gameplay import effects_from_lines
    result=[];latest={};parsed=[]
    for row in readings:
        for e in row.get('effects',[]):
            full=e.get('raw_text') or e.get('original_text')
            if isinstance(full,str) and full.strip():parsed.append((row['source_timestamp_ms'],full))
    for row in readings:
        if row['screen'] not in ('unknown','event_outcome'):continue
        for line in row.get('ocr',{}).get('neural',[]):
            if not plausible_receipt_line(line):continue
            if effects_from_lines([line]):continue
            if any(line['text'] in e.get('raw_text','') or line['text']==e.get('original_text') for e in row.get('effects',[])):continue
            key=line['text'];time=row['source_timestamp_ms'];entry=latest.get(key)
            if not entry or time-entry['last_seen_ms']>750:
                entry=dict(first_seen_ms=time,last_seen_ms=time,raw_text=key,observations=0,evidence=[],
                           status='needs_review',scope='Possible unparsed receipt or OCR fragment; not an asserted missed effect.')
                result.append(entry);latest[key]=entry
            entry['last_seen_ms']=time;entry['observations']+=1
            entry['evidence'].append(row['evidence'])
    for entry in result:
        nearby=[full for time,full in parsed if entry['first_seen_ms']-_FRAGMENT_WINDOW_MS<=time<=entry['last_seen_ms']+_FRAGMENT_WINDOW_MS]
        full=fragment_of(entry['raw_text'],nearby)
        if full is None:
            # A fragment of a longer unparsed line seen nearby is a fragment too.
            longer=[o['raw_text'] for o in result if o is not entry and len(o['raw_text'])>len(entry['raw_text'])
                    and entry['first_seen_ms']-_FRAGMENT_WINDOW_MS<=o['first_seen_ms']<=entry['last_seen_ms']+_FRAGMENT_WINDOW_MS]
            full=fragment_of(entry['raw_text'],longer)
        if full is not None:
            entry['status']='ocr_fragment';entry['fragment_of']=full
            entry['scope']='OCR fragment of a receipt read nearby; not a missed effect.'
    return result
