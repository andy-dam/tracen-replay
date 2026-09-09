"""Validate bounded extra race samples without using them as other gameplay events."""
import argparse
import copy
import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path
from PIL import Image

from .vision import parse
from .race_quantity_refinement import apply as apply_quantities

MANIFEST = 'race-reward-inspection.json'


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Race inspection requires a relative evidence path.')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Race inspection evidence leaves its directory.')
    return path


def _window_checked(root, entry, source):
    if not isinstance(entry, dict) or not isinstance(source, dict):
        raise ValueError('Malformed race inspection provenance.')
    directory = _inside(root, entry['directory'])
    capture_path = directory / 'capture.json'
    if _hash(capture_path) != entry['capture_sha256']:
        raise ValueError('Race inspection capture changed.')
    capture = _read(capture_path)
    if capture['source'] != source:
        raise ValueError('Race inspection belongs to another source.')
    start, end = capture['scope']['start_ms'], capture['scope']['end_ms']
    if (type(start) is not int or type(end) is not int or
            not 0 <= start < end <= source['duration_ms'] or end - start > 5000):
        raise ValueError('Race inspection must be bounded to five seconds.')
    frames = capture['frames']
    if not isinstance(frames, list) or not 1 <= len(frames) <= 301:
        raise ValueError('Invalid race inspection frame count.')
    times, identities, rows = [], set(), []
    raw_hashes = entry['raw_sha256']
    if not isinstance(raw_hashes, dict) or len(raw_hashes) != len(frames):
        raise ValueError('Race inspection OCR hash manifest is incomplete.')
    for frame in frames:
        time, identity = frame['source_timestamp_ms'], frame['id']
        if not isinstance(identity, str) or re.fullmatch(r'[A-Za-z0-9_-]+', identity) is None:
            raise ValueError('Race inspection frame identity must be a safe basename.')
        if (type(time) is not int or not start <= time < end or
                (times and time <= times[-1]) or identity in identities):
            raise ValueError('Race inspection samples must have unique increasing times and identities.')
        if type(frame['source_pts']) is not int:
            raise ValueError('Invalid race inspection PTS.')
        base = Fraction(frame['time_base'])
        if base <= 0:
            raise ValueError('Invalid race inspection time base.')
        pts = round((frame['source_pts'] * base - Fraction(str(source.get('timeline_origin_seconds', 0)))) * 1000)
        if pts != time:
            raise ValueError('Race inspection timestamp differs from source PTS.')
        times.append(time)
        identities.add(identity)
        raw_path = _inside(directory, 'neural/' + identity + '.json')
        payload = raw_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != raw_hashes.get(identity):
            raise ValueError('Race inspection original OCR JSON changed.')
        raw = json.loads(payload)
        proof = _inside(directory, raw['evidence'])
        source_frame = _inside(directory, frame['evidence'])
        if raw['source_timestamp_ms'] != time or _hash(source_frame) != raw['source_frame_sha256']:
            raise ValueError('Race inspection OCR source or timestamp changed.')
        if not raw.get('engine_fingerprint') or not raw.get('model_sha256'):
            raise ValueError('Race inspection OCR provenance is missing.')
        with Image.open(source_frame) as image:
            if image.size != (1920, 1080):
                raise ValueError('Unsupported race inspection frame size.')
            pixels = image.convert('RGB').crop((148, 0, 958, 1080)).tobytes()
        with Image.open(proof) as image:
            if image.size != (810, 1080) or image.convert('RGB').tobytes() != pixels:
                raise ValueError('Race inspection gameplay proof differs from source crop.')
        if hashlib.sha256(pixels).hexdigest() != raw['gameplay_sha256']:
            raise ValueError('Race inspection OCR pixels changed.')
        row = parse(raw)
        artifact = _inside(directory, 'race-quantity-refinement/' + identity + '.json')
        if artifact.exists():
            row = apply_quantities(row, _read(artifact), raw=raw, root=directory)
        # Other OCR facts, effects and stats are outside this inspection's scope.
        facts = {key: copy.deepcopy(row['facts'].get(key)) for key in
                 ('fans', 'fans_gained', 'visible_item_quantities')}
        if row['screen'] != 'race_result':
            facts['visible_item_quantities'] = []
        rows.append(dict(screen=row['screen'], source_timestamp_ms=time,
                         evidence=proof.relative_to(root).as_posix(), facts=facts,
                         ocr=row.get('ocr', {})))
    return rows, dict(directory=entry['directory'], start_ms=start, end_ms=end,
                      capture_sha256=entry['capture_sha256'], verified_frames=len(frames))


