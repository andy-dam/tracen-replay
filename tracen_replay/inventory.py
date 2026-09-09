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
            cards.append(dict(name_text=text,slot=[row,column],text_evidence=names,
                              variant_verified=False,level_verified=False,
                              semantics='visible_owned_card_text; suffix and level not established'))
    return cards


def summarize(readings):
    groups={};frames=[]
    for reading in readings:
        cards=reading['facts'].get('visible_owned_skill_cards',[])
        if not cards:continue
        frames.append(dict(timestamp_ms=reading['source_timestamp_ms'],evidence=reading['evidence'],visible_cards=len(cards)))
        for card in cards:
            groups.setdefault(card['name_text'],[]).append(dict(timestamp_ms=reading['source_timestamp_ms'],
                evidence=reading['evidence'],slot=card['slot'],text_evidence=card['text_evidence']))
    owned=[]
    for name,observations in groups.items():
        times=sorted(set(o['timestamp_ms'] for o in observations))
        if not any(0<b-a<=500 for a,b in zip(times,times[1:])):continue
        owned.append(dict(name_text=name,observations=observations,variant_verified=False,level_verified=False))
    return dict(observed_owned_cards=owned,summary_frames=frames,complete=False,
                scope='Repeated visible final-summary card text only; not a complete inventory or purchase event.',
                unresolved=['Off-screen cards and scroll coverage are not established.','Missing symbol variants and levels remain unverified.'])
