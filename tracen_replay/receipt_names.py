"""Retain receipt identity uncertainty and resolve evidence-backed variants."""

from copy import deepcopy
import math
import re


def _dialogue_text_moved(first,second):
    """A shared line changing row disproves a stationary receipt-slot match.

    This only vetoes identity inference. It does not accept either recipient
    or promote an OCR reading, and it does not need a character-name catalog.
    """
    def lines(row):
        found={}
        for line in row.get('ocr',{}).get('neural',[]):
            box=line.get('box',[])
            if len(box)!=4 or line.get('confidence',0)<95:continue
            if not 780<=(box[1]+box[3])/2<=960:continue
            found.setdefault(line.get('text'),[]).append(box)
        return found
    a,b=lines(first),lines(second)
    for text in a.keys()&b.keys():
        if not text or len(a[text])!=1 or len(b[text])!=1:continue
        left,right=a[text][0],b[text][0]
        dy=(right[1]+right[3]-left[1]-left[3])/2
        if 8<=abs(dy)<=80 and abs(left[0]-right[0])<=5 and abs(left[2]-right[2])<=5:
            return True
    return False


def flag_friendship_identity_conflicts(event,rows_by_evidence):
    """Abstain when adjacent views of one receipt slot disagree on a name.

    No spelling is selected. Simultaneous recipients and separate receipt
    positions remain distinct, even when their names and gains are similar.
    """
    effects=[e for e in event['effects'] if e['kind'] in ('friendship_change','friendship_status')]
    def same_slot(ba,bb):
        if len(ba)!=4 or len(bb)!=4:return False
        width=max(0,min(ba[2],bb[2])-max(ba[0],bb[0]))
        height=max(0,min(ba[3],bb[3])-max(ba[1],bb[1]))
        intersection=width*height
        union=(ba[2]-ba[0])*(ba[3]-ba[1])+(bb[2]-bb[0])*(bb[3]-bb[1])-intersection
        return union>0 and intersection/union>=.8 and abs((ba[1]+ba[3]-bb[1]-bb[3])/2)<=3
    def occluded_bridge(ta,tb,pa,pb,ba,bb,effect):
        if abs(ta-tb)!=500:return False
        middle=(ta+tb)//2
        proofs={p for paths in event['field_evidence'].values() for p in paths}
        rows=[rows_by_evidence[p] for p in proofs if p in rows_by_evidence
              and rows_by_evidence[p]['source_timestamp_ms']==middle]
        if len(rows)!=1:return False
        row=rows[0]
        if _dialogue_text_moved(rows_by_evidence[pa],row) or _dialogue_text_moved(row,rows_by_evidence[pb]):return False
        from .gameplay import effects_from_lines
        for line in row.get('facts',{}).get('occluded_receipt_lines',[]):
            if line.get('recipient_name_occluded') is not True:continue
            if not same_slot(ba,line.get('box',[])) or not same_slot(bb,line.get('box',[])):continue
            # Read the retained pre-occlusion grammar only to identify this
            # unknown slot. Its recipient is never restored as an effect.
            parsed=effects_from_lines([line])
            if len(parsed)==1 and all(parsed[0].get(k)==effect.get(k) for k in ('kind','amount','value')):
                return row['evidence']
        return False
    def identity(effect):return (effect['kind'],effect['name'])
    def field(effect):return effect['kind']+'||'+effect['name']
    def observations(effect):
        key=field(effect);result=[]
        for proof in event['field_evidence'].get(key,[]):
            row=rows_by_evidence.get(proof)
            if row is None:continue
            texts={effect.get('raw_text'),effect.get('original_text')}-{None}
            lines=[l for l in row.get('ocr',{}).get('neural',[])
                   if l.get('confidence',0)>=95 and l.get('text') in texts]
            if len(lines)==1:
                result.append((row['source_timestamp_ms'],proof,lines[0]['box']))
        return result
    observed={identity(e):observations(e) for e in effects}
    disputed=set()
    for index,left in enumerate(effects):
        for right in effects[index+1:]:
            a,b=left['name'],right['name']
            # Geometry and time identify the disputed slot. OCR can lose many
            # characters under an overlay; edit distance cannot establish that
            # the changing text describes separate people.
            if left['kind']!=right['kind'] or a==b:continue
            if left.get('amount')!=right.get('amount') or left.get('value')!=right.get('value'):continue
            first,second=observed[identity(left)],observed[identity(right)]
            if {x[0] for x in first}&{x[0] for x in second}:continue
            pairs=[];bridges=[]
            for ta,pa,ba in first:
                for tb,pb,bb in second:
                    bridge=occluded_bridge(ta,tb,pa,pb,ba,bb,left) if abs(ta-tb)>250 else False
                    if not (0<abs(ta-tb)<=250 or bridge):continue
                    if _dialogue_text_moved(rows_by_evidence[pa],rows_by_evidence[pb]):continue
                    if same_slot(ba,bb):
                        pairs.append([pa,pb])
                        if bridge:bridges.append(bridge)
            if not pairs:continue
            disputed.update((identity(left),identity(right)))
            for effect in (left,right):
                event['conflicting_readings'].append(dict(field=field(effect),
                    reason='recipient_name_changes_in_adjacent_same_slot_receipt',
                    name_candidates=[a,b],evidence_pairs=pairs,
                    **({'occluded_bridge_evidence':sorted(set(bridges))} if bridges else {})))
    if disputed:
        event.setdefault('ambiguous_effect_candidates',[]).extend(
            dict(effect=e,reason='unresolved_recipient_identity',
                 evidence=event['field_evidence'].get(field(e),[]))
            for e in effects if identity(e) in disputed)
        event['effects']=[e for e in event['effects'] if not
            (e.get('name') and identity(e) in disputed)]


