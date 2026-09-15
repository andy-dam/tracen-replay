"""Reproduce the original-run evaluation using its preserved local evidence.

This runs evaluators only. It does not rerun OCR, edit the analyzer, relabel
screenshots, or reseal source labels. A successful execution verifies artifact
reproduction and integrity, not exhaustive event recall or visual correctness.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from baseline_status import SECTIONS, inspect


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    repo = Path(__file__).resolve().parents[2]
    root = repo/'.local/full-run-baseline-v1'
    load = lambda p: json.loads(p.read_text(encoding='utf-8'))
    freeze = load(root/'freeze.json')
    source = Path(freeze['source_path'])
    assert source.stat().st_size == freeze['source']['size_bytes']
    assert digest(source) == freeze['source_sha256'], 'Original recording changed'
    protected = [root/'frozen-report.json']
    protected += [root/name/file for name, _, _ in SECTIONS for file in ('labels.json','seal.json')]
    protected += [repo/path for path in freeze['analyzer_code_sha256']]
    before = {str(p.relative_to(repo)): digest(p) for p in protected}
    original_score = load(root/'combined-scorecard.json')
    original_inputs = original_score['inputs_sha256']
    logs = root/'reproduction-logs'
    logs.mkdir(exist_ok=True)
    r = '.local/full-run-baseline-v1'
    plan = []
    for name, _, _ in SECTIONS:
        plan.append(['analyzer/lab/audit_baseline_sources.py',r,'--shard',name,'--output',f'{r}/{name}/integrity-audit.json'])
    plan.append([f'{r}/source-a/grade_source_a.py'])
    for name in ('middle-a','tail-a','source-b','tail-c','source-d'):
        plan.append([f'{r}/source_balance_check.py','--shard',name] + (['--normalize'] if name=='source-b' else []))
        plan.append([f'{r}/grade_source_d.py','--shard',name])
    plan += [[f'{r}/grade_middle_a.py'], [f'{r}/grade_tail_a.py'],
             [f'{r}/source-b/adjudicate_effects.py'], [f'{r}/source-b/adjudicate_states_actions.py'],
             [f'{r}/source-b/grade_purchases.py'], [f'{r}/source-b/audit_balances.py'],
             [f'{r}/source-b/build_scorecard.py'],
             [f'{r}/grade_source_d.py','--shard','source-c'],
             [f'{r}/source-c/audit_source_c_balances.py'], [f'{r}/source-c/adjudicate_source_c.py'],
             [f'{r}/grade_tail_turn_states.py'],
             [f'{r}/grade_source_balances.py','--shard','tail-c'],
             [f'{r}/grade_source_balances.py','--shard','source-d'],
             [f'{r}/grade_effect_context.py'], [f'{r}/audit_boundary_balances.py'],
             ['analyzer/lab/audit_baseline_reference_density.py',r,'--output',f'{r}/reference-density.json'],
             ['analyzer/lab/audit_baseline_occurrences.py',r,'--output',f'{r}/occurrence-audit.json'],
             ['analyzer/lab/build_baseline_scorecard.py',r,'--output',f'{r}/combined-scorecard.json','--markdown','docs/full-run-baseline-scorecard.md']]
    records = []
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    for index, args in enumerate(plan,1):
        started = time.monotonic()
        result = subprocess.run([sys.executable,*args],cwd=repo,env=env,capture_output=True)
        log = logs/f'{index:02}-{Path(args[0]).stem}.log'
        log.write_bytes(result.stdout + b'\nSTDERR:\n' + result.stderr)
        records.append({'argv':args,'exit_code':result.returncode,'elapsed_seconds':round(time.monotonic()-started,3),
                        'log':str(log.relative_to(root)),'log_sha256':digest(log),'evaluator_sha256':digest(repo/args[0])})
        print(json.dumps({'step':index,'total':len(plan),'script':args[0],'exit_code':result.returncode}),flush=True)
        if result.returncode:
            (root/'reproduction-failure.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')
            raise SystemExit(f'Evaluator failed; inspect {log}')
    state = inspect(root,repo)
    assert not state['freeze_errors']
    assert all(not s['pending_artifact_checks'] for s in state['sections'])
    after = {str(p.relative_to(repo)):digest(p) for p in protected}
    assert before == after, 'Protected analyzer/report/source/seal changed'
    score = load(root/'combined-scorecard.json')
    changes = {p:{'before':h,'after':score['inputs_sha256'].get(p)} for p,h in original_inputs.items() if score['inputs_sha256'].get(p)!=h}
    assert [x['metrics'] for x in original_score['sections']] == [x['metrics'] for x in score['sections']], 'Score metrics changed on reproduction'
    audit = load(root/'occurrence-audit.json')
    assert not audit['pending_sections'] and not audit['cross_section_reuse']
    output = {'status':'reproduced','source_sha256':freeze['source_sha256'],
              'protected_artifact_sha256':after,'grade_artifacts_changed_on_reproduction':changes,
              'metric_tables_unchanged':True,'commands':records,'integrity':state,
              'limitations':['This executes preserved adjudications; it does not repeat independent visual review.',
                             'Local sealed labels, QA amendments, screenshots, frozen report and evaluators are prerequisites.',
                             'Success does not imply exhaustive native-frame recall, precision or complete event attribution.']}
    (root/'reproduction-audit.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'reproduced','commands':len(records),'changed_grade_artifacts':changes}))


if __name__=='__main__':
    main()
