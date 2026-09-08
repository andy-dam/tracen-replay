"""Score visible choices in a declared, bounded source-reference window."""
import argparse
import hashlib
import json
from pathlib import Path


def evaluate(reference,report,root=None):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Choice reference source mismatch.')
    if report['gameplay_tracking'].get('auxiliary_log_used') is not False:raise ValueError('Gameplay-only report required.')
    expected=reference['choices']
    times=[e['source_timestamp_ms'] for c in expected for e in c['evidence']]
    if not times:raise ValueError('Reference requires timestamped evidence.')
    start,end=min(times),max(times)
    predictions=[c for c in report['gameplay_tracking'].get('dialogue_choices',[]) if start<=c['selection_observed_ms']<=end]
    used=set();results=[]
    for choice in expected:
        low=min(e['source_timestamp_ms'] for e in choice['evidence']);high=max(e['source_timestamp_ms'] for e in choice['evidence'])
        matches=[(i,c) for i,c in enumerate(predictions) if i not in used and low<=c['selection_observed_ms']<=high]
        errors=[]
        if len(matches)!=1:errors.append('missing_or_ambiguous_selection')
        else:
            i,p=matches[0];used.add(i)
            for key in ('options','selected_index','selected_text'):
                if p.get(key)!=choice[key]:errors.append(key+'_mismatch')
            kind='dialogue_choice' if len(choice['options'])>1 else 'dialogue_response'
            if p.get('kind')!=kind:errors.append('response_kind_mismatch')
            if not p.get('selection_marks') or not p.get('evidence'):errors.append('missing_selection_evidence')
        if root is not None:
            base=Path(root).resolve()
            for evidence in choice['evidence']:
                path=(base/evidence['evidence']).resolve()
                if not path.is_relative_to(base):raise ValueError('Choice evidence leaves run directory.')
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=evidence['sha256']:
                    errors.append('reference_proof_changed')
        results.append(dict(selected_text=choice['selected_text'],passed=not errors,errors=errors))
    extras=[p for i,p in enumerate(predictions) if i not in used]
    return dict(scope=reference['scope'],start_ms=start,end_ms=end,matched=sum(r['passed'] for r in results),expected=len(expected),
                results=results,extra_predictions=extras,passed=all(r['passed'] for r in results) and not extras,
                full_recording_choice_recall_measured=False,reference_proof_hashes_checked=root is not None)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference',type=Path);parser.add_argument('report',type=Path)
    parser.add_argument('--evidence-root',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),json.loads(args.report.read_text(encoding='utf-8')),args.evidence_root)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