def flag_inheritance_identity_conflicts(event,rows_by_evidence):
    """Abstain when adjacent views disagree within one scrolling receipt.

    Inheritance names are deliberately never normalized here.  Two accepted
    names become ambiguous only when their source rows are adjacent,
    event-outcome rows share a title context, and their line geometry proves
    one receipt slot.  A moving slot also needs one unique exact neighboring
    OCR line with the same upward motion.  This keeps separate visible
    inspiration lines distinct and does not depend on a name catalog,
    similarity score, cursor interpretation, or animation effects.
    """
    if not isinstance(event,dict) or not isinstance(rows_by_evidence,dict):return
    effects=[e for e in event.get('effects',[]) if isinstance(e,dict)
             and e.get('kind')=='inheritance_inspiration'
             and isinstance(e.get('name'),str) and e.get('name')]
    field_evidence=event.get('field_evidence',{})
    if len(effects)<2 or not isinstance(field_evidence,dict):return

    def valid_box(box):
        return (isinstance(box,(list,tuple)) and len(box)==4
                and all(type(value) in (int,float) and math.isfinite(value) for value in box)
                and 148<=box[0]<box[2]<=958 and 0<=box[1]<box[3]<=1080)

    def center(box):return (box[1]+box[3])/2

    def height(box):return box[3]-box[1]

    def row_lines(row):
        if not isinstance(row,dict) or row.get('screen')!='event_outcome':return []
        ocr=row.get('ocr',{})
        if not isinstance(ocr,dict) or not isinstance(ocr.get('neural'),list):return []
        result=[]
        for line in ocr['neural']:
            box=line.get('box',[]) if isinstance(line,dict) else []
            confidence=line.get('confidence',0) if isinstance(line,dict) else 0
            if (not isinstance(line,dict) or not valid_box(box) or
                type(confidence) not in (int,float) or not math.isfinite(confidence) or
                confidence<95 or line.get('overlay_occluded') is True or
                not 780<=center(box)<=960 or not isinstance(line.get('text'),str)):
                continue
            result.append(line)
        return result

    def titles(value):
        if not isinstance(value,dict):return set()
        return {item.strip() for key in ('context_title','context_title_candidate')
                for item in [value.get(key)] if isinstance(item,str) and item.strip()}

    event_titles=titles(event)

    def same_context(left,right):
        left_titles,right_titles=titles(left),titles(right)
        return left_titles==right_titles and len(event_titles|left_titles)<=1

    def field(effect):return effect['kind']+'||'+effect['name']

    def texts(effect):
        return {item for item in (effect.get('raw_text'),effect.get('original_text'))
                if isinstance(item,str) and item}

    def observations(effect):
        proofs=field_evidence.get(field(effect),[])
        if not isinstance(proofs,list):return []
        result=[]
        for proof in dict.fromkeys(item for item in proofs if isinstance(item,str)):
            row=rows_by_evidence.get(proof)
            matches=[line for line in row_lines(row) if line.get('text') in texts(effect)]
            if len(matches)!=1 or not isinstance(row,dict):continue
            timestamp=row.get('source_timestamp_ms')
            if type(timestamp) is not int:continue
            result.append(dict(timestamp=timestamp,evidence=proof,row=row,line=matches[0]))
        return result

    def adjacent(left,right):
        if left['timestamp']==right['timestamp']:return False
        start,end=sorted((left['timestamp'],right['timestamp']))
        if end-start>250:return False
        # The accepted observations must be consecutive source rows.  A row
        # between them would make a two-point identity pairing ambiguous.
        return not any(isinstance(row,dict) and type(row.get('source_timestamp_ms')) is int
                       and start<row['source_timestamp_ms']<end
                       for row in rows_by_evidence.values())

    def stable_geometry(left,right):
        return (valid_box(left) and valid_box(right)
                and abs(left[0]-right[0])<=5
                # Keep the x origin, width, and height stable while allowing
                # a modest OCR box change for a different spelling.
                and abs((left[2]-left[0])-(right[2]-right[0]))<=35
                and abs(height(left)-height(right))<=8)

    def inspiration_lines(row):
        """Retain even weak Inspired-by boxes as topology evidence only."""
        if not isinstance(row,dict):return []
        ocr=row.get('ocr',{})
        if not isinstance(ocr,dict) or not isinstance(ocr.get('neural'),list):return []
        return [line for line in ocr['neural']
                if isinstance(line,dict) and valid_box(line.get('box',[]))
                and 780<=center(line['box'])<=960
                and isinstance(line.get('text'),str)
                and re.match(r'^\s*Inspired\s+by\b',line['text'],re.I)]

    def competing_inspiration(row,target_box,left,right):
        """Reject a target slot with another nearby Inspired-by candidate.

        A low-confidence neighboring name is not accepted as an effect.  Its
        box still prevents a generic spark line from being mistaken for the
        same physical receipt occurrence.
        """
        target_texts=texts(left)|texts(right)
        for line in inspiration_lines(row):
            if line.get('text') in target_texts and line.get('box')==target_box:continue
            if abs(center(line['box'])-center(target_box))<=35:return True
        return False

    def simultaneous(row,left,right):
        left_texts,right_texts=texts(left),texts(right)
        found={line.get('text') for line in row_lines(row)}
        return bool(found&left_texts) and bool(found&right_texts)

    def moving_anchor(first,second,target_first,target_second,left,right):
        target_dy=center(target_second)-center(target_first)
        if not -80<=target_dy<=-8:return None
        first_lines=row_lines(first);second_lines=row_lines(second)
        first_by={};second_by={}
        for line in first_lines:first_by.setdefault(line['text'],[]).append(line)
        for line in second_lines:second_by.setdefault(line['text'],[]).append(line)
        excluded=texts(left)|texts(right);candidates=[]
        for text in sorted(first_by.keys()&second_by.keys()):
            if text in excluded:continue
            # A repeated line cannot identify one physical neighboring slot,
            # even when another line in the frame would otherwise qualify.
            if len(first_by[text])!=1 or len(second_by[text])!=1:return None
            anchor_first,anchor_second=first_by[text][0],second_by[text][0]
            box_first,box_second=anchor_first['box'],anchor_second['box']
            if not stable_geometry(box_first,box_second):return None
            anchor_dy=center(box_second)-center(box_first)
            if abs(anchor_dy)<8:continue
            # Any other clearly moving shared line that disagrees with the
            # target motion is contradictory evidence for this receipt track.
            if not -80<=anchor_dy<=-8 or abs(target_dy-anchor_dy)>8:return None
            # The target-to-anchor spacing must survive the scroll.  This
            # rejects a name that merely moved into another receipt slot.
            first_spacing=center(target_first)-center(box_first)
            second_spacing=center(target_second)-center(box_second)
            if abs(first_spacing-second_spacing)>8:return None
            candidates.append(dict(text=text,first=box_first,second=box_second,
                                   delta_y=anchor_dy))
        if not candidates:return None
        # More than one independently unique anchor is stronger evidence when
        # all anchors agree on the same rigid motion. Contradictory anchors
        # must leave the names unresolved.
        first_delta=candidates[0]['delta_y']
        if any(abs(item['delta_y']-first_delta)>8 for item in candidates):return None
        return dict(anchor=candidates[0],anchors=candidates)

    def track(left,right,first,second):
        if not adjacent(first,second):return None
        earlier,later=(first,second) if first['timestamp']<second['timestamp'] else (second,first)
        if not same_context(earlier['row'],later['row']):return None
        if simultaneous(earlier['row'],left,right) or simultaneous(later['row'],left,right):return None
        first_box,later_box=earlier['line']['box'],later['line']['box']
        if not stable_geometry(first_box,later_box):return None
        # A stationary slot alone cannot distinguish a replacement receipt
        # from an OCR variant. The cursor gate handles the known Seiun case;
        # this generic guard requires source-backed scrolling motion.
        if competing_inspiration(earlier['row'],first_box,left,right) or competing_inspiration(later['row'],later_box,left,right):return None
        anchor=moving_anchor(earlier['row'],later['row'],first_box,later_box,left,right)
        if anchor is None:return None
        return dict(mode='upward_scroll',evidence_pair=[earlier['evidence'],later['evidence']],
                    timestamp_pair=[earlier['timestamp'],later['timestamp']],
                    target_boxes=[list(first_box),list(later_box)],
                    anchor=anchor['anchor'],anchors=anchor['anchors'])

    def combine_tracks(pairs):
        """Combine adjacent supporting windows only when they form one track."""
        unique={tuple(item['evidence_pair']):item for item in pairs}
        tracks=sorted(unique.values(),key=lambda item:(tuple(item['timestamp_pair']),tuple(item['evidence_pair'])))
        if not tracks:return None
        if len(tracks)==1:return tracks[0]
        if {item['mode'] for item in tracks}!={'upward_scroll'}:return None
        times=sorted({time for item in tracks for time in item['timestamp_pair']})
        if any(later-earlier>250 for earlier,later in zip(times,times[1:])):return None
        links={evidence:set() for item in tracks for pair in [item['evidence_pair']]
               for evidence in pair}
        for item in tracks:
            first,second=item['evidence_pair'];links[first].add(second);links[second].add(first)
        pending=[sorted(links)[0]];seen=set()
        while pending:
            evidence=pending.pop()
            if evidence in seen:continue
            seen.add(evidence);pending.extend(links[evidence]-seen)
        if len(seen)!=len(links):return None
        common={anchor['text'] for anchor in tracks[0]['anchors']}
        for item in tracks[1:]:common &= {anchor['text'] for anchor in item['anchors']}
        if not common:return None
        # A connected evidence graph is necessary but not sufficient: the
        # target and each shared anchor must also continue through the joins
        # in timestamp order.  This rejects two unrelated receipt windows
        # that happen to have the same neighboring text.
        for previous,current in zip(tracks,tracks[1:]):
            previous_target=previous['target_boxes'][1]
            current_target=current['target_boxes'][0]
            if not stable_geometry(previous_target,current_target):return None
            target_join=center(current_target)-center(previous_target)
            if not -80<=target_join<=8:return None
            for anchor_text in sorted(common):
                previous_anchor=next(anchor for anchor in previous['anchors']
                                     if anchor['text']==anchor_text)
                current_anchor=next(anchor for anchor in current['anchors']
                                    if anchor['text']==anchor_text)
                if not stable_geometry(previous_anchor['second'],current_anchor['first']):return None
        anchors=[]
        for text in sorted(common):
            anchors.append(next(anchor for anchor in tracks[0]['anchors'] if anchor['text']==text))
        return dict(mode='upward_scroll',evidence_pair=tracks[0]['evidence_pair'],
                    timestamp_pair=tracks[0]['timestamp_pair'],
                    evidence_pairs=[item['evidence_pair'] for item in tracks],
                    timestamp_pairs=[item['timestamp_pair'] for item in tracks],
                    target_boxes=[item['target_boxes'] for item in tracks],
                    anchor=anchors[0],anchors=anchors,
                    supporting_tracks=tracks)

    observed={id(effect):observations(effect) for effect in effects}
    disputed=[]
    for index,left in enumerate(effects):
        for right in effects[index+1:]:
            if left.get('name')==right.get('name'):continue
            if any(left.get(key)!=right.get(key) for key in ('field','amount','direction','value')):continue
            left_observed,right_observed=observed[id(left)],observed[id(right)]
            if not left_observed or not right_observed:continue
            if any(simultaneous(item['row'],left,right)
                   for item in left_observed+right_observed):continue
            pairs=[]
            for first in left_observed:
                for second in right_observed:
                    candidate=track(left,right,first,second)
                    if candidate is not None:pairs.append(candidate)
            # Only one pair or one connected, geometrically continuous track
            # can establish the disputed slot. Otherwise preserve both names.
            continuity=combine_tracks(pairs)
            if continuity is None:continue
            disputed.extend((left,right))
            for effect in (left,right):
                event.setdefault('conflicting_readings',[]).append(dict(
                    field=field(effect),
                    reason='inheritance_inspiration_identity_changes_in_tracked_receipt',
                    name_candidates=[left['name'],right['name']],
                    evidence_pairs=continuity.get('evidence_pairs', [continuity['evidence_pair']]),
                    continuity=deepcopy(continuity)))

    disputed_ids={id(effect) for effect in disputed}
    if not disputed_ids:return
    existing=set()
    for candidate in event.get('ambiguous_effect_candidates',[]):
        if (isinstance(candidate,dict) and isinstance(candidate.get('effect'),dict)
                and isinstance(candidate['effect'].get('name'),str)):
            existing.add(field(candidate['effect']))
    for effect in effects:
        if id(effect) not in disputed_ids or field(effect) in existing:continue
        event.setdefault('ambiguous_effect_candidates',[]).append(dict(
            effect=deepcopy(effect),reason='unresolved_inheritance_inspiration_identity',
            evidence=list(field_evidence.get(field(effect),[]))))
    event['effects']=[effect for effect in event.get('effects',[])
                      if id(effect) not in disputed_ids]


