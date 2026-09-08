"""Recheck visually reviewed receipt identities without claiming missed-event recall."""
import argparse
import hashlib
import json
from pathlib import Path


def evaluate(reference,report,root=None):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source recording.')
    data=report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Gameplay-only report required.')
    errors=[];matched=0
    actions=data['turn_action_receipts']
    for expected in reference['actions']:
        found=[a for a in actions if a['source_timestamp_ms']==expected['source_timestamp_ms']]
        if len(found)!=1 or any(found[0].get(k)!=v for k,v in expected['expected'].items()):
            errors.append(dict(source_timestamp_ms=expected['source_timestamp_ms'],reason='receipt_identity_mismatch'));continue
        matched+=1
    reviewed_times={a['source_timestamp_ms'] for a in reference['actions']}
    additional=[a for a in actions if a['source_timestamp_ms'] not in reviewed_times]
    gains=reference.get('training_gain_reviews',[])
    for gain in gains:
        found=[e for e in data.get('events',[]) if e['kind']=='training' and e['first_seen_ms']<=gain['source_timestamp_ms']<=e['last_seen_ms']]
        if len(found)!=1 or found[0]['deltas'].get(gain['field'])!=gain['amount']:
            errors.append(dict(source_timestamp_ms=gain['source_timestamp_ms'],reason='reviewed_gain_mismatch'))
    if root is not None:
        root=Path(root).resolve()
        for item in reference['actions']+gains:
            path=(root/item['evidence']).resolve()
            if not path.is_relative_to(root):raise ValueError('Evidence path leaves run directory.')
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['evidence_sha256']:
                errors.append(dict(evidence=item['evidence'],reason='reviewed_evidence_changed'))
    return dict(source_sha256=reference['source_sha256'],scope=reference['scope'],
        reviewed_receipts=len(reference['actions']),matched_receipts=matched,reviewed_gains=len(gains),
        additional_unreviewed_receipts=additional,errors=errors,passed=not errors and not additional,
        missed_action_recall_measured=False,complete_effect_recall_measured=False,independent_recording=False,
        reviewed_evidence_hashes_checked=root is not None)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path)
    parser.add_argument('--evidence-root',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')),args.evidence_root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
