"""Read large event stat and currency awards without surrounding balance changes."""
import re


LABELS={'Dance':'dance','Passion':'passion','Vocals':'vocal','Visuals':'visual','Composure':'composure'}
STAT_LABELS={'Speed':'speed','Stamina':'stamina','Power':'power','Guts':'guts','Wit':'wit','Skill Pts':'skill_points'}


def candidates(lines,screen,*,stat=False):
    if screen!='event_outcome':return []
    found=[]
    for label in lines:
        field=(STAT_LABELS if stat else LABELS).get(label['text'])
        a,b,c,d=label['box']
        if not field or label['confidence']<97 or not (150<=a<c<=900 and 240<=b<d<=(750 if stat else 650) and 30<=d-b<=70):continue
        captions=[l for l in lines if l['confidence']>=95 and 770<=l['box'][1]<1000
                  and re.match(r'^'+re.escape(label['text'])+r' went up by\b',l['text'])]
        if not captions:continue
        gains=[]
        for line in lines:
            x,y,z,w=line['box'];match=re.fullmatch(r'\+(\d{1,3})',line['text'])
            if match and line['confidence']>=97 and 50<=w-y<=(130 if stat else 110) and (-20 if stat else -5)<=b-w<=25 and abs((x+z-a-c)/2)<=60:
                gains.append(line)
        if len(gains)!=1:continue
        gain=gains[0]
        found.append(dict(kind='stat_change' if stat else 'performance_change',field=field,amount=int(gain['text'][1:]),
            raw_text=gain['text']+' '+label['text'],confidence=min(gain['confidence'],label['confidence']),
            gain_box=gain['box'],label_box=label['box'],receipt_text=captions[0]['text']))
    return found


def reconcile(event,readings,*,stat=False,receipt_observations=None):
    fact='animated_stat_candidates' if stat else 'animated_performance_candidates'
    proof_key='animated_stat_evidence' if stat else 'animated_performance_evidence'
    kind='stat_change' if stat else 'performance_change'
    groups={}
    for row in readings:
        if not event['first_seen_ms']<=row['source_timestamp_ms']<=event['last_seen_ms']:continue
        for candidate in row.get('facts',{}).get(fact,[]):
            groups.setdefault(candidate['field'],[]).append((row,candidate))
    for field,observations in groups.items():
        values={c['amount'] for _,c in observations};times={r['source_timestamp_ms'] for r,_ in observations}
        if len(values)!=1 or len(times)<3 or max(times)-min(times)<50:continue
        key=kind+'|'+field+'|';candidate=observations[0][1]
        prior=event['effects'].get(key)
        conflicts=[c for c in event['conflicting_readings'] if c['field']==key]
        observed=(receipt_observations or {}).get(key,[])
        amounts={e.get('amount') for _,_,e in observed}
        prefix_resolution=(stat and bool(observed) and all(type(n) is int and str(candidate['amount']).startswith(str(n)) for n in amounts)
                           and any(n!=candidate['amount'] for n in amounts)
                           and all(c.get('reason')=='changing_effect_value' for c in conflicts))
        if prefix_resolution and (conflicts or prior and prior['amount']!=candidate['amount']):
            event.setdefault('resolved_reading_conflicts',[]).append(dict(field=key,observed_amounts=sorted(amounts),
                accepted_amount=candidate['amount'],basis='repeated_labeled_animation_resolves_receipt_prefixes',
                receipt_evidence=[evidence for _,evidence,_ in observed]))
            event['conflicting_readings']=[c for c in event['conflicting_readings'] if c['field']!=key]
            event['effects'].pop(key,None);prior=None
        elif conflicts:continue
        if prior and prior['amount']!=candidate['amount']:
            event['effects'].pop(key)
            event['conflicting_readings'].append(dict(field=key,reason='receipt_animation_disagreement',
                receipt_amount=prior['amount'],animated_amount=candidate['amount']))
            continue
        proofs=[dict(source_timestamp_ms=r['source_timestamp_ms'],evidence=r['evidence'],
                     gain_box=c['gain_box'],label_box=c['label_box'],receipt_text=c['receipt_text']) for r,c in observations]
        event.setdefault(proof_key,{})[field]=proofs
        if not prior:
            event['effects'][key]=dict(candidate,confirmation='repeated_labeled_award_animation')
            event['field_evidence'][key]=[r['evidence'] for r,_ in observations]
