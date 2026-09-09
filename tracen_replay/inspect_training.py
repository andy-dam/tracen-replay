"""Inspect every detected training result at 30 FPS using only gameplay pixels."""
import argparse
import hashlib
import json
from pathlib import Path
from .pipeline import decode_frames, PipelineError
from .vision import NeuralReader, parse
from .transactions import training_events
from .full_recording import save_json


def windows(readings,duration_ms):
    result=[]
    candidates=[dict(first_seen_ms=r['source_timestamp_ms']) for r in readings if r['screen']=='training_result_candidate']
    for event in sorted(training_events(readings)+candidates,key=lambda e:e['first_seen_ms']):
        start=max(0,event['first_seen_ms']-500);end=min(duration_ms,event['first_seen_ms']+1500)
        if result and start<=result[-1]['end_ms']:
            result[-1]['end_ms']=max(result[-1]['end_ms'],end)
        else:result.append(dict(start_ms=start,end_ms=end,reason='training_result_gain_animation'))
    return result


def inspect(source,root,readings):
    root=Path(root);capture=json.loads((root/'capture.json').read_text(encoding='utf-8'))
    with Path(source).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=capture['source']['sha256']:raise PipelineError('Inspection source differs from the base recording.')
    reader=NeuralReader();completed=[]
    for window in windows(readings,capture['source']['duration_ms']):
        directory=root/'training-inspection'/str(window['start_ms'])
        existing=directory/'frames.json'
        if existing.exists():
            prior=json.loads(existing.read_text(encoding='utf-8'))
            if not prior or prior[-1]['source_timestamp_ms']+35<window['end_ms']:
                directory=root/'training-inspection'/f'{window["start_ms"]}-{window["end_ms"]}'
        directory.mkdir(parents=True,exist_ok=True)
        manifest=directory/'frames.json';dest=directory/'frames';dest.mkdir(exist_ok=True)
        if manifest.exists():frames=json.loads(manifest.read_text(encoding='utf-8'))
        else:
            if any(dest.iterdir()):raise PipelineError(f'Incomplete inspection capture: {directory}')
            frames=decode_frames(source,dest,window['start_ms']/1000,(window['end_ms']-window['start_ms'])/1000,30,capture['source']['timeline_origin_seconds'])
            save_json(manifest,frames)
        for frame in frames:
            cache=directory/(frame['id']+'.v2.json');evidence=directory/(frame['id']+'.png')
            image_path=directory/frame['evidence'];image_digest=hashlib.sha256(image_path.read_bytes()).hexdigest()
            if cache.exists():
                raw=json.loads(cache.read_text(encoding='utf-8'))
                if raw.get('source_frame_sha256')!=image_digest:raise PipelineError('Inspection evidence changed.')
            else:
                with reader.Image.open(image_path) as image:pane=image.convert('RGB').crop((148,0,958,1080))
                raw=reader.read_training(pane);pane.save(evidence)
                raw.update(source_timestamp_ms=frame['source_timestamp_ms'],source_frame_sha256=image_digest,
                           evidence=evidence.relative_to(root).as_posix(),source_sha256=digest)
                save_json(cache,raw)
            completed.append(dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
        print(json.dumps(dict(stage='training_inspection',window=window,frames=len(frames))),flush=True)
    save_json(root/'training-inspection.json',dict(source_sha256=digest,readings=completed,windows=windows(readings,capture['source']['duration_ms'])))
    return completed


def merge(base,supplemental):
    # Specialized crops cannot replace general observations of resources and dialogue.
    rows={r['source_timestamp_ms']:r for r in base}
    for row in supplemental:
        timestamp=row['source_timestamp_ms']
        if row['screen']!='training_result':continue
        if timestamp in rows:
            old=rows[timestamp]
            if old['screen'] in ('unknown','training_result_candidate'):
                rows[timestamp]=dict(row,ocr=old['ocr'],base_evidence=old['evidence']);continue
            if old['screen']!='training_result':continue
            facts=dict(old['facts']);facts.update(row['facts']);facts['training_gains']=dict(old['facts'].get('training_gains',{}))
            conflicts={}
            for field,value in row['facts'].get('training_gains',{}).items():
                prior=facts['training_gains'].get(field)
                if prior is not None and prior!=value:
                    conflicts[field]=[prior,value];facts['training_gains'].pop(field)
                else:facts['training_gains'][field]=value
            rows[timestamp]=dict(old,facts=facts,supplemental_evidence=row['evidence'],inspection_conflicts=conflicts)
        else:rows[timestamp]=row
    return [rows[t] for t in sorted(rows)]


def reparse_inspection(inspection,root):
    result=[];root=Path(root);manifests={}
    for row in inspection['readings']:
        evidence=root/row['evidence'];path=evidence.with_suffix('.v2.json')
        if not path.exists():path=evidence.with_suffix('.json')
        raw=json.loads(path.read_text(encoding='utf-8'))
        if raw.get('source_sha256')!=inspection['source_sha256'] or raw['source_timestamp_ms']!=row['source_timestamp_ms']:
            raise PipelineError('Training inspection provenance mismatch.')
        if evidence.parent not in manifests:
            manifests[evidence.parent]={f['id']:f for f in json.loads((evidence.parent/'frames.json').read_text(encoding='utf-8'))}
        frame=manifests[evidence.parent].get(evidence.stem)
        if not frame or frame['source_timestamp_ms']!=raw['source_timestamp_ms']:
            raise PipelineError('Inspection capture timestamp mismatch.')
        if hashlib.sha256((evidence.parent/frame['evidence']).read_bytes()).hexdigest()!=raw['source_frame_sha256']:
            raise PipelineError('Inspection source frame changed.')
        proof_hash=hashlib.sha256(evidence.read_bytes()).hexdigest()
        for suffix in ('totals','contrast','performance','awards','receipt'):
            extra_path=path.with_suffix('.'+suffix+'.json')
            if extra_path.exists() and json.loads(extra_path.read_text(encoding='utf-8'))['evidence_sha256']!=proof_hash:
                raise PipelineError('Inspection refinement image changed.')
        original=raw
        refinement=path.with_suffix('.totals.json')
        if refinement.exists():
            from .refine_results import apply_result_refinement
            raw=apply_result_refinement(raw,json.loads(refinement.read_text(encoding='utf-8')))
        contrast=path.with_suffix('.contrast.json')
        if contrast.exists():
            from .refine_contrast import apply_contrast_refinement
            raw=apply_contrast_refinement(raw,json.loads(contrast.read_text(encoding='utf-8')),original)
        performance=path.with_suffix('.performance.json')
        if performance.exists():
            from .refine_contrast import fingerprint
            extra=json.loads(performance.read_text(encoding='utf-8'))
            if extra['raw_sha256']!=fingerprint(original):raise PipelineError('Performance refinement source mismatch.')
            raw=dict(raw,regions=dict(raw['regions'],**extra['regions']))
        awards=path.with_suffix('.awards.json')
        if awards.exists():
            from .refine_contrast import fingerprint
            extra=json.loads(awards.read_text(encoding='utf-8'))
            if extra['raw_sha256']!=fingerprint(original):raise PipelineError('Award refinement source mismatch.')
            raw=dict(raw,regions=dict(raw['regions'],**extra['regions']))
        receipt=path.with_suffix('.receipt.json')
        if receipt.exists():
            from .refine_receipts import apply
            raw=apply(raw,json.loads(receipt.read_text(encoding='utf-8')))
        from .full_recording import parse_receipt_pixels
        source_frame=dict(frame,evidence=(evidence.parent/frame['evidence']).relative_to(root).as_posix())
        result.append(dict(parse_receipt_pixels(raw,root,source_frame,original),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    rows=[]
    for path in sorted((args.output/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'));rows.append(dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
    inspect(args.source,args.output,rows)


if __name__=='__main__':main()
