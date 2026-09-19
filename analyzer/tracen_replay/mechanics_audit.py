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
    # A receipt is a sentence of its own and starts with a capital; a line of
    # dialogue that happens to contain a keyword ("learned the reason for
    # her state") does not.
    return (line['confidence']>=95 and within(line,(250,770,850,1000))
            and str(line['text'])[:1].isupper()
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
# Receipt families the timeline does not account for: friendship, a
# supporter joining, a supporter appearing in training.  A line from one of
# these that the grammar could not parse (usually cut or garbled) is kept
# for the record but is not a missed stat, point or hint.
_OUT_OF_SCOPE_FAMILIES=(
    re.compile(r'^friendship with\b',re.I),
    re.compile(r'\bjoined your\b',re.I),
    re.compile(r'\bwill now appear\b',re.I),
)


def out_of_scope_family(text):
    """True when an unparsed line belongs to a receipt family outside the ledger's scope."""
    key=_receipt_key(text)
    return len(key)>=8 and any(p.search(key) for p in _OUT_OF_SCOPE_FAMILIES)


def _receipt_key(text):
    """A receipt line as compared for fragments: case-folded, one space, no trailing punctuation."""
    return ' '.join(text.split()).rstrip(' .,;:').casefold()


def fragment_of(text,parsed):
    """The parsed receipt this unparsed line is a fragment of, or None.

    The log panel scrolls and fades: a receipt caught mid-motion is read cut
    short (``Friendship with Light Hello is maxed``), with a letter or two
    wrong (``is mald`` for ``is maxed``), or with its number garbled
    (``Skill Pts went un hv 57``). Such a line is a fragment when it is a
    prefix of a receipt that was parsed nearby, within a few edits of one of
    about the same length, or a word-by-word match of one. The bound grows
    slowly with the length so a long line tolerates a couple of misread
    glyphs while a short one does not turn into a different receipt.
    """
    import re
    from .gameplay import _edit_distance
    key=_receipt_key(text)
    if len(key)<8:return None
    # A number the recognizer split ("1 10" for "110") is the same number:
    # what must agree is the digits the receipt shows, in order.
    digits=''.join(re.findall(r'\d+',key))
    for full in parsed:
        other=_receipt_key(full)
        if other==key:return full
        if len(key)<len(other) and other.startswith(key):return full
        # A variant must keep the receipt's subject and every number it shows;
        # "Guts went up by 3" is not a misread "Wit went up by 3", and a
        # different amount is a different receipt.
        if key.split(' ',1)[0]!=other.split(' ',1)[0] or (digits and digits!=''.join(re.findall(r'\d+',other))):continue
        bound=2+len(other)//12
        if abs(len(other)-len(key))<=3 and _edit_distance(key,other)<=bound:return full
        # Cut short and misread at once. Compared word by word: a line that
        # ends inside the receipt's name must not spend a whole line's
        # tolerance on that name, or a hint for one skill folds into a hint
        # for another and a real award goes missing.
        if _words_fold(key.split(' '),other.split(' ')):return full
    return None


def _words_fold(words,full_words):
    """True when a line is a word-by-word reading of a longer receipt.

    A receipt whose wording is fixed around its number and a name ("Gained 4
    hint level(s) for Pace Chaser Savvy") is read with the boilerplate
    damaged a glyph or two at a time (``leve"s) or`` for ``level(s) for``)
    while the number and the name come through. Comparing word by word
    spends the tolerance where the damage is instead of across the whole
    line: every word must be its own word, a number must be that number
    exactly, and the line may stop early where the panel cut it off.
    """
    from .gameplay import _edit_distance
    if len(words)>len(full_words):return False
    for word,other in zip(words,full_words):
        if word==other:continue
        # A number is that number: an amount read differently is another receipt.
        if word.isdigit() or other.isdigit():return False
        if _edit_distance(word,other)>max(2,len(other)//4):return False
    return True


def _same_scene(entry,title):
    """A fragment repeats a receipt of its own scene: when both frames name the event on screen, the names agree."""
    mine=entry.get('context_title')
    return mine is None or title is None or mine==title


def unparsed_receipt_candidates(readings):
    """Expose plausible missed receipts without guessing their meaning or value.

    A line that is a fragment of a receipt parsed within a few seconds is
    kept for the record but marked ``ocr_fragment`` with the receipt it
    repeats; it is not a missed effect and does not need a review. The
    receipt must be of the same scene: a cut line under one event's title
    is not a fragment of a receipt shown under another's, however close in
    time (a "Skill Pts went up by" under the next event is its own receipt,
    not the tail of the "+100" before it).
    """
    from .gameplay import effects_from_lines
    result=[];latest={};parsed=[]
    for row in readings:
        for e in row.get('effects',[]):
            full=e.get('raw_text') or e.get('original_text')
            if isinstance(full,str) and full.strip():parsed.append((row['source_timestamp_ms'],full,row.get('context_title')))
            # An energy recovery read from its centered popup stands for the
            # receipt sentence the cursor covered below it.
            if (e.get('kind')=='energy_change' and e.get('observation_basis')=='visible_energy_recovery_popup'
                    and type(e.get('amount')) is int and e['amount']>0):
                parsed.append((row['source_timestamp_ms'],f"Energy recovered by {e['amount']}.",row.get('context_title')))
    for row in readings:
        if row['screen'] not in ('unknown','event_outcome'):continue
        for line in row.get('ocr',{}).get('neural',[]):
            if not plausible_receipt_line(line):continue
            if effects_from_lines([line]):continue
            if any(line['text'] in e.get('raw_text','') or line['text']==e.get('original_text') for e in row.get('effects',[])):continue
            key=line['text'];time=row['source_timestamp_ms'];entry=latest.get(key)
            if not entry or time-entry['last_seen_ms']>750:
                entry=dict(first_seen_ms=time,last_seen_ms=time,raw_text=key,observations=0,evidence=[],
                           context_title=row.get('context_title'),
                           status='needs_review',scope='Possible unparsed receipt or OCR fragment; not an asserted missed effect.')
                result.append(entry);latest[key]=entry
            entry['last_seen_ms']=time;entry['observations']+=1
            entry['evidence'].append(row['evidence'])
    for entry in result:
        nearby=[full for time,full,title in parsed if entry['first_seen_ms']-_FRAGMENT_WINDOW_MS<=time<=entry['last_seen_ms']+_FRAGMENT_WINDOW_MS
                and _same_scene(entry,title)]
        full=fragment_of(entry['raw_text'],nearby)
        if full is None:
            # A fragment of a longer unparsed line seen nearby is a fragment too.
            longer=[o['raw_text'] for o in result if o is not entry and len(o['raw_text'])>len(entry['raw_text'])
                    and entry['first_seen_ms']-_FRAGMENT_WINDOW_MS<=o['first_seen_ms']<=entry['last_seen_ms']+_FRAGMENT_WINDOW_MS
                    and _same_scene(entry,o.get('context_title'))]
            full=fragment_of(entry['raw_text'],longer)
        if full is not None:
            entry['status']='ocr_fragment';entry['fragment_of']=full
            entry['scope']='OCR fragment of a receipt read nearby; not a missed effect.'
        elif out_of_scope_family(entry['raw_text']):
            entry['status']='out_of_scope'
            entry['scope']='Friendship, joining or appearance line the ledger does not account for; kept, not a missed effect.'
    return result
