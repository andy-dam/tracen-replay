"""Inspect gaps between a training preview and an empty result fragment.

A recovery window is a request for more evidence, not a completed action.
Only subsequently observed result screens can supply an option or award.
"""
import argparse
import hashlib
import json
import inspect as inspection
from importlib.metadata import version
import re
from pathlib import Path
from .pipeline import PipelineError, clear_partial_capture, decode_frames
from .full_recording import save_json


def windows(report):
    data=report['gameplay_tracking'];duration=report['source']['duration_ms']
    rows=data['readings'];events=data['events'];result=[]
    if type(duration) is not int or duration<=0:raise ValueError('Invalid source duration.')
    times=[r['source_timestamp_ms'] for r in rows]
    if (any(type(t) is not int or not 0<=t<duration for t in times)
        or any(a>=b for a,b in zip(times,times[1:]))):raise ValueError('Source readings must have distinct ordered timestamps.')
    for event in events:
        first,last=event['first_seen_ms'],event['last_seen_ms']
        if type(first) is not int or type(last) is not int or not 0<=first<=last<duration:
            raise ValueError('Event timestamps must lie within the source.')
    gaps=[g for g in data['intervals'] if g['status']=='unresolved']
    allowed={'training_preview','training_result','training_result_candidate','unknown','event_outcome'}
    for event in sorted(events,key=lambda e:e['first_seen_ms']):
        first,last=event['first_seen_ms'],event['last_seen_ms']
        if (event['kind']!='training' or event.get('training_option')
            or event.get('deltas') or event.get('performance_deltas')):continue
        if not any(g['start_ms']<first<=g['end_ms'] for g in gaps):continue
        previews=[r for r in rows if r['screen']=='training_preview' and 0<first-r['source_timestamp_ms']<=6000]
        if not previews:continue
        preview=previews[-1];before=preview['source_timestamp_ms']
        between=[r for r in rows if before<r['source_timestamp_ms']<=last]
        if any(r['screen'] not in allowed for r in between):continue
        # A second empty fragment after an already recognized result does not
        # create another training or another recovery of the same action.
        if any(e['kind']=='training' and e.get('training_option')
               and before<=e['first_seen_ms']<=first for e in events):continue
        start=max(0,before-500);end=min(duration,last+500)
        if not 0<end-start<=8000:continue
        witness=dict(preview_timestamp_ms=before,preview_evidence=preview['evidence'],
                     empty_result_event_id=event['id'],empty_result_timestamp_ms=first)
        if (result and start<=result[-1]['end_ms']
            and max(result[-1]['end_ms'],end)-result[-1]['start_ms']<=8000):
            result[-1]['end_ms']=max(result[-1]['end_ms'],end)
            result[-1]['witnesses'].append(witness)
        else:
            result.append(dict(start_ms=start,end_ms=end,reason='preview_before_unresolved_empty_result',
                               action_verified=False,witnesses=[witness]))
    return result


def validate_capture(frames,window,origin):
    """Check the decoder's source clock and local frame identities before reuse."""
    if not isinstance(frames,list) or not frames:raise PipelineError('Empty recovery capture.')
    previous=None;identities=set()
    for frame in frames:
        timestamp=frame['source_timestamp_ms'];pts=frame['source_pts']
        base=re.fullmatch(r'([1-9]\d*)/([1-9]\d*)',str(frame['time_base']))
        identity=frame['id'];path=frame['evidence']
        if (type(timestamp) is not int or type(pts) is not int or not base
            or not window['start_ms']<=timestamp<window['end_ms']
            or previous is not None and timestamp<=previous):raise PipelineError('Invalid recovery source clock.')
        numerator,denominator=map(int,base.groups())
        if round((pts*numerator/denominator-origin)*1000)!=timestamp:
            raise PipelineError('Recovery timestamp differs from source PTS.')
        if (not isinstance(identity,str) or not re.fullmatch(r'frame-\d{6}',identity)
            or path!='frames/'+identity.removeprefix('frame-')+'.jpg'
            or identity in identities):raise PipelineError('Invalid recovery frame identity.')
        previous=timestamp;identities.add(identity)


