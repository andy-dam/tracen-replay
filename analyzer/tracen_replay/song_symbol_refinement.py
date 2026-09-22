"""Explicit, source-bound OCR preparation for misread terminal music notes.

The note's stem, flag, head and quotes must be visible at both strict intensity
thresholds. A separately recognized title crop proves the text before the note;
it prevents deleting a real trailing letter merely because a note follows it.
Replaying stored observations checks pixels and crop boundaries without OCR.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image

from .refine_contrast import fingerprint
from .song_symbols import _music_note_observation
from .ocr_confidence import confidence_percent

VERSION=2
_RECEIPT=re.compile(r'^Learned the song "(?P<base>.+?)(?P<suffix>\s+[A-Za-z])"(?P<stop>[.!])$')
_SHA=re.compile(r'^[0-9a-f]{64}$')


def _confident(value):
    value=confidence_percent(value)
    return value is not None and value>=95


def _match(line):
    if not isinstance(line,dict) or not isinstance(line.get('text'),str):return None
    return _RECEIPT.fullmatch(line['text']) if _confident(line.get('confidence')) else None


def _layout(pane,line):
    """Find the two opening quotes and a uniquely isolated terminal note."""
    match=_match(line)
    if not match:return None
    box=line.get('box')
    if not isinstance(box,(list,tuple)) or len(box)!=4 or any(type(v) is not int for v in box):return None
    if not (148<=box[0]<box[2]<=958 and 0<=box[1]<box[3]<=1080):return None
    # Preserve the historical geometry when its thresholds see the glyph;
    # the late result panel is darker, so use the lower same-frame range only
    # when that first source witness is unavailable.
    symbol=_music_note_observation(pane,line['box'],(145,165))
    if symbol is None:
        symbol=_music_note_observation(pane,line['box'],(135,145))
    if symbol is None:return None
    left,top,right,bottom=line['box'];glyph=symbol['box']
    x0=left-148;y0=top-2
    gray=cv2.cvtColor(np.array(pane.crop((x0,y0,glyph[0]-148,bottom+2))),cv2.COLOR_RGB2GRAY)
    votes=[]
    for threshold in (145,165):
        _,_,stats,_=cv2.connectedComponentsWithStats((gray<threshold).astype('uint8'),8)
        quotes=[tuple(map(int,s[:4])) for s in stats[1:]
                if 1<=s[2]<=4 and 3<=s[3]<=9 and abs(int(s[1])+y0-glyph[1])<=3]
        pairs=[(a,b) for a in quotes for b in quotes
               if 2<=b[0]-a[0]<=6 and a[0]+a[2]<=b[0]
               and abs(a[1]-b[1])<=1 and abs(a[3]-b[3])<=1]
        if len(pairs)!=1:return None
        a,b=pairs[0]
        votes.append([x0+a[0]+148,y0+min(a[1],b[1]),x0+b[0]+b[2]+148,
                      y0+max(a[1]+a[3],b[1]+b[3])])
    if max(abs(a-b) for a,b in zip(*votes))>2:return None
    opening=votes[1]
    # At least one ordinary title word must lie between the opening quote and
    # the note. Keep the original text line's vertical bounds for crop proof.
    crop=[opening[2]+2-148, glyph[1]-3, glyph[0]-1-148, glyph[3]+4]
    if crop[2]-crop[0]<20 or not 0<=crop[0]<crop[2]<=810:return None
    return {'symbol':dict(symbol,method='strict_note_and_independently_read_title'),
            'opening_quote_box':opening,'title_crop_box':crop}


def _pixels(raw,pane):
    if pane.size!=(810,1080) or hashlib.sha256(pane.tobytes()).hexdigest()!=raw.get('gameplay_sha256'):
        raise ValueError('Song-symbol gameplay pixels changed.')


def _title_coverage(crop,title):
    """Require an isolated ink-column run for every recognized title letter.

    OCR can omit a real letter at high confidence. This narrow alphabetic-font
    check rejects an extra trailing glyph even when the returned text matches.
    Joined letters, punctuation and ambiguous spacing abstain.
    """
    if not re.fullmatch(r'[A-Za-z]+(?: [A-Za-z]+)*',title):return None
    words=title.split();letters=sum(map(len,words))
    word_ends=set();offset=0
    for word in words[:-1]:offset+=len(word);word_ends.add(offset)
    gray=cv2.cvtColor(np.array(crop),cv2.COLOR_RGB2GRAY);votes=[]
    for threshold in (145,165):
        ink=gray<threshold
        changes=np.diff(np.r_[False,ink.any(axis=0),False].astype('int8'))
        starts=np.where(changes==1)[0];ends=np.where(changes==-1)[0]
        runs=[[int(a),int(b)] for a,b in zip(starts,ends)]
        if len(runs)!=letters:return None
        for i,(a,b) in enumerate(runs):
            if not 1<=b-a<=18:return None
            occupied=np.where(ink[:,a:b].any(axis=1))[0]
            if not len(occupied) or occupied[-1]-occupied[0]<7:return None
            if i:
                gap=a-runs[i-1][1]
                if (i in word_ends and not 5<=gap<=15) or (i not in word_ends and not 1<=gap<=4):return None
        votes.append(runs)
    if any(max(abs(a-b) for a,b in zip(x,y))>2 for x,y in zip(*votes)):return None
    return {'method':'isolated_letter_columns_and_word_gaps','thresholds':[145,165],
            'letter_count':letters,'column_runs':votes[1]}


def prepare(raw,proof,reader):
    """Return an immutable-cache payload; does not edit OCR or report files."""
    with Image.open(proof) as image:
        pane=image.convert('RGB');_pixels(raw,pane);observations=[]
        for index,line in enumerate(raw['lines']):
            layout=_layout(pane,line)
            if layout is None:continue
            crop=pane.crop(layout['title_crop_box'])
            rec=reader.engine.text_rec(reader.TextRecInput(img=[np.array(crop)[:,:,::-1]]))
            if len(rec.txts)!=1 or len(rec.scores)!=1:continue
            title=str(rec.txts[0]).strip()
            try:
                confidence=confidence_percent(float(rec.scores[0])*100)
            except (TypeError,ValueError,OverflowError):
                confidence=None
            if not _confident(confidence) or title!=_match(line)['base']:continue
            coverage=_title_coverage(crop,title)
            if coverage is None:continue
            observations.append(dict(line_index=index,line=deepcopy(line),layout=layout,
                title=title,title_confidence=confidence,title_pixel_coverage=coverage,
                title_crop_sha256=hashlib.sha256(crop.tobytes()).hexdigest()))
    return {'version':VERSION,'raw_sha256':fingerprint(raw),
            'evidence_sha256':hashlib.sha256(Path(proof).read_bytes()).hexdigest(),
            'model_sha256':reader.models,'engine_fingerprint':reader.fingerprint,
            'observations':observations}


def apply(raw,extra,proof,original=None):
    """Verify saved source evidence and apply it without running a model."""
    original=raw if original is None else original
    if not isinstance(extra,dict):raise ValueError('Song-symbol refinement must be an object.')
    models=extra.get('model_sha256')
    engine=extra.get('engine_fingerprint')
    if (type(extra.get('version')) is not int or extra['version']!=VERSION or extra.get('raw_sha256')!=fingerprint(original)
            or extra.get('evidence_sha256')!=hashlib.sha256(Path(proof).read_bytes()).hexdigest()
            or not isinstance(models,dict) or not models
            or any(not isinstance(k,str) or not k or not isinstance(v,str) or not _SHA.fullmatch(v) for k,v in models.items())
            or not isinstance(engine,str) or not _SHA.fullmatch(engine)
            or not isinstance(extra.get('observations'),list)):
        raise ValueError('Song-symbol refinement provenance mismatch.')
    lines=deepcopy(raw['lines']);seen=set()
    with Image.open(proof) as image:
        pane=image.convert('RGB');_pixels(original,pane)
        for observation in extra['observations']:
            if not isinstance(observation,dict):raise ValueError('Song-symbol observation must be an object.')
            index=observation.get('line_index')
            if type(index) is not int or not 0<=index<len(lines) or index in seen:
                raise ValueError('Invalid song-symbol line index.')
            seen.add(index);line=lines[index]
            if line!=observation.get('line') or not _match(line):
                raise ValueError('Song-symbol source line changed.')
            layout=_layout(pane,line)
            if layout is None or layout!=observation.get('layout'):
                raise ValueError('Song-symbol pixel geometry changed.')
            crop=pane.crop(layout['title_crop_box'])
            if (hashlib.sha256(crop.tobytes()).hexdigest()!=observation.get('title_crop_sha256')
                    or not _confident(observation.get('title_confidence'))
                    or observation.get('title')!=_match(line)['base']):
                raise ValueError('Song-symbol title evidence changed.')
            coverage=_title_coverage(crop,observation['title'])
            if coverage is None or coverage!=observation.get('title_pixel_coverage'):
                raise ValueError('Song-symbol title glyph coverage changed.')
            match=_match(line)
            fixed=f'Learned the song "{observation["title"]} ♪"{match["stop"]}'
            evidence=dict(layout['symbol'],title_evidence=deepcopy(observation),
                source_timestamp_ms=original['source_timestamp_ms'],evidence=original['evidence'],
                source_frame_sha256=original['source_frame_sha256'],
                gameplay_sha256=original['gameplay_sha256'],evidence_sha256=extra['evidence_sha256'],
                model_sha256=extra['model_sha256'],engine_fingerprint=extra['engine_fingerprint'])
            lines[index]=dict(line,text=fixed,original_symbol_text=line['text'],visual_symbol_observation=evidence)
    return dict(raw,lines=lines)
