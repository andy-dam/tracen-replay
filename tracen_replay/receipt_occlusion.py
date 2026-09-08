"""Abstain on dialogue text covered by the gameplay profile's green overlay.

This detects an obstruction, not a replacement digit. Other unobstructed frames
must supply the receipt. It does not claim to detect every cursor or overlay.
"""
import hashlib
import json
import re


def numeric_bounds(box,words,columns,line_length):
    """Include the gap after 'by': an obscured leading digit has no OCR column."""
    if line_length<=0 or len(words)!=len(columns) or any(not c for c in columns):return None
    if any(v<0 or v>=line_length for c in columns for v in c):return None
    positions=[i for i,w in enumerate(words[:-1]) if w=='by' and re.fullmatch(r'\d+[.!]?',words[i+1])]
    if len(words)==3 and words[0]=='Gained' and re.fullmatch(r'\d+',words[1]) and re.fullmatch(r'fans[.!]?',words[2]):positions=[0]
    if len(positions)!=1:return None
    i=positions[0];a,b,c,d=box;scale=(c-a)/line_length
    start=max(columns[i])+.75
    digits=words[i+1].rstrip('.!')
    if len(columns[i+1])!=len(words[i+1]):return None
    last_digit=columns[i+1][len(digits)-1]
    # Sentence punctuation and detector padding are not part of the award.
    end=min(line_length,last_digit+1.5)
    if i+2<len(words):end=min(end,min(columns[i+2])-.5)
    if start>=min(columns[i+1]) or end<=last_digit:return None
    return [a+start*scale,b,a+end*scale,d]


def overlay_boxes(pane):
    import numpy as np
    if pane.size != (810,1080):raise ValueError('Expected the gameplay crop.')
    pixels=np.asarray(pane.convert('RGB')).astype('int16')
    band=pixels[770:1000]
    r,g,b=band[:,:,0],band[:,:,1],band[:,:,2]
    mask=(g>140)&(g>r*1.3)&(g>b*1.2)&(r<170)&(b<170)
    ys,xs=np.nonzero(mask);pending=set(zip(xs.tolist(),ys.tolist()));boxes=[]
    while pending:
        point=pending.pop();stack=[point];component=[point]
        while stack:
            x,y=stack.pop()
            for neighbor in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if neighbor in pending:
                    pending.remove(neighbor);stack.append(neighbor);component.append(neighbor)
        xs,ys=zip(*component);left,right=min(xs),max(xs)+1;top,bottom=min(ys)+770,max(ys)+771
        if not (20<=len(component)<=180 and 5<=right-left<=20 and 8<=bottom-top<=26):continue
        neighborhood=pixels[max(770,top-10):min(1000,bottom+10),max(0,left-10):min(810,right+10)]
        white=(neighborhood.min(axis=2)>190)&(neighborhood.max(axis=2)-neighborhood.min(axis=2)<55)
        if float(white.mean())<.5:continue
        boxes.append([left+148-2,top-2,right+148+2,bottom+2])
    return boxes


def annotate(raw,pane):
    candidates=[i for i,line in enumerate(raw['lines']) if 770<=line['box'][1]<1000
                and re.search(r'went|recover|Gained|Friendship',line['text'])]
    if not candidates:return raw
    if raw.get('gameplay_sha256')!=hashlib.sha256(pane.convert('RGB').tobytes()).hexdigest():
        raise ValueError('Receipt overlay proof differs from original OCR pixels.')
    boxes=overlay_boxes(pane)
    if not boxes:return raw
    lines=[dict(line) for line in raw['lines']];blocked=[]
    for index in candidates:
        line=lines[index];a,b,c,d=line['box']
        localized=[]
        for item in raw.get('overlay_alignment',[]):
            if item['line_box']!=line['box'] or item.get('confidence',100)<95:continue
            bounds=numeric_bounds(item['line_box'],item['words'],item['columns'],item['line_length']) if 'words' in item else item.get('numeric_box')
            if bounds:localized.append(bounds)
        if len(localized)==1:a,b,c,d=localized[0]
        # Detector boxes include space below the baseline. A cursor there can
        # overlap the box while the glyphs remain readable. Require obstruction
        # through the text's vertical center, not padding or a glyph's edge.
        middle=(b+d)/2
        overlaps=[box for box in boxes if min(c,box[2])-max(a,box[0])>=3 and box[1]<=middle<=box[3]]
        if overlaps:
            blocked.append(dict(text=line['text'],box=line['box'],confidence=line['confidence'],overlay_boxes=overlaps))
            line.update(confidence=0,overlay_occluded=True,pre_occlusion_confidence=line['confidence'])
    return dict(raw,lines=lines,occluded_receipt_lines=blocked) if blocked else raw


def annotate_path(raw,path,original=None):
    if not any(770<=l['box'][1]<1000 and re.search(r'went|recover|Gained|Friendship',l['text']) for l in raw['lines']):return raw
    from PIL import Image
    from .refine_contrast import fingerprint
    extra_path=path.with_suffix('.overlay.json')
    if extra_path.exists():
        extra=json.loads(extra_path.read_text(encoding='utf-8'))
        if extra['raw_sha256']!=fingerprint(original or raw) or extra['evidence_sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Receipt overlay alignment provenance changed.')
        raw=dict(raw,overlay_alignment=extra['lines'])
    with Image.open(path) as pane:return annotate(raw,pane)
