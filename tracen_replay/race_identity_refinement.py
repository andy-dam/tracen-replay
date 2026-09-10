"""Corroborate unchanged race-field text with source-bound crop rereads.

This optional adapter never runs OCR or supplies replacement text. It checks
actual capture, frame, gameplay and crop bytes before promoting a reading.
Different crops are correlated views; support requires distinct source times.
"""
import copy
import hashlib
import json
import math
import re
from pathlib import Path

from .vision import parse, within

SCHEMA = 'tracen-replay/race-identity-refinement-v1'
FIELDS = {
    'race_name': (95, (280, 425, 810, 456)),
    'placing': (95, (280, 160, 550, 355)),
    'course': (97, (260, 457, 810, 495)),
}
PADDINGS = (4, 10, 20)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _score(value):
    return type(value) in (int, float) and 0 <= value <= 100 and math.isfinite(value)


def _path(root, relative):
    _require(isinstance(relative, str) and bool(relative), 'Missing evidence path.')
    path = (root / relative).resolve()
    _require(path.is_relative_to(root) and path.is_file(), 'Evidence is outside the recording or missing.')
    return path


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _field_line(raw, field):
    threshold, band = FIELDS[field]
    lines = []
    for index, line in enumerate(raw['lines']):
        text = line['text']
        if not within(line, band):
            continue
        if field == 'race_name' and re.fullmatch(r'DEBUT|G[123]|OP|PRE-OP|EX', text, re.I):
            continue
        if field == 'placing' and not re.fullmatch(r'\d{1,2}(?:st|nd|rd|th)', text, re.I):
            continue
        if field == 'course' and not re.search(r'\b(?:Turf|Dirt)\b', text):
            continue
        lines.append((index, line))
    _require(len(lines) == 1, 'Race field does not have one source line.')
    index, line = lines[0]
    _require(_score(line['confidence']) and 90 <= line['confidence'] < threshold,
             'Original field is outside the fixed refinement band.')
    return index, line


def _load_frame(root, frame):
    from PIL import Image
    raw_path = _path(root, 'neural/' + frame['id'] + '.json')
    raw = _read(raw_path)
    _require(isinstance(raw.get('model_sha256'), dict) and raw['model_sha256']
             and all(_hash(h) for h in raw['model_sha256'].values())
             and _hash(raw.get('engine_fingerprint')), 'Missing original OCR model identity.')
    _require(raw['source_timestamp_ms'] == frame['source_timestamp_ms'], 'Source timestamp mismatch.')
    source_path = _path(root, frame['evidence'])
    evidence_path = _path(root, raw['evidence'])
    _require(_sha(source_path) == raw['source_frame_sha256'], 'Source frame changed.')
    with Image.open(source_path) as image:
        _require(image.size == (1920, 1080), 'Unsupported source layout.')
        source_pixels = image.convert('RGB').crop((148, 0, 958, 1080)).tobytes()
    with Image.open(evidence_path) as image:
        pane = image.convert('RGB')
    _require(pane.size == (810, 1080) and pane.tobytes() == source_pixels,
             'Gameplay proof differs from the decoded source frame.')
    _require(hashlib.sha256(source_pixels).hexdigest() == raw['gameplay_sha256'], 'OCR pixels changed.')
    _require(parse(raw)['screen'] == 'race_result', 'Source is not a recognized race-result panel.')
    return raw, pane, _sha(evidence_path)


