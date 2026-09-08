"""Locate numeric phrases in obstructed receipt lines using OCR alignment."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from .receipt_occlusion import annotate,numeric_bounds
from .refine_contrast import fingerprint


def refine(root):
    from .vision import NeuralReader
    from .full_recording import save_json
    from PIL import Image
    root=Path(root);reader=None;count=0
    paths=list((root/'neural').glob('*.json'))
    for folder in ('receipt-inspection','training-inspection','native-inspection'):
        paths.extend((root/folder).glob('*/*.v2.json'))
    for path in sorted(paths):
        raw=json.loads(path.read_text(encoding='utf-8'));proof=root/raw['evidence'];target=proof.with_suffix('.overlay.json')
        def eligible(text):return 'by' in text or bool(re.fullmatch(r'Gained \d+ fans[.!]?',text))
        if not any(eligible(l['text']) and 770<=l['box'][1]<1000 for l in raw['lines']):continue
        existing=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
        if existing and (existing['raw_sha256']!=fingerprint(raw) or existing['evidence_sha256']!=hashlib.sha256(proof.read_bytes()).hexdigest()):
            raise ValueError('Existing overlay alignment provenance changed.')
        previous=existing['lines'] if existing else []
        with Image.open(proof) as pane:
            marked=annotate(raw,pane)
            boxes=[l['box'] for l in marked.get('occluded_receipt_lines',[]) if eligible(l['text'])
                   and not any(old['line_box']==l['box'] for old in previous)]
            if not boxes:continue
            if reader is None:reader=NeuralReader()
            images=[reader.np.array(pane.convert('RGB').crop((a-148,b,c-148,d)))[:,:,::-1] for a,b,c,d in boxes]
        result=reader.engine.text_rec(reader.TextRecInput(img=images,return_word_box=True));lines=list(previous)
        for box,text,score,info in zip(boxes,result.txts,result.scores,result.word_results):
            words=[''.join(w) for w in info.words]
            numeric=numeric_bounds(box,words,info.word_cols,info.line_txt_len) if score>=.95 else None
            lines.append(dict(line_box=box,recognized_text=text,confidence=round(float(score)*100,4),
                              words=words,columns=info.word_cols,line_length=info.line_txt_len,numeric_box=numeric))
        save_json(target,dict(raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                              model_sha256=reader.models,lines=lines,independent_observations=False))
        count+=1
        if count%100==0:print(json.dumps(dict(stage='overlay_alignment',updated_frames=count)),flush=True)
    print(json.dumps(dict(stage='overlay_alignment_complete',updated_frames=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
