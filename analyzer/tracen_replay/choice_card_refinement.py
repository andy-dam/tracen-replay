"""Read weak dialogue cards at native size, retaining same-frame disagreement."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from .choice_evidence import observe, _card_text
from .refine_contrast import fingerprint
from .vision import NeuralReader, parse

POLICY = 'native_card_exact_text_agreement_v1'


def _candidates(pane, raw):
    return observe(pane, raw['lines'], include_slots=True)


def _weak_slots(observation):
    return [s for s in observation['offered_card_slots']
            if s.get('text') and s.get('confidence', 0) < 97]


def _box(slot):
    return [166, slot['card_y'][0], 684, slot['card_y'][1]]


def build(pane, raw, proof_sha256, reader):
    """Expected options, selected indices and downstream rewards are not inputs."""
    observation = _candidates(pane, raw)
    views = []
    for slot in _weak_slots(observation):
        box = _box(slot)
        crop = pane.crop(box)
        result = reader.engine(np.asarray(crop)[:, :, ::-1])
        lines = []
        if result.txts:
            for text, confidence, corners in zip(result.txts, result.scores, result.boxes):
                lines.append(dict(text=text, confidence=round(float(confidence) * 100, 4),
                    box=[round(float(corners[:, 0].min())) + box[0] + 148,
                         round(float(corners[:, 1].min())) + box[1],
                         round(float(corners[:, 0].max())) + box[0] + 148,
                         round(float(corners[:, 1].max())) + box[1]]))
        views.append(dict(card_y=slot['card_y'], crop_box=box,
                          crop_rgb_sha256=hashlib.sha256(crop.tobytes()).hexdigest(), lines=lines))
    return dict(version=2, policy=POLICY, raw_sha256=fingerprint(raw),
                evidence_sha256=proof_sha256, models=reader.models,
                engine_fingerprint=reader.fingerprint, views=views,views_sha256=fingerprint(views),
                independent_observations=False)


def _validate_extra(extra):
    if not isinstance(extra,dict) or extra.get('version')!=2 or extra.get('policy')!=POLICY:
        raise ValueError('Unsupported choice card refinement policy or version; regenerate observations.')
    if not isinstance(extra.get('views'),list):
        raise ValueError('Choice card views must be a list.')
    if extra.get('views_sha256')!=fingerprint(extra['views']):
        raise ValueError('Choice card OCR contents changed.')
    if (extra.get('independent_observations') is not False or not isinstance(extra.get('models'),dict)
            or not extra['models'] or not isinstance(extra.get('engine_fingerprint'),str) or not extra['engine_fingerprint']):
        raise ValueError('Choice card refinement provenance is incomplete.')
    def numbers(values,size):
        return isinstance(values,list) and len(values)==size and all(
            type(v) in (int,float) and -10000<=v<=10000 and math.isfinite(v) for v in values)
    for view in extra['views']:
        if (not isinstance(view,dict) or not numbers(view.get('crop_box'),4)
                or not numbers(view.get('card_y'),2) or not isinstance(view.get('crop_rgb_sha256'),str)
                or not isinstance(view.get('lines'),list)):
            raise ValueError('Malformed choice card crop observation.')
        for line in view['lines']:
            if (not isinstance(line,dict) or not isinstance(line.get('text'),str) or not line['text']
                    or type(line.get('confidence')) not in (int,float)
                    or not 0<=line['confidence']<=100 or not math.isfinite(line['confidence'])
                    or not numbers(line.get('box'),4)):
                raise ValueError('Malformed choice card OCR line.')
            left,top,right,bottom=line['box']
            crop=view['crop_box']
            if not crop[0]+148<=left<right<=crop[2]+148 or not crop[1]<=top<bottom<=crop[3]:
                raise ValueError('Choice card OCR line leaves its source crop.')


def observation(pane, raw, extra):
    """Recompute geometry and accept only exact base/crop text agreement."""
    _validate_extra(extra)
    if extra['raw_sha256'] != fingerprint(raw):
        raise ValueError('Choice card raw provenance mismatch.')
    if pane.size != (810, 1080) or hashlib.sha256(pane.tobytes()).hexdigest() != raw['gameplay_sha256']:
        raise ValueError('Choice card gameplay pixels changed.')
    result = _candidates(pane, raw)
    weak = _weak_slots(result)
    if len(weak) != len(extra['views']):
        raise ValueError('Choice card crop count differs from weak source slots.')
    for slot, view in zip(weak, extra['views']):
        if view['crop_box'] != _box(slot) or view['card_y'] != slot['card_y']:
            raise ValueError('Choice card crop geometry changed.')
        crop = pane.crop(view['crop_box'])
        if hashlib.sha256(crop.tobytes()).hexdigest() != view['crop_rgb_sha256']:
            raise ValueError('Choice card crop pixels changed.')
        focused, complete = _card_text(view['lines'], [slot['card_y']])
        if complete and focused[0]['text'] == slot['text']:
            slot.update(confidence=focused[0]['confidence'],
                        text_basis='same_frame_native_card_exact_text_agreement')
        elif focused and focused[0]['text'] != slot['text']:
            slot['focused_text_conflict'] = focused[0]['text']
    result['offered_card_candidates'] = [s for s in result['offered_card_slots']
                                        if s.get('text') and s.get('confidence', 0) >= 97]
    result['menu_text_complete'] = bool(result['offered_card_slots']) and all(
        s.get('text') and s.get('confidence', 0) >= 97 for s in result['offered_card_slots'])
    return result


def apply(row, raw, extra, proof):
    _validate_extra(extra)
    if hashlib.sha256(proof.read_bytes()).hexdigest() != extra['evidence_sha256']:
        raise ValueError('Choice card evidence provenance mismatch.')
    with Image.open(proof) as image:
        observed = observation(image.convert('RGB'), raw, extra)
    if row['screen'] != 'unknown':
        return row
    observed['refinement_provenance']=dict(
        policy=extra['policy'],version=extra['version'],raw_sha256=extra['raw_sha256'],
        evidence_sha256=extra['evidence_sha256'],views_sha256=extra['views_sha256'],
        refinement_content_sha256=fingerprint(extra),models=extra['models'],
        engine_fingerprint=extra['engine_fingerprint'],independent_observations=False,
        crops=[{k:v[k] for k in ('card_y','crop_box','crop_rgb_sha256')} for v in extra['views']])
    return dict(row, facts=dict(row.get('facts', {}), choice_observation=observed))


def refine(root, start_ms=None, end_ms=None):
    root = Path(root)
    directory = root / 'choice-card-refinement'
    directory.mkdir(exist_ok=True)
    reader = None
    count = 0
    for path in sorted((root / 'neural').glob('*.json')):
        raw = json.loads(path.read_text(encoding='utf-8'))
        time = raw['source_timestamp_ms']
        if (start_ms is not None and time < start_ms) or (end_ms is not None and time >= end_ms):
            continue
        row = parse(raw)
        if row['screen'] != 'unknown':
            continue
        proof = root / raw['evidence']
        target = directory / path.name
        if target.exists():
            apply(row, raw, json.loads(target.read_text(encoding='utf-8')), proof)
            continue
        with Image.open(proof) as image:
            pane = image.convert('RGB')
        if not _candidates(pane, raw)['offered_card_slots']:
            continue
        if reader is None:
            reader = NeuralReader()
        extra = build(pane, raw, hashlib.sha256(proof.read_bytes()).hexdigest(), reader)
        apply(row, raw, extra, proof)
        with target.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(extra, indent=2) + '\n')
        count += 1
    print(json.dumps(dict(stage='choice_card_refinement',new_observations=count)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--start-ms', type=int)
    parser.add_argument('--end-ms', type=int)
    args = parser.parse_args()
    refine(args.output, args.start_ms, args.end_ms)
