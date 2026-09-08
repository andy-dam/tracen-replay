"""Evaluate manually reviewed OCR candidates without claiming complete effect recall."""
import argparse
import hashlib
import json
from pathlib import Path


def name_key(value):
    return value.rstrip(' O○◯◎!')


def evaluate(reference,report,root=None):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source recording.')
    data=report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Gameplay-only report required.')
    results=[]
    for item in reference['items']:
        expected=item['expected_effect'];time=item['source_timestamp_ms'];matches=[]
        for event in data['events']:
            if event['first_seen_ms']>time+750 or event['last_seen_ms']<time-750:continue
            for effect in event.get('effects',[]):
                key='|'.join(str(value or '') for value in (effect['kind'],effect.get('field'),effect.get('name')))
                if any(c.get('field')==key for c in event.get('conflicting_readings',[])):continue
                def equal(key,value):
                    actual=effect.get(key)
                    if key=='name' and expected['kind']=='skill_hint_change' and isinstance(actual,str):
                        return name_key(actual)==name_key(value)
                    return actual==value
                if all(equal(k,v) for k,v in expected.items()):matches.append(event['id']);break
        proof_ok=None
        if root is not None:
            directory=Path(root).resolve();path=(directory/item['evidence']).resolve()
            if not path.is_relative_to(directory):raise ValueError('Evidence path leaves run directory.')
            proof_ok=path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==item['evidence_sha256']
        results.append(dict(index=item['index'],source_timestamp_ms=time,matched_event_ids=matches,
                            evidence_hash_matches=proof_ok,passed=bool(matches) and proof_ok is not False))
    return dict(source_sha256=reference['source_sha256'],scope=reference['scope'],items=results,
        reviewed_candidates=len(results),matched_candidates=sum(x['passed'] for x in results),
        passed=all(x['passed'] for x in results),complete_effect_recall_measured=False,independent_recording=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path)
    parser.add_argument('--evidence-root',type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')),args.evidence_root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
