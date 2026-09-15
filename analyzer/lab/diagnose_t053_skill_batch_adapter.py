"""Run the canonical skill-batch adapter against the reviewed T053 source.

This is a bounded diagnostic.  It rebuilds only the skill-batch transaction
records from the supplied report's existing readings, then checks the source
bound committed batch fields.  Menu candidates and displayed prices are
reported as evidence only; no per-item charge is inferred.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tracen_replay.evaluation_adapters import report_document
from tracen_replay.transactions import skill_transactions


CASE_ID = 'independent-02-turn-053'
TARGET_SOURCE_ID = 'ind02-t053-skill-learn-batch'
TARGET_CONFIRMATION_MS = 1294250
CHECKED_FIELDS = (
    'visible_confirmation_names', 'confirmation_names_complete',
    'identity_status', 'post_learn_obtained_names', 'skill_points_after',
    'price_status', 'confirmation_text',
)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_target(source_path):
    source = json.loads(source_path.read_text(encoding='utf-8'))
    case = next(case for case in source['cases'] if case.get('case_id') == CASE_ID)
    target = next(row for row in case['observations']
                  if row.get('id') == TARGET_SOURCE_ID)
    return source, case, target


def _find_batch(batches):
    matches = [batch for batch in batches
               if batch.get('confirmation_first_seen_ms') == TARGET_CONFIRMATION_MS]
    if len(matches) != 1:
        raise ValueError(f'Expected one T053 skill batch, found {len(matches)}')
    return matches[0]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)

    report = json.loads(args.report.read_text(encoding='utf-8'))
    data = report['gameplay_tracking']
    batches = skill_transactions(data.get('readings', []), data.get('checkpoints', []))
    target_batch = _find_batch(batches)
    prepared = copy.deepcopy(report)
    prepared['gameplay_tracking']['skill_purchases'] = batches
    adapted = report_document(prepared)
    batch_index = batches.index(target_batch)
    row_id = f'/gameplay_tracking/skill_purchases/{batch_index}'
    row = next(row for row in adapted['observations'] if row.get('id') == row_id)

    source, case, target = _load_target(args.source)
    expected = target.get('payload', {})
    checks = {}
    for field in CHECKED_FIELDS:
        actual = row['payload'].get(field)
        wanted = expected.get(field)
        checks[field] = {
            'status': 'correct' if actual == wanted else ('missing' if actual is None else 'wrong'),
            'present': field in row['payload'],
            'actual': actual,
            'source_expected': wanted,
        }
    purchase = target_batch
    result = {
        'diagnostic': 'independent-02-t053-skill-batch-adapter-v1',
        'report_sha256': _sha256(args.report),
        'source_reference_sha256': _sha256(args.source),
        'source_recording_sha256': source.get('source_sha256'),
        'case_id': case.get('case_id'),
        'source_id': TARGET_SOURCE_ID,
        'source_interval_ms': [target.get('start_ms'), target.get('end_ms')],
        'source_evidence': target.get('evidence', []),
        'batch_index': batch_index,
        'batch_id': purchase.get('id'),
        'batch_interval_ms': [purchase.get('first_seen_ms'), purchase.get('last_seen_ms')],
        'confirmation_interval_ms': [purchase.get('confirmation_first_seen_ms'),
                                     purchase.get('confirmation_last_seen_ms')],
        'projected_payload': row['payload'],
        'projected_evidence': row['evidence'],
        'metadata_proof_paths': {
            'identity_status': purchase.get('identity_status_evidence', []),
            'post_learn_obtained_names': purchase.get(
                'post_learn_obtained_name_evidence', {}),
            'price_status': purchase.get('price_status_evidence', []),
            'confirmation_text': purchase.get('confirmation_text_evidence', []),
        },
        'field_checks': checks,
        'all_checked_fields_correct': all(
            check['status'] == 'correct' for check in checks.values()),
        'per_item_costs_projected': any(
            key in row['payload'] for key in ('selected_item_candidates', 'item_cost_sum')),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n',
                           encoding='utf-8')
    print(json.dumps({
        'output': str(args.output),
        'batch_id': purchase.get('id'),
        'all_checked_fields_correct': result['all_checked_fields_correct'],
        'per_item_costs_projected': result['per_item_costs_projected'],
    }, sort_keys=True))


if __name__ == '__main__':
    main()
