"""Read result totals with room for four-digit attributes and adjacent grade icons."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from .vision import NeuralReader
from .reconcile import FIELDS
from .full_recording import save_json


def apply_result_refinement(raw,refinement):
    if refinement['raw_sha256']!=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest():raise ValueError('Result refinement source mismatch.')
    regions=dict(raw['regions'])
    def valid(name,observation):
        if observation.get('confidence',0)<90:return False
        text=observation.get('text','').strip()
        if name=='result.skill_points':return bool(re.fullmatch(r'\d{1,4}',text))
        match=re.fullmatch(r'(\d{1,4})/(\d{4})',text)
        return bool(match and int(match[1])<=int(match[2]))
    for name,observation in refinement['regions'].items():
        if valid(name,observation) and (observation['confidence']>=97 or not valid(name,regions.get(name,{}))):regions[name]=observation
    from .result_counter import partial_counter
    partials={name:observation for name,observation in refinement['regions'].items()
              if name!='result.skill_points' and partial_counter(observation)}
    return dict(raw,regions=regions,result_numerator_refinement=partials)


def refine(root):
    root=Path(root);reader=NeuralReader();count=0
    for path in sorted((root/'training-inspection').glob('*/*.v2.json')):
        raw=json.loads(path.read_text(encoding='utf-8'))
        # Inspect the entire bounded result window. Occlusion can hide the blue
        # grid and most ratios; requiring prior recognition discards the very
        # frames that this wider numeric pass is intended to recover.
        target=path.with_suffix('.totals.json')
        if target.exists():continue
        image_path=root/raw['evidence']
        boxes=[((309,505,701)[i%3],832 if i<3 else 950,(452,648,844)[i%3],880 if i<3 else 998) for i in range(6)]
        boxes[-1]=(701,950,822,998)
        with reader.Image.open(image_path) as image:
            images=[reader.np.array(image.convert('RGB').crop((b[0]-148,b[1],b[2]-148,b[3])))[:,:,::-1] for b in boxes]
        result=reader.engine.text_rec(reader.TextRecInput(img=images))
        regions={'result.'+f:dict(text=text,confidence=round(float(score)*100,4),box=list(box)) for f,box,text,score in zip(FIELDS,boxes,result.txts,result.scores)}
        save_json(target,dict(raw_sha256=hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest(),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),model_sha256=reader.models,regions=regions))
        count+=1
        if count%100==0:print(json.dumps(dict(stage='result_totals_refinement',frames=count)),flush=True)
    print(json.dumps(dict(stage='result_totals_refinement_complete',new_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path);args=parser.parse_args();refine(args.output)
