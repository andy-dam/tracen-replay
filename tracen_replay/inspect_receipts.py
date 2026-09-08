"""Read bounded receipt windows more densely, with no expected labels as OCR input."""
import argparse
import hashlib
import json
from pathlib import Path
from .vision import NeuralReader,parse
from .pipeline import decode_frames,PipelineError
from .full_recording import save_json


def merge(base,extra):
    rows={r['source_timestamp_ms']:r for r in base}
    for row in extra:
        if row['screen']!='event_outcome' or not row['effects']:continue
        time=row['source_timestamp_ms'];old=rows.get(time)
        if old and old['screen'] not in ('unknown','event_outcome'):continue
        if old:
            effects=list(old['effects'])
            signatures={json.dumps({k:e.get(k) for k in ('kind','field','name','amount','direction','value')},sort_keys=True) for e in effects}
            for effect in row['effects']:
                key=json.dumps({k:effect.get(k) for k in ('kind','field','name','amount','direction','value')},sort_keys=True)
                if key not in signatures:effects.append(effect);signatures.add(key)
            row=dict(row,effects=effects,stats=old['stats'],base_evidence=old['evidence'])
        rows[time]=row
    return [rows[t] for t in sorted(rows)]


def inspect(source,root,start,end,fps=16):
    root=Path(root);capture=json.loads((root/'capture.json').read_text(encoding='utf-8'))
    if not 0<=start<end<=capture['source']['duration_ms'] or end-start>5000:raise PipelineError('Use a bounded window of at most five seconds within the source.')
    if not 4<=fps<=60:raise PipelineError('Receipt sampling must be 4 to 60 FPS.')
    with Path(source).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=capture['source']['sha256']:raise PipelineError('Receipt source mismatch.')
    path=root/'receipt-inspection.json'
    result=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(source_sha256=digest,windows=[],readings=[])
    if result['source_sha256']!=digest:raise PipelineError('Existing receipt inspection belongs to another source.')
    if any(w['start_ms']==start and w['end_ms']==end and w['fps']==fps for w in result['windows']):return
    directory=root/'receipt-inspection'/f'{start}-{end}-{fps}';directory.mkdir(parents=True,exist_ok=True)
    frames_dir=directory/'frames';frames_dir.mkdir(exist_ok=True);manifest=directory/'frames.json'
    if manifest.exists():frames=json.loads(manifest.read_text(encoding='utf-8'))
    else:
        frames=decode_frames(source,frames_dir,start/1000,(end-start)/1000,fps,capture['source'].get('timeline_origin_seconds',0));save_json(manifest,frames)
    reader=NeuralReader()
    for frame in frames:
        image_path=directory/frame['evidence'];cache=directory/(frame['id']+'.v2.json');proof=directory/(frame['id']+'.png')
        frame_hash=hashlib.sha256(image_path.read_bytes()).hexdigest()
        if cache.exists():
            raw=json.loads(cache.read_text(encoding='utf-8'))
            if raw['source_frame_sha256']!=frame_hash:raise PipelineError('Receipt frame changed.')
        else:
            with reader.Image.open(image_path) as image:pane=image.convert('RGB').crop((148,0,958,1080))
            raw=reader.read(pane);pane.save(proof)
            raw.update(source_timestamp_ms=frame['source_timestamp_ms'],source_frame_sha256=frame_hash,
                       source_sha256=digest,evidence=proof.relative_to(root).as_posix())
            save_json(cache,raw)
        result['readings'].append(dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
    result['windows'].append(dict(start_ms=start,end_ms=end,fps=fps,reason='unparsed_receipt_review'))
    save_json(path,result);print(json.dumps(dict(stage='receipt_inspection',start_ms=start,frames=len(frames))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--start-ms',required=True,type=int)
    parser.add_argument('--end-ms',required=True,type=int);parser.add_argument('--fps',type=int,default=16)
    args=parser.parse_args();inspect(args.source,args.output,args.start_ms,args.end_ms,args.fps)
