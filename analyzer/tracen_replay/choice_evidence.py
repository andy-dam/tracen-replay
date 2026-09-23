"""Gameplay-only observations of dialogue cards and bilateral selection marks.

These are visual observations, not completed-choice or action claims. A temporal
consumer must associate marks with a previously observed option set.
"""
import numpy as np

from .layout import pane_size, place_rows, place_x
from .source_clock import elapsed


def _menu_rows():
    """Pane rows 250..800 of the PC pane, where the menu sits; it moves with the event's text box."""
    return place_rows(250,800,'m','s')


def _card_bands(mask):
    """Join text-sized holes inside cards, but not the gaps between cards."""
    top,bottom=_menu_rows()
    rows=np.where(mask[top:bottom,place_x(175):place_x(610)].mean(axis=1)>=.85)[0]+top
    runs=[]
    for y in rows:
        if not runs or y-runs[-1][-1]>22:runs.append([])
        runs[-1].append(int(y))
    return [(r[0],r[-1]+1) for r in runs if 55<=r[-1]+1-r[0]<=100]


def _card_text(lines,bands,minimum=97):
    cards=[];complete=True
    for top,bottom in bands:
        inside=[l for l in lines if place_x(300)<=l['box'][0]<=place_x(350)
                and top<=l['box'][1]<l['box'][3]<=bottom+3
                and 15<=l['box'][3]-l['box'][1]<=45]
        inside.sort(key=lambda l:l['box'][1])
        if not inside or any(l['confidence']<minimum for l in inside):
            complete=False;continue
        cards.append(dict(text=' '.join(l['text'] for l in inside),
            text_box=[min(l['box'][0] for l in inside),inside[0]['box'][1],
                      max(l['box'][2] for l in inside),inside[-1]['box'][3]],
            confidence=min(l['confidence'] for l in inside),card_y=[top,bottom]))
    return cards,complete and bool(bands)


def observe(pane,lines=(),include_slots=False):
    if pane.size!=pane_size():raise ValueError('Expected isolated gameplay pixels.')
    # Every detector below reads the menu rows only.  Build the masks for that
    # band and pad them back to full height so the row indices stay unchanged.
    menu_top,menu_bottom=_menu_rows()
    band=np.asarray(pane.convert('RGB'))[menu_top:menu_bottom].astype('int16')
    def _full(mask):
        full=np.zeros((pane.height,pane.width),dtype=bool);full[menu_top:menu_bottom]=mask;return full
    yellow=_full((band[:,:,0]>220)&(band[:,:,1]>210)&(band[:,:,2]<130))
    sides=[]
    for left in (place_x(110),place_x(650)):
        ys=np.where(yellow[menu_top:menu_bottom,left:left+55].sum(axis=1)>=5)[0]+menu_top
        runs=[]
        for y in ys:
            if not runs or y-runs[-1][-1]>3:runs.append([])
            runs[-1].append(int(y))
        marks=[]
        for run in runs:
            top,bottom=run[0],run[-1]+1
            area=int(yellow[top:bottom,left:left+55].sum())
            if 15<=bottom-top<=50 and area>=300:
                marks.append(dict(box=[left+148,top,left+203,bottom],yellow_pixels=area))
        sides.append(marks)
    pairs=[]
    for left in sides[0]:
        matches=[right for right in sides[1] if abs(left['box'][1]-right['box'][1])<=8 and abs(left['box'][3]-right['box'][3])<=8]
        if len(matches)==1:pairs.append(dict(left=left,right=matches[0]))
    low=band.min(axis=2);high=band.max(axis=2)
    white=_full((low>=240)&(high-low<=15))
    green=_full((band[:,:,0]>150)&(band[:,:,1]>220)&(band[:,:,2]<190)&(band[:,:,1]-band[:,:,0]>20))
    bands=_card_bands(white)
    cards,complete=_card_text(lines,bands)
    # The selected card is a separate visual channel from menu text.  Keep
    # its OCR candidate even when a moving highlight damages one glyph below
    # the offered-menu confidence gate.  The commitment adapter still binds
    # this candidate to the repeated prior menu and validates this marker;
    # this does not lower the confidence required for ordinary option text.
    green_bands=_card_bands(green)
    selected,_=_card_text(lines,green_bands,minimum=0)
    for card,(top,bottom) in zip(selected,green_bands):
        card['selection_visual_proof']={
            'kind':'green_card_fill',
            'detector':'choice_evidence.green_card_fill_v1',
            'green_pixels':int(green[top:bottom,place_x(175):place_x(610)].sum()),
        }
    result=dict(offered_card_candidates=cards,selection_mark_pairs=pairs,
                menu_text_complete=complete,selected_card_candidates=selected,
                selected_option=None,selection_verified=False)
    if include_slots:
        slots=[]
        for band in bands:
            weak,_=_card_text(lines,[band],minimum=0)
            slots.append(weak[0] if weak else dict(card_y=list(band),text=None))
        result['offered_card_slots']=slots
    return result


def _same_slots(left,right):
    return len(left)==len(right) and all(
        all(abs(a-b)<=3 for a,b in zip(x['card_y'],y['card_y']))
        and (not x.get('text') or not y.get('text') or x['text']==y['text'])
        and (not x.get('text') or not y.get('text') or _card_agrees(x,y))
        for x,y in zip(left,right))


