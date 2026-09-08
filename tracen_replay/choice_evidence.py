"""Gameplay-only observations of dialogue cards and bilateral selection marks.

These are visual observations, not completed-choice or action claims. A temporal
consumer must associate marks with a previously observed option set.
"""
import numpy as np


def _card_bands(mask):
    """Join text-sized holes inside cards, but not the gaps between cards."""
    rows=np.where(mask[250:800,175:610].mean(axis=1)>=.85)[0]+250
    runs=[]
    for y in rows:
        if not runs or y-runs[-1][-1]>22:runs.append([])
        runs[-1].append(int(y))
    return [(r[0],r[-1]+1) for r in runs if 55<=r[-1]+1-r[0]<=100]


def _card_text(lines,bands):
    cards=[];complete=True
    for top,bottom in bands:
        inside=[l for l in lines if 300<=l['box'][0]<=350
                and top<=l['box'][1]<l['box'][3]<=bottom+3
                and 15<=l['box'][3]-l['box'][1]<=45]
        inside.sort(key=lambda l:l['box'][1])
        if not inside or any(l['confidence']<97 for l in inside):
            complete=False;continue
        cards.append(dict(text=' '.join(l['text'] for l in inside),
            text_box=[min(l['box'][0] for l in inside),inside[0]['box'][1],
                      max(l['box'][2] for l in inside),inside[-1]['box'][3]],
            confidence=min(l['confidence'] for l in inside),card_y=[top,bottom]))
    return cards,complete and bool(bands)


def observe(pane,lines=()):
    if pane.size!=(810,1080):raise ValueError('Expected isolated 810x1080 gameplay pixels.')
    pixels=np.asarray(pane.convert('RGB')).astype('int16')
    yellow=(pixels[:,:,0]>220)&(pixels[:,:,1]>210)&(pixels[:,:,2]<130)
    sides=[]
    for left in (110,650):
        ys=np.where(yellow[250:800,left:left+55].sum(axis=1)>=5)[0]+250
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
    white=(pixels.min(axis=2)>=240)&(pixels.max(axis=2)-pixels.min(axis=2)<=15)
    green=(pixels[:,:,0]>150)&(pixels[:,:,1]>220)&(pixels[:,:,2]<190)&(pixels[:,:,1]-pixels[:,:,0]>20)
    cards,complete=_card_text(lines,_card_bands(white))
    selected,_=_card_text(lines,_card_bands(green))
    return dict(offered_card_candidates=cards,selection_mark_pairs=pairs,
                menu_text_complete=complete,selected_card_candidates=selected,
                selected_option=None,selection_verified=False)


def reconstruct(observations):
    """Associate selection marks with repeated recent menus; never use rewards."""
    active=None;pending=[];events=[];used=set()
    for row in sorted(observations,key=lambda r:r['source_timestamp_ms']):
        if row.get('screen_boundary'):
            active=None;pending=[];continue
        time=row['source_timestamp_ms'];cards=row['offered_card_candidates']
        if active and time-active[-1]['source_timestamp_ms']>1500:active=None
        # A readable different response at the same height is a new menu,
        # not evidence that an option from the previous menu was selected.
        visible=cards+row.get('selected_card_candidates',[])
        if active and any(c['text'] not in {o['text'] for o in active[-1]['offered_card_candidates']} for c in visible):
            active=None;pending=[]
        if cards and row.get('menu_text_complete',True):
            signature=tuple(c['text'] for c in cards)
            if pending and (tuple(c['text'] for c in pending[-1]['offered_card_candidates'])!=signature
                            or time-pending[-1]['source_timestamp_ms']>500):pending=[]
            pending.append(row)
            if len({r['source_timestamp_ms'] for r in pending})>=2:
                active=list(pending)
        pairs=row['selection_mark_pairs']
        if not active or len(pairs)!=1:continue
        options=active[-1]['offered_card_candidates'];pair=pairs[0]
        matches=[i for i,c in enumerate(options) if all(20<=c['text_box'][1]-pair[side]['box'][1]<=65 for side in ('left','right'))]
        key=active[0]['source_timestamp_ms']
        if len(matches)!=1 or key in used:continue
        index=matches[0];used.add(key)
        events.append(dict(kind='dialogue_choice' if len(options)>1 else 'dialogue_response',
            options=[c['text'] for c in options],selected_index=index,selected_text=options[index]['text'],
            first_seen_ms=active[0]['source_timestamp_ms'],selection_observed_ms=time,click_timestamp_ms=None,
            evidence=[r['evidence'] for r in active]+[row['evidence']],selection_marks=pair,
            selection_basis='repeated_menu_and_bilateral_selection_marks',complete_effects_verified=False))
        active=None;pending=[]
    return events
