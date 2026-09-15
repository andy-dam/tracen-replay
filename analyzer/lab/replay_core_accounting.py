"""Rebuild a complete report from preserved, source-bound recovery observations."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracen_replay.full_recording import assemble
from tracen_replay.inspect_choices import load as load_choices
from tracen_replay.report_contract import validate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    protected = {str(p): digest(p) for p in (args.report, args.observations, args.config)}
    package = Path(__file__).resolve().parents[1]/'tracen_replay'
    code = {p.name: digest(p) for p in package.glob('*.py')}
    before = json.loads(args.report.read_text(encoding='utf-8'))
    observations = json.loads(args.observations.read_text(encoding='utf-8'))
    config = json.loads(args.config.read_text(encoding='utf-8'))
    if (observations['input_report_sha256'] != protected[str(args.report)]
            or observations['source_sha256'] != before['source']['sha256']):
        raise ValueError('Observations are not bound to this baseline report')
    _, choices = load_choices(Path(config['cache_root']), before['source']['sha256'])
    data = before['gameplay_tracking']
    replay_input = copy.deepcopy(before)
    # The earlier snapshot cannot certify newly sampled recovery evidence.
    # The baseline report preserves it; the new audit covers the additions.
    replay_input.pop('evidence_integrity_snapshot', None)
    source_root = Path(config['cache_root']).resolve()
    after = assemble(replay_input, observations['readings'], choices,
                     data.get('race_reward_observations', []), data.get('hint_card_observations', []),
                     source_root=source_root)
    after['numeric_receipt_recovery'] = observations['metadata']
    after['evaluation_context'] = dict(evidence_root=str(Path(config['cache_root']).resolve()),
                                       development_recording=True)
    validate(after, require_gameplay=True, source_root=source_root)
    if any(digest(Path(p)) != value for p, value in protected.items()):
        raise ValueError('Replay inputs changed')
    if code != {p.name: digest(p) for p in package.glob('*.py')}:
        raise ValueError('Implementation changed during replay')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(after, stream, ensure_ascii=False)
    audit = dict(source_sha256=before['source']['sha256'], input_sha256=protected,
                 implementation_sha256=code, output_sha256=digest(args.output),
                 prior_integrity_snapshot_carried_forward=False,
                 observations=len(observations['readings']),
                 scope='Full reconstruction from existing observations plus bounded receipt recovery; development data, not held-out validation.')
    with args.output.with_suffix('.audit.json').open('x', encoding='utf-8') as stream:
        json.dump(audit, stream, indent=2)
    print(json.dumps(after['causal_accounting']['summary']))


if __name__ == '__main__':
    main()
