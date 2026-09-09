"""Score transaction recall and exact numeric fields in a reviewed source interval."""
import argparse
import json
from pathlib import Path
from .gameplay import CURRENCIES
from .reconcile import FIELDS


def evaluate(reference,report):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Reference belongs to a different recording.')
    if not isinstance(reference.get('transactions'),list):raise ValueError('Explicit transaction labels are required.')
    kinds=reference.get('kinds')
    if not isinstance(kinds,list) or not kinds or any(k not in ('lesson','concert') for k in kinds):
        raise ValueError('Declare supported lesson/concert transaction kinds.')
    negative=reference.get('no_transactions') is True
    if not reference['transactions'] and not (negative and reference.get('independently_reviewed') is True):
        raise ValueError('Empty transactions require an explicitly reviewed negative reference.')
    if negative and reference['transactions']:raise ValueError('Negative reference contains transactions.')
    start,end=reference['start_ms'],reference['end_ms']
    if not 0<=start<end:raise ValueError('Invalid reference bounds.')
    if 'duration_ms' in report['source'] and end>report['source']['duration_ms']:
        raise ValueError('Reference exceeds source duration.')
    data=report['gameplay_tracking'];predictions=[]
    if data.get('auxiliary_log_used') is not False:raise ValueError('Only gameplay-only reports are eligible.')
    for purchase in data.get('lesson_purchases',[]):
        predictions.append(dict(kind='lesson',timestamp_ms=purchase['source_timestamp_ms'],id=purchase['id'],
                                performance_cost=purchase['performance_cost'],stats=purchase['awarded_stats'],
                                name=purchase.get('name'),requested_name=purchase.get('requested_name'),
                                receipt_name=purchase.get('receipt_name')))
    for concert in data.get('concerts',[]):
        effects=[e for group in concert['observed_rewards'] for e in group]
        predictions.append(dict(kind='concert',timestamp_ms=concert['first_seen_ms'],id=concert['id'],
                                stats={f:sum(e['amount'] for e in effects if e['kind']=='stat_change' and e['field']==f) for f in FIELDS},
                                fans=sum(e['amount'] for e in effects if e['kind']=='fan_change')))
    predictions=[p for p in predictions if start<=p['timestamp_ms']<end and p['kind'] in reference['kinds']]
    used=set();matches=[];missing=[];field_errors=[]
    for expected in reference['transactions']:
        if expected['kind'] not in reference['kinds']:raise ValueError('Expected transaction outside declared kinds.')
        if not start<=expected['start_ms']<=expected['end_ms']<end:raise ValueError('Expected transaction outside reference scope.')
        for key in ('name','requested_name','receipt_name'):
            if key in expected and (expected['kind']!='lesson' or not isinstance(expected[key],str) or not expected[key].strip()):
                raise ValueError('Identity labels require a nonempty source-observed lesson name.')
        options=[(i,p) for i,p in enumerate(predictions) if i not in used and p['kind']==expected['kind'] and expected['start_ms']<=p['timestamp_ms']<=expected['end_ms']]
        if len(options)!=1:missing.append(expected);continue
        index,prediction=options[0];used.add(index);matches.append(prediction['id'])
        for key in ('performance_cost','stats','fans','name','requested_name','receipt_name'):
            if key not in expected:continue
            actual=prediction.get(key);wanted=expected[key]
            if key in ('performance_cost','stats') and actual is not None:
                fields=CURRENCIES if key=='performance_cost' else FIELDS
                actual={f:actual.get(f,0) for f in fields};wanted={f:wanted.get(f,0) for f in fields}
            if actual!=wanted:field_errors.append(dict(transaction=prediction['id'],field=key,expected=wanted,actual=actual))
    extras=[p for i,p in enumerate(predictions) if i not in used]
    precision=len(matches)/len(predictions) if predictions else None
    recall=len(matches)/len(reference['transactions']) if reference['transactions'] else None
    return dict(source_sha256=reference['source_sha256'],scope=reference['scope'],start_ms=start,end_ms=end,
                expected=len(reference['transactions']),matched=len(matches),precision=precision,recall=recall,
                missing=missing,extra_predictions=extras,field_errors=field_errors,
                passed=not missing and not extras and not field_errors,
                independently_reviewed=reference.get('independently_reviewed',False),
                evaluated_fields=sorted({key for expected in reference['transactions']
                    for key in ('performance_cost','stats','fans','name','requested_name','receipt_name') if key in expected}),
                explicitly_reviewed_negative=negative,
                independent_recording=False,complete_effect_recall_measured=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
