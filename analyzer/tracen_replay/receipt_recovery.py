"""Request denser source observations for incomplete numeric receipt captions.

The planner uses visible receipt text and existing occurrences only. It never
uses a balance residual, expected award amount, character, or recording ID.
"""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import re

from .reconcile import FIELDS


_CAPTION = re.compile(
    r'^(Speed|Stamina|Power|Guts|Wit|Skill (?:Pts|Points)|Dance|Passion|Vocals?|Visuals?|Composure|Energy) '
    r'(?:went (?:up|down)|recovered) by\b', re.I)


def caption_identity(line):
    """Identify a numeric receipt's resource without reading its amount."""
    box = line.get('box', [])
    if len(box) != 4 or not 770 <= box[1] < box[3] <= 1000:
        return None
    confidence = line.get('pre_occlusion_confidence', line.get('confidence', 0))
    if confidence < 90:
        return None
    match = _CAPTION.match(' '.join(line.get('text', '').split()))
    if not match:
        return None
    name = match[1].lower()
    field = 'skill_points' if name.startswith('skill ') else {'vocals': 'vocal', 'visuals': 'visual'}.get(name, name)
    kind = 'energy_change' if field == 'energy' else 'stat_change' if field in FIELDS else 'performance_change'
    return kind, None if field == 'energy' else field


def plan(readings, events, duration_ms):
    """Find unresolved caption occurrences and merge overlapping short probes.

    A represented effect in the same event suppresses redundant recovery.
    Earlier/later events with the same resource do not. Windows look backwards
    because a scrolling receipt may be readable before an obstruction arrives.
    """
    requests = []
    for row in readings:
        if row.get('screen') != 'event_outcome':
            continue
        time = row['source_timestamp_ms']
        owners = [e for e in events if e.get('kind') == 'outcome' and e['first_seen_ms'] <= time <= e['last_seen_ms']]
        for line in row.get('ocr', {}).get('neural', []):
            identity = caption_identity(line)
            if identity is None:
                continue
            if len(owners) == 1:
                event = owners[0]
                key = '|'.join((identity[0], identity[1] or '', ''))
                conflicts = event.get('conflicting_readings', [])
                disputed = any(c.get('field') == key for c in conflicts if isinstance(c, dict))
                if not disputed and any((e['kind'], e.get('field')) == identity and type(e.get('amount')) is int
                                        for e in event.get('effects', [])):
                    continue
            requests.append(dict(start_ms=max(0, time-750), end_ms=min(duration_ms, time+250),
                                 trigger=dict(source_timestamp_ms=time, evidence=row['evidence'],
                                              kind=identity[0], field=identity[1], raw_text=line['text'],
                                              owner_ref=owners[0].get('id') if len(owners) == 1 else None,
                                              owner_start_ms=owners[0]['first_seen_ms'] if len(owners) == 1 else None,
                                              owner_end_ms=owners[0]['last_seen_ms'] if len(owners) == 1 else None)))
    windows = []
    for request in sorted(requests, key=lambda r: (r['start_ms'], r['end_ms'])):
        if (windows and request['start_ms'] <= windows[-1]['end_ms']
                and request['end_ms']-windows[-1]['start_ms'] <= 5000):
            windows[-1]['end_ms'] = max(windows[-1]['end_ms'], request['end_ms'])
            windows[-1]['triggers'].append(request['trigger'])
        else:
            windows.append(dict(start_ms=request['start_ms'], end_ms=request['end_ms'],
                                reason='unresolved_visible_numeric_receipt', triggers=[request['trigger']]))
    return windows


def scoped_observations(base, fresh, windows):
    """Promote only the numeric fields requested by this inspection pass.

    Full OCR remains in the immutable cache for later review. A targeted
    numeric probe must not silently replace unrelated names, inventories,
    state readings or action facts with newly sampled animation fragments.
    """
    originals = {r['source_timestamp_ms']: r for r in base}
    result = []
    for row in fresh:
        time = row['source_timestamp_ms']
        triggers = [t for w in windows if w['start_ms'] <= time <= w['end_ms'] for t in w['triggers']
                    if t.get('owner_ref') and t['owner_start_ms'] <= time <= t['owner_end_ms']]
        owners = {t['owner_ref'] for t in triggers}
        if len(owners) != 1:
            continue
        wanted = {(t['kind'], t['field']) for t in triggers}
        effects = [e for e in row.get('effects', []) if (e['kind'], e.get('field')) in wanted]
        if not effects or row.get('screen') != 'event_outcome':
            continue
        old = originals.get(time)
        facts = deepcopy(old.get('facts', {})) if old else {}
        facts['numeric_receipt_recovery'] = dict(fields=[dict(kind=k, field=f) for k, f in sorted(wanted)],
                                                effects=deepcopy(effects),
                                                evidence=row['evidence'], source_timestamp_ms=time,
                                                requested_owner_ref=next(iter(owners)),
                                                trigger_evidence=list(dict.fromkeys(t['evidence'] for t in triggers)))
        for key in ('effect_candidates', 'animated_stat_candidates', 'animated_performance_candidates'):
            items = [e for e in row.get('facts', {}).get(key, []) if (e['kind'], e.get('field')) in wanted]
            if items:
                existing = facts.setdefault(key, [])
                existing.extend(deepcopy(e) for e in items if e not in existing)
        promoted = dict(old if old else row, effects=effects, facts=facts,
                        stats=deepcopy(old.get('stats', {})) if old else {})
        # At an existing timestamp the original title, OCR and primary proof
        # remain authoritative. The numeric alternate has its own proof above.
        result.append(promoted)
    return result


