"""Re-read receipt text with padded crops; never supply expected text to OCR."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from .vision import NeuralReader, within
from .full_recording import save_json
from .gameplay import effects_from_lines


def fingerprint(raw):
    return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()


def consensus(views):
    # These are correlated views of ONE frame, not three independent observations.
    texts={v['text'].strip() for v in views}
    if len(views)!=3:return None
    if len(texts)==1 and receipt(next(iter(texts))) and min(v['confidence'] for v in views)>=95 and max(v['confidence'] for v in views)>=97:
        return dict(text=next(iter(texts)),confidence=min(v['confidence'] for v in views))
    valid=[v for v in views if v['confidence']>=95 and receipt(v['text'])]
    strong=[v for v in valid if v['confidence']>=97]
    if len(strong)>=2 and len({receipt(v['text']) for v in valid})==1:
        return dict(text=strong[0]['text'],confidence=min(v['confidence'] for v in strong))
    return None


def receipt(text):
    text=re.sub(r'\bby(\d)',r'by \1',text)
    effects=effects_from_lines([dict(text=text,confidence=100)])
    if not effects:return None
    return json.dumps([{k:v for k,v in e.items() if k not in ('raw_text','confidence')} for e in effects],sort_keys=True)


def apply(raw,extra):
    if extra['raw_sha256']!=fingerprint(raw):raise ValueError('Receipt refinement source mismatch.')
    result=dict(raw,lines=[dict(line) for line in raw['lines']])
    for item in extra['lines']:
        accepted=consensus(item['views'])
        if accepted:
            original=result['lines'][item['index']]
            result['lines'][item['index']]=dict(original,**accepted,original_text=original['text'],
                original_confidence=original['confidence'],receipt_crop_views=item['views'])
        else:
            original=result['lines'][item['index']]
            alternatives={receipt(v['text']) for v in item['views'] if v['confidence']>=95 and receipt(v['text'])}
            if alternatives and alternatives!={receipt(original['text'])}:
                # An unresolved crop disagreement invalidates the original confident
                # reading; it does not authorize choosing one of the alternatives.
                result['lines'][item['index']]=dict(original,confidence=0,original_confidence=original['confidence'],
                    receipt_crop_views=item['views'],receipt_crop_conflict=True)
    return result


def refine(root):
    root=Path(root);reader=NeuralReader();count=0
    for path in sorted((root/'receipt-inspection').glob('*/*.v2.json')):
        target=path.with_suffix('.receipt.json')
        if target.exists():continue
        raw=json.loads(path.read_text(encoding='utf-8'))
        candidates=[(i,line) for i,line in enumerate(raw['lines']) if within(line,(250,770,850,1000))]
        images=[]
        with reader.Image.open(root/raw['evidence']) as pane:
            for _,line in candidates:
                left,top,right,bottom=line['box']
                for padding in (4,10,20):
                    crop=pane.convert('RGB').crop((max(0,left-148-padding),max(0,top-6),min(810,right-148+padding),min(1080,bottom+6)))
                    images.append(reader.np.array(crop)[:,:,::-1])
        observations=[]
        if images:
            result=reader.engine.text_rec(reader.TextRecInput(img=images))
            views=[dict(text=text,confidence=round(float(score)*100,4)) for text,score in zip(result.txts,result.scores)]
            for j,(index,_) in enumerate(candidates):observations.append(dict(index=index,views=views[j*3:j*3+3]))
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256((root/raw['evidence']).read_bytes()).hexdigest(),
            lines=observations,model_sha256=reader.models,independent_observations=False))
        count+=1
    print(json.dumps(dict(stage='receipt_crop_refinement',new_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
