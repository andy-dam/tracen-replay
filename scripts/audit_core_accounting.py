"""Audit core-accounting changes against preserved reports and source frames."""
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracen_replay.causal_accounting import build
from tracen_replay.report_contract import validate
from compare_hardening_reports import compare


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def effects(report):
    return Counter(json.dumps(dict(event_id=e['id'], event_kind=e['kind'],
                   effect={k:a[k] for k in ('kind','field','name','amount','direction','value') if k in a}), sort_keys=True)
                   for e in report['gameplay_tracking']['events'] for a in e.get('effects', []))


def verify_recovery(root, source, expected_sha):
    if digest(source) != expected_sha:
        raise ValueError('Source recording changed')
    origin = root/'numeric-receipt-recovery'
    inspection = read(origin/'receipt-inspection.json')
    if inspection['source_sha256'] != expected_sha:
        raise ValueError('Recovery manifest source mismatch')
    checked = 0
    hashes = {}
    for row in inspection['readings']:
        proof = origin/row['evidence']; raw_path = proof.with_suffix('.v2.json')
        raw = read(raw_path)
        frames = read(proof.parent/'frames.json')
        frame = next(f for f in frames if f['id'] == proof.stem)
        original = proof.parent/frame['evidence']
        time = frame['source_timestamp_ms']
        epoch = read(origin/'capture.json')['source'].get('timeline_origin_seconds', 0)
        pts = round((float(frame['source_pts']*Fraction(frame['time_base']))-epoch)*1000)
        if (raw['source_sha256'] != expected_sha or row['source_timestamp_ms'] != time
                or raw['source_timestamp_ms'] != time or abs(pts-time) > 1):
            raise ValueError('Recovery source timestamp mismatch')
        if digest(original) != raw['source_frame_sha256']:
            raise ValueError('Recovery source frame changed')
        with Image.open(original) as image:
            pixels = image.convert('RGB').crop((148,0,958,1080)).tobytes()
        with Image.open(proof) as image:
            if image.size != (810,1080) or image.convert('RGB').tobytes() != pixels:
                raise ValueError('Recovery proof differs from source crop')
        if hashlib.sha256(pixels).hexdigest() != raw['gameplay_sha256'] or not raw['model_sha256'] or not raw['engine_fingerprint']:
            raise ValueError('Recovery OCR provenance mismatch')
        for path in (proof, raw_path, original, proof.parent/'frames.json'):
            hashes[str(path)] = digest(path)
        checked += 1
    hashes[str(origin/'receipt-inspection.json')] = digest(origin/'receipt-inspection.json')
    return dict(verified_frames=checked, files_sha256=hashes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--final', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    root = repo/'.local/core-accounting-v1'
    manifest = read(root/'baseline-manifest.json')
    starting_files = {Path(name).as_posix(): value for name, value in manifest['worktree_sha256'].items()}
    for name, expected in manifest['reports_sha256'].items():
        if digest(root/'before'/name) != expected or digest(repo/'.local/evaluation-hardening-v1/final'/name) != expected:
            raise ValueError('Preserved baseline report changed')
    for name in ('go.mod', 'tests/test_receipt_keyword_normalization.py'):
        if digest(repo/name) != starting_files[name]:
            raise ValueError('Unrelated starting work changed')
    prior_audit = read(repo/'.local/evaluation-hardening-v1/final/integrity-audit.json')
    prior_artifacts = {Path(name).as_posix(): value for name, value in prior_audit['artifacts_sha256'].items()}
    checked_references = set()
    results = []
    for run in ('v1', 'independent-01', 'independent-02'):
        before_path, after_path = root/'before'/f'{run}-report.json', args.final/f'{run}-report.json'
        before, after = read(before_path), read(after_path)
        validate(after, require_gameplay=True)
        if after['causal_accounting'] != build(after):
            raise ValueError('Final accounting cannot be reproduced')
        if 'evidence_integrity_snapshot' in after:
            raise ValueError('Earlier integrity snapshot was incorrectly carried forward')
        replay_audit = read(after_path.with_suffix('.audit.json'))
        if replay_audit['output_sha256'] != digest(after_path):
            raise ValueError('Replay report hash changed')
        for name, value in replay_audit['implementation_sha256'].items():
            if digest(repo/'tracen_replay'/name) != value:
                raise ValueError('Analyzer changed after final replay')
        corpus_path = repo/f'.local/evaluation-hardening-v1/{run}-corpus.json'
        references = read(corpus_path)['references']
        for reference in references:
            for key in ('reference', 'amendments'):
                if not reference.get(key):
                    continue
                name = Path(reference[key]).as_posix()
                if name not in prior_artifacts or digest(repo/name) != prior_artifacts[name]:
                    raise ValueError('Historical source labels or amendments changed')
                checked_references.add(name)
        comparison = compare(before, after, references)
        comparison_path = args.final/f'{run}-comparison.json'
        if comparison_path.exists():
            if read(comparison_path) != comparison:
                raise ValueError('Saved comparison cannot be reproduced')
        else:
            with comparison_path.open('x', encoding='utf-8') as stream:
                json.dump(comparison, stream, ensure_ascii=False)
        removed, added = effects(before)-effects(after), effects(after)-effects(before)
        if removed:
            raise ValueError(f'Previously accepted canonical effects removed: {run}')
        added_values = [json.loads(x) for x in added.elements()]
        expected = [dict(event_id='outcome-0156', event_kind='outcome',
                         effect=dict(kind='performance_change', field='passion', amount=10))] if run == 'v1' else []
        if added_values != expected:
            raise ValueError(f'Unexpected added effects: {run}: {added_values}')
        if not comparison['selected_actions_unchanged'] or not comparison['stat_interval_totals_unchanged']:
            raise ValueError('Action or stat accounting regression')
        for name in ('checkpoints','lesson_purchases','skill_purchases','races','dialogue_choices'):
            if not comparison['unchanged_collections'][name]:
                raise ValueError(f'Unexpected collection change: {run}: {name}')
        numeric = lambda r: [{k:e.get(k) for k in ('id','kind','deltas','performance_deltas')} for e in r['gameplay_tracking']['events']]
        if numeric(before) != numeric(after):
            raise ValueError('Numeric event summaries changed')
        if [(t['start_ms'],t['end_ms']) for t in before['turn_ledger']['turns']] != [(t['start_ms'],t['end_ms']) for t in after['turn_ledger']['turns']]:
            raise ValueError('Calendar windows changed')
        for grade in comparison['shared_reference_grades']:
            for change in grade['changes']:
                if change['before']['status'] == 'correct' and change['after']['status'] != 'correct':
                    raise ValueError('Previously correct source reference regressed')
        if after['causal_accounting']['issues']:
            raise ValueError('Structural accounting issues remain in final output')
        config_path = repo/f'.local/evaluation-hardening-v1/{run}-replay-config{ "-v3" if run == "independent-02" else ""}.json'
        config = read(config_path)
        evidence = verify_recovery(Path(config['cache_root']), Path(config['source_video']), before['source']['sha256'])
        field_proofs = 0
        for row in after['gameplay_tracking']['readings']:
            recovery = row.get('facts', {}).get('numeric_receipt_recovery')
            if not recovery:
                continue
            identity = lambda e: tuple(e.get(k) for k in ('kind','field','name','amount','direction','value'))
            for event in after['gameplay_tracking']['events']:
                if not event['first_seen_ms'] <= row['source_timestamp_ms'] <= event['last_seen_ms']:
                    continue
                for effect in recovery['effects']:
                    if not any(identity(effect) == identity(e) for e in event.get('effects', [])):
                        continue
                    key = '|'.join(str(effect.get(k) or '') for k in ('kind','field','name'))
                    if recovery['evidence'] not in event.get('field_evidence', {}).get(key, []):
                        raise ValueError('Accepted recovery lacks canonical alternate field proof')
                    if not (Path(config['cache_root'])/recovery['evidence']).is_file():
                        raise ValueError('Canonical recovery field proof does not resolve')
                    field_proofs += 1
        results.append(dict(recording=run, before_sha256=digest(before_path), after_sha256=digest(after_path),
            before_accounting=before['causal_accounting']['summary'], after_accounting=after['causal_accounting']['summary'],
            canonical_effects_before=sum(effects(before).values()), canonical_effects_after=sum(effects(after).values()),
            added_effects=added_values, removed_effects=[], source_references=len(references), source_reference_regressions=0,
            evidence=evidence, recovery_field_proofs_verified=field_proofs))
        print(json.dumps(dict(recording=run, verified_recovery_frames=evidence['verified_frames'], added_effects=len(added_values))), flush=True)
    fixture = read(repo/'tests/fixtures/core-accounting-source-cases.json')
    for name, expected in fixture['image_sha256'].items():
        if digest(repo/'.local/full-recording/v1'/name) != expected:
            raise ValueError('Reviewed source fixture image changed')
    output = dict(status='verified', results=results, preserved_reports=3, source_fixture_images_verified=len(fixture['image_sha256']),
                  unchanged_source_and_amendment_files=len(checked_references),
                  independent_validation='Outstanding: three development recordings; no additional full run available.',
                  scope='Numeric balance and source-linked recognition changes are separate. This is not exhaustive event-history verification.')
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(output, stream, indent=2)


if __name__ == '__main__':
    main()
