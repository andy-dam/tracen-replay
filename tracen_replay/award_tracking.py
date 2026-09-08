"""Associate visible stat awards between two confident, stationary label reads."""
import re
from .animated_performance import STAT_LABELS


def _field(text,*,anchor=False):
    cleaned=re.sub(r'\s+' if anchor else r'[\s.-]+','',text)
    return {name.replace(' ',''):field for name,field in STAT_LABELS.items()}.get(cleaned)


def _near(a,b):
    return max(abs(x-y) for x,y in zip(a,b))<=8


def tracked_stats(readings,start,end):
    """No state totals or expected awards are inputs to this association."""
    tracks={};complete=[]
    for row in sorted(readings,key=lambda r:r['source_timestamp_ms']):
        time=row['source_timestamp_ms']
        if not start<=time<=end:continue
        labels={}
        lines=row.get('ocr',{}).get('neural',[]) if row['screen']=='event_outcome' else []
        for label in lines:
            a,b,c,d=label['box'];field=_field(label['text'])
            if field and 150<=a<c<=900 and 240<=b<d<=750 and 30<=d-b<=70:
                labels.setdefault(field,[]).append(label)
        for field in list(tracks):
            found=labels.get(field,[]);previous=tracks[field][-1]
            if (len(found)!=1 or time-previous['row']['source_timestamp_ms']>50
                    or not _near(found[0]['box'],previous['label']['box'])):
                complete.append(tracks.pop(field))
        for field,found in labels.items():
            if len(found)!=1:continue
            label=found[0];a,b,c,d=label['box']
            canonical=next(name for name,value in STAT_LABELS.items() if value==field)
            pattern=r'\s*'.join(map(re.escape,canonical.split()))
            forbidden=any(re.match('^'+pattern+r'\s+(?:cap|Bonus)\b',l['text']) for l in lines)
            if forbidden:
                if field in tracks:complete.append(tracks.pop(field))
                continue
            captions=[l for l in lines if l['confidence']>=95 and 770<=l['box'][1]<1000
                      and re.match('^'+pattern+r'\s+went up by\b',l['text'])]
            gains=[l for l in lines if re.fullmatch(r'\+\d{1,3}',l['text']) and l['confidence']>=97
                   and 50<=l['box'][3]-l['box'][1]<=130 and -20<=b-l['box'][3]<=25
                   and abs((l['box'][0]+l['box'][2]-a-c)/2)<=60]
            tracks.setdefault(field,[]).append(dict(row=row,label=label,field=field,canonical=canonical,
                anchor=label['confidence']>=97 and _field(label['text'],anchor=True)==field,
                gain=gains[0] if len(gains)==1 else None,ambiguous_gains=len(gains)>1,
                caption=captions[0]['text'] if captions else None))
    complete.extend(tracks.values());result=[];used=set()
    for track in complete:
        anchors=[r for r in track if r['anchor']]
        for first,last in zip(anchors,anchors[1:]):
            a=first['row']['source_timestamp_ms'];b=last['row']['source_timestamp_ms']
            if not 50<=b-a<=250 or not _near(first['label']['box'],last['label']['box']):continue
            window=[r for r in track if a<=r['row']['source_timestamp_ms']<=b]
            if any(r['ambiguous_gains'] or not _near(r['label']['box'],first['label']['box']) for r in window):continue
            visible=[r for r in window if r['gain']]
            times={r['row']['source_timestamp_ms'] for r in visible}
            if len(times)<3 or max(times)-min(times)<50 or len({r['gain']['text'] for r in visible})!=1:continue
            if not any(r['caption'] for r in visible):continue
            proofs=[dict(source_timestamp_ms=r['row']['source_timestamp_ms'],evidence=r['row']['evidence'],
                         label_box=r['label']['box'],raw_text=r['label']['text'],confidence=r['label']['confidence']) for r in (first,last)]
            for item in visible:
                row=item['row'];key=(item['field'],row['source_timestamp_ms'])
                if key in used:continue
                used.add(key);gain=item['gain']
                candidate=dict(kind='stat_change',field=item['field'],amount=int(gain['text'][1:]),
                    raw_text=gain['text']+' '+item['canonical'],confidence=min(gain['confidence'],*(p['confidence'] for p in proofs)),
                    gain_box=gain['box'],label_box=item['label']['box'],receipt_text=item['caption'],
                    label_anchors=proofs,label_identity_basis='two_confident_labels_bracket_stationary_award')
                result.append((row,candidate))
    return result
