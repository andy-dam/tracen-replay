"""Sample short dialogue transitions without expected choices as OCR input."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image
from .choice_evidence import observe
from .refine_choices import apply
from .refine_contrast import fingerprint
from .vision import NeuralReader,parse
from .pipeline import decode_frames,PipelineError
from .full_recording import save_json


def load(root,source_sha256):
    """Validate stored choice-only observations before report reconstruction."""
    from .verify_evidence import inside
    root=Path(root);path=root/'choice-inspection.json'
    if not path.exists():return None,[]
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest['source_sha256']!=source_sha256:raise PipelineError('Choice inspection belongs to another source.')
    rows=[]
    for item in manifest['readings']:
        proof=inside(root,item['evidence']);raw=json.loads(proof.with_suffix('.v2.json').read_text(encoding='utf-8'))
        if raw['source_sha256']!=source_sha256 or raw['source_timestamp_ms']!=item['source_timestamp_ms'] or raw['evidence']!=item['evidence']:
            raise PipelineError('Choice inspection source or timestamp mismatch.')
        extra=json.loads(proof.with_suffix('.choice.json').read_text(encoding='utf-8'))
        row=apply(parse(raw),raw,extra,proof)
        time=raw['source_timestamp_ms']
        if row['screen']!='unknown':rows.append(dict(source_timestamp_ms=time,screen_boundary=True))
        else:rows.append(dict(row['facts']['choice_observation'],source_timestamp_ms=time,evidence=raw['evidence']))
    return dict(windows=manifest['windows'],method='bounded_choice_inspection',observations=rows),rows


def inspect(source,root,start,end,fps=60):
    root=Path(root);capture=json.loads((root/'capture.json').read_text(encoding='utf-8'))
    if not 0<=start<end<=capture['source']['duration_ms'] or end-start>5000:
        raise PipelineError('Use a choice window of at most five seconds within the source.')
    if not 4<=fps<=60:raise PipelineError('Choice sampling must be 4 to 60 FPS.')
    with Path(source).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=capture['source']['sha256']:raise PipelineError('Choice source mismatch.')
    path=root/'choice-inspection.json'
    result=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(source_sha256=digest,windows=[],readings=[])
    if result['source_sha256']!=digest:raise PipelineError('Existing choice inspection belongs to another source.')
    if any(w['start_ms']==start and w['end_ms']==end and w['fps']==fps for w in result['windows']):
        load(root,digest);return
    directory=root/'choice-inspection'/f'{start}-{end}-{fps}';directory.mkdir(parents=True,exist_ok=True)
    frames_dir=directory/'frames';frames_dir.mkdir(exist_ok=True);manifest=directory/'frames.json'
    if manifest.exists():frames=json.loads(manifest.read_text(encoding='utf-8'))
    else:
        frames=decode_frames(source,frames_dir,start/1000,(end-start)/1000,fps,capture['source'].get('timeline_origin_seconds',0))
        save_json(manifest,frames)
    reader=NeuralReader()
    for index,frame in enumerate(frames):
        image_path=directory/frame['evidence'];cache=directory/(frame['id']+'.v2.json');proof=directory/(frame['id']+'.png')
        frame_hash=hashlib.sha256(image_path.read_bytes()).hexdigest()
        with Image.open(image_path) as image:pane=image.convert('RGB').crop((148,0,958,1080))
        if cache.exists():
            raw=json.loads(cache.read_text(encoding='utf-8'))
            if (raw['source_frame_sha256']!=frame_hash or raw['source_sha256']!=digest
                    or raw['source_timestamp_ms']!=frame['source_timestamp_ms']
                    or raw['gameplay_sha256']!=hashlib.sha256(pane.tobytes()).hexdigest()):
                raise PipelineError('Cached choice source frame changed.')
        else:
            raw=reader.read(pane);pane.save(proof)
            raw.update(source_timestamp_ms=frame['source_timestamp_ms'],source_frame_sha256=frame_hash,
                       source_sha256=digest,evidence=proof.relative_to(root).as_posix())
            save_json(cache,raw)
        extra_path=proof.with_suffix('.choice.json')
        if extra_path.exists():apply(parse(raw),raw,json.loads(extra_path.read_text(encoding='utf-8')),proof)
        else:
            with Image.open(proof) as saved:
                if saved.size!=(810,1080) or hashlib.sha256(saved.convert('RGB').tobytes()).hexdigest()!=raw['gameplay_sha256']:
                    raise PipelineError('Choice proof differs from source gameplay pixels.')
            save_json(extra_path,dict(version=2,raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                observation=observe(pane,raw['lines']),independent_observations=False))
        result['readings'].append(dict(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
        if (index+1)%10==0:print(json.dumps(dict(stage='choice_inspection',processed=index+1,total=len(frames))),flush=True)
    result['windows'].append(dict(start_ms=start,end_ms=end,fps=fps,reason='brief_dialogue_transition'))
    save_json(path,result);load(root,digest)
    print(json.dumps(dict(stage='choice_inspection_complete',start_ms=start,frames=len(frames))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--start-ms',required=True,type=int)
    parser.add_argument('--end-ms',required=True,type=int);parser.add_argument('--fps',type=int,default=60)
    args=parser.parse_args();inspect(args.source,args.output,args.start_ms,args.end_ms,args.fps)
