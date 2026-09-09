"""One-to-one effect scoring for a fixed, source-reviewed time interval."""
import argparse
import hashlib
import json
from pathlib import Path


def evaluate(reference,report,root=None):
    reference_complete=reference.get('reference_complete',True)
    if type(reference_complete) is not bool:
        raise ValueError('Reference completeness must be a boolean.')
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source recording.')
    start,end=reference['start_ms'],reference['end_ms']
    if not 0<=start<end:raise ValueError('Invalid interval.')
    data=report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Gameplay-only report required.')
    timing_basis=reference.get('timing_basis','event_start')
    if timing_basis not in ('event_start','first_exact_effect_observation'):
        raise ValueError('Unknown effect timing basis.')
    include_training=reference.get('include_training_results',False)
    if type(include_training) is not bool:
        raise ValueError('Training result inclusion must be a boolean.')
    if include_training and timing_basis!='event_start':
        raise ValueError('Typed training results require event-start timing; derived deltas are not exact receipt observations.')
    rows={r['evidence']:r for r in data.get('readings',[])}
    timing_errors=[]
    predictions=[]
    for event in data['events']:
        if timing_basis=='event_start' and not start<=event['first_seen_ms']<end:continue
        for effect in event.get('effects',[]):
            key='|'.join(str(v or '') for v in (effect['kind'],effect.get('field'),effect.get('name')))
            time=event['first_seen_ms']
            if timing_basis=='first_exact_effect_observation':
                times=[]
                for proof in event.get('field_evidence',{}).get(key,[]):
                    row=rows.get(proof)
                    if row is None:continue
                    if any(all(candidate.get(k)==effect.get(k) for k in
                               ('kind','field','name','amount','value'))
                           for candidate in row.get('effects',[])):
                        times.append(row['source_timestamp_ms'])
                if not times:
                    if event['first_seen_ms']<end and event.get('last_seen_ms',event['first_seen_ms'])>=start:
                        timing_errors.append(dict(event_id=event['id'],field=key,
                            reason='no_exact_effect_observation_for_window_ownership'))
                    continue
                time=min(times)
                if not start<=time<end:continue
            predictions.append(dict(event_id=event['id'],time=time,effect=effect,
                conflicted=any(c.get('field')==key for c in event.get('conflicting_readings',[]))))
        if include_training and event.get('kind')=='training':
            for field_key,kind in (('deltas','stat_change'),('performance_deltas','performance_change')):
                for field,amount in event.get(field_key,{}).items():
                    if type(amount) is not int:continue
                    effect=dict(kind=kind,field=field,amount=amount)
                    if any(all(old.get(k)==v for k,v in effect.items()) for old in event.get('effects',[])):
                        continue
                    conflicts=event.get('conflicting_readings' if field_key=='deltas' else 'performance_reading_conflicts',{})
                    conflicted=(field in conflicts if isinstance(conflicts,dict)
                                else any(c.get('field')==field for c in conflicts))
                    predictions.append(dict(event_id=event['id'],time=event['first_seen_ms'],
                        effect=effect,conflicted=conflicted,report_field=field_key+'.'+field))
    used=set();missing=[];matched=0;onset_windows=0
    reviewed_times=[sample['source_timestamp_ms'] for sample in reference['reviewed_samples']]
    for group in reference['groups']:
        if not start<=group['start_ms']<=group['end_ms']<end:raise ValueError('Group outside scope.')
        onset=group.get('onset_window')
        after=None
        if onset is not None:
            if not isinstance(onset,dict):raise ValueError('Invalid source onset window.')
            after,present=onset.get('last_absent_ms'),onset.get('first_present_ms')
            if type(after) is not int or type(present) is not int or present!=group['start_ms'] or not start<=after<present:
                raise ValueError('Onset window must end at the first reviewed group sample.')
            if not any(a==after and b==present for a,b in zip(reviewed_times,reviewed_times[1:])):
                raise ValueError('Onset bounds must be adjacent reviewed source samples.')
            onset_windows+=1
        for expected in group['effects']:
            candidates=[(i,p) for i,p in enumerate(predictions) if i not in used and not p['conflicted']
                and (group['start_ms']<=p['time'] if after is None else after<p['time'])
                and p['time']<=group['end_ms']
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
    # Preserve the original denominator when source adjudication finds hidden
    # fields. These annotations explain reference limitations; they do not
    # excuse a prediction, remove a missing effect, or certify an identity.
    exceptions=reference.get('observability_exceptions',[])
    if not isinstance(exceptions,list):raise ValueError('Invalid observability exceptions.')
    for item in exceptions:
        if not isinstance(item,dict):raise ValueError('Invalid observability exception.')
        group_index,effect_index=item.get('group_index'),item.get('effect_index')
        if type(group_index) is not int or not 0<=group_index<len(reference['groups']):
            raise ValueError('Observability exception has no reference group.')
        group=reference['groups'][group_index]
        if type(effect_index) is not int or not 0<=effect_index<len(group['effects']):
            raise ValueError('Observability exception has no reference effect.')
        fields=item.get('fields')
        if not isinstance(fields,list) or not fields or any(not isinstance(f,str) or f not in group['effects'][effect_index] for f in fields):
            raise ValueError('Observability exception must identify expected fields.')
        if not isinstance(item.get('reason'),str) or not item['reason'].strip():
            raise ValueError('Observability exception requires a reason.')
        proof_times=item.get('source_timestamps_ms')
        if not isinstance(proof_times,list) or not proof_times or any(type(t) is not int or t not in times or not group['start_ms']<=t<=group['end_ms'] for t in proof_times):
            raise ValueError('Observability exception requires reviewed same-group samples.')
    return dict(scope=reference['scope'],source_sha256=reference['source_sha256'],start_ms=start,end_ms=end,
        expected=expected,predicted=len(predictions),matched=matched,precision=matched/len(predictions) if predictions else None,
        recall=matched/expected if expected else None,missing=missing,extra_predictions=extras,evidence_errors=evidence_errors,
        passed=reference_complete and not missing and not extras and not evidence_errors and not timing_errors,reviewed_samples=len(samples),
        reference_complete=reference_complete,
        timing_basis=timing_basis,timing_errors=timing_errors,
        include_training_results=include_training,
        typed_training_predictions=sum('report_field' in p for p in predictions),
        source_onset_windows=onset_windows,
        evidence_hashes_checked=root is not None,complete_video_frame_review=False,full_recording_effect_recall_measured=False,
        observability_exceptions=exceptions,
        reference_observability='known_exceptions' if exceptions else 'not_adjudicated',
        scoring_basis='agreement_with_preserved_reference',
        independent_recording=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path)
    parser.add_argument('--evidence-root',type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')),args.evidence_root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
