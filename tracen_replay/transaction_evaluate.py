"""Score transaction recall and exact numeric fields in a reviewed source interval."""
import argparse
import hashlib
import json
from pathlib import Path
from .gameplay import CURRENCIES
from .reconcile import FIELDS


def numeric_valid(value,key):
    if key=='fans':return type(value) is int
    fields=CURRENCIES if key=='performance_cost' else FIELDS
    return (isinstance(value,dict) and not set(value)-set(fields)
            and all(type(v) is int and (key!='performance_cost' or v>=0) for v in value.values()))


def validate_source_balance(expected,start,end,root=None):
    """Check optional source labels against each other, before scoring output."""
    if 'source_performance_balance' not in expected:return False
    check=expected['source_performance_balance']
    if expected.get('kind')!='lesson' or not isinstance(check,dict) or 'performance_cost' not in expected:
        raise ValueError('Source performance balances require a labeled lesson cost.')
    def vector(value,nonnegative):
        return isinstance(value,dict) and set(value)==set(CURRENCIES) and all(
            type(v) is int and (not nonnegative or v>=0) for v in value.values())
    other=check.get('other_changes')
    if not vector(other,False):raise ValueError('Explicit changes for all five performance currencies are required.')
    points=[]
    for phase in ('before','after'):
        point=check.get(phase)
        if not isinstance(point,dict) or not vector(point.get('values'),True):
            raise ValueError('Source balances require all five nonnegative currency values.')
        time=point.get('source_timestamp_ms')
        if type(time) is not int or not start<=time<end:raise ValueError('Source balance time outside reference.')
        evidence,digest=point.get('evidence'),point.get('sha256')
        if not isinstance(evidence,str) or not evidence or not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Source balance requires evidence path and SHA-256.')
        if root is not None:
            directory=Path(root).resolve();path=(directory/evidence).resolve()
            if not path.is_relative_to(directory) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('Source balance evidence hash or path is invalid.')
        points.append(point)
    before,after=points
    if not before['source_timestamp_ms']<=expected['start_ms']<=after['source_timestamp_ms'] or before['source_timestamp_ms']==after['source_timestamp_ms']:
        raise ValueError('Source balances must bracket the reviewed purchase.')
    observed={f:before['values'][f]+other[f]-after['values'][f] for f in CURRENCIES}
    wanted={f:expected['performance_cost'].get(f,0) for f in CURRENCIES}
    if observed!=wanted:raise ValueError('Reference lesson cost disagrees with its source balance labels.')
    return True


def evaluate(reference,report,root=None):
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
        stat_effects=[e for e in effects if e['kind']=='stat_change']
        fan_effects=[e for e in effects if e['kind']=='fan_change']
        predictions.append(dict(kind='concert',timestamp_ms=concert['first_seen_ms'],id=concert['id'],
                                stats={f:sum(e['amount'] for e in stat_effects if e['field']==f) for f in FIELDS}
                                    if all(e.get('field') in FIELDS and type(e.get('amount')) is int for e in stat_effects) else None,
                                fans=sum(e['amount'] for e in fan_effects)
                                    if all(type(e.get('amount')) is int for e in fan_effects) else None))
    predictions=[p for p in predictions if start<=p['timestamp_ms']<end and p['kind'] in reference['kinds']]
    used=set();matches=[];missing=[];field_errors=[];balance_checks=0
    for expected in reference['transactions']:
        if expected['kind'] not in reference['kinds']:raise ValueError('Expected transaction outside declared kinds.')
        if not start<=expected['start_ms']<=expected['end_ms']<end:raise ValueError('Expected transaction outside reference scope.')
        for key in ('performance_cost','stats','fans'):
            if key in expected and not numeric_valid(expected[key],key):
                raise ValueError('Numeric labels require known fields and integer values; omit unobserved fields.')
        for key in ('name','requested_name','receipt_name'):
            if key in expected and (expected['kind']!='lesson' or not isinstance(expected[key],str) or not expected[key].strip()):
                raise ValueError('Identity labels require a nonempty source-observed lesson name.')
        balance_checks+=validate_source_balance(expected,start,end,root)
        options=[(i,p) for i,p in enumerate(predictions) if i not in used and p['kind']==expected['kind'] and expected['start_ms']<=p['timestamp_ms']<=expected['end_ms']]
        if len(options)!=1:missing.append(expected);continue
        index,prediction=options[0];used.add(index);matches.append(prediction['id'])
        for key in ('performance_cost','stats','fans','name','requested_name','receipt_name'):
            if key not in expected:continue
            actual=prediction.get(key);wanted=expected[key]
            if key in ('performance_cost','stats','fans') and not numeric_valid(actual,key):
                field_errors.append(dict(transaction=prediction['id'],field=key,expected=wanted,actual=actual,
                                         reason='unknown_or_malformed_numeric_prediction'))
                continue
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
                source_balance_checks=balance_checks,source_balance_hashes_checked=bool(balance_checks and root is not None),
                independent_recording=False,complete_effect_recall_measured=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--evidence-root',type=Path)
    args=parser.parse_args();result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')),args.evidence_root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
