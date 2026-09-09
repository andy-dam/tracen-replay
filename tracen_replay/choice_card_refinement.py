"""Read weak dialogue cards at native size, retaining same-frame disagreement."""
import argparse
import hashlib
import json
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
    return dict(version=1, policy=POLICY, raw_sha256=fingerprint(raw),
                evidence_sha256=proof_sha256, models=reader.models,
                engine_fingerprint=reader.fingerprint, views=views,
                independent_observations=False)


def observation(pane, raw, extra):
    """Recompute geometry and accept only exact base/crop text agreement."""
    if extra.get('version') != 1 or extra.get('policy') != POLICY:
        raise ValueError('Unsupported choice card refinement policy.')
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
    if hashlib.sha256(proof.read_bytes()).hexdigest() != extra['evidence_sha256']:
        raise ValueError('Choice card evidence provenance mismatch.')
    with Image.open(proof) as image:
        observed = observation(image.convert('RGB'), raw, extra)
    if row['screen'] != 'unknown':
        return row
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
