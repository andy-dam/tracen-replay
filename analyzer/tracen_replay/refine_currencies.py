"""Read all digits of lesson balances, retaining the original OCR observations."""
import argparse
import hashlib
import json
from pathlib import Path
from .vision import NeuralReader,parse
from .gameplay import CURRENCIES
from .full_recording import save_json
from .refine_contrast import fingerprint


def refine(root,model_dir='.local/models/rapidocr'):
    """Write a wide-crop reading of every lesson balance slot; return the frame count.

    Runs as a stage of every fresh analysis (after the base pass, before the
    readings are reloaded) and as a tool on a preserved run; an existing
    sidecar is never rewritten.
    """
    root=Path(root);reader=None;count=0
    (root/'currency-refinement').mkdir(exist_ok=True)
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'));screen=parse(raw)['screen']
        if screen not in ('lesson_selection','lesson_confirmation'):continue
        target=root/'currency-refinement'/path.name
        if target.exists():continue
        if reader is None:reader=NeuralReader(model_dir)
        modal=screen=='lesson_confirmation';image_path=root/raw['evidence']
        boxes=[(382+83*i,840,440+83*i,883) if modal else (330+104*i,85,410+104*i,126) for i in range(5)]
        with reader.Image.open(image_path) as image:
            images=[reader.np.array(image.convert('RGB').crop((b[0]-148,b[1],b[2]-148,b[3])))[:,:,::-1] for b in boxes]
        result=reader.engine.text_rec(reader.TextRecInput(img=images))
        prefix='wide_projected_performance.' if modal else 'wide_performance.'
        regions={prefix+f:dict(text=t,confidence=round(float(s)*100,4),box=list(b)) for f,b,t,s in zip(CURRENCIES,boxes,result.txts,result.scores)}
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),model_sha256=reader.models,regions=regions))
        count+=1
        if count%100==0:print(json.dumps(dict(stage='currency_refinement',frames=count)),flush=True)
    print(json.dumps(dict(stage='currency_refinement_complete',new_frames=count)),flush=True)
    from .refine_currency_padding import refine as refine_padding
    padded=refine_padding(root,model_dir=model_dir)
    return dict(method='wide_currency_crops_and_padding_views',new_frames=count,padded_frames=padded,
                model_dir=str(model_dir),model_sha256=reader.models if reader is not None else None)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    parser.add_argument('--model-dir',type=Path,default=Path('.local/models/rapidocr'))
    args=parser.parse_args();refine(args.output,model_dir=args.model_dir)
