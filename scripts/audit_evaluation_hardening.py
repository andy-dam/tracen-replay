"""Verify preservation, final accounting, comparisons and compact fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracen_replay.causal_accounting import build
from tracen_replay.report_contract import validate
from compare_hardening_reports import compare


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.local/evaluation-hardening-v1'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    root=args.root.resolve();before=root/'before';manifest=read(before/'manifest.json')
    preserved={name:sha(before/name) for name in manifest['files_sha256']}
    if preserved!=manifest['files_sha256']:raise ValueError('Preserved starting snapshot changed')
    protected=[name for name in preserved if name.startswith(('.local/full-run-baseline-v1/','.local/full-recording/','docs/full-run-baseline'))]
    if any(sha(repo/name)!=preserved[name] for name in protected):raise ValueError('Historical baseline artifact changed')
    if any(sha(repo/name)!=value for name,value in manifest['unrelated_worktree_sha256'].items()):
        raise ValueError('Unrelated worktree changes were modified')
    artifacts={}
    results=[]
    for run in ('v1','independent-01','independent-02'):
        original=before/'.local/full-recording'/run/'go-integration-replay-v3/candidate-report.json'
        current=root/'final'/f'{run}-report.json'
        comparison=root/'final'/f'{run}-comparison-v3.json'
        corpus=root/f'{run}-corpus.json'
        b,a=read(original),read(current)
        validate(a,require_gameplay=True)
        if build(a)!=a['causal_accounting']:raise ValueError('Final accounting cannot be reproduced')
        expected=compare(b,a,read(corpus)['references'])
        saved=read(comparison)
        if any(saved.get(k)!=value for k,value in expected.items()):raise ValueError('Final comparison cannot be reproduced')
        for name,value in saved['evaluator_code_sha256'].items():
            if sha(repo/'tracen_replay'/name)!=value:raise ValueError('Final evaluator changed after grading')
        for path in (original,current,comparison,corpus):artifacts[str(path.relative_to(repo))]=sha(path)
        for reference in read(corpus)['references']:
            for key in ('reference','amendments'):
                if reference.get(key):artifacts[reference[key]]=sha(repo/reference[key])
        results.append(dict(recording=run,report_contract_valid=True,accounting_reproduced=True,comparison_reproduced=True,
            selected_actions_unchanged=saved['selected_actions_unchanged'],numeric_events_unchanged=saved['numeric_event_changes_unchanged'],
            removed_effects=len(saved['effects']['removed']),added_effects=len(saved['effects']['added']),
            training_outcomes=saved['training_outcomes'],accounting_issues=len(a['causal_accounting']['issues'])))
    for path in (repo/'tests/fixtures').glob('*hardening*.json'):artifacts[str(path.relative_to(repo))]=sha(path)
    for name in ('receipt-fixed-grammar-484750.json','receipt-boundary-obstructions.json'):
        path=repo/'tests/fixtures'/name;artifacts[str(path.relative_to(repo))]=sha(path)
    output=dict(status='verified',preserved_files=len(preserved),historical_artifacts_unchanged=len(protected),
        unrelated_worktree_hashes_unchanged=manifest['unrelated_worktree_sha256'],results=results,artifacts_sha256=artifacts,
        source_coverage='Three complete development-recording replays plus explicit partial source references. No exhaustive or held-out recognition claim.')
    with args.output.open('x',encoding='utf-8') as stream:json.dump(output,stream,indent=2)
    print(json.dumps(dict(status=output['status'],preserved_files=len(preserved),historical_artifacts_unchanged=len(protected),recordings=len(results))))


if __name__=='__main__':main()
