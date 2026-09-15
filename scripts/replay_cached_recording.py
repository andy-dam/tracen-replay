"""Replay a validated source-input manifest into a fresh report without OCR.

The manifest mode is the common producer path: it reads capture, raw OCR,
inspection, recovery and source-sidecar inputs from one disposable cache root
and assembles a fresh report. An accepted report may be retained only as a
hash reference in the manifest; its values are never loaded as producer input.
The older configuration mode remains below for historical comparisons.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _implementation_hashes(root):
    """Hash the replay modules that define the producer semantics."""
    root=Path(root).resolve()
    package=root/'tracen_replay'
    if not package.is_dir():
        raise ValueError(f'Implementation root has no tracen_replay package: {root}')
    return {
        path.relative_to(root).as_posix(): digest(path)
        for path in sorted(package.glob('*.py'))
    }


def _replay_manifest(manifest_path, input_root, source_video, output, *, fps=4,
                     implementation_root=None):
    """Run the common source-bound loader and write a fresh cached replay."""
    started_at_ms=round(time.time()*1000)
    started=time.monotonic()

    def progress(phase, **details):
        """Emit bounded phase progress without exposing report/effect values."""
        payload = {
            'stage': 'manifest-replay-progress',
            'phase': phase,
            'elapsed_seconds': round(time.monotonic() - started, 3),
        }
        payload.update(details)
        print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)

    implementation=Path(implementation_root or Path(__file__).resolve().parents[1]).resolve()
    implementation_sha256=_implementation_hashes(implementation)
    if str(implementation) not in sys.path:
        sys.path.insert(0,str(implementation))
    from tracen_replay.full_recording import (
        assemble,
        build_event_choice_observations,
        load_replay_input_bundle,
    )
    from tracen_replay.automatic_refinement import run as run_automatic_refinement
    from tracen_replay.inspect_choices import load as load_choices
    from tracen_replay.race_reward_inspection import load as load_races
    from tracen_replay.hint_card_cache import load as load_hints
    from tracen_replay.report_contract import validate

    source_video=Path(source_video).resolve()
    input_root=Path(input_root).resolve()
    manifest_path=Path(manifest_path).resolve()
    source_sha256=digest(source_video)
    progress('load_bundle_start')
    bundle=load_replay_input_bundle(
        manifest_path,input_root,expected_source_sha256=source_sha256,fps=fps,
    )
    report=bundle['report']
    rows=bundle['readings']
    progress(
        'load_bundle_complete',
        base_frames=len(report.get('frames', [])),
        readings=len(rows),
        inspections=len(bundle.get('inspections', [])),
        recoveries=len(bundle.get('recoveries', [])),
        supplements=len(bundle.get('supplements', [])),
    )
    choice_metadata,choice_observations=load_choices(input_root,source_sha256)
    progress('choices_loaded', observations=len(choice_observations), registered=bool(choice_metadata))
    if choice_metadata:
        report['choice_inspection']=choice_metadata
    reward_metadata,reward_observations=load_races(input_root,source_sha256)
    progress('race_windows_loaded', observations=len(reward_observations), registered=bool(reward_metadata))
    if reward_metadata:
        report['race_reward_inspection']=reward_metadata
    hint_observations=load_hints(rows,input_root,source_sha256)
    progress('hints_loaded', observations=len(hint_observations))
    # The capture/report envelope is rebased to the disposable manifest root,
    # while immutable neural observations remain under the declared base
    # namespace.  Keep the automatic audit bound to that base namespace so a
    # nested layout is not silently skipped or prefixed twice.  Choice proof
    # paths, by contrast, are root-relative after bundle loading and must use
    # the disposable root.
    automatic_refinement=run_automatic_refinement(
        report,bundle['base_root'],allow_ocr=False,
    )
    report['automatic_refinement']=automatic_refinement
    choice_source=build_event_choice_observations(rows,input_root,choice_observations)
    progress(
        'choice_source_built',
        observations=len(choice_source.get('observations', [])),
        committed_choices=len(choice_source.get('committed_choices', [])),
    )
    candidate=assemble(
        report,rows,choice_observations,reward_observations,hint_observations,
        source_root=input_root,
        committed_choices=choice_source['committed_choices'],
        event_choice_observations=choice_source,
    )
    progress('assembled', rows=len(candidate.get('frames', [])) if isinstance(candidate, dict) else None)
    validate(candidate,require_gameplay=True,source_root=input_root)
    progress('validated')
    if _implementation_hashes(implementation) != implementation_sha256:
        raise ValueError('Implementation changed during manifest replay')
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)

    def save(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')

    save('base-readings.json',rows)
    save('merged-readings.json',rows)
    candidate_path=output/'candidate-report.json'
    save('candidate-report.json',candidate)
    audit={
        'schema_version':'tracen-replay/cached-replay-v2',
        'source_sha256':source_sha256,
        'source_video_sha256_verified':True,
        'manifest_path':str(manifest_path),
        'manifest_sha256':digest(manifest_path),
        'input_root':str(input_root),
        'candidate_sha256':digest(candidate_path),
        'base_frames':len(bundle['report']['frames']),
        'merged_readings':len(rows),
        'inspections':bundle['inspections'],
        'recoveries':bundle['recoveries'],
        'supplements':bundle['supplements'],
        'comparison_reference':bundle['manifest'].get('reference'),
        'automatic_refinement':automatic_refinement,
        'choice_source':choice_source,
        'accepted_report_values_loaded':False,
        'ocr_executed':False,
        'worker_executed':False,
        'implementation_root':str(implementation),
        'implementation_sha256':implementation_sha256,
        'implementation_unchanged':True,
        'started_at_ms':started_at_ms,
        'finished_at_ms':round(time.time()*1000),
        'elapsed_seconds':round(time.monotonic()-started,3),
    }
    save('audit.json',audit)
    print(json.dumps(dict(stage='complete',output=str(output),candidate=str(candidate_path),
                          merged_readings=len(rows),source_sha256=source_sha256),sort_keys=True),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path, nargs='?')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--replay-input-manifest', '--manifest', dest='replay_input_manifest', type=Path,
                        help='Validated source-bound replay-input-manifest-v1 file.')
    parser.add_argument('--input-root', type=Path,
                        help='Disposable cache root validated by the replay input manifest.')
    parser.add_argument('--source-video', type=Path,
                        help='Source video used to verify the manifest source hash.')
    parser.add_argument('--fps', type=float, default=4,
                        help='Base sampling rate recorded by the manifest (default: 4).')
    parser.add_argument('--implementation-root', type=Path)
    parser.add_argument('--reuse-base', type=Path, help='Reuse explicitly identified, hash-bound base output from a preceding replay')
    parser.add_argument('--refresh-receipt-boundaries', action='store_true', help='Reparse every base row eligible for the revised fixed-separator rule')
    args = parser.parse_args()
    manifest_path=args.replay_input_manifest
    if manifest_path is None and args.config is not None:
        try:
            candidate=read(args.config)
        except (OSError,UnicodeDecodeError,json.JSONDecodeError):
            candidate=None
        if isinstance(candidate,dict) and candidate.get('schema_version')=='tracen-replay/replay-input-manifest-v1':
            manifest_path=args.config
    if manifest_path is not None:
        if args.input_root is None or args.source_video is None:
            parser.error('--replay-input-manifest requires --input-root and --source-video')
        if not 1<=args.fps<=8:
            parser.error('--fps must be between 1 and 8')
        _replay_manifest(manifest_path,args.input_root,args.source_video,args.output,
                         fps=args.fps,implementation_root=args.implementation_root)
        return
    if args.config is None:
        parser.error('config is required unless --replay-input-manifest is supplied')
    implementation = (args.implementation_root or Path(__file__).resolve().parents[1]).resolve()
    sys.path.insert(0, str(implementation))
    from tracen_replay.full_recording import assemble, cached_readings
    from tracen_replay.inspect_training import reparse_inspection, merge as merge_training
    from tracen_replay.inspect_receipts import merge as merge_receipts
    from tracen_replay.inspect_choices import load as load_choices
    from tracen_replay.race_reward_inspection import load as load_races
    from tracen_replay.hint_card_cache import load as load_hints
    from tracen_replay.report_contract import validate
    config = read(args.config)
    root = Path(config['cache_root']).resolve()
    base = root / config.get('base_relative', '')
    accepted_path = Path(config['accepted_report']).resolve()
    accepted = read(accepted_path)
    baseline = read(base / 'report.json')
    if baseline['source']['sha256'] != accepted['source']['sha256']:
        raise ValueError('Accepted report and source cache are different recordings')
    if digest(config['source_video']) != accepted['source']['sha256']:
        raise ValueError('Recording changed')
    code = {p.name: digest(p) for p in (implementation / 'tracen_replay').glob('*.py')}
    protected = {str(accepted_path): digest(accepted_path), str(base/'report.json'): digest(base/'report.json')}
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()

    def save(name, value):
        with (args.output/name).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)

    def progress(stage, count):
        print(json.dumps(dict(stage=stage, count=count, elapsed_seconds=round(time.monotonic()-started, 1))), flush=True)

    def relocate(value, origin):
        if isinstance(value, dict):
            return {k: relocate(v, origin) for k,v in value.items()}
        if isinstance(value, list):
            return [relocate(v, origin) for v in value]
        if isinstance(value, str) and value.endswith(('.png','.jpg','.json')):
            path = Path(value)
            if not path.is_absolute() and (origin/path).is_file():
                return (origin/path).relative_to(root).as_posix()
        return value

    with patch('tracen_replay.vision.NeuralReader', side_effect=AssertionError('OCR is prohibited during cache replay')):
        if args.reuse_base:
            protected[str(args.reuse_base.resolve())] = digest(args.reuse_base)
            rows = read(args.reuse_base)
            if [r['source_timestamp_ms'] for r in rows] != [f['source_timestamp_ms'] for f in baseline['frames']]:
                raise ValueError('Reused base output does not cover the source frame manifest')
            progress('reused_base',len(rows))
            if args.refresh_receipt_boundaries:
                from tracen_replay.receipt_grammar import friendship_receipt
                refresh = []
                for row in rows:
                    if any((receipt:=friendship_receipt(line.get('text',''))) and receipt['boundary_repaired']
                           for line in row.get('ocr',{}).get('neural',[])):
                        refresh.append(row['source_timestamp_ms'])
                frames = [f for f in baseline['frames'] if f['source_timestamp_ms'] in set(refresh)]
                fresh = relocate(cached_readings(dict(source=baseline['source'],frames=frames),base),base)
                by_time = {r['source_timestamp_ms']:r for r in fresh}
                rows = [by_time.get(r['source_timestamp_ms'],r) for r in rows]
                progress('refreshed_receipt_boundaries',len(fresh))
        else:
            rows = []
            for offset in range(0, len(baseline['frames']), 100):
                rows.extend(cached_readings(dict(source=baseline['source'],frames=baseline['frames'][offset:offset+100]), base))
                progress('base', len(rows))
            rows = relocate(rows, base)
        save('base-readings.json', rows)
        supplement_audit = []
        for supplement in config.get('supplements', []):
            if supplement['kind'] != 'song_symbols':
                continue
            from tracen_replay.song_symbols import apply as apply_song_symbols
            from tracen_replay.full_recording import parse_receipt_pixels
            frame_by_time = {f['source_timestamp_ms']:f for f in baseline['frames']}
            for index, row in enumerate(rows):
                sidecar = root/supplement['folder']/(Path(row['evidence']).stem+'.json')
                if not sidecar.exists(): continue
                raw_path = base/'neural'/sidecar.name
                raw = read(raw_path)
                parsed = parse_receipt_pixels(apply_song_symbols(raw,read(sidecar),root/row['evidence']),
                    base,frame_by_time[row['source_timestamp_ms']],raw,source_sha256=accepted['source']['sha256'])
                if parsed['screen'] != row['screen'] or parsed['stats'] != row['stats']:
                    raise ValueError('Song supplement changed unrelated screen/state observations')
                rows[index] = dict(row,effects=parsed['effects'])
                protected[str(sidecar)] = digest(sidecar)
                supplement_audit.append(dict(kind=supplement['kind'],path=str(sidecar),sha256=protected[str(sidecar)]))
        for supplement in config.get('supplements', []):
            if supplement['kind'] != 'race_identity_accepted': continue
            from tracen_replay.race_identity_refinement import apply as apply_race_identity
            from tracen_replay.vision import parse
            accepted_rows = {r['source_timestamp_ms']:r for r in accepted['gameplay_tracking']['readings']}
            for row in rows:
                extra = accepted_rows.get(row['source_timestamp_ms'],{}).get('facts',{}).get('race_identity_refinement')
                if not extra: continue
                raw = read(base/'neural'/(Path(row['evidence']).stem+'.json'))
                refined = apply_race_identity(raw,extra,base)
                parsed = parse(refined)
                if parsed['screen'] != row['screen']: raise ValueError('Race identity supplement changed screen class')
                row['facts'][extra['field']] = parsed['facts'][extra['field']]
                row['facts']['race_identity_refinement'] = parsed['facts']['race_identity_refinement']
                row['ocr']['neural'] = parsed['ocr']['neural']
                supplement_audit.append(dict(kind=supplement['kind'],source_timestamp_ms=row['source_timestamp_ms'],
                    accepted_report_sha256=protected[str(accepted_path)],capture_sha256=extra['capture_sha256']))
        inspections = []
        for item in config.get('inspections', []):
            origin = root / item.get('folder','')
            path = origin / item['manifest']
            inspection = read(path)
            protected[str(path)] = digest(path)
            if inspection['source_sha256'] != accepted['source']['sha256']:
                raise ValueError('Inspection belongs to a different recording')
            fresh = []
            for offset in range(0, len(inspection['readings']), 100):
                # Merged manifests can contain observations whose original OCR
                # uses paths relative to an earlier inspection subdirectory.
                # Resolve the exact suffix; never search by image basename.
                groups = {}
                for row in inspection['readings'][offset:offset+100]:
                    evidence = (origin/row['evidence']).resolve()
                    evidence.relative_to(root)
                    raw_path = evidence.with_suffix('.v2.json')
                    if not raw_path.exists(): raw_path=evidence.with_suffix('.json')
                    raw = read(raw_path)
                    relative = Path(raw['evidence'])
                    if relative.is_absolute() or '..' in relative.parts:
                        raise ValueError('Unsafe original inspection evidence path')
                    own_root = evidence
                    for _ in relative.parts: own_root=own_root.parent
                    if (own_root/relative).resolve() != evidence:
                        raise ValueError('Original and merged inspection paths disagree')
                    own_root.relative_to(root)
                    groups.setdefault(own_root,[]).append(dict(row,evidence=relative.as_posix()))
                for own_root, own_rows in groups.items():
                    fresh.extend(relocate(reparse_inspection(dict(inspection,readings=own_rows),own_root),own_root))
                progress(item['manifest'],len(fresh))
            merger = {'training':merge_training,'receipt':merge_receipts}[item['merge']]
            rows = merger(rows,fresh)
            inspections.append(dict(path=str(path),sha256=protected[str(path)],readings=len(fresh)))
        for supplement in config.get('supplements', []):
            if supplement['kind'] != 'currency_regions':
                if supplement['kind'] not in ('song_symbols','race_identity_accepted'): raise ValueError('Unknown supplement kind')
                continue
            from tracen_replay.vision import parse
            from tracen_replay.refine_contrast import fingerprint
            for row in rows:
                sidecar = root/supplement['folder']/(Path(row['evidence']).stem+'.json')
                if not sidecar.exists(): continue
                extra = read(sidecar)
                raw = read(base/'neural'/sidecar.name)
                if (extra['raw_sha256'] != fingerprint(raw) or extra['evidence_sha256'] != digest(root/row['evidence'])
                    or extra['source_timestamp_ms'] != row['source_timestamp_ms'] or not extra.get('model_sha256')):
                    raise ValueError('Currency supplement source evidence changed')
                parsed = parse(dict(raw,regions=dict(raw['regions'],**extra['regions'])))
                if parsed['screen'] != row['screen']: raise ValueError('Currency supplement changed screen identity')
                key = {'lesson_selection':'performance_points','lesson_confirmation':'projected_performance_points'}[row['screen']]
                if any(v is not None and parsed['facts'][key][k] != v for k,v in row['facts'][key].items()):
                    raise ValueError('Currency supplement contradicts an accepted observation')
                row['facts'][key] = parsed['facts'][key]
                row['facts']['currency_refinement_evidence'] = dict(path=sidecar.relative_to(root).as_posix(),
                    sha256=digest(sidecar),raw_sha256=extra['raw_sha256'],evidence_sha256=extra['evidence_sha256'],independent_frame_count=1)
                protected[str(sidecar)] = digest(sidecar)
                supplement_audit.append(dict(kind=supplement['kind'],path=str(sidecar),sha256=protected[str(sidecar)]))
        save('merged-readings.json',rows)
        _, choices = load_choices(root,accepted['source']['sha256'])
        _, races = load_races(root,accepted['source']['sha256'])
        hints = load_hints(rows,root,accepted['source']['sha256'])
        candidate = assemble(copy.deepcopy(accepted),rows,choices,races,hints,
                             source_root=root)
        validate(candidate,require_gameplay=True,source_root=root)
        if code != {p.name:digest(p) for p in (implementation/'tracen_replay').glob('*.py')}:
            raise ValueError('Implementation changed during replay')
        if any(digest(path) != sha for path,sha in protected.items()):
            raise ValueError('Protected input changed during replay')
        save('candidate-report.json',candidate)
        save('audit.json',dict(source_sha256=accepted['source']['sha256'],source_video_sha256_verified=True,
            input_sha256=protected,implementation_sha256=code,config_sha256=digest(args.config),
            candidate_sha256=digest(args.output/'candidate-report.json'),inspections=inspections,supplements=supplement_audit,
            base_frames=len(baseline['frames']),merged_readings=len(rows),
            base_reused_from=str(args.reuse_base) if args.reuse_base else None,
            receipt_boundary_refresh=args.refresh_receipt_boundaries,
            elapsed_seconds=round(time.monotonic()-started,1),
            scope='Complete configured OCR cache and inspection replay; no new OCR and no held-out validation claim.'))
        progress('complete',len(rows))


if __name__ == '__main__':
    main()
