"""Prepare bounded state probes on a current reconstruction of a preserved report."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracen_replay.boundary_state_recovery import recover
from tracen_replay.full_recording import assemble
from tracen_replay.inspect_choices import load


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--observations',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reparse-only',action='store_true')
    args=parser.parse_args()
    raw=args.report.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    observation_raw=args.observations.read_bytes();observations=json.loads(observation_raw)
    report=json.loads(raw);source_sha=report['source']['sha256']
    if observations['input_report_sha256']!=digest or observations['source_sha256']!=source_sha:
        raise ValueError('Supplemental observations belong to another starting report')
    config=json.loads(args.config.read_text(encoding='utf-8'))
    source_root=Path(config['cache_root']).resolve()
    data=report['gameplay_tracking']
    _,choices=load(source_root,source_sha)
    report=assemble(report,observations['readings'],choices,
                    data.get('race_reward_observations',[]),data.get('hint_card_observations',[]),
                    source_root=source_root)
    rows,metadata=recover(config['source_video'],config['cache_root'],report,
                          report['gameplay_tracking']['readings'],allow_ocr=not args.reparse_only)
    if args.report.read_bytes()!=raw or args.observations.read_bytes()!=observation_raw:
        raise ValueError('Starting report or observations changed')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(dict(input_report_sha256=digest,source_sha256=source_sha,
            prior_observations_sha256=hashlib.sha256(observation_raw).hexdigest(),readings=rows,
            metadata=dict(training_gain_recovery=observations['metadata'],boundary_state_recovery=metadata)),
            stream,ensure_ascii=False)
    print(json.dumps({k:v for k,v in metadata.items()
                      if k not in ('requested_windows','processed_windows','pending_windows','candidate_windows','observed_ocr_models')},ensure_ascii=True))


if __name__=='__main__':main()