def _window(root, entry, source):
    try:
        return _window_checked(root, entry, source)
    except (TypeError, KeyError, AttributeError, ZeroDivisionError, OverflowError) as error:
        raise ValueError('Malformed race inspection provenance.') from error


def load(root, source_sha256):
    """Revalidate source PTS, pixels and crop refinements on every load."""
    root = Path(root).resolve()
    path = root / MANIFEST
    if not path.exists():
        return None, []
    manifest = _read(path)
    source = _read(root / 'capture.json')['source']
    if not isinstance(manifest, dict) or not isinstance(manifest.get('windows'), list):
        raise ValueError('Malformed race inspection manifest.')
    if source['sha256'] != source_sha256 or manifest['source_sha256'] != source_sha256:
        raise ValueError('Race inspection recording mismatch.')
    rows, windows = [], []
    directories = set()
    for entry in manifest['windows']:
        if not isinstance(entry, dict) or not isinstance(entry.get('directory'), str):
            raise ValueError('Malformed race inspection window entry.')
        resolved = _inside(root, entry['directory'])
        if resolved in directories:
            raise ValueError('Duplicate race inspection window.')
        directories.add(resolved)
        extra, metadata = _window(root, entry, source)
        rows.extend(extra)
        windows.append(metadata)
    return dict(method='bounded_race_reward_inspection', windows=windows,
                manifest_sha256=_hash(path), verified_frames=sum(w['verified_frames'] for w in windows)), rows


def register(root, relative_directory):
    """Register a captured, OCR-processed bounded window after validating it."""
    root = Path(root).resolve()
    source = _read(root / 'capture.json')['source']
    directory = _inside(root, relative_directory)
    capture = _read(directory / 'capture.json')
    hashes = {}
    for frame in capture['frames']:
        identity = frame['id']
        if not isinstance(identity, str) or re.fullmatch(r'[A-Za-z0-9_-]+', identity) is None:
            raise ValueError('Race inspection frame identity must be a safe basename.')
        hashes[identity] = _hash(directory / 'neural' / (identity + '.json'))
    entry = dict(directory=relative_directory,
                 capture_sha256=_hash(directory / 'capture.json'), raw_sha256=hashes)
    _window(root, entry, source)
    path = root / MANIFEST
    manifest = _read(path) if path.exists() else dict(source_sha256=source['sha256'], windows=[])
    if manifest['source_sha256'] != source['sha256']:
        raise ValueError('Race inspection recording mismatch.')
    existing = [w for w in manifest['windows'] if w['directory'] == relative_directory]
    if existing:
        if existing != [entry]:
            raise ValueError('Registered race inspection changed.')
        return
    manifest['windows'].append(entry)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(path)


def inspect(source_path, root, start_ms, end_ms, fps=60, model_dir='.local/models/rapidocr'):
    """Capture a bounded source window, cache OCR, refine badges and register it."""
    from .pipeline import decode_frames
    from .vision import NeuralReader
    from .race_quantity_refinement import generate
    from .full_recording import save_json
    root = Path(root).resolve()
    source = _read(root / 'capture.json')['source']
    if (type(start_ms) is not int or type(end_ms) is not int or
            not 0 <= start_ms < end_ms <= source['duration_ms'] or end_ms - start_ms > 5000):
        raise ValueError('Race inspection must be bounded to five seconds.')
    if type(fps) is not int or not 4 <= fps <= 60:
        raise ValueError('Race inspection sampling must be 4 to 60 FPS.')
    with Path(source_path).open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != source['sha256']:
            raise ValueError('Race inspection source recording changed.')
    relative = f'race-reward-inspection/{start_ms}-{end_ms}-{fps}'
    directory = _inside(root, relative)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('frames', 'gameplay', 'neural'):
        (directory / name).mkdir(exist_ok=True)
    scope = dict(start_ms=start_ms, end_ms=end_ms)
    capture_path = directory / 'capture.json'
    if capture_path.exists():
        capture = _read(capture_path)
        if capture['source'] != source or capture['scope'] != scope:
            raise ValueError('Existing race inspection capture identity changed.')
    else:
        if any((directory / 'frames').iterdir()):
            raise ValueError('Incomplete race inspection capture has unregistered frames.')
        frames = decode_frames(source_path, directory / 'frames', start_ms / 1000,
                               (end_ms - start_ms) / 1000, fps, source.get('timeline_origin_seconds', 0))
        capture = dict(source=source, frames=frames, scope=scope,
                       sampling=dict(requested_fps=fps, method='minimum_interval_on_decoded_pts'))
        save_json(capture_path, capture)
    reader = None
    for frame in capture['frames']:
        cache = _inside(directory, 'neural/' + frame['id'] + '.json')
        if cache.exists():
            continue  # register revalidates all cached source and gameplay pixels.
        if reader is None:
            reader = NeuralReader(model_dir)
        source_frame = _inside(directory, frame['evidence'])
        proof = _inside(directory, 'gameplay/' + frame['id'] + '.png')
        with Image.open(source_frame) as image:
            pane = image.convert('RGB').crop((148, 0, 958, 1080))
        raw = reader.read(pane)
        pane.save(proof)
        raw.update(source_timestamp_ms=frame['source_timestamp_ms'],
                   evidence=proof.relative_to(directory).as_posix(),
                   source_frame_sha256=_hash(source_frame))
        save_json(cache, raw)
    generate(directory, model_dir=Path(model_dir))
    register(root, relative)
    return dict(directory=relative, frames=len(capture['frames']), registered=True)


