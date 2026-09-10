"""Suppress repeated stat receipts only across observed stationary OCR gaps."""
import re
import math
from .receipt_continuity import (
    _effect_key, _effect_signature, _explicit_observations, _is_boundary,
    _same_receipt_slot, _titles_are_compatible, _valid_box,
)


_LABELS={'speed':'Speed','stamina':'Stamina','power':'Power','guts':'Guts',
         'wit':'Wit','skill_points':'Skill Pts'}


def _slot(row,effect):
    """Read only receipt topology; incomplete grammar supplies no award."""
    label=_LABELS.get(effect.get('field'))
    if label is None or type(effect.get('amount')) is not int:return None
    matches=[]
    for line in row.get('ocr',{}).get('neural',[]):
        if not isinstance(line,dict):return None
        box=line.get('box',[])
        if not _valid_box(box) or not 790<(box[1]+box[3])/2<950:continue
        text=' '.join(str(line.get('text','')).casefold().split())
        if not text.startswith(label.casefold()+' '):continue
        # A second line for the same stat is ambiguous regardless of which
        # OCR spelling happens to have the stronger confidence.
        matches.append((line,text[len(label):].strip()))
    if len(matches)!=1:return None
    line,tail=matches[0]
    confidence=line.get('confidence',0)
    if type(confidence) not in (int,float) or not 90<=confidence<=100:return None
    match=re.fullmatch(r'([a-z ]+?)(?:\s*(\d+))?[.!]?',tail)
    if match is None:return None
    letters=match[1].replace(' ','')
    expected='wentupby' if effect['amount']>=0 else 'wentdownby'
    # Deletions in fixed receipt grammar can preserve a slot. Arbitrary prose,
    # substitutions, changed amounts, and unknown field labels cannot.
    remaining=iter(expected)
    if not letters.startswith('w') or len(letters)<4 or not all(c in remaining for c in letters):return None
    if match[2] is not None and match[2]!=str(abs(effect['amount'])):return None
    return dict(box=list(line['box']),text=line['text'],confidence=confidence,
                complete=letters==expected and match[2] is not None,
                amount_observed=match[2] is not None)


def _bridge(left,right,effect,rows,by_evidence):
    key=_effect_key(effect)
    if any(c.get('field')==key for event in (left,right) for c in event.get('conflicting_readings',[])):return None
    before=_explicit_observations(left,effect,by_evidence)
    after=_explicit_observations(right,effect,by_evidence)
    if not before or not after:return None
    a,b=before[-1],after[0]
    if not 0<b[0]-a[0]<=1000:return None
    if any(sum(r['source_timestamp_ms']==t for r in rows)!=1 for t in (a[0],b[0])):return None
    middle=[r for r in rows if a[0]<r['source_timestamp_ms']<b[0]]
    if len(middle)<2:return None
    chain=[a[2]]+middle+[b[2]]
    if any(not 0<y['source_timestamp_ms']-x['source_timestamp_ms']<=250 for x,y in zip(chain,chain[1:])):return None
    slots=[_slot(row,effect) for row in chain]
    if any(slot is None for slot in slots):return None
    if not _titles_are_compatible(left,right,chain) or any(_is_boundary(r) for r in chain):return None
    if not slots[0]['complete'] or not slots[-1]['complete']:return None
    if not any(slot['confidence']>=95 for slot in slots[1:-1]):return None
    if not any(not slot['complete'] for slot in slots[1:-1]):return None
    # A stationary row and compatible explicit values are required through
    # every observed frame. Scrolling/another effect value vetoes continuity.
    anchor=slots[0]['box']
    if any(not _same_receipt_slot(anchor,slot['box']) or
           abs((anchor[1]+anchor[3]-slot['box'][1]-slot['box'][3])/2)>4
           for slot in slots[1:]):return None
    for row in middle:
        for observed in row.get('effects',[])+row.get('facts',{}).get('effect_candidates',[]):
            if _effect_key(observed)==key and _effect_signature(observed)!=_effect_signature(effect):return None
    return [dict(source_timestamp_ms=row['source_timestamp_ms'],evidence=row['evidence'],
                 **slot,accepted_as_effect=False) for row,slot in zip(chain,slots)]


def collapse_cross_event_stat_duplicates(events,readings):
    """Keep later companion effects while suppressing a proven repeated stat."""
    # A frame is one observation. Invalid times and reused evidence cannot
    # establish the independent, ordered frame chain required below.
    if any(not isinstance(row,dict) or type(row.get('source_timestamp_ms')) not in (int,float)
           or not math.isfinite(row['source_timestamp_ms']) or row['source_timestamp_ms']<0
           or not isinstance(row.get('evidence'),str) or not row['evidence'] for row in readings):return events
    by_evidence={row['evidence']:row for row in readings}
    if len(by_evidence)!=len(readings):return events
    for index,right in enumerate(events):
        for effect in list(right.get('effects',[])):
            if effect.get('kind')!='stat_change':continue
            candidates=[]
            for left in events[:index]:
                if left.get('last_seen_ms',0)<right.get('first_seen_ms',0)-1000:continue
                for prior in left.get('effects',[]):
                    if _effect_signature(prior)!=_effect_signature(effect):continue
                    track=_bridge(left,right,effect,readings,by_evidence)
                    if track:candidates.append((left,prior,track))
            if len(candidates)!=1:continue
            left,prior,track=candidates[0]
            proof=dict(basis='stationary_stat_receipt_across_observed_grammar_dropout',
                       earlier_event_id=left.get('id'),later_event_id=right.get('id'),track_evidence=track)
            prior.setdefault('cross_event_duplicate_evidence',[]).append(proof)
            right.setdefault('deduplicated_receipt_effects',[]).append(dict(effect=dict(effect),**proof))
            right['effects']=[e for e in right['effects'] if e is not effect]
            right.get('field_evidence',{}).pop(_effect_key(effect),None)
    return events
