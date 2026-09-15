"""Read awarded performance currency in bounded training-result frames."""
import argparse
import hashlib
import json
from pathlib import Path
from .vision import NeuralReader, parse
from .gameplay import CURRENCIES
from .full_recording import save_json
from .refine_contrast import fingerprint
from .refine_results import apply_result_refinement


def refine(root):
    root=Path(root);reader=NeuralReader();count=0
    for path in sorted((root/'training-inspection').glob('*/*.v2.json')):
        raw=json.loads(path.read_text(encoding='utf-8'));candidate=raw
        totals=path.with_suffix('.totals.json')
        if totals.exists():candidate=apply_result_refinement(raw,json.loads(totals.read_text(encoding='utf-8')))
        if parse(candidate)['screen']!='training_result':continue
        target=path.with_suffix('.performance.json')
        if target.exists():continue
        image_path=root/raw['evidence']
        boxes=[(245,296+56*i,319,333+56*i) for i in range(5)]
        with reader.Image.open(image_path) as image:
            images=[reader.np.array(image.convert('RGB').crop((b[0]-148,b[1],b[2]-148,b[3])))[:,:,::-1] for b in boxes]
        result=reader.engine.text_rec(reader.TextRecInput(img=images))
        regions={'performance_gain.'+f:dict(text=t,confidence=round(float(s)*100,4),box=list(b)) for f,b,t,s in zip(CURRENCIES,boxes,result.txts,result.scores)}
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),model_sha256=reader.models,regions=regions))
        count+=1
        if count%100==0:print(json.dumps(dict(stage='performance_refinement',frames=count)),flush=True)
    print(json.dumps(dict(stage='performance_refinement_complete',new_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
