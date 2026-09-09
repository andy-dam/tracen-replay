"""Audit declared sampled effect references and expose every unreviewed interval."""
import argparse
import hashlib
import json
from pathlib import Path


def coverage(capture,references,root):
    root=Path(root).resolve();duration=capture['source']['duration_ms'];intervals=[];records=[]
    if type(duration) is not int or duration<=0:raise ValueError('Invalid source duration.')
    for relative in references:
        path=(root/relative).resolve()
        if not path.is_relative_to(root):raise ValueError('Reference leaves run directory.')
        ref=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(ref.get('groups'),list):raise ValueError('Expected an effect reference.')
        if ref['source_sha256']!=capture['source']['sha256']:raise ValueError('Different source reference.')
        start,end,spacing=ref['start_ms'],ref['end_ms'],ref['sample_interval_ms']
        if not 0<=start<end<=duration or type(spacing) is not int or spacing<=0:raise ValueError('Invalid reference interval.')
        samples=ref['reviewed_samples']
        if [s['source_timestamp_ms'] for s in samples]!=list(range(start,end,spacing)):raise ValueError('Incomplete sample manifest.')
        for sample in samples:
            proof=(root/sample['evidence']).resolve()
            if not proof.is_relative_to(root):raise ValueError('Proof leaves run directory.')
            if hashlib.sha256(proof.read_bytes()).hexdigest()!=sample['sha256']:raise ValueError('Reviewed evidence changed.')
        intervals.append((start,end))
        records.append(dict(path=str(relative),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            start_ms=start,end_ms=end,sample_interval_ms=spacing,samples=len(samples),scope=ref['scope']))
    merged=[]
    for start,end in sorted(intervals):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    gaps=[];cursor=0
    for start,end in merged:
        if cursor<start:gaps.append([cursor,start])
        cursor=end
    if cursor<duration:gaps.append([cursor,duration])
    total=sum(b-a for a,b in merged)
    return dict(source_sha256=capture['source']['sha256'],source_duration_ms=duration,references=records,
                declared_reviewed_intervals=merged,declared_reviewed_duration_ms=total,
                declared_reviewed_percent=round(total/duration*100,3),unreviewed_intervals=gaps,
                next_source_order_window=[gaps[0][0],min(gaps[0][0]+10000,gaps[0][1])] if gaps else None,
                full_recording_effect_recall_measured=False,complete_video_frame_review=False,
                scope='Union of declared sampled effect-reference intervals with checked sample hashes. This audits manifests, not the truth of labels or between-sample recall. No prediction-based interval selection.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    parser.add_argument('references',nargs='+');parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=coverage(json.loads((args.root/'capture.json').read_text(encoding='utf-8')),args.references,args.root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='references'}))
