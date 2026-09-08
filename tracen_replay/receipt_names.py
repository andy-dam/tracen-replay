"""Resolve narrowly supported missing-character variants inside one song receipt."""


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
