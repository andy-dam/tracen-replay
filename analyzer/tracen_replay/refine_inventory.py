"""Re-read final skill panels, preserving unknown variants and inventory scope."""
import argparse
import hashlib
import json
from pathlib import Path
from .inventory import visible_cards
from .refine_contrast import fingerprint

BOX=(258,500,838,944)


def apply(row, original, extra, image_path):
    if (extra['raw_sha256']!=fingerprint(original) or
        extra['evidence_sha256']!=hashlib.sha256(Path(image_path).read_bytes()).hexdigest() or
        extra['source_timestamp_ms']!=original['source_timestamp_ms'] or
        extra['evidence']!=original['evidence'] or extra['box']!=list(BOX) or
        not extra.get('model_sha256')):
        raise ValueError('Inventory panel provenance mismatch.')
    if row['screen']!='career_summary':return row
    # Retain original layout anchors; crop text alone cannot establish ownership.
    lines=[l for l in original['lines'] if not 500<=l['box'][1]<944]+extra['lines']
    cards=visible_cards(dict(original,lines=lines),row['facts']['final_attributes'])
    from PIL import Image
    from .inventory_suffix import detect as circle_suffix
    with Image.open(image_path) as pane:
        for card in cards:
            box=card['text_evidence'][-1]['box']
            variant=circle_suffix(pane,box)
            if variant:
                card.update(observed_variant=variant,variant_evidence=dict(box=box,
                    method='inventory_circle_geometry_with_terminal_letter_rejection_v1',
                    evidence_sha256=extra['evidence_sha256']))
    base={tuple(c['slot']):c for c in row['facts'].get('visible_owned_skill_cards',[])}
    conflicts=[]
    for card in cards:
        slot=tuple(card['slot']);old=base.get(slot)
        if old and old['name_text']!=card['name_text']:
            conflicts.append(dict(slot=list(slot),names=[old['name_text'],card['name_text']]))
            base.pop(slot)
        else:
            if old:
                for field in ('level','variant'):
                    values={v for v in (old.get('observed_'+field),card.get('observed_'+field)) if v is not None}
                    values.update(old.get(field+'_conflicts',[]))
                    values.update(card.get(field+'_conflicts',[]))
                    if len(values)>1:
                        card['observed_'+field]=None
                        card[field+'_conflicts']=sorted(values)
                        card['base_'+field+'_evidence']=old.get(field+'_evidence')
            base[slot]=dict(card,recognition_basis='source_panel_crop',independent_frame_count=1)
    facts=dict(row['facts'],visible_owned_skill_cards=list(base.values()),
               owned_skill_panel_conflicts=conflicts)
    return dict(row,facts=facts)


def refine(root):
    from .vision import NeuralReader
    root=Path(root);report=json.loads((root/'report.json').read_text(encoding='utf-8'))
    destination=root/'inventory-refinement';destination.mkdir(exist_ok=True)
    reader=NeuralReader();count=0
    for frame in report['gameplay_tracking']['owned_skill_inventory']['summary_frames']:
        path=root/'neural'/(Path(frame['evidence']).stem+'.json')
        raw=json.loads(path.read_text(encoding='utf-8'));image_path=root/frame['evidence']
        target=destination/path.name
        if target.exists():continue
        with reader.Image.open(image_path) as image:
            crop=image.convert('RGB').crop((BOX[0]-148,BOX[1],BOX[2]-148,BOX[3]))
            result=reader.engine(reader.np.array(crop)[:,:,::-1]);lines=[]
            if result.txts:
                for text,score,box in zip(result.txts,result.scores,result.boxes):
                    lines.append(dict(text=text,confidence=round(float(score)*100,4),box=[
                        round(float(box[:,0].min()))+BOX[0],round(float(box[:,1].min()))+BOX[1],
                        round(float(box[:,0].max()))+BOX[0],round(float(box[:,1].max()))+BOX[1]]))
        extra=dict(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence'],
            raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
            model_sha256=reader.models,box=list(BOX),lines=lines,independent_frame_count=1)
        target.write_text(json.dumps(extra,indent=2),encoding='utf-8');count+=1
    print(json.dumps(dict(stage='inventory_panel_refinement',new_frames=count)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    refine(parser.parse_args().root)