def _occluded_wrapped_hint_bridge(start,end,effect,rows_by_evidence):
    """Prove continuity across one obscured first line, without reading its name."""
    if end-start!=500:return None
    selected=[]
    for time in (start,start+250,end):
        rows=[r for r in rows_by_evidence.values() if r['source_timestamp_ms']==time]
        if len(rows)!=1:return None
        selected.append(rows[0])
    before,middle,after=selected
    if _dialogue_text_moved(before,middle) or _dialogue_text_moved(middle,after):return None
    prefix=f"Gained {effect['amount']} hint level(s) for "
    def parts(row):
        lines=row.get('ocr',{}).get('neural',[])
        matches=[]
        for a,b in zip(lines,lines[1:]):
            if min(a.get('confidence',0),b.get('confidence',0))<95:continue
            if a.get('text','')+' '+b.get('text','')!=effect.get('raw_text'):continue
            ba,bb=a.get('box',[]),b.get('box',[])
            if len(ba)!=4 or len(bb)!=4:continue
            if not 780<=ba[1]<bb[1]<=960 or bb[1]-ba[1]>=40 or abs(ba[0]-bb[0])>15:continue
            matches.append((a,b))
        return matches
    a,b=parts(before),parts(after)
    if len(a)!=1 or len(b)!=1:return None
    def near(x,y):
        if len(x)!=4 or len(y)!=4:return False
        intersection=max(0,min(x[2],y[2])-max(x[0],y[0]))*max(0,min(x[3],y[3])-max(x[1],y[1]))
        union=(x[2]-x[0])*(x[3]-x[1])+(y[2]-y[0])*(y[3]-y[1])-intersection
        return union>0 and intersection/union>=.8 and abs(x[0]-y[0])<=3 and abs(x[2]-y[2])<=3 and abs(x[1]+x[3]-y[1]-y[3])<=6
    if any(x['text']!=y['text'] or not near(x['box'],y['box']) for x,y in zip(a[0],b[0])):return None
    occluded=[l for l in middle.get('facts',{}).get('occluded_receipt_lines',[])
              if l.get('text','').startswith(prefix) and l.get('overlay_boxes')
              and near(l.get('box',[]),a[0][0]['box']) and near(l.get('box',[]),b[0][0]['box'])]
    tails=[l for l in middle.get('ocr',{}).get('neural',[]) if l.get('confidence',0)>=95
           and l.get('text')==a[0][1]['text'] and near(l.get('box',[]),a[0][1]['box'])
           and near(l.get('box',[]),b[0][1]['box'])]
    if len(occluded)!=1 or len(tails)!=1:return None
    return middle['evidence']