def _validate(root, extra):
    root = Path(root).resolve()
    _require(extra.get('schema_version') == SCHEMA and extra.get('field') in FIELDS,
             'Unsupported race refinement schema or field.')
    _require(extra.get('independent_observations') is False, 'Crop views cannot claim independence.')
    model = extra.get('model_sha256')
    _require(isinstance(model, dict) and model and all(_hash(h) for h in model.values())
             and _hash(extra.get('engine_fingerprint')), 'Missing reread model identity.')
    capture_path = root / 'capture.json'
    _require(_sha(capture_path) == extra['capture_sha256'], 'Capture manifest changed.')
    capture = _read(capture_path)
    _require(_hash(extra.get('source_sha256')) and capture['source']['sha256'] == extra['source_sha256'],
             'Recording source mismatch.')
    observations = extra['observations']
    _require(isinstance(observations, list) and len(observations) >= 2, 'Two source frames are required.')
    ids = [item['frame_id'] for item in observations]
    _require(len(set(ids)) == len(ids), 'Duplicate source frame IDs.')
    indexed = {frame['id']: (index, frame) for index, frame in enumerate(capture['frames'])}
    _require(all(frame_id in indexed for frame_id in ids), 'Frame is absent from capture.')
    selected = sorted((indexed[frame_id] for frame_id in ids))
    times = [frame['source_timestamp_ms'] for _, frame in selected]
    _require(all(type(t) is int and t >= 0 for t in times) and len(set(times)) == len(times)
             and 0 < times[-1] - times[0] <= 1000, 'Source times are duplicated or too far apart.')
    # Every captured observation between the witnesses must still be a result
    # panel. A caller cannot omit a known navigation/unknown frame from support.
    loaded = {}
    for frame in capture['frames'][selected[0][0]:selected[-1][0] + 1]:
        loaded[frame['id']] = _load_frame(root, frame)
    threshold = FIELDS[extra['field']][0]
    originals, scores, records = [], [], {}
    for item in observations:
        raw, pane, proof_hash = loaded[item['frame_id']]
        _require(item['raw_sha256'] == fingerprint(raw) and item['evidence_sha256'] == proof_hash
                 and item['source_frame_sha256'] == raw['source_frame_sha256']
                 and item['gameplay_sha256'] == raw['gameplay_sha256'], 'Source observation changed.')
        index, original = _field_line(raw, extra['field'])
        _require(item['line_index'] == index, 'Source line index changed.')
        views = item['views']
        _require(isinstance(views, list) and len(views) == 3, 'Three recorded crop geometries are required.')
        left, top, right, bottom = original['box']
        geometries = {(max(0, left - 148 - p), max(0, top - 6),
                       min(810, right - 148 + p), min(1080, bottom + 6)) for p in PADDINGS}
        _require(len(geometries) == 3 and {tuple(view['crop_box']) for view in views} == geometries,
                 'Crop geometry differs from the fixed reread policy.')
        strong = []
        for view in views:
            _require(view.get('model_sha256') == model
                     and view.get('engine_fingerprint') == extra['engine_fingerprint'],
                     'Crop reread model identity differs from its sidecar.')
            _require(view['original'] == original and view['index'] == index, 'Crop source line changed.')
            pixels = pane.crop(tuple(view['crop_box'])).tobytes()
            _require(view['crop_pixel_sha256'] == hashlib.sha256(pixels).hexdigest(), 'Crop pixels changed.')
            text, confidence = view['text'], view['confidence']
            _require(isinstance(text, str) and _score(confidence), 'Invalid crop reading.')
            _require(confidence < threshold or text == original['text'], 'Confident crop text disagrees.')
            if confidence >= 97 and text == original['text']:
                strong.append(confidence)
        _require(bool(strong), 'Each frame needs a strong unchanged-text reread.')
        originals.append(original)
        scores.append(max(strong))
        records[item['frame_id']] = (raw, index, original)
    _require(all(line['text'] == originals[0]['text'] and line['box'] == originals[0]['box']
                 for line in originals), 'Source text or field geometry changes between frames.')
    target = extra['target_frame_id']
    _require(target in records, 'Target is not a supporting source frame.')
    raw, index, original = records[target]
    _require(extra['raw_sha256'] == fingerprint(raw)
             and extra['evidence_sha256'] == loaded[target][2], 'Target provenance changed.')
    return raw, index, original, min(scores)


def build(root, field, observations):
    """Return one validated sidecar per supplied target; no OCR is performed.

    Each input has a capture ``frame_id`` and its three recorded crop ``views``.
    Views contain index, original line, crop_box, crop_pixel_sha256, text and
    confidence, model_sha256 and engine_fingerprint from the recorded reread.
    Base source metadata is read from the immutable recording cache.
    """
    root = Path(root).resolve()
    _require(field in FIELDS, 'Unsupported race identity field.')
    _require(isinstance(observations, list) and len(observations) >= 2,
             'Two source frames are required.')
    _require(isinstance(observations[0].get('views'), list) and len(observations[0]['views']) == 3,
             'Three recorded crop geometries are required.')
    identity = observations[0]['views'][0]
    capture = _read(root / 'capture.json')
    frames = {frame['id']: frame for frame in capture['frames']}
    records = []
    for observation in observations:
        frame_id = observation['frame_id']
        _require(frame_id in frames, 'Frame is absent from capture.')
        raw, _, evidence_hash = _load_frame(root, frames[frame_id])
        index, _ = _field_line(raw, field)
        records.append(dict(frame_id=frame_id, line_index=index, raw_sha256=fingerprint(raw),
                            evidence_sha256=evidence_hash, source_frame_sha256=raw['source_frame_sha256'],
                            gameplay_sha256=raw['gameplay_sha256'], views=copy.deepcopy(observation['views'])))
    shared = dict(schema_version=SCHEMA, field=field, source_sha256=capture['source']['sha256'],
                  capture_sha256=_sha(root / 'capture.json'), model_sha256=identity['model_sha256'],
                  engine_fingerprint=identity['engine_fingerprint'], independent_observations=False,
                  observations=records)
    sidecars = []
    for record in records:
        extra = dict(copy.deepcopy(shared), target_frame_id=record['frame_id'],
                     raw_sha256=record['raw_sha256'], evidence_sha256=record['evidence_sha256'])
        _validate(root, extra)
        sidecars.append(extra)
    return sidecars


def apply(raw, extra, root, *, original=None):
    """Validate the backing files and promote one unchanged source line."""
    baseline, index, line, confidence = _validate(root, extra)
    _require(fingerprint(raw if original is None else original) == fingerprint(baseline),
             'Target does not match the immutable source observation.')
    _require(raw['lines'][index] == line, 'A prior refinement already changed this field.')
    result = copy.deepcopy(raw)
    result['lines'][index].update(confidence=confidence, original_confidence=line['confidence'],
                                  race_identity_refined=True)
    result['race_identity_refinement'] = dict(copy.deepcopy(extra), accepted_confidence=confidence,
                                              same_text=True)
    return result
