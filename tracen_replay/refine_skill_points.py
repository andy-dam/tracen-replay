"""Read the skill-menu counter directly when detection misses a small number."""
import argparse
import hashlib
import json
from pathlib import Path
import re


BOXES=((670,330,800,378),(650,330,825,378))


def counter_reading(views,original=None):
    """Crop agreement is one frame's evidence, not independent observations."""
    values=[]
    for view in views:
        text=view.get('text','').strip()
        if view.get('confidence',0)>=97 and re.fullmatch(r'\d{1,4}',text):
            values.append(int(text))
    candidates=set(values)
    if type(original) is int:candidates.add(original)
    if len(candidates)>1:return None,sorted(candidates)
    if len(views)==2 and len(values)==2:return values[0],[]
    return original,[]


def refine(root):
    from .vision import NeuralReader,parse,within
    from .full_recording import save_json
    from .refine_contrast import fingerprint
    root=Path(root);destination=root/'skill-points-refinement';destination.mkdir(exist_ok=True)
    reader=None;count=0
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'))
        if parse(raw)['screen']!='skill_selection':continue
        labels=[line for line in raw['lines'] if line['confidence']>=97
                and line['text']=='Skill Points' and within(line,(500,325,650,380))]
        if len(labels)!=1:continue
        target=destination/path.name
        if target.exists():continue
        if reader is None:reader=NeuralReader()
        image_path=root/raw['evidence']
        with reader.Image.open(image_path) as pane:
            images=[reader.np.array(pane.convert('RGB').crop((left-148,top,right-148,bottom)))[:,:,::-1]
                    for left,top,right,bottom in BOXES]
        result=reader.engine.text_rec(reader.TextRecInput(img=images))
        views=[dict(text=text,confidence=round(float(score)*100,4),box=list(box))
               for text,score,box in zip(result.txts,result.scores,BOXES)]
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
                              model_sha256=reader.models,views=views,independent_frame_count=1))
        count+=1
        if count%100==0:print(json.dumps(dict(stage='skill_points_refinement',frames=count)),flush=True)
    print(json.dumps(dict(stage='skill_points_refinement_complete',new_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