def collapse_visual_hint_variants(event,timestamps,rows_by_evidence=None):
    """Keep one award when contiguous receipt frames lose a proven marker.

    Only the exact original OCR sentence of a pixel-corrected observation may
    be folded into it. Uncorrected frames remain alternate evidence, never
    proof of the corrected symbol. This operates within one receipt event.
    """
    hints=[e for e in event['effects'] if e['kind']=='skill_hint_change']
    removed=[]
    for weak in hints:
        if weak.get('visual_symbol_observation'):continue
        weak_key='skill_hint_change||'+weak['name']
        weak_proofs=event['field_evidence'].get(weak_key,[])
        weak_times=sorted({timestamps[p] for p in weak_proofs if p in timestamps})
        candidates=[]
        for strong in hints:
            symbol=strong.get('visual_symbol_observation',{})
            if symbol.get('method')!='strict_terminal_ring_geometry':continue
            if strong.get('original_text')!=weak.get('raw_text') or strong.get('amount')!=weak.get('amount'):continue
            strong_key='skill_hint_change||'+strong['name']
            if any(c.get('field') in (weak_key,strong_key) for c in event.get('conflicting_readings',[])):continue
            strong_times=sorted({timestamps[p] for p in event['field_evidence'].get(strong_key,[]) if p in timestamps})
            if len(strong_times)<2 or not weak_times:continue
            combined=sorted(set(strong_times+weak_times))
            bridges=[];unresolved=False
            for a,b in zip(combined,combined[1:]):
                if b-a<=250:continue
                bridge=_occluded_wrapped_hint_bridge(a,b,weak,rows_by_evidence or {})
                if bridge is None:unresolved=True;break
                bridges.append(bridge)
            if unresolved:continue
            candidates.append((strong,bridges))
        if len(candidates)!=1:continue
        target,bridges=candidates[0]
        target.setdefault('observed_name_candidates',[target['name']]).append(weak['name'])
        target.setdefault('alternate_name_evidence',[]).append(dict(name=weak['name'],evidence=list(weak_proofs)))
        target['name_resolution']='exact_original_text_in_contiguous_pixel_verified_hint_receipt'
        if bridges:target['occluded_continuity_evidence']=bridges
        removed.append(weak)
    event['effects']=[e for e in event['effects'] if e not in removed]


