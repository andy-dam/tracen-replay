"""Locate numeric phrases in obstructed receipt lines using OCR alignment."""
import argparse
import hashlib
import json
from pathlib import Path
from .receipt_occlusion import numeric_bounds,overlay_boxes,receipt_line
from .refine_contrast import fingerprint


def alignment_boxes(raw,pane,previous):
    """Inspect edge obstructions before deciding which glyphs they cover.

    The semantic filter's center-line heuristic must not select its own
    alignment evidence: a cursor at a lower name edge can omit a suffix while
    missing the OCR detector box's vertical center.
    """
    if raw.get('gameplay_sha256')!=hashlib.sha256(pane.convert('RGB').tobytes()).hexdigest():
        raise ValueError('Receipt overlay proof differs from original OCR pixels.')
    overlays=overlay_boxes(pane)
    return [line['box'] for line in raw['lines'] if receipt_line(line)
            and not any(old['line_box']==line['box'] for old in previous)
            and any(min(line['box'][2],box[2])-max(line['box'][0],box[0])>=3
                    and min(line['box'][3],box[3])-max(line['box'][1],box[1])>=3
                    for box in overlays)]


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
        if not any(receipt_line(line) for line in raw['lines']):continue
        existing=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
        if existing and (existing['raw_sha256']!=fingerprint(raw) or existing['evidence_sha256']!=hashlib.sha256(proof.read_bytes()).hexdigest()):
            raise ValueError('Existing overlay alignment provenance changed.')
        previous=existing['lines'] if existing else []
        with Image.open(proof) as pane:
            boxes=alignment_boxes(raw,pane,previous)
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
