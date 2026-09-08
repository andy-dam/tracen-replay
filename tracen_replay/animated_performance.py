"""Read large event currency awards without using surrounding balance changes."""
import re


LABELS={'Dance':'dance','Passion':'passion','Vocals':'vocal','Visuals':'visual','Composure':'composure'}


def candidates(lines,screen):
    if screen!='event_outcome':return []
    found=[]
    for label in lines:
        field=LABELS.get(label['text'])
        a,b,c,d=label['box']
        if not field or label['confidence']<97 or not (150<=a<c<=900 and 240<=b<d<=650 and 30<=d-b<=70):continue
        captions=[l for l in lines if l['confidence']>=95 and 770<=l['box'][1]<1000
                  and re.match(r'^'+re.escape(label['text'])+r' went up by\b',l['text'])]
        if not captions:continue
        gains=[]
        for line in lines:
            x,y,z,w=line['box'];match=re.fullmatch(r'\+(\d{1,3})',line['text'])
            if match and line['confidence']>=97 and 50<=w-y<=110 and -5<=b-w<=25 and abs((x+z-a-c)/2)<=60:
                gains.append(line)
        if len(gains)!=1:continue
        gain=gains[0]
        found.append(dict(kind='performance_change',field=field,amount=int(gain['text'][1:]),
            raw_text=gain['text']+' '+label['text'],confidence=min(gain['confidence'],label['confidence']),
            gain_box=gain['box'],label_box=label['box'],receipt_text=captions[0]['text']))
    return found


def reconcile(event,readings):
    groups={}
    for row in readings:
        if not event['first_seen_ms']<=row['source_timestamp_ms']<=event['last_seen_ms']:continue
        for candidate in row.get('facts',{}).get('animated_performance_candidates',[]):
            groups.setdefault(candidate['field'],[]).append((row,candidate))
    for field,observations in groups.items():
        values={c['amount'] for _,c in observations};times={r['source_timestamp_ms'] for r,_ in observations}
        if len(values)!=1 or len(times)<3 or max(times)-min(times)<50:continue
        key='performance_change|'+field+'|';candidate=observations[0][1]
        if any(c['field']==key for c in event['conflicting_readings']):continue
        prior=event['effects'].get(key)
        if prior and prior['amount']!=candidate['amount']:
            event['effects'].pop(key)
            event['conflicting_readings'].append(dict(field=key,reason='receipt_animation_disagreement',
                receipt_amount=prior['amount'],animated_amount=candidate['amount']))
            continue
        proofs=[dict(source_timestamp_ms=r['source_timestamp_ms'],evidence=r['evidence'],
                     gain_box=c['gain_box'],label_box=c['label_box'],receipt_text=c['receipt_text']) for r,c in observations]
        event.setdefault('animated_performance_evidence',{})[field]=proofs
        if not prior:
            event['effects'][key]=dict(candidate,confirmation='repeated_labeled_award_animation')
            event['field_evidence'][key]=[r['evidence'] for r,_ in observations]