def merge_reward_rows(base, extra):
    """Merge same-PTS quantity observations once; conflicting quantities abstain."""
    by_time = {row['source_timestamp_ms']: copy.deepcopy(row) for row in base}
    for row in sorted(extra, key=lambda r: (r['source_timestamp_ms'], r['evidence'])):
        time = row['source_timestamp_ms']
        old = by_time.get(time)
        if old is None:
            by_time[time] = copy.deepcopy(row)
            continue
        if old.get('quantity_inspection_conflict'):
            continue
        prior = old['facts'].get('visible_item_quantities') or []
        current = row['facts'].get('visible_item_quantities') or []
        merged = copy.deepcopy(prior)
        conflict = False
        for item in current:
            matches = [p for p in merged if len(p.get('box', [])) == len(item.get('box', [])) == 4 and
                       abs((p['box'][0] + p['box'][2] - item['box'][0] - item['box'][2]) / 2) <= 16 and
                       abs((p['box'][1] + p['box'][3] - item['box'][1] - item['box'][3]) / 2) <= 16]
            if len(matches) > 1 or (matches and matches[0]['quantity'] != item['quantity']):
                conflict = True
                break
            if not matches:
                merged.append(copy.deepcopy(item))
        old['facts']['visible_item_quantities'] = [] if conflict else sorted(merged, key=lambda x: (x['box'][1] // 50, x['box'][0]))
        if conflict:
            old['quantity_inspection_conflict'] = True
        old.setdefault('quantity_inspection_evidence', []).append(row['evidence'])
    return [by_time[t] for t in sorted(by_time)]


def refine_base_rows(base, extra):
    """Apply corroborating same-frame reward facts while preserving base observations."""
    indexed = {}
    for row in extra:
        indexed.setdefault(row['source_timestamp_ms'], []).append(row)
    result = []
    for original in base:
        candidates = indexed.get(original['source_timestamp_ms'], [])
        facts = original.get('facts', {})
        matching = [row for row in candidates if row['screen'] == original['screen'] == 'race_result'
                    and (row['facts'].get('fans'), row['facts'].get('fans_gained')) ==
                    (facts.get('fans'), facts.get('fans_gained'))]
        if not matching:
            result.append(original)
            continue
        row = merge_reward_rows([original], matching)[0]
        if row['facts'].get('visible_item_quantities') != facts.get('visible_item_quantities'):
            row['facts']['base_visible_item_quantities'] = copy.deepcopy(facts.get('visible_item_quantities'))
        row['quantity_inspection_evidence'] = list(dict.fromkeys(row.get('quantity_inspection_evidence', [])))
        result.append(row)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start-ms', type=int, required=True)
    parser.add_argument('--end-ms', type=int, required=True)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--model-dir', type=Path, default=Path('.local/models/rapidocr'))
    args = parser.parse_args()
    print(json.dumps(inspect(args.source, args.output, args.start_ms, args.end_ms, args.fps, args.model_dir)))
