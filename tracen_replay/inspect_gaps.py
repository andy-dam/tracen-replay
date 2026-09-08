"""Inspect unresolved training animations at the recording's native 60 FPS."""
import argparse
import hashlib
import json
from pathlib import Path
from .vision import NeuralReader,parse
from .pipeline import decode_frames,PipelineError
from .full_recording import save_json


def inspect(source,root):
    root=Path(root);report=json.loads((root/'report.json').read_text(encoding='utf-8'))
    with Path(source).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=report['source']['sha256']:raise PipelineError('Native inspection source mismatch.')
    data=report['gameplay_tracking'];reader=NeuralReader();windows=[];rows=[]
    previous=root/'native-inspection.json'
    if previous.exists():
        old=json.loads(previous.read_text(encoding='utf-8'))
        if old['source_sha256']!=digest:raise PipelineError('Native inspection source changed.')
        if old.get('decoder_version')==2:windows=old['windows'];rows=old['readings']
    for gap in data['intervals']:
        if gap['status']!='unresolved':continue
        for event in data['events']:
            if event['kind']!='training' or not gap['start_ms']<event['first_seen_ms']<gap['end_ms'] or not event['training_option']:continue
            start=max(0,event['first_seen_ms']-100);end=event['first_seen_ms']+700
            if any(w['start_ms']<=event['first_seen_ms']<w['end_ms'] for w in windows):continue
            directory=root/'training-inspection'/f'{start}-native-v2';directory.mkdir(exist_ok=True)
            dest=directory/'frames';dest.mkdir(exist_ok=True);manifest=directory/'frames.json'
            if manifest.exists():frames=json.loads(manifest.read_text(encoding='utf-8'))
            else:
                frames=decode_frames(source,dest,start/1000,(end-start)/1000,60,report['source']['timeline_origin_seconds']);save_json(manifest,frames)
            for frame in frames:
                cache=directory/(frame['id']+'.v2.json');evidence=directory/(frame['id']+'.png');image_path=directory/frame['evidence']
                if cache.exists():raw=json.loads(cache.read_text(encoding='utf-8'))
                else:
                    with reader.Image.open(image_path) as image:pane=image.convert('RGB').crop((148,0,958,1080))
                    raw=reader.read_training(pane)
                    # The enlarged Speed award extends into the neighboring cell.
                    # An exact signed number is required; background text abstains.
                    box=(215,790,600,930);crop=pane.crop((box[0]-148,box[1],box[2]-148,box[3]))
                    result=reader.engine.text_rec(reader.TextRecInput(img=[reader.np.array(crop)[:,:,::-1]]))
                    raw['regions']['expanded_gain.speed']=dict(text=result.txts[0],confidence=round(float(result.scores[0])*100,4),box=list(box))
                    pane.save(evidence);raw.update(source_timestamp_ms=frame['source_timestamp_ms'],source_frame_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),evidence=evidence.relative_to(root).as_posix(),source_sha256=digest)
                    save_json(cache,raw)
                rows.append(dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
            windows.append(dict(start_ms=start,end_ms=end,reason='unresolved_training_animation'))
            save_json(previous,dict(source_sha256=digest,readings=rows,windows=windows,decoder_version=2))
            print(json.dumps(dict(stage='native_inspection',start_ms=start,frames=len(frames))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();inspect(args.source,args.output)