def inspect(source,report_path,root):
    from .vision import NeuralReader,parse,PARAMS
    source,report_path,root=Path(source),Path(report_path),Path(root)
    payload=report_path.read_bytes();report=json.loads(payload);selected=windows(report)
    with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=report['source']['sha256']:raise PipelineError('Recovery source differs from the report.')
    reader=NeuralReader() if selected else None
    # marshal(code) changes with Python's object-reference state. Use stable
    # source/model/runtime identity, and seal each actual OCR payload instead.
    engine_identity=dict(source_sha256=hashlib.sha256(inspection.getsource(NeuralReader).encode()).hexdigest(),
        models=reader.models,params=PARAMS,rapidocr=version('rapidocr'),onnxruntime=version('onnxruntime')) if reader else None
    plan=dict(version=3,engine_identity=engine_identity,source_sha256=digest,report_sha256=hashlib.sha256(payload).hexdigest(),requested_fps=60,
              windows=selected,action_verified=False)
    root.mkdir(parents=True,exist_ok=True);plan_path=root/'plan.json'
    if plan_path.exists():
        if json.loads(plan_path.read_text(encoding='utf-8'))!=plan:raise PipelineError('Recovery plan changed.')
    elif any(root.iterdir()):raise PipelineError('Recovery directory has unrelated artifacts.')
    else:save_json(plan_path,plan)
    observations=[];source_frames={}
    for window in selected:
        directory=root/'training-inspection'/str(window['start_ms']);directory.mkdir(parents=True,exist_ok=True)
        manifest=directory/'frames.json';frames_dir=directory/'frames';frames_dir.mkdir(exist_ok=True)
        seal=directory/'capture-seal.json'
        if manifest.exists():
            frames=json.loads(manifest.read_text(encoding='utf-8'))
            expected=dict(plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
            if not seal.exists() or json.loads(seal.read_text(encoding='utf-8'))!=expected:
                raise PipelineError('Recovery capture manifest changed or is unsealed.')
        else:
            clear_partial_capture(frames_dir)
            frames=decode_frames(source,frames_dir,window['start_ms']/1000,
                (window['end_ms']-window['start_ms'])/1000,60,report['source']['timeline_origin_seconds'])
            save_json(manifest,frames)
            save_json(seal,dict(plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest()))
        validate_capture(frames,window,report['source']['timeline_origin_seconds'])
        for frame in frames:
            source_frame=directory/frame['evidence'];frame_hash=hashlib.sha256(source_frame.read_bytes()).hexdigest()
            identity=(frame['source_pts'],frame['time_base'],frame_hash)
            timestamp=frame['source_timestamp_ms']
            if timestamp in source_frames and source_frames[timestamp]!=identity:
                raise PipelineError('Overlapping recovery windows have different source frames.')
            source_frames[timestamp]=identity
            path=directory/(frame['id']+'.v2.json');evidence=directory/(frame['id']+'.png')
            raw_seal=path.with_suffix('.cache-seal.json')
            if path.exists():
                if (not raw_seal.exists() or json.loads(raw_seal.read_text(encoding='utf-8'))!=dict(
                    raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),engine_identity=engine_identity)):
                    raise PipelineError('Recovery OCR payload or engine changed.')
                raw=json.loads(path.read_text(encoding='utf-8'))
                if (raw.get('source_sha256')!=digest or raw.get('source_frame_sha256')!=frame_hash
                    or raw.get('source_timestamp_ms')!=frame['source_timestamp_ms']
                    or raw.get('model_sha256')!=reader.models):raise PipelineError('Recovery cache provenance mismatch.')
                with reader.Image.open(evidence) as image:
                    if (image.size!=(810,1080) or hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()
                        !=raw.get('gameplay_sha256')):raise PipelineError('Recovery evidence pixels changed.')
            else:
                with reader.Image.open(source_frame) as im:pane=im.convert('RGB').crop((148,0,958,1080))
                raw=reader.read_training(pane);pane.save(evidence)
                raw.update(source_sha256=digest,source_frame_sha256=frame_hash,
                    source_timestamp_ms=frame['source_timestamp_ms'],evidence=evidence.relative_to(root).as_posix())
                save_json(path,raw)
                save_json(raw_seal,dict(raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),engine_identity=engine_identity))
            observations.append(dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
        print(json.dumps(dict(stage='missing_result_inspection',window=window,frames=len(frames))),flush=True)
    unique={}
    for row in observations:
        timestamp=row['source_timestamp_ms']
        if timestamp in unique:
            previous=dict(unique[timestamp]);current=dict(row)
            previous.pop('evidence');current.pop('evidence')
            if previous!=current:raise PipelineError('Overlapping recovery windows disagree.')
        else:unique[timestamp]=row
    manifest=dict(source_sha256=digest,windows=selected,readings=[unique[t] for t in sorted(unique)],requested_fps=60)
    save_json(root/'training-inspection.json',manifest)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('report',type=Path)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    inspect(args.source,args.report,args.output)
