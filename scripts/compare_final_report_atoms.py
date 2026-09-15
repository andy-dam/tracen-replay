"""Inventory changed canonical actions/effects; never certify changes by balance."""

import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def atoms(report):
    """Use each report's stored accounting, including the original baseline."""
    accounting = report['causal_accounting']
    result = []
    events = {f'/gameplay_tracking/events/{i}': event
              for i, event in enumerate(report['gameplay_tracking'].get('events', []))}
    numeric_keys = ('channel', 'field', 'amount', 'observation_start_ms', 'observation_end_ms',
                    'basis', 'conflicts_present', 'turn_id', 'candidate_turn_ids', 'turn_assignment_basis')
    for row in accounting['contributions']:
        result.append(dict(category='numeric', payload={k: deepcopy(row[k]) for k in numeric_keys if k in row},
                           source_ref=row.get('source_ref', row.get('id')), evidence=deepcopy(row.get('evidence', []))))
    for row in accounting['other_effects']:
        effect = {k: deepcopy(v) for k, v in row['effect'].items() if k not in ('raw_text', 'confidence')}
        owner = events.get(row.get('event_ref'), {})
        payload = {'effect': effect, 'owner': {k: deepcopy(owner[k]) for k in
                   ('kind', 'first_seen_ms', 'last_seen_ms', 'turn_id', 'candidate_turn_ids') if k in owner}}
        result.append(dict(category='other_effect', payload=payload, source_ref=row['source_ref'],
                           evidence=deepcopy(row.get('evidence', []))))
    for i, row in enumerate(report['gameplay_tracking']['turn_action_receipts']):
        fields = ('kind', 'training_option', 'source_timestamp_ms', 'training_outcome',
                  'training_name', 'training_name_conflicts', 'companion', 'turn_id', 'candidate_turn_ids')
        result.append(dict(category='action', payload={k: deepcopy(row[k]) for k in fields if k in row},
                           source_ref=f'/gameplay_tracking/turn_action_receipts/{i}',
                           evidence=deepcopy(row.get('evidence', []))))
    return result


def compare(before, after):
    if before['source']['sha256'] != after['source']['sha256']:
        raise ValueError('Cannot compare different recordings')
    groups = []
    for report in (before, after):
        grouped = defaultdict(list)
        for atom in atoms(report):
            grouped[key([atom['category'], atom['payload']])].append(atom)
        groups.append(grouped)
    old, new = groups
    removed, added, changed_proof = [], [], []
    retained = 0
    for signature in sorted(old.keys() | new.keys()):
        left, right = list(old[signature]), list(new[signature])
        # Match identical evidence first to avoid unstable duplicate pairing.
        for prior in list(left):
            match = next((current for current in right if current['evidence'] == prior['evidence']), None)
            if match is not None:
                left.remove(prior)
                right.remove(match)
                retained += 1
        while left and right:
            changed_proof.append(dict(before=left.pop(0), after=right.pop(0),
                                      review_status='source_review_required'))
        removed.extend(left)
        added.extend(right)
    return dict(source_sha256=before['source']['sha256'], retained=retained,
                added=added, removed=removed, evidence_changes=changed_proof,
                review_required=bool(added or removed or changed_proof),
                scope='Stored canonical numeric contributions, other effects, and selected actions; no net-balance shortcut.',
                limits=['This inventory does not verify that any changed output is source-correct.',
                        'State snapshots, purchases, inventories and observation metadata require their separate report comparisons.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = [path.read_bytes() for path in (args.before, args.after)]
    result = compare(*(json.loads(data) for data in inputs))
    result['input_sha256'] = {str(path): hashlib.sha256(data).hexdigest()
                              for path, data in zip((args.before, args.after), inputs)}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k: len(result[k]) for k in ('added', 'removed', 'evidence_changes')}))


if __name__ == '__main__':
    main()