def recover(source, root, source_info, readings, events, *, allow_ocr=True,
            max_windows=20, max_duration_ms=30000, fps=30, model_dir='.local/models/rapidocr', dense_workers=1):
    """Run one bounded pass, preserving the plan and all unresolved requests.

    Reparse mode consumes already captured observations and never starts OCR.
    New samples use the same source hash and pixel/occlusion checks as the
    main pipeline. Neither a completed probe nor a readable caption implies
    that the corresponding effect was recovered.
    """
    from .full_recording import save_json
    from .inspect_receipts import inspect, merge
    from .inspect_training import reparse_inspection
    from .vision import NeuralReader
    root = Path(root)
    directory = root / 'numeric-receipt-recovery'
    requested = plan(readings, events, source_info['duration_ms'])
    if not requested and not (directory / 'receipt-inspection.json').exists():
        return readings, dict(requested_windows=[], processed_windows=[], pending_windows=[], new_frames=0)
    directory.mkdir(parents=True, exist_ok=True)
    capture_path = directory / 'capture.json'
    capture = dict(source=source_info)
    if capture_path.exists():
        if json.loads(capture_path.read_text(encoding='utf-8')) != capture:
            raise ValueError('Receipt recovery source changed')
    else:
        save_json(capture_path, capture)
    manifest = directory / 'receipt-inspection.json'
    existing = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else None
    if existing and existing['source_sha256'] != source_info['sha256']:
        raise ValueError('Receipt recovery observations belong to another recording')
    old_count = len(existing['readings']) if existing else 0
    completed = {(w['start_ms'], w['end_ms'], w['fps']) for w in existing['windows']} if existing else set()
    processed, pending = [], []
    used_ms = 0
    reader = None
    new_windows = 0
    steps = []
    for window in requested:
        start, end = window['start_ms'], window['end_ms']
        if (start, end, fps) not in completed:
            if not allow_ocr or new_windows >= max_windows or used_ms+end-start > max_duration_ms:
                pending.append(window)
                continue
            steps.append((window, 'new'))
            used_ms += end-start
            new_windows += 1
        elif allow_ocr:
            # Check the actual cached frames/model even when the window was
            # previously completed. Reparse-only validates source proof below.
            steps.append((window, 'revalidate'))
        else:
            steps.append((window, None))
    if allow_ocr:
        from .dense_inspection_pool import prepare_windows
        prepare_windows(source, directory, [w for w, kind in steps if kind == 'new'], fps,
                        kind='base', model_dir=model_dir, workers=dense_workers)
    for window, kind in steps:
        if kind is not None:
            if reader is None:reader = NeuralReader(model_dir)
            inspect(source, directory, window['start_ms'], window['end_ms'], fps, reader=reader)
        processed.append(window)
    if manifest.exists():
        inspection = json.loads(manifest.read_text(encoding='utf-8'))
        models = {}
        for observed in inspection['readings']:
            raw_path = (directory/observed['evidence']).with_suffix('.v2.json')
            raw = json.loads(raw_path.read_text(encoding='utf-8'))
            if not raw.get('engine_fingerprint') or not raw.get('model_sha256'):
                raise ValueError('Receipt recovery cache lacks OCR provenance')
            fingerprint = raw['engine_fingerprint']
            if fingerprint in models and models[fingerprint] != raw['model_sha256']:
                raise ValueError('One receipt OCR fingerprint has different models')
            models[fingerprint] = raw['model_sha256']
        fresh = reparse_inspection(inspection, directory)
        # All image paths inside the new observations refer to this cache.
        # Preserve source pointers and timestamps; only relocate actual files.
        def relocate(value):
            if isinstance(value, dict):
                return {k: relocate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relocate(v) for v in value]
            if isinstance(value, str) and value.endswith(('.png', '.jpg', '.json')) and (directory/value).is_file():
                return (directory/value).relative_to(root).as_posix()
            return value
        relocated = relocate(fresh)
        promoted = scoped_observations(readings, relocated, requested)
        promoted_times = {r['source_timestamp_ms'] for r in promoted}
        unassigned = [dict(source_timestamp_ms=r['source_timestamp_ms'], evidence=r['evidence'],
                           reason='outside_requested_numeric_scope_or_ambiguous_occurrence', effects=r['effects'])
                      for r in relocated if r['effects'] and r['source_timestamp_ms'] not in promoted_times]
        readings = merge(readings, promoted)
        new_count = len(inspection['readings'])-old_count
        manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    else:
        new_count, manifest_hash, unassigned, models = 0, None, [], {}
    metadata = dict(method='bounded_unresolved_numeric_receipt_recovery', source_sha256=source_info['sha256'],
                    requested_windows=requested, processed_windows=processed, pending_windows=pending,
                    requested_fps=fps, model_dir=str(model_dir), new_frames=new_count, manifest_sha256=manifest_hash,
                    unpromoted_observations=unassigned,
                    ocr_engine_fingerprint=reader.fingerprint if reader else None,
                    observed_ocr_models=models,
                    complete_event_history=False)
    save_json(directory/'last-plan.json', metadata)
    return readings, metadata