def _slot_consensus(rows):
    """Every visible slot needs strong text at two distinct source timestamps."""
    options=[]
    for index in range(len(rows[-1]['offered_card_slots'])):
        observations=[(r,r['offered_card_slots'][index]) for r in rows]
        readable=[(r,c) for r,c in observations if c.get('text') and c.get('confidence',0)>=97]
        if len({r['source_timestamp_ms'] for r,c in readable})<2:return None
        texts={c['text'] for r,c in observations if c.get('text')}
        if len(texts)!=1:return None
        options.append(readable[-1][1])
    return options or None


def _card_agrees(card,option):
    if 'text_box' in option:
        aligned=all(abs(card['text_box'][i]-option['text_box'][i])<=20 for i in (0,1,2))
    else:
        aligned=option['card_y'][0]<=card['text_box'][1]<option['card_y'][1]
    return aligned and (not option.get('text') or card['text']==option['text'])


def reconstruct(observations):
    """Associate selection marks with repeated recent menus; never use rewards."""
    active=None;pending=[];slot_rows=[];events=[];used=set()
    for row in sorted(observations,key=lambda r:r['source_timestamp_ms']):
        if row.get('screen_boundary'):
            active=None;pending=[];slot_rows=[];continue
        time=row['source_timestamp_ms'];cards=row['offered_card_candidates']
        slots=row.get('offered_card_slots')
        if slots and not any(s.get('text') for s in slots):
            active=None;pending=[];slot_rows=[];continue
        if active and elapsed(active['last_ms'],time)>1500:active=None
        # A readable different response at the same height is a new menu,
        # not evidence that an option from the previous menu was selected.
        visible=cards+row.get('selected_card_candidates',[])
        weak=[c for c in row.get('offered_card_slots',[]) if c.get('text')]
        if pending and any(not any(_card_agrees(c,o) for o in pending[-1]['offered_card_candidates'])
                           for c in visible+weak):pending=[]
        if slot_rows and any(not any(_card_agrees(c,o) for o in previous['offered_card_slots'])
                             for previous in slot_rows for c in visible+weak):slot_rows=[]
        if active and any(not any(_card_agrees(c,o) for o in active['options']) for c in visible+weak):
            active=None;pending=[];slot_rows=[]
        # Green selected cards leave the white-card set during the animation.
        # Those remaining white cards are not a new, smaller offered menu.
        if active and row.get('selected_card_candidates'):
            active['collapsing']=True
            active.setdefault('transition_evidence',[]).append(row['evidence'])
        collapsing=active and active.get('collapsing',False)
        visible_count=len(slots) if slots is not None else len(cards)
        shrinking=active and 0<visible_count<len(active['options'])
        if collapsing or shrinking:
            pending=[];slot_rows=[]
        elif slots:
            pending=[]
            if slot_rows and (elapsed(slot_rows[-1]['source_timestamp_ms'],time)>500
                              or any(not _same_slots(r['offered_card_slots'],slots) for r in slot_rows)):
                slot_rows=[]
            slot_rows=[r for r in slot_rows if elapsed(r['source_timestamp_ms'],time)<=1500]
            slot_rows.append(row)
            options=_slot_consensus(slot_rows)
            if options:
                active=dict(rows=list(slot_rows),options=options,last_ms=time,
                            first_ms=slot_rows[0]['source_timestamp_ms'],basis='repeated_card_text_and_bilateral_selection_marks')
        elif cards and row.get('menu_text_complete',True):
            signature=tuple(c['text'] for c in cards)
            if pending and (tuple(c['text'] for c in pending[-1]['offered_card_candidates'])!=signature
                            or elapsed(pending[-1]['source_timestamp_ms'],time)>500):pending=[]
            pending.append(row)
            if len({r['source_timestamp_ms'] for r in pending})>=2:
                active=dict(rows=list(pending),options=cards,last_ms=time,
                            first_ms=pending[0]['source_timestamp_ms'],basis='repeated_menu_and_bilateral_selection_marks')
        pairs=row['selection_mark_pairs']
        if not active or len(pairs)!=1:continue
        options=active['options'];pair=pairs[0]
        matches=[i for i,c in enumerate(options) if all(20<=c['text_box'][1]-pair[side]['box'][1]<=65 for side in ('left','right'))]
        key=active['first_ms']
        if len(matches)!=1 or key in used:continue
        index=matches[0];used.add(key)
        events.append(dict(kind='dialogue_choice' if len(options)>1 else 'dialogue_response',
            options=[c['text'] for c in options],selected_index=index,selected_text=options[index]['text'],
            first_seen_ms=active['first_ms'],selection_observed_ms=time,click_timestamp_ms=None,
            evidence=list(dict.fromkeys([r['evidence'] for r in active['rows']]+active.get('transition_evidence',[])+[row['evidence']])),selection_marks=pair,
            selection_basis=active['basis'],complete_effects_verified=False))
        active=None;pending=[];slot_rows=[]
    return events


def collect(readings,extra=()):
    """Merge choice-only inspections without adding rows to stat accounting."""
    rows={}
    for r in readings:
        time=r['source_timestamp_ms']
        if r['screen']!='unknown':rows[time]=dict(source_timestamp_ms=time,screen_boundary=True)
        elif 'choice_observation' in r.get('facts',{}) and not rows.get(time,{}).get('screen_boundary'):
            rows[time]=dict(r['facts']['choice_observation'],source_timestamp_ms=time,evidence=r['evidence'])
    for r in extra:
        time=r['source_timestamp_ms'];old=rows.get(time)
        if old and old.get('screen_boundary'):continue
        # A known screen at this timestamp takes precedence over an unknown one.
        if old and not r.get('screen_boundary'):continue
        rows[time]=r
    return [rows[t] for t in sorted(rows)]
