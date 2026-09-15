"""Recheck uncertain receipt lines with padded local crops, preserving original OCR."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from .vision import NeuralReader, within
from .gameplay import effects_from_lines
from .full_recording import save_json


def apply_refinements(raw,refinement):
    if refinement.get('raw_sha256')!=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest():
        raise ValueError('Refinement does not match the original observation.')
    result=dict(raw,lines=[dict(l) for l in raw['lines']])
    seen=set()
    for item in refinement['lines']:
        index=item['index']
        if type(index) is not int or not 0<=index<len(raw['lines']) or index in seen:
            raise ValueError('Invalid or duplicate outcome refinement line index.')
        seen.add(index)
        original=raw['lines'][index]
        score=item['confidence']
        if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=100:
            raise ValueError('Invalid outcome refinement confidence.')
        if item['original_confidence']!=original['confidence']:
            raise ValueError('Outcome refinement original confidence mismatch.')
        # A cached decision is not evidence. Recompute the same-text promotion
        # from the bound original and the recorded reread on every load.
        eligible=80<=original['confidence']<95 and within(original,(250,770,850,1000)) and effects_from_lines([original])
        if eligible and score>=97 and item['text'].strip()==original['text'].strip():
            result['lines'][index].update(confidence=score,original_confidence=original['confidence'],refined=True)
    return result


def refine(root):
    root=Path(root);destination=root/'outcome-refinement';destination.mkdir(exist_ok=True);reader=None;count=0
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'))
        candidates=[(i,l) for i,l in enumerate(raw['lines']) if 80<=l['confidence']<95 and within(l,(250,770,850,1000)) and effects_from_lines([l])]
        if not candidates:continue
        target=destination/path.name
        if target.exists():continue
        if reader is None:reader=NeuralReader()
        image_path=root/raw['evidence']
        with reader.Image.open(image_path) as pane:
            images=[]
            for _,line in candidates:
                left,top,right,bottom=line['box']
                crop=pane.convert('RGB').crop((max(0,left-148-8),max(0,top-6),min(810,right-148+8),min(1080,bottom+6)))
                images.append(reader.np.array(crop)[:,:,::-1])
            result=reader.engine.text_rec(reader.TextRecInput(img=images))
        observations=[]
        for (index,line),text,score in zip(candidates,result.txts,result.scores):
            confidence=round(float(score)*100,4)
            observations.append(dict(index=index,text=text,confidence=confidence,original_confidence=line['confidence'],
                accepted=confidence>=97 and text.strip()==line['text'].strip()))
        save_json(target,dict(raw_sha256=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest(),
            evidence=raw['evidence'],evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),model_sha256=reader.models,lines=observations))
        count+=1
        if count%100==0:print(json.dumps(dict(stage='outcome_refinement',frames=count)),flush=True)
    print(json.dumps(dict(stage='outcome_refinement_complete',new_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path);args=parser.parse_args();refine(args.output)