def collapse_punctuated_hint_variants(event,rows_by_evidence):
    """Resolve a lost name terminator using repeated same-frame skill labels.

    A longer spelling alone is insufficient: the shorter receipt must overlap
    two independent frames displaying the complete skill label. Keep its
    evidence separate from the observations that actually read both marks.
    """
    hints=[e for e in event['effects'] if e['kind']=='skill_hint_change']
    removed=[]
    def observations(effect):
        key='skill_hint_change||'+effect['name']
        result=[]
        for proof in event['field_evidence'].get(key,[]):
            row=rows_by_evidence.get(proof)
            if row is None:continue
            lines=row.get('ocr',{}).get('neural',[])
            matches=[l for l in lines if l.get('text')==effect.get('raw_text')
                     and l.get('confidence',0)>=95 and len(l.get('box',[]))==4
                     and 780<=(l['box'][1]+l['box'][3])/2<=960]
            if len(matches)==1:result.append((row['source_timestamp_ms'],proof,lines))
        return result
    for weak in hints:
        candidates=[]
        first=observations(weak)
        for strong in hints:
            if strong['name']!=weak['name']+'!':continue
            if strong.get('amount')!=weak.get('amount'):continue
            if strong.get('raw_text')!=weak.get('raw_text','')+'.':continue
            keys={'skill_hint_change||'+e['name'] for e in (weak,strong)}
            if any(c.get('field') in keys for c in event.get('conflicting_readings',[])):continue
            second=observations(strong)
            a={t for t,_,_ in first};b={t for t,_,_ in second}
            if not a or len(b)<2 or a&b:continue
            times=sorted(a|b)
            if any(y-x>250 for x,y in zip(times,times[1:])):continue
            labels=[]
            for t,proof,lines in first:
                matches=[l for l in lines if l.get('text')==strong['name']
                         and l.get('confidence',0)>=95 and len(l.get('box',[]))==4
                         and 600<=(l['box'][1]+l['box'][3])/2<=780]
                if len(matches)==1:labels.append((t,proof))
            if len({t for t,_ in labels})<2:continue
            candidates.append((strong,labels))
        if len(candidates)!=1:continue
        target,labels=candidates[0]
        target.setdefault('observed_name_candidates',[target['name']]).append(weak['name'])
        target.setdefault('alternate_name_evidence',[]).append(dict(
            name=weak['name'],evidence=[p for _,p,_ in first]))
        target['name_resolution']='repeated_same_frame_label_and_contiguous_punctuated_receipt'
        target['name_label_evidence']=[p for _,p in labels]
        removed.append(weak)
    event['effects']=[e for e in event['effects'] if e not in removed]


