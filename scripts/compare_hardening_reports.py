"""Compare preserved reports and run shared grading over an explicit corpus."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracen_replay.evaluation_adapters import report_document,source_document
from tracen_replay.observation_evaluate import evaluate
from tracen_replay.causal_accounting import build as accounting


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def key(value):return json.dumps(value,sort_keys=True,ensure_ascii=True)


def effects(data):
    rows=[]
    for event in data['events']:
        for effect in event.get('effects',[]):
            payload={k:effect[k] for k in ('kind','field','name','amount','value','direction') if k in effect}
            rows.append(dict(event_kind=event['kind'],start_ms=event['first_seen_ms'],end_ms=event['last_seen_ms'],effect=payload))
    return Counter(key(r) for r in rows)


def compare(before,after,references):
    if before['source']['sha256']!=after['source']['sha256']:raise ValueError('Different recordings')
    b,a=before['gameplay_tracking'],after['gameplay_tracking']
    old,new=effects(b),effects(a)
    outcome=lambda d:dict(Counter(x.get('training_outcome','unknown') for x in d['turn_action_receipts'] if x['kind']=='training'))
    actions=lambda d:Counter(key({k:r[k] for k in ('kind','training_option','source_timestamp_ms','event_id','race_id') if k in r}) for r in d['turn_action_receipts'])
    numeric=lambda d:[{k:e.get(k,{}) for k in ('id','first_seen_ms','last_seen_ms','deltas','performance_deltas')} for e in d['events']]
    interval_totals=lambda d:[{k:r[k] for k in ('start_ms','end_ms','observed_change','supported_change','unexplained_change','status')} for r in d['intervals']]
    result=dict(source_sha256=before['source']['sha256'],development_recording=True,
        observations=dict(before=len(b['readings']),after=len(a['readings'])),
        training_outcomes=dict(before=outcome(b),after=outcome(a)),
        action_counts=dict(before=dict(Counter(x['kind'] for x in b['turn_action_receipts'])),after=dict(Counter(x['kind'] for x in a['turn_action_receipts']))),
        selected_actions_unchanged=actions(b)==actions(a),numeric_event_changes_unchanged=numeric(b)==numeric(a),
        stat_interval_totals_unchanged=interval_totals(b)==interval_totals(a),
        unchanged_collections={k:b[k]==a[k] for k in ('checkpoints','lesson_purchases','skill_purchases','races','dialogue_choices','performance_accounting')},
        effects=dict(before=sum(old.values()),after=sum(new.values()),retained=sum((old&new).values()),
                     added=[json.loads(k) for k in (new-old).elements()],removed=[json.loads(k) for k in (old-new).elements()]),
        causal_accounting=dict(before=accounting(before)['summary'],after=accounting(after)['summary'],
            basis='Both summaries recomputed by the comparison implementation; not preserved historical metrics.'),
        stored_causal_accounting=dict(before=before.get('causal_accounting',{}).get('summary'),
                                      after=after.get('causal_accounting',{}).get('summary')))
    prediction_before,prediction_after=report_document(before),report_document(after)
    grades=[]
    for config in references:
        source=read(config['reference'])
        amendments=read(config['amendments'])['amendments'] if config.get('amendments') else []
        ref=source_document(source,evidence_root=Path(config['evidence_root']).resolve(),amendments=amendments)
        scores=[evaluate(ref,p) for p in (prediction_before,prediction_after)]
        fields=[]
        for score in scores:
            fields.append(dict(Counter(f['status'] for row in score['results'] for f in row['fields'])))
        old_rows={r['source_id']:r for r in scores[0]['results']}
        changed=[dict(source_id=r['source_id'],before=old_rows[r['source_id']],after=r)
                 for r in scores[1]['results'] if r!=old_rows[r['source_id']]]
        grades.append(dict(reference=config['reference'],reference_sha256=sha(config['reference']),
            amendments_sha256=sha(config['amendments']) if config.get('amendments') else None,
            adapted_reference_sha256=hashlib.sha256(key(ref).encode('utf-8')).hexdigest(),
            scope_ms=ref['scope_ms'],observations=len(ref['observations']),reference_complete=False,
            status_counts=dict(before=scores[0]['status_counts'],after=scores[1]['status_counts']),
            field_status_counts=dict(before=fields[0],after=fields[1]),changes=changed,
            amendments_applied=len(amendments),unscored_fields=sum(bool(r['unscored_expected_fields']) for r in ref['observations'])))
    result['shared_reference_grades']=grades
    result['limitations']=['These are development recordings, not held-out validation.',
        'Strict shared-matcher counts use different rules from preserved historical adjudicated scores.',
        'Unscored fields and unmatched predictions in partial references are not graded as correct.',
        'Unchanged output is a regression check, not proof that every source event is recognized.',
        'The amount of directly observed success metadata is not a training-action accuracy percentage.']
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--after',type=Path,required=True)
    parser.add_argument('--corpus',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=compare(read(args.before),read(args.after),read(args.corpus)['references'])
    result['input_sha256']={str(p):sha(p) for p in (args.before,args.after,args.corpus)}
    package=Path(__file__).resolve().parents[1]/'tracen_replay'
    result['evaluator_code_sha256']={p.name:sha(p) for p in [package/name for name in ('evaluation_adapters.py','evaluation_labels.py','observation_evaluate.py','causal_accounting.py')]}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,ensure_ascii=False)
    print(json.dumps({k:result[k] for k in ('observations','training_outcomes','selected_actions_unchanged','numeric_event_changes_unchanged','stat_interval_totals_unchanged','unchanged_collections')}))
    print(json.dumps(dict(added_effects=len(result['effects']['added']),removed_effects=len(result['effects']['removed']),references=len(result['shared_reference_grades']))))


if __name__=='__main__':main()
