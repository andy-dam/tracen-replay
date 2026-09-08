"""Recheck unresolved menu balances without adjacent badge or separator pixels."""
import hashlib
import json
from pathlib import Path


def refine(root):
    from .vision import NeuralReader,parse
    from .gameplay import CURRENCIES
    from .full_recording import save_json
    from .refine_contrast import fingerprint
    root=Path(root);dest=root/'currency-padding-refinement';dest.mkdir(exist_ok=True)
    reader=None;count=0
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'));current=raw
        wide=root/'currency-refinement'/path.name
        if wide.exists():
            extra=json.loads(wide.read_text(encoding='utf-8'))
            if extra['raw_sha256']!=fingerprint(raw):raise ValueError('Currency refinement source changed.')
            current=dict(raw,regions=dict(raw['regions'],**extra['regions']))
        row=parse(current)
        if row['screen']!='lesson_selection':continue
        missing=[f for f in CURRENCIES if row['facts']['performance_points'].get(f) is None]
        target=dest/path.name
        if not missing or target.exists():continue
        if reader is None:reader=NeuralReader()
        image_path=root/raw['evidence'];requests=[]
        for field in missing:
            i=CURRENCIES.index(field)
            requests.extend((field,b) for b in ((326+104*i,87,394+104*i,123),(330+104*i,84,392+104*i,125)))
        with reader.Image.open(image_path) as pane:
            images=[reader.np.array(pane.convert('RGB').crop((a-148,b,c-148,d)))[:,:,::-1] for _,(a,b,c,d) in requests]
        result=reader.engine.text_rec(reader.TextRecInput(img=images));views={}
        for (field,box),text,score in zip(requests,result.txts,result.scores):
            views.setdefault(field,[]).append(dict(text=text,confidence=round(float(score)*100,4),box=list(box)))
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
                              model_sha256=reader.models,views=views,independent_frame_count=1))
        count+=1
    print(json.dumps(dict(stage='currency_padding_refinement_complete',new_frames=count)),flush=True)
