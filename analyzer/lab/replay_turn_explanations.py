"""Reconstruct a preserved full report and inventory with current analyzer code."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracen_replay.full_recording import assemble
from tracen_replay.inspect_choices import load
from tracen_replay.report_contract import validate
from inventory_turn_explanations import inventory


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path);parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--observations',type=Path)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    before_hash=digest(args.report); config_hash=digest(args.config)
    code={str(p):digest(p) for p in Path('tracen_replay').glob('*.py')}
    before=json.loads(args.report.read_text(encoding='utf-8')); data=before['gameplay_tracking']
    rows=data['readings'];observations_hash=None
    if args.observations:
        observations_hash=digest(args.observations)
        supplemental=json.loads(args.observations.read_text(encoding='utf-8'))
        if supplemental['input_report_sha256']!=before_hash or supplemental['source_sha256']!=before['source']['sha256']:
            raise ValueError('Supplemental observations belong to another starting report')
        rows=supplemental['readings']
    config=json.loads(args.config.read_text(encoding='utf-8'))
    source_root=Path(config['cache_root']).resolve()
    _,choices=load(source_root,before['source']['sha256'])
    after=assemble(before,rows,choices,data.get('race_reward_observations',[]),data.get('hint_card_observations',[]),
                   source_root=source_root)
    if args.observations:
        metadata=supplemental['metadata']
        if 'boundary_state_recovery' in metadata:
            after['training_gain_recovery']=metadata['training_gain_recovery']
            after['boundary_state_recovery']=metadata['boundary_state_recovery']
        else:after['training_gain_recovery']=metadata
    validate(after,require_gameplay=True,source_root=source_root)
    summary=inventory(after)
    if (digest(args.report)!=before_hash or digest(args.config)!=config_hash
        or (args.observations and digest(args.observations)!=observations_hash)
        or code!={str(p):digest(p) for p in Path('tracen_replay').glob('*.py')}):
        raise ValueError('Replay inputs or implementation changed')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(after,stream,ensure_ascii=False)
    with args.output.with_suffix('.inventory.json').open('x',encoding='utf-8') as stream:json.dump(summary,stream,indent=2,ensure_ascii=False)
    with args.output.with_suffix('.audit.json').open('x',encoding='utf-8') as stream:
        json.dump(dict(input_report_sha256=before_hash,config_sha256=config_hash,implementation_sha256=code,
                       supplemental_observations_sha256=observations_hash,
                       output_sha256=digest(args.output),scope='Full reconstruction from preserved observations and source-bound training/boundary probes; no held-out validation.'),stream,indent=2)
    print(json.dumps(summary['summary']))


if __name__=='__main__':main()
