"""Suppress repeated stat receipts only across observed stationary OCR gaps."""
import re
import math
from .receipt_continuity import (
    _effect_key, _effect_signature, _explicit_observations, _is_boundary,
    _same_line_geometry, _same_receipt_slot, _titles_are_compatible, _valid_box,
)


_LABELS={'speed':'Speed','stamina':'Stamina','power':'Power','guts':'Guts',
         'wit':'Wit','skill_points':'Skill Pts'}


def _slot(row,effect):
    """Read only receipt topology; incomplete grammar supplies no award."""
    label=_LABELS.get(effect.get('field'))
    if label is None or type(effect.get('amount')) is not int:return None
    candidates=[]

    def add(line,source):
        if not isinstance(line,dict):return
        box=line.get('box',[])
        if not _valid_box(box) or not 790<(box[1]+box[3])/2<950:return
        confidence=line.get('confidence')
        # ``pre_occlusion_confidence`` is useful for planning, but cannot
        # certify continuity after the visible line was occluded.  Source
        # facts therefore need their own finite confidence too.
        if type(confidence) not in (int,float) or not math.isfinite(confidence) or not 90<=confidence<=100:return
        text=' '.join(str(line.get('text','')).casefold().split())
        if not text.startswith(label.casefold()+' '):return
        tail=text[len(label):].strip()
        # The effect parser already accepts repeated terminal punctuation. Keep
        # that same grammar for continuity, without changing any numeric token.
        match=re.fullmatch(r'([a-z ]+?)(?:\s*(\d+))?[.!]*',tail)
        if match is None:return
        letters=match[1].replace(' ','')
        expected='wentupby' if effect['amount']>=0 else 'wentdownby'
        # Deletions in fixed receipt grammar can preserve a slot. Record every
        # plausible up/down grammar before comparing its value to the target;
        # discarding a contradictory same-box line here would let a valid line
        # hide an amount or direction conflict from the grouping check below.
        directions=[]
        for direction in ('wentupby','wentdownby'):
            remaining=iter(direction)
            if (letters.startswith('w') and len(letters)>=4
                    and all(c in remaining for c in letters)):
                directions.append(direction)
        if not directions:return
        amount=match[2]
        candidates.append(dict(
            box=list(box),text=line.get('text'),confidence=confidence,
            complete=letters==expected and amount is not None and amount==str(abs(effect['amount'])),
            amount_observed=amount is not None,source=source,
            observed_amount=int(amount) if amount is not None else None,
            directions=tuple(directions),
            matches_effect=(expected in directions
                            and (amount is None or amount==str(abs(effect['amount'])))),
        ))

    neural=(row.get('ocr',{}) or {}).get('neural',[])
    if isinstance(neural,list):
        for line in neural:
            if not isinstance(line,dict):return None
            if line.get('overlay_occluded') is not True:add(line,'ocr')
    facts=row.get('facts',{})
    occluded=facts.get('occluded_receipt_lines',[]) if isinstance(facts,dict) else []
    if isinstance(occluded,list):
        for line in occluded:
            if not isinstance(line,dict):return None
            overlays=line.get('overlay_boxes')
            if not isinstance(overlays,list) or not any(_valid_box(box) for box in overlays):continue
            add(line,'source_occlusion')

    # OCR and source-fact records may describe the same physical line. Merge
    # those duplicate views by tight geometry, while retaining distinct lines
    # as an ambiguity even if one has higher confidence.
    groups=[]
    for candidate in candidates:
        group=next((group for group in groups
                    if _same_line_geometry(group[0]['box'],candidate['box'])),None)
        if group is None:groups.append([candidate])
        else:group.append(candidate)
    if len(groups)!=1:return None
    group=groups[0]
    # Two entries from the same channel are duplicate OCR output, not two
    # independent observations. Only a neural/source-fact pair can be merged
    # because the latter carries the immutable occlusion proof.
    if len(group)>1 and len({item['source'] for item in group})==1:return None
    if any(not item['matches_effect'] for item in group):return None
    observed={item['observed_amount'] for item in group if item['amount_observed']}
    if observed and observed!={abs(effect['amount'])}:return None
    selected=max(group,key=lambda item:(item['source']=='source_occlusion',item['confidence']))
    return {key:selected[key] for key in
            ('box','text','confidence','complete','amount_observed','source')}


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
