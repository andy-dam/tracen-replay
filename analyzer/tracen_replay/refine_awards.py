"""Read enlarged rank-up awards at multiple observed animation scales."""
import argparse
import hashlib
import json
from pathlib import Path
from .vision import NeuralReader
from .full_recording import save_json
from .refine_contrast import fingerprint


def refine(root):
    root=Path(root);reader=NeuralReader();count=0
    boxes=[(297,821,497,910),(250,800,515,925),(250,800,600,920)]
    for path in sorted((root/'training-inspection').glob('*-native-v2/*.v2.json')):
        target=path.with_suffix('.awards.json')
        if target.exists():continue
        raw=json.loads(path.read_text(encoding='utf-8'));image_path=root/raw['evidence']
        with reader.Image.open(image_path) as image:
            images=[reader.np.array(image.convert('RGB').crop((b[0]-148,b[1],b[2]-148,b[3])))[:,:,::-1] for b in boxes]
        result=reader.engine.text_rec(reader.TextRecInput(img=images))
        regions={f'scaled_gain_{i}.speed':dict(text=t,confidence=round(float(s)*100,4),box=list(b)) for i,(b,t,s) in enumerate(zip(boxes,result.txts,result.scores))}
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),model_sha256=reader.models,regions=regions));count+=1
    print(json.dumps(dict(stage='award_refinement_complete',new_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path);refine(parser.parse_args().output)
