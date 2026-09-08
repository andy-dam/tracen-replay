"""One-to-one effect scoring for a fixed, source-reviewed time interval."""
import argparse
import hashlib
import json
from pathlib import Path


def evaluate(reference,report,root=None):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source recording.')
    start,end=reference['start_ms'],reference['end_ms']
    if not 0<=start<end:raise ValueError('Invalid interval.')
    data=report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Gameplay-only report required.')
    predictions=[]
    for event in data['events']:
        if not start<=event['first_seen_ms']<end:continue
        for effect in event.get('effects',[]):
            key='|'.join(str(v or '') for v in (effect['kind'],effect.get('field'),effect.get('name')))
            predictions.append(dict(event_id=event['id'],time=event['first_seen_ms'],effect=effect,
                conflicted=any(c.get('field')==key for c in event.get('conflicting_readings',[]))))
    used=set();missing=[];matched=0
    for group in reference['groups']:
        if not start<=group['start_ms']<=group['end_ms']<end:raise ValueError('Group outside scope.')
        for expected in group['effects']:
            candidates=[(i,p) for i,p in enumerate(predictions) if i not in used and not p['conflicted']
                and group['start_ms']<=p['time']<=group['end_ms']
                and all(k in p['effect'] and p['effect'][k]==v for k,v in expected.items())]
            if candidates:used.add(candidates[0][0]);matched+=1
            else:missing.append(dict(start_ms=group['start_ms'],effect=expected))
    extras=[p for i,p in enumerate(predictions) if i not in used]
    samples=reference['reviewed_samples'];times=[s['source_timestamp_ms'] for s in samples]
    expected_times=list(range(start,end,reference['sample_interval_ms']))
    evidence_errors=[]
    if times!=expected_times:evidence_errors.append('incomplete_review_sample_manifest')
    if root is not None:
        root=Path(root).resolve()
        for sample in samples:
            path=(root/sample['evidence']).resolve()
            if not path.is_relative_to(root):raise ValueError('Evidence path leaves run directory.')
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=sample['sha256']:
                evidence_errors.append(sample['evidence'])
    expected=sum(len(g['effects']) for g in reference['groups'])
    return dict(scope=reference['scope'],source_sha256=reference['source_sha256'],start_ms=start,end_ms=end,
        expected=expected,predicted=len(predictions),matched=matched,precision=matched/len(predictions) if predictions else None,
        recall=matched/expected if expected else None,missing=missing,extra_predictions=extras,evidence_errors=evidence_errors,
        passed=not missing and not extras and not evidence_errors,reviewed_samples=len(samples),
        evidence_hashes_checked=root is not None,complete_video_frame_review=False,full_recording_effect_recall_measured=False,
        independent_recording=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path)
    parser.add_argument('--evidence-root',type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')),args.evidence_root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