_HINT_SEPARATOR_RE = re.compile(r"[\s\-\u2010-\u2015\u2212]+")


def _separator_identity(value):
    """Compare hint names while retaining every non-separator character.

    Spaces and hyphen-like separators are the only characters normalized here.
    In particular, trailing rank glyphs or OCR letters are retained as part of
    the identity; ``Skill ○`` and ``Skill O`` therefore remain different.
    """
    if not isinstance(value,str):return None
    return _HINT_SEPARATOR_RE.sub(' ',value.strip())


def _separator_hint_observations(effect,event,rows_by_evidence):
    """Return high-confidence source lines for one exact hint spelling."""
    raw_text=effect.get('raw_text')
    if not isinstance(raw_text,str) or not raw_text.strip():return []
    key='skill_hint_change||'+effect.get('name','')
    proofs=event.get('field_evidence',{}).get(key,[])
    if not isinstance(proofs,list):return []
    result=[]
    for proof in dict.fromkeys(item for item in proofs if isinstance(item,str)):
        row=rows_by_evidence.get(proof)
        if not isinstance(row,dict) or type(row.get('source_timestamp_ms')) is not int:continue
        ocr=row.get('ocr',{})
        if not isinstance(ocr,dict) or not isinstance(ocr.get('neural'),list):continue
        lines=[]
        for line in ocr['neural']:
            if not isinstance(line,dict):continue
            box=line.get('box',[])
            confidence=line.get('confidence')
            line_text=line.get('text')
            if (not isinstance(line_text,str)
                    or (_separator_identity(line_text)!=_separator_identity(raw_text)
                        and line_text!=raw_text)
                    or line.get('overlay_occluded') is True
                    or type(confidence) not in (int,float) or not 0<=confidence<=100
                    or not math.isfinite(confidence)
                    or confidence<95 or not isinstance(box,(list,tuple)) or len(box)!=4
                    or not all(type(value) in (int,float) and -10000<=value<=10000
                               and math.isfinite(value) for value in box)
                    or not 148<=box[0]<box[2]<=958 or not 0<=box[1]<box[3]<=1080
                    or not 780<=(box[1]+box[3])/2<=960):continue
            lines.append(line)
        if len(lines)==1:
            result.append(dict(timestamp=row['source_timestamp_ms'],evidence=proof,
                               row=row,line=lines[0]))
    return result


