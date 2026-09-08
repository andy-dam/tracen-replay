"""Gameplay-only observations of dialogue cards and bilateral selection marks.

These are visual observations, not completed-choice or action claims. A temporal
consumer must associate marks with a previously observed option set.
"""
import numpy as np


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
    cards=[]
    for line in lines:
        a,b,c,d=line['box']
        if line['confidence']<97 or not (300<=a<=350 and 270<=b<d<=790 and 15<=d-b<=45):continue
        # Broad white card interior, excluding the text baseline and right motif.
        top=max(250,b-18);bottom=b-3
        area=pixels[top:bottom,175:610]
        if not area.size:continue
        white=(area.min(axis=2)>=240)&(area.max(axis=2)-area.min(axis=2)<=15)
        if float(white.mean())<.85:continue
        cards.append(dict(text=line['text'],text_box=line['box'],confidence=line['confidence']))
    cards.sort(key=lambda c:c['text_box'][1])
    return dict(offered_card_candidates=cards,selection_mark_pairs=pairs,
                selected_option=None,selection_verified=False)


def reconstruct(observations):
    """Associate selection marks with repeated recent menus; never use rewards."""
    active=None;pending=[];events=[];used=set()
    for row in sorted(observations,key=lambda r:r['source_timestamp_ms']):
        time=row['source_timestamp_ms'];cards=row['offered_card_candidates']
        if active and time-active[-1]['source_timestamp_ms']>1500:active=None
        if cards:
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
