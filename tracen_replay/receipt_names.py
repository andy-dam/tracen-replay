"""Resolve narrowly supported missing-character variants inside one song receipt."""


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