def _separator_hint_line_compatible(first,second):
    """Require one stationary receipt slot for two separator spellings."""
    if not isinstance(first,dict) or not isinstance(second,dict):return False
    a,b=first.get('box',[]),second.get('box',[])
    if not isinstance(a,(list,tuple)) or not isinstance(b,(list,tuple)) or len(a)!=4 or len(b)!=4:return False
    if not all(type(value) in (int,float) and -10000<=value<=10000 and math.isfinite(value)
               for value in (*a,*b)):return False
    return (abs(a[0]-b[0])<=5 and abs(a[2]-b[2])<=8
            and abs((a[1]+a[3])-(b[1]+b[3]))/2<=10
            and abs((a[3]-a[1])-(b[3]-b[1]))<=8)


def _separator_hint_pair_compatible(first,second):
    """Require adjacent, same-context source observations for one receipt."""
    if first['timestamp']==second['timestamp'] or abs(first['timestamp']-second['timestamp'])>250:return False
    left,right=first['row'],second['row']
    if left.get('screen')!='event_outcome' or right.get('screen')!='event_outcome':
        return False
    left_title=left.get('context_title') or left.get('context_title_candidate')
    right_title=right.get('context_title') or right.get('context_title_candidate')
    if left_title and right_title and left_title!=right_title:return False
    return _separator_hint_line_compatible(first['line'],second['line'])


