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
from .ocr_confidence import confidence_percent


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
    # Keep the historical threshold result when it is available so existing
    # source proofs retain their exact geometry.  The committed receipt in the
    # late recording is darker, so fall back to a lower same-frame range.
    result=_music_note_observation(pane,box,(145,165,185))
    return result if result is not None else _music_note_observation(pane,box,(135,145,150))


def music_note_title_suffix(pane,box):
    """Find a light music note at the end of a lesson-card title.

    Lesson-card titles use a different polarity and background from committed
    receipts, so the receipt detector cannot be reused by simply changing its
    threshold.  The search remains bounded to the OCR title's right edge and
    uses the same stem/flag/head geometry as the receipt parser.  A threshold
    sweep is still one image observation; ``independent_observations`` stays
    false in the returned proof.
    """
    if getattr(pane,'size',None)!=(810,1080):raise ValueError('Expected isolated gameplay pixels.')
    if not isinstance(box,(list,tuple)) or len(box)!=4:
        return None
    values=[]
    for value in box:
        if isinstance(value,bool) or not isinstance(value,(int,float)):
            return None
        try:
            numeric=float(value)
        except (TypeError,ValueError,OverflowError):
            return None
        if not np.isfinite(numeric) or numeric!=int(numeric):
            return None
        values.append(int(numeric))
    try:
        left,top,right,bottom=values
    except (TypeError,ValueError,OverflowError):
        return None
    if not (148<=left<right<=958 and 0<=top<bottom<=1080):return None
    image=np.array(pane.convert('RGB'))
    gray=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
    pane_right=right-148
    x0=max(0,pane_right-35);x1=min(810,pane_right+8)
    y0=max(0,top-5);y1=min(1080,bottom+5)
    if x1<=x0 or y1<=y0:return None
    observations=[]
    for threshold in range(150,251,5):
        mask=(gray[y0:y1,x0:x1]>threshold).astype('uint8')
        _,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
        found=[]
        for index,(x,y,w,h,area) in enumerate(stats[1:],1):
            x,y,w,h,area=(int(value) for value in (x,y,w,h,area))
            if area<8 or not _note_shape(labels[y:y+h,x:x+w]==index):continue
            absolute_left=x0+x;absolute_right=absolute_left+w
            absolute_top=y0+y;absolute_bottom=absolute_top+h
            # The note follows the title text.  Keep the right-edge slot
            # narrow enough that nearby card controls and decorative symbols
            # cannot become a title identity.
            if not pane_right-28<=absolute_left<=pane_right+1:continue
            if not absolute_right<=pane_right+8:continue
            if not -1<=pane_right-absolute_right<=16:continue
            if abs((absolute_top+absolute_bottom)/2-(top+bottom)/2)>6.5:continue
            found.append([absolute_left+148,absolute_top,
                          absolute_right+148,absolute_bottom])
        if len(found)==1:observations.append((threshold,found[0]))
    if not observations:return None
    clusters=[]
    for threshold,box_value in observations:
        for cluster in clusters:
            anchor=cluster[0][1]
            if max(abs(box_value[i]-anchor[i]) for i in range(4))<=3:
                cluster.append((threshold,box_value));break
        else:clusters.append([(threshold,box_value)])
    clusters.sort(key=lambda cluster:len(cluster),reverse=True)
    if len(clusters[0])<3:return None
    if len(clusters)>1 and len(clusters[1])>=3:return None
    cluster=clusters[0]
    anchor=cluster[len(cluster)//2][1]
    return dict(symbol='\u266a',box=[int(value) for value in anchor],
                method='source_title_note_stem_flag_head',
                thresholds=[int(item[0]) for item in cluster],votes=len(cluster),
                independent_observations=False,coordinate_space='gameplay_crop',
                polarity='light_on_card')


def _music_note_observation(pane,box,thresholds):
    if getattr(pane,'size',None)!=(810,1080):raise ValueError('Expected isolated gameplay pixels.')
    if not isinstance(box,(list,tuple)) or len(box)!=4 or any(
            isinstance(value,bool) or not isinstance(value,(int,float)) for value in box):
        return None
    try:
        values=[float(value) for value in box]
    except (TypeError,ValueError,OverflowError):
        return None
    if any(not np.isfinite(value) or value!=int(value) for value in values):
        return None
    left,top,right,bottom=(int(value) for value in values)
    if not (250<=left<right<=850 and 770<=top<bottom<=1000):return None
    x0=right-148-45;y0=top-2
    gray=cv2.cvtColor(np.array(pane.convert('RGB').crop((x0,y0,right-148+3,bottom+2))),cv2.COLOR_RGB2GRAY)
    votes=[]
    for threshold in thresholds:
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
    if not votes:return None
    anchor=votes[len(votes)//2]
    if any(max(abs(a-b) for a,b in zip(anchor,v))>2 for v in votes):return None
    return dict(symbol='\u266a',box=anchor,method='isolated_note_stem_flag_head_and_closing_quote',
                thresholds=list(thresholds),independent_observations=False)


def _eligible(line):
    # Only fill an OCR whitespace hole; a recognized letter may be real text.
    if not isinstance(line,dict) or confidence_percent(line.get('confidence')) is None \
            or confidence_percent(line.get('confidence'))<95:
        return False
    text=line.get('text')
    if not isinstance(text,str):
        return False
    return bool(re.fullmatch(
        r'Learned the song "[^"\n]+?(?:\s+|\s*[>▶→]\s*)"[.!]',
        text,
    ))


def _letter_suffix_candidate(line):
    """Return the conservative shape handled by song-symbol refinement.

    A terminal letter may be real song text.  This gate only tells the fresh
    pipeline that source pixels are worth checking; the refinement path still
    requires an independently read title crop before it can replace that
    letter.
    """
    if not isinstance(line,dict) or confidence_percent(line.get('confidence')) is None \
            or confidence_percent(line.get('confidence'))<95:
        return False
    text=line.get('text')
    if not isinstance(text,str):
        return False
    return bool(re.fullmatch(
        r'Learned the song "[^"\n]+\s+[A-Za-z]"[.!]',text)
    )


def has_eligible_song_receipt(lines):
    """Return whether a source-backed song suffix check is applicable."""
    return any(_eligible(line) or _letter_suffix_candidate(line)
               for line in lines if isinstance(line,dict))


def _restore_symbol_separator(text,symbol):
    """Replace the missing suffix glyph while retaining the source spacing."""
    return re.sub(
        r'(?P<separator>\s*)(?P<arrow>[>▶→]\s*)?"(?P<stop>[.!])$',
        lambda match: (
            ((match.group('separator') or ' ') + symbol + '"' + match.group('stop'))
            if match.group('separator') or match.group('arrow')
            else match.group(0)
        ),
        text,
    )


def _verify_proof(raw,pane,proof):
    """Verify the same source envelope used by the other pixel refinements."""
    if not isinstance(proof,dict):raise ValueError('Song-symbol proof metadata is required.')
    if pane.size!=(810,1080):raise ValueError('Song-symbol proof requires an 810x1080 gameplay crop.')
    image=np.asarray(pane.convert('RGB'))
    actual_gameplay=hashlib.sha256(image.tobytes()).hexdigest()
    if raw.get('gameplay_sha256')!=actual_gameplay or proof.get('gameplay_sha256')!=actual_gameplay:
        raise ValueError('Song-symbol gameplay pixels do not match the OCR observation.')
    for key in ('source_timestamp_ms','evidence','source_frame_sha256'):
        if key not in raw or key not in proof or raw[key]!=proof[key]:
            raise ValueError(f'Song-symbol proof metadata mismatch: {key}.')
    source=raw.get('source_sha256')
    proof_source=proof.get('source_sha256')
    if source is not None or proof_source is not None:
        if source!=proof_source or not isinstance(source,str) or not re.fullmatch(r'[0-9a-f]{64}',source):
            raise ValueError('Song-symbol source recording hash mismatch.')
    frame_hash=proof.get('source_frame_sha256')
    if not isinstance(frame_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',frame_hash):
        raise ValueError('Song-symbol source-frame hash is malformed.')
    evidence_hash=proof.get('evidence_sha256')
    if not isinstance(evidence_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',evidence_hash):
        raise ValueError('Song-symbol evidence hash is required.')
    for path_key,hash_key in (('evidence_path','evidence_sha256'),('source_frame_path','source_frame_sha256')):
        path_value=proof.get(path_key)
        if path_value is None:continue
        path=Path(path_value)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=proof[hash_key]:
            raise ValueError(f'Song-symbol proof file mismatch: {path_key}.')
    return actual_gameplay


def _symbol_observation(raw,proof,symbol):
    observation=dict(symbol,source_timestamp_ms=raw['source_timestamp_ms'],
                     evidence=raw['evidence'],gameplay_sha256=raw['gameplay_sha256'],
                     source_frame_sha256=raw['source_frame_sha256'],
                     evidence_sha256=proof['evidence_sha256'])
    if raw.get('source_sha256') is not None:observation['source_sha256']=raw['source_sha256']
    return observation


def annotate(raw,pane,proof):
    """Apply a source-pixel music-note correction to fresh OCR rows.

    This path only fills an omitted suffix or a trailing UI arrow.  A
    recognized terminal letter is left for ``song_symbol_refinement``, whose
    independent title crop is required before a letter can be replaced.
    """
    if not isinstance(raw,dict):raise ValueError('Song-symbol OCR observation is required.')
    _verify_proof(raw,pane,proof)
    lines=[dict(line) for line in raw.get('lines',())]
    changed=False
    for index,line in enumerate(lines):
        if not _eligible(line) or line.get('overlay_occluded'):continue
        symbol=music_note_suffix(pane,line.get('box',()))
        if symbol is None:continue
        original=line['text']
        lines[index]=dict(line,text=_restore_symbol_separator(original,symbol['symbol']),
                          original_symbol_text=original,
                          visual_symbol_observation=_symbol_observation(raw,proof,symbol),
                          text_normalization='song_note_suffix')
        changed=True
    return dict(raw,lines=lines) if changed else dict(raw)


def apply(raw,extra,proof,original=None):
    """Replay a persisted note observation only after rechecking its pixels.

    A sidecar is source-bound metadata, not an authorization to trust its
    claimed glyph.  The gameplay crop is the proof image, so replay the same
    detector against that image and require the persisted observation to agree
    on the symbol, detector method, geometry, and one-image status.
    """
    original=raw if original is None else original
    if not isinstance(raw,dict) or not isinstance(extra,dict):
        raise ValueError('Song-symbol provenance mismatch.')
    if extra.get('version')!=1:
        raise ValueError('Song-symbol provenance mismatch.')
    if extra.get('raw_sha256')!=fingerprint(original):
        raise ValueError('Song-symbol provenance mismatch.')
    try:
        evidence_hash=hashlib.sha256(proof.read_bytes()).hexdigest()
    except (OSError,TypeError,AttributeError) as exc:
        raise ValueError('Song-symbol provenance mismatch.') from exc
    if extra.get('evidence_sha256')!=evidence_hash:
        raise ValueError('Song-symbol provenance mismatch.')
    gameplay_hash=original.get('gameplay_sha256')
    if not isinstance(gameplay_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',gameplay_hash):
        raise ValueError('Song-symbol gameplay pixels are not source-bound.')
    try:
        with Image.open(proof) as image:
            pane=image.convert('RGB')
            actual_gameplay=hashlib.sha256(pane.tobytes()).hexdigest()
            if actual_gameplay!=gameplay_hash:
                raise ValueError('Song-symbol gameplay pixels do not match the OCR observation.')
            persisted=extra.get('observations')
            if not isinstance(persisted,list):
                raise ValueError('Song-symbol observations are malformed.')
            lines=[dict(l) for l in raw.get('lines',())]
            seen=set()
            for item in persisted:
                if not isinstance(item,dict):
                    raise ValueError('Song-symbol observation is malformed.')
                i=item.get('line_index')
                if type(i) is not int or i<0 or i>=len(lines) or i in seen:
                    raise ValueError('Song-symbol observation line is malformed.')
                seen.add(i)
                line=lines[i]
                if line.get('box')!=item.get('line_box') or not _eligible(line):
                    raise ValueError('Song-symbol OCR line changed.')
                symbol=item.get('symbol')
                if not isinstance(symbol,dict):
                    raise ValueError('Song-symbol observation is malformed.')
                observed=music_note_suffix(pane,line.get('box',()))
                required_method='isolated_note_stem_flag_head_and_closing_quote'
                if (observed is None or symbol.get('symbol')!='\u266a'
                        or symbol.get('method')!=required_method
                        or symbol.get('independent_observations') is not False
                        or symbol.get('thresholds')!=observed.get('thresholds')
                        or symbol.get('box')!=observed.get('box')
                        or observed.get('symbol')!=symbol.get('symbol')
                        or observed.get('method')!=symbol.get('method')
                        or observed.get('independent_observations') is not False):
                    raise ValueError('Song-symbol cached pixel proof does not match the source image.')
                proof_metadata=dict(
                    source_timestamp_ms=raw['source_timestamp_ms'],
                    evidence=raw['evidence'],
                    gameplay_sha256=gameplay_hash,
                    source_frame_sha256=raw['source_frame_sha256'],
                    evidence_sha256=evidence_hash,
                )
                if raw.get('source_sha256') is not None:
                    proof_metadata['source_sha256']=raw['source_sha256']
                text=_restore_symbol_separator(line['text'],observed['symbol'])
                lines[i]=dict(line,text=text,original_symbol_text=line['text'],
                              visual_symbol_observation=_symbol_observation(
                                  raw,proof_metadata,observed))
    except ValueError:
        raise
    except (OSError,TypeError,AttributeError) as exc:
        raise ValueError('Song-symbol gameplay proof is unreadable.') from exc
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
