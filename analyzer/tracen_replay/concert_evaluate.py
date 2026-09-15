"""Score visually reviewed current/planned panels without certifying activation."""
import argparse
import hashlib
import json
from pathlib import Path


def evaluate(reference,report,root):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source.')
    data=report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Gameplay-only report required.')
    root=Path(root).resolve();checks=[]
    for item in reference['items']:
        proof=(root/item['evidence']).resolve()
        if not proof.is_relative_to(root):raise ValueError('Evidence leaves run directory.')
        if hashlib.sha256(proof.read_bytes()).hexdigest()!=item['evidence_sha256']:raise ValueError('Reviewed proof changed.')
        rows=[r for r in data['readings'] if r['source_timestamp_ms']==item['source_timestamp_ms'] and r['evidence']==item['evidence']]
        for column in ('current','planned'):
            for field,expected in item[column].items():
                actual=rows[0]['facts'].get(column+'_concert_bonuses',{}).get(field) if len(rows)==1 else None
                checks.append(dict(timestamp_ms=item['source_timestamp_ms'],column=column,field=field,
                                   expected=expected,actual=actual,status='matched' if actual==expected else 'missing' if actual is None else 'incorrect'))
    counts={status:sum(c['status']==status for c in checks) for status in ('matched','missing','incorrect')}
    return dict(scope=reference['scope'],checks=checks,**counts,expected=len(checks),passed=counts['matched']==len(checks),
                full_panel_recall_measured=False,all_active_totals_verified=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path)
    parser.add_argument('--evidence-root',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')),args.evidence_root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='checks'}))
    raise SystemExit(0 if result['passed'] else 1)