def collapse_separator_hint_variants(event,rows_by_evidence):
    """Collapse a same-receipt hint spelling that only changes separators.

    The helper preserves the selected OCR spelling and records the alternate
    evidence. It never strips or rewrites rank symbols, and it requires a
    same-amount, same-sentence, adjacent source line in one receipt slot.
    Separate events, differing amounts, differing suffixes, context changes,
    and moving line geometry remain separate or unresolved.
    """
    if not isinstance(event,dict) or not isinstance(rows_by_evidence,dict):return
    effects=[e for e in event.get('effects',[]) if isinstance(e,dict)
             and e.get('kind')=='skill_hint_change' and isinstance(e.get('name'),str)
             and e.get('name')]
    if len(effects)<2:return
    field_evidence=event.get('field_evidence',{})
    if not isinstance(field_evidence,dict):return

    def key(effect):return 'skill_hint_change||'+effect['name']
    def conflicts(effect):
        items=event.get('conflicting_readings',[])
        if not isinstance(items,list):return True
        return any(item.get('field')==key(effect)
                   for item in items if isinstance(item,dict))
    def score(effect,observations):
        by_timestamp={}
        for item in observations:
            timestamp=item['timestamp']
            by_timestamp[timestamp]=max(by_timestamp.get(timestamp,0),item['line'].get('confidence',0))
        confidences=list(by_timestamp.values())
        return (len(confidences),sum(confidences),max(confidences,default=0),effect['name'])

    removed=[]
    while True:
        merged_one=False
        active=[effect for effect in effects if effect not in removed]
        for index,left in enumerate(active):
            if conflicts(left):continue
            left_identity=_separator_identity(left['name'])
            if left_identity is None:continue
            for right in active[index+1:]:
                if conflicts(right) or left.get('name')==right.get('name'):continue
                if any(left.get(field)!=right.get(field) for field in ('field','amount','direction','value')):continue
                if _separator_identity(right['name'])!=left_identity:continue
                left_raw,right_raw=left.get('raw_text'),right.get('raw_text')
                if (not isinstance(left_raw,str) or not isinstance(right_raw,str)
                        or _separator_identity(left_raw)!=_separator_identity(right_raw)):continue
                left_observations=_separator_hint_observations(left,event,rows_by_evidence)
                right_observations=_separator_hint_observations(right,event,rows_by_evidence)
                pairs=[(a,b) for a in left_observations for b in right_observations
                       if _separator_hint_pair_compatible(a,b)]
                if not pairs:continue
                # Prefer the spelling backed by more source observations. This is
                # an evidence tie-breaker, not a canonical name or skill catalog.
                target,alternate=(left,right) if score(left,left_observations)>=score(right,right_observations) else (right,left)
                target_key=key(target);alternate_key=key(alternate)
                target.setdefault('observed_name_candidates',[target['name']])
                for candidate in alternate.get('observed_name_candidates',[]):
                    if candidate not in target['observed_name_candidates']:
                        target['observed_name_candidates'].append(candidate)
                if alternate['name'] not in target['observed_name_candidates']:
                    target['observed_name_candidates'].append(alternate['name'])
                prior_evidence=alternate.get('alternate_name_evidence',[])
                if isinstance(prior_evidence,list):
                    target.setdefault('alternate_name_evidence',[])
                    for item in prior_evidence:
                        if item not in target['alternate_name_evidence']:
                            target['alternate_name_evidence'].append(deepcopy(item))
                target.setdefault('alternate_name_evidence',[]).append(dict(
                    name=alternate['name'],evidence=[item['evidence'] for item in
                    _separator_hint_observations(alternate,event,rows_by_evidence)],
                    evidence_pairs=[[a['evidence'],b['evidence']] for a,b in pairs],
                    source_timestamps_ms=sorted({item['timestamp'] for pair in pairs for item in pair}),
                    basis='same_amount_sentence_and_adjacent_stationary_receipt_slot'))
                target['name_resolution']='same_receipt_separator_variant'
                merged=list(field_evidence.get(target_key,[]))
                for proof in field_evidence.get(alternate_key,[]):
                    if proof not in merged:merged.append(proof)
                field_evidence[target_key]=merged
                removed.append(alternate)
                merged_one=True
                break
            if merged_one:break
        if not merged_one:break
    event['effects']=[effect for effect in event['effects'] if effect not in removed]


def collapse_song_variants(event,timestamps):
    songs=[e for e in event['effects'] if e['kind']=='song_learned']
    removed=[]
    def times(effect):
        return sorted({timestamps[p] for p in event['field_evidence'].get('song_learned||'+effect['name'],[]) if p in timestamps})
    for weak in songs:
        observed=times(weak)
        if len(observed)!=1:continue
        candidates=[]
        for strong in songs:
            complete=times(strong);a=strong['name'];b=weak['name']
            if len(complete)<3 or not complete[0]<=observed[0]<=complete[-1]+250:continue
            if len(a)==len(b)+1 and any(a[:i]+a[i+1:]==b for i in range(len(a))):candidates.append(strong)
        if len(candidates)!=1:continue
        target=candidates[0]
        target.setdefault('observed_name_candidates',[target['name']]).append(weak['name'])
        target['name_resolution']='repeated_complete_name_with_one_overlapping_or_adjacent_missing_character_observation'
        removed.append(weak)
    event['effects']=[e for e in event['effects'] if e not in removed]
