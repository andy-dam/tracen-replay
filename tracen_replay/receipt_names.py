"""Retain receipt identity uncertainty and resolve evidence-backed variants."""


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


def collapse_visual_hint_variants(event,timestamps):
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
            if any(b-a>250 for a,b in zip(combined,combined[1:])):continue
            candidates.append(strong)
        if len(candidates)!=1:continue
        target=candidates[0]
        target.setdefault('observed_name_candidates',[target['name']]).append(weak['name'])
        target.setdefault('alternate_name_evidence',[]).append(dict(name=weak['name'],evidence=list(weak_proofs)))
        target['name_resolution']='exact_original_text_in_contiguous_pixel_verified_hint_receipt'
        removed.append(weak)
    event['effects']=[e for e in event['effects'] if e not in removed]


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
