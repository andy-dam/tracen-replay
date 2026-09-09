"""Observe isolated music-note suffixes omitted by receipt text recognition."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from .refine_contrast import fingerprint


def _note_shape(mask):
    h,w=mask.shape
    if not (12<=h<=26 and 7<=w<=17 and .4<=w/h<=.85):return False
    first=np.where(mask[0])[0]
    if not len(first) or len(first)>max(2,w*.3):return False
    stem=int(round(float(np.median(first))))
    if not .25*w<=stem<=.65*w:return False
    if not mask[:int(h*.7),max(0,stem-1):stem+2].any(axis=1).all():return False
    foot=mask[int(h*.8):]
    if foot[:,:stem+1].mean()<.45 or foot[:,stem+1:].sum()>1:return False
    flag=mask[:int(h*.75),stem+2:]
    return bool(flag.sum()>=6 and mask[:int(h*.75),-1].any())


def music_note_suffix(pane,box):
    """Return a pixel observation, never a catalog-based name correction.

    OCR boxes use full-frame coordinates; the proof begins at x=148. The
    geometry is restricted to this layout's small receipt font. Three binary
    thresholds test stability of one image, not independent observations.
    """
    if pane.size!=(810,1080):raise ValueError('Expected isolated gameplay pixels.')
    left,top,right,bottom=box
    if not (250<=left<right<=850 and 770<=top<bottom<=1000):return None
    x0=right-148-45;y0=top-2
    gray=cv2.cvtColor(np.array(pane.convert('RGB').crop((x0,y0,right-148+3,bottom+2))),cv2.COLOR_RGB2GRAY)
    votes=[]
    for threshold in (145,165,185):
        _,labels,stats,_=cv2.connectedComponentsWithStats((gray<threshold).astype('uint8'),8)
        found=[]
        for i,(x,y,w,h,area) in enumerate(stats[1:],1):
            if area<8 or not _note_shape(labels[y:y+h,x:x+w]==i):continue
            quotes=[s for s in stats[1:] if x+w+2<=s[0]<=x+w+14 and abs(int(s[1])-int(y))<=3
                    and 1<=s[2]<=4 and 3<=s[3]<=9]
            if len(quotes)!=2:continue
            quotes.sort(key=lambda s:s[0])
            if not 2<=quotes[1][0]-quotes[0][0]<=6:continue
            found.append([int(x+x0+148),int(y+y0),int(x+x0+148+w),int(y+y0+h)])
        if len(found)!=1:return None
        votes.append(found[0])
    if any(max(abs(a-b) for a,b in zip(votes[0],v))>2 for v in votes[1:]):return None
    return dict(symbol='\u266a',box=votes[1],method='isolated_note_stem_flag_head_and_closing_quote',
                thresholds=[145,165,185],independent_observations=False)


def _eligible(line):
    # Only fill an OCR whitespace hole; a recognized letter may be real text.
    return line['confidence']>=95 and re.fullmatch(r'Learned the song "[^"\n]+\s+"[.!]',line['text'])


def apply(raw,extra,proof,original=None):
    original=raw if original is None else original
    if extra.get('version')!=1 or extra['raw_sha256']!=fingerprint(original) or extra['evidence_sha256']!=hashlib.sha256(proof.read_bytes()).hexdigest():
        raise ValueError('Song-symbol provenance mismatch.')
    lines=[dict(l) for l in raw['lines']]
    for item in extra['observations']:
        i=item['line_index'];line=lines[i]
        if line['box']!=item['line_box'] or not _eligible(line):continue
        text=re.sub(r'\s+"([.!])$',item['symbol']['symbol']+'"'+r'\1',line['text'])
        lines[i]=dict(line,text=text,original_symbol_text=line['text'],visual_symbol_observation=item['symbol'])
    return dict(raw,lines=lines)


def refine(root):
    from .full_recording import save_json
    root=Path(root);dest=root/'song-symbols';dest.mkdir(exist_ok=True);count=0;notes=0
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'))
        eligible=[(i,l) for i,l in enumerate(raw['lines']) if _eligible(l)]
        if not eligible:continue
        proof=root/raw['evidence'];target=dest/path.name
        if target.exists():apply(raw,json.loads(target.read_text(encoding='utf-8')),proof);continue
        observations=[]
        with Image.open(proof) as pane:
            if hashlib.sha256(pane.convert('RGB').tobytes()).hexdigest()!=raw['gameplay_sha256']:raise ValueError('Song proof pixels changed.')
            for i,line in eligible:
                symbol=music_note_suffix(pane,line['box'])
                if symbol:observations.append(dict(line_index=i,line_box=line['box'],symbol=symbol))
        save_json(target,dict(version=1,raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),observations=observations))
        count+=1;notes+=len(observations)
    print(json.dumps(dict(stage='song_symbols',new_observations=count,note_observations=notes)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
