"""Recheck obscured training digits with two contrast views of source pixels."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from PIL import ImageOps
from .vision import NeuralReader
from .reconcile import FIELDS
from .full_recording import save_json


def fingerprint(raw):
    return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()


def apply_contrast_refinement(raw, refinement, original=None):
    if refinement['raw_sha256'] != fingerprint(original or raw):
        raise ValueError('Contrast refinement source mismatch.')
    regions=dict(raw['regions'])
    for name,views in refinement['regions'].items():
        # These views are correlated; agreement does not count as multiple
        # frames. Temporal and before/result checks still happen downstream.
        if len(views)!=2 or any(v['confidence']<97 for v in views):continue
        texts={v['text'] for v in views}
        if len(texts)!=1:continue
        text=texts.pop()
        ratio=re.fullmatch(r'(\d{1,4})/(\d{4})',text)
        valid=bool(ratio and int(ratio[1])<=int(ratio[2]))
        if name=='result.skill_points':valid=bool(re.fullmatch(r'\d{1,4}',text))
        if valid:regions[name]=dict(views[0],confidence=min(v['confidence'] for v in views),contrast_consensus=True)
    return dict(raw,regions=regions)


def refine(root):
    root=Path(root);report=json.loads((root/'report.json').read_text(encoding='utf-8'))
    gaps=[i for i in report['gameplay_tracking']['intervals'] if i['status']=='unresolved']
    reader=NeuralReader();count=0
    boxes=[((309,505,701)[i%3],832 if i<3 else 950,(452,648,844)[i%3],880 if i<3 else 998) for i in range(6)]
    boxes[-1]=(701,950,822,998)
    for path in sorted((root/'training-inspection').glob('*/*.v2.json')):
        raw=json.loads(path.read_text(encoding='utf-8'));time=raw['source_timestamp_ms']
        if not any(i['start_ms']<time<i['end_ms'] for i in gaps):continue
        target=path.with_suffix('.contrast.json')
        if target.exists():continue
        image_path=root/raw['evidence'];images=[]
        with reader.Image.open(image_path) as image:
            image=image.convert('RGB')
            for box in boxes:
                crop=image.crop((box[0]-148,box[1],box[2]-148,box[3]))
                for view in (ImageOps.grayscale(crop),crop.getchannel('B')):
                    view=ImageOps.expand(view.resize((view.width*3,view.height*3)),border=8,fill=255).convert('RGB')
                    images.append(reader.np.array(view)[:,:,::-1])
        result=reader.engine.text_rec(reader.TextRecInput(img=images));regions={}
        for i,field in enumerate(FIELDS):
            regions['result.'+field]=[dict(text=result.txts[j],confidence=round(float(result.scores[j])*100,4),box=list(boxes[i]),view=('gray','blue')[j%2]) for j in (i*2,i*2+1)]
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),model_sha256=reader.models,regions=regions))
        count+=1
        if count%100==0:print(json.dumps(dict(stage='contrast_refinement',frames=count)),flush=True)
    print(json.dumps(dict(stage='contrast_refinement_complete',new_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
