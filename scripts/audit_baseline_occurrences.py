"""Audit cross-section reuse of matched actions and atomic effects.

This checks evaluator joins, not source recall or prediction precision. Event
objects may span sections; individual effect IDs may not be counted twice.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from baseline_status import SECTIONS, digest


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def inspect(root):
    records = []
    pending = []
    artifacts = {}
    for section, _, _ in SECTIONS:
        folder = root / section
        if not (folder / 'seal.json').exists():
            pending.append(section)
            continue
        path = folder / 'adjudicated-grade.json'
        if not path.exists() or 'results' not in load(path):
            path = folder / 'draft-grade.json'
        if not path.exists():
            pending.append(section)
            continue
        artifacts[str(path.relative_to(root))] = digest(path)
        rows = {row['label_id']: dict(row) for row in load(path)['results']}
        if section == 'source-b':
            ap, ep = folder/'states-actions-grade.json', folder/'effect-grade.json'
            if not ap.exists() or not ep.exists():
                pending.append(section)
                continue
            artifacts[str(ap.relative_to(root))] = digest(ap)
            artifacts[str(ep.relative_to(root))] = digest(ep)
            # Only adjudicated committed actions, excluding staged concerts.
            rows = {row['source_label_id']: {'label_id': row['source_label_id'], 'category': 'action',
                    'matched_prediction': row['actual'].get('event_id')}
                    for row in load(ap)['actions']['rows']}
            for effect in load(ep)['rows']:
                for i, atom in enumerate(effect.get('report_atomic_ids') or []):
                    if effect['explicit_miss']:
                        continue
                    key = effect['label_id']+f'/join/{i}'
                    rows[key] = {'label_id': effect['label_id'], 'category': 'effect', 'matched_prediction': atom}
        # Explicit adjudications retain their original strict result separately.
        if section == 'source-c':
            ap = folder / 'adjudications.json'
            adjudicated = load(ap)['adjudicated']
            artifacts[str(ap.relative_to(root))] = digest(ap)
            joins = adjudicated['action']['manual_joins'] + adjudicated['effect']['manual_event_joins']
            for join in joins:
                rows[join['label_id']]['matched_prediction'] = join['prediction_id']
        if section == 'middle-a':
            for action in load(path)['actions']:
                rows[action['label_id']]['matched_prediction'] = action['event_id']
        for row in rows.values():
            prediction = row.get('matched_prediction')
            category = row['category']
            if not isinstance(prediction, str):
                continue
            if category == 'action' or category == 'effect' and '/' in prediction:
                records.append({'section': section, 'category': category,
                                'source_label_id': row['label_id'],
                                'prediction_id': prediction})
    groups = defaultdict(list)
    for row in records:
        groups[(row['category'], row['prediction_id'])].append(row)
    duplicates = [rows for rows in groups.values()
                  if len({row['section'] for row in rows}) > 1]
    return {'artifact_sha256': artifacts, 'pending_sections': pending,
            'checked_join_count': len(records), 'cross_section_reuse': duplicates,
            'rows': records, 'scope_complete': not pending,
            'limitations': [
                'Event-level effect joins without an atomic suffix are excluded.',
                'Joins are evaluator records; this is not an independent correctness grade.',
                'Different report representations of the same source occurrence may have different IDs.',
                'State snapshots and purchase component/bundle overlap require separate semantic review.',
                'No semantic completion or whole-run accuracy follows from zero duplicate IDs.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.review_dir)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ['rows', 'artifact_sha256']}))
    if result['cross_section_reuse']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
