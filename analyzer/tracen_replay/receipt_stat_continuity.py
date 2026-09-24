"""Suppress repeated stat receipts only across observed stationary OCR gaps."""
import re
import math
from .gameplay import receipt_rows
from .receipt_continuity import (
    _effect_key, _effect_signature, _explicit_observations, _is_boundary,
    _same_line_geometry, _same_receipt_slot, _titles_are_compatible, _valid_box,
)
from .source_clock import elapsed


_LABELS={'speed':'Speed','stamina':'Stamina','power':'Power','guts':'Guts',
         'wit':'Wit','skill_points':'Skill Pts'}
# The confidence a dialogue line needs before a frame parses it as a receipt.
_PARSE_CONFIDENCE=95
_STEP_MS=300


def _slot(row,effect):
    """Read only receipt topology; incomplete grammar supplies no award."""
    label=_LABELS.get(effect.get('field'))
    if label is None or type(effect.get('amount')) is not int:return None
    candidates=[]
    top,bottom=receipt_rows(790,950)

    def add(line,source):
        if not isinstance(line,dict):return
        box=line.get('box',[])
        if not _valid_box(box) or not top<(box[1]+box[3])/2<bottom:return
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
    # A covered line the dense reread restored on this frame, in its own proof.
    for restored in row.get('effects',[]):
        proof=restored.get('source_bound_receipt_proof') if isinstance(restored,dict) else None
        if isinstance(proof,dict):
            add(dict(text=restored.get('raw_text'),box=proof.get('line_box'),confidence=proof.get('confidence')),'source_restored')

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


def _blank_receipt_band(row):
    """Nothing read at all where the receipt lines sit, and nothing parsed."""
    facts=row.get('facts') if isinstance(row.get('facts'),dict) else {}
    if row.get('screen','unknown')!='unknown' or row.get('effects') or facts.get('effect_candidates') or facts.get('occluded_receipt_lines'):return False
    top,bottom=receipt_rows(790,950)
    for line in (row.get('ocr') or {}).get('neural',[]):
        box=line.get('box',[]) if isinstance(line,dict) else []
        if _valid_box(box) and top<(box[1]+box[3])/2<bottom:return False
    return True


def _bridge(left,right,effect,rows,by_evidence):
    key=_effect_key(effect)
    if any(c.get('field')==key for event in (left,right) for c in event.get('conflicting_readings',[])):return None
    before=_explicit_observations(left,effect,by_evidence)
    after=_explicit_observations(right,effect,by_evidence)
    if not before or not after:return None
    a,b=before[-1],after[0]
    # Every sample between the two reads must show the line in place (checked
    # below), so the chain, not its length, is the proof.
    if not 0<elapsed(a[0],b[0]):return None
    if any(sum(r['source_timestamp_ms']==t for r in rows)!=1 for t in (a[0],b[0])):return None
    middle=[r for r in rows if a[0]<r['source_timestamp_ms']<b[0]]
    if not middle:return None
    chain=[a[2]]+middle+[b[2]]
    # Consecutive samples only: one sampling step as ``source_clock`` counts
    # it, with slack; a skipped sample is two.
    if any(not 0<elapsed(x['source_timestamp_ms'],y['source_timestamp_ms'])<=_STEP_MS for x,y in zip(chain,chain[1:])):return None
    slots=[_slot(row,effect) for row in chain]
    if slots[0] is None or slots[-1] is None:return None
    # The one sample between two reads may show nothing in the receipt band
    # at all: the award's own animation covers the box as its line lands, and
    # the reader finds no text under it. Only its neighbours then say the box
    # stayed, so exactly one such frame is allowed, blank rather than showing
    # any other line, with the line back on the same pixels after it.
    blank=len(chain)==3 and slots[1] is None and _blank_receipt_band(chain[1])
    if any(slot is None for slot in slots) and not blank:return None
    if not _titles_are_compatible(left,right,chain) or any(_is_boundary(r) for r in chain):return None
    if not slots[0]['complete'] or not slots[-1]['complete']:return None
    if blank and not _same_line_geometry(slots[0]['box'],slots[-1]['box']):return None
    # The frames between the two reads must show why they parsed nothing: a
    # line read clearly with part of its grammar lost, the whole line read
    # under the confidence a receipt parse needs, or the line under an overlay
    # the frame recorded over it. A whole line read at that confidence and
    # uncovered would have parsed, so frames that show only that are no proof.
    inner=[slot for slot in slots[1:-1] if slot is not None]
    dropout=any(slot['confidence']>=_PARSE_CONFIDENCE for slot in inner) and any(not slot['complete'] for slot in inner)
    under=(any(slot['complete'] and slot['confidence']<_PARSE_CONFIDENCE for slot in inner)
           and not any(slot['complete'] and slot['confidence']>=_PARSE_CONFIDENCE for slot in inner))
    covered=any(slot['source']=='source_occlusion' for slot in inner)
    if not (dropout or under or blank or covered):return None
    # A stationary row and compatible explicit values are required through
    # every observed frame. Scrolling/another effect value vetoes continuity.
    anchor=slots[0]['box']
    def stationary(slot):
        box=slot['box']
        # A line cut before its amount ends early; its start and row must hold.
        if not slot['amount_observed'] and box[2]<anchor[2]:box=[box[0],box[1],anchor[2],box[3]]
        return _same_receipt_slot(anchor,box) and abs((anchor[1]+anchor[3]-box[1]-box[3])/2)<=4
    if not all(stationary(slot) for slot in slots[1:] if slot is not None):return None
    for row in middle:
        for observed in row.get('effects',[])+row.get('facts',{}).get('effect_candidates',[]):
            if _effect_key(observed)==key and _effect_signature(observed)!=_effect_signature(effect):return None
    basis=('stationary_stat_receipt_across_blank_frame' if blank
           else 'stationary_stat_receipt_across_observed_grammar_dropout' if dropout or under
           else 'stationary_stat_receipt_across_covered_line')
    return basis,[dict(source_timestamp_ms=row['source_timestamp_ms'],evidence=row['evidence'],
                       **(slot if slot is not None else dict(blank=True)),accepted_as_effect=False) for row,slot in zip(chain,slots)]


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
                    bridged=_bridge(left,right,effect,readings,by_evidence)
                    if bridged:candidates.append((left,prior,bridged))
            if len(candidates)!=1:continue
            left,prior,(basis,track)=candidates[0]
            proof=dict(basis=basis,earlier_event_id=left.get('id'),later_event_id=right.get('id'),track_evidence=track)
            prior.setdefault('cross_event_duplicate_evidence',[]).append(proof)
            right.setdefault('deduplicated_receipt_effects',[]).append(dict(effect=dict(effect),**proof))
            right['effects']=[e for e in right['effects'] if e is not effect]
            right.get('field_evidence',{}).pop(_effect_key(effect),None)
    return events
