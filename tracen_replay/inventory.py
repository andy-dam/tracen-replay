"""Visible final-summary ownership, separate from carts and complete inventory."""
import re


def visible_cards(raw,final_attributes):
    if sum(type(v) is int for v in final_attributes.values())<3:return []
    lines=raw['lines']
    tabs={l['text'] for l in lines if l['confidence']>=90 and 445<=l['box'][1]<=470}
    if not {'Skills','Inspiration','Career Info'}<=tabs:return []
    cards=[]
    for row in range(7):
        top=510+63*row;bottom=min(top+63,943)
        for column,(left,right) in enumerate(((310,550),(590,830))):
            observed=[l for l in lines if left<=l['box'][0] and l['box'][2]<=right
                      and top<=(l['box'][1]+l['box'][3])/2<bottom]
            names=[l for l in observed if not re.fullmatch(r'Lvl\s*\d+',l['text'])]
            # Low-confidence wrapped fragments invalidate the card, rather than
            # producing a confident prefix as a different owned skill.
            if not names or any(l['confidence']<95 for l in names):continue
            names.sort(key=lambda l:(l['box'][1],l['box'][0]))
            text=' '.join(l['text'].strip() for l in names)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 '.,!()\-]+",text):continue
            levels=[l for l in observed if re.fullmatch(r'Lvl\s*\d+',l['text']) and l['confidence']>=95]
            level_values={int(re.fullmatch(r'Lvl\s*(\d+)',l['text'])[1]) for l in levels}
            cards.append(dict(name_text=text,slot=[row,column],text_evidence=names,
                              observed_level=next(iter(level_values)) if len(level_values)==1 else None,
                              level_evidence=levels,
                              level_conflicts=sorted(level_values) if len(level_values)>1 else [],
                              variant_verified=False,level_verified=False,
                              semantics='visible_owned_card_text; details require temporal agreement'))
    return cards


def summarize(readings):
    groups={};frames=[]
    for reading in readings:
        cards=reading['facts'].get('visible_owned_skill_cards',[])
        if not cards:continue
        frames.append(dict(timestamp_ms=reading['source_timestamp_ms'],evidence=reading['evidence'],visible_cards=len(cards)))
        for card in cards:
            groups.setdefault(card['name_text'],[]).append(dict(timestamp_ms=reading['source_timestamp_ms'],
                evidence=reading['evidence'],slot=card['slot'],text_evidence=card['text_evidence'],
                observed_level=card.get('observed_level'),level_evidence=card.get('level_evidence',[]),
                observed_variant=card.get('observed_variant'),variant_evidence=card.get('variant_evidence'),
                level_conflicts=card.get('level_conflicts',[]),variant_conflicts=card.get('variant_conflicts',[])))
    owned=[]
    for name,observations in groups.items():
        times=sorted(set(o['timestamp_ms'] for o in observations))
        if not any(0<b-a<=500 for a,b in zip(times,times[1:])):continue
        details={}
        for field in ('level','variant'):
            values={o.get('observed_'+field) for o in observations if o.get('observed_'+field) is not None}
            values.update(v for o in observations for v in o.get(field+'_conflicts',[]))
            value=next(iter(values)) if len(values)==1 else None
            supporting=sorted({o['timestamp_ms'] for o in observations if value is not None and o.get('observed_'+field)==value})
            repeated=any(0<b-a<=500 for a,b in zip(supporting,supporting[1:]))
            details[field]=value if repeated else None
            details[field+'_verified']=repeated
            if len(values)>1:details[field+'_conflicts']=sorted(values)
        owned.append(dict(name_text=name,observations=observations,**details))
    return dict(observed_owned_cards=owned,summary_frames=frames,complete=False,
                scope='Repeated visible final-summary card text only; not a complete inventory or purchase event.',
                unresolved=['Off-screen cards and scroll coverage are not established.','Missing symbol variants and levels remain unverified.'])
