"""Diagnose the bounded independent-02 T004 adapter regression.

The diagnostic binds an event to the source result frame by its report-owned
path and timestamp.  It reports the four producer fields and the adapter's
separate amount/phase evidence; it does not read source labels or choose an
amount from an endpoint balance.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tracen_replay.evaluation_adapters import evidence_ids, report_document


RESULT_PATH = 'initial-baseline/gameplay/part-001-frame-000142.png'
RESULT_TIMESTAMP_MS = 155250
FIELDS = ('speed', 'power', 'guts', 'skill_points')


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _contains_path(value, wanted):
    return wanted in evidence_ids(value)


def _find_event(report):
    """Find the one training event owning the reviewed result frame."""
    data = report['gameplay_tracking']
    candidates = []
    for index, event in enumerate(data.get('events', [])):
        if event.get('kind') != 'training':
            continue
        group = event.get('result_group')
        members = group.get('observations', []) if isinstance(group, dict) else []
        if any(isinstance(member, dict)
               and member.get('source_timestamp_ms') == RESULT_TIMESTAMP_MS
               and _contains_path(member.get('evidence'), RESULT_PATH)
               for member in members):
            candidates.append((index, event, 'result_group'))
    if not candidates:
        # Baseline reports predate result_group.  Keep the same source path and
        # timestamp binding, with the event span as a structural fallback.
        for index, event in enumerate(data.get('events', [])):
            if event.get('kind') != 'training':
                continue
            start, end = event.get('first_seen_ms'), event.get('last_seen_ms')
            if (type(start) is int and type(end) is int and start <= RESULT_TIMESTAMP_MS <= end
                    and any(_contains_path(paths, RESULT_PATH)
                            for paths in event.get('field_evidence', {}).values())):
                candidates.append((index, event, 'legacy_field_evidence'))
    if len(candidates) != 1:
        raise ValueError(f'Expected one source-bound T004 event, found {len(candidates)}')
    return candidates[0]


def _row_map(adapted, event_index):
    prefix = f'/gameplay_tracking/events/{event_index}/deltas/'
    return {
        row['payload']['field']: row
        for row in adapted.get('observations', [])
        if isinstance(row, dict) and row.get('id', '').startswith(prefix)
        and row.get('payload', {}).get('field') in FIELDS
    }


def _summarize(report_path):
    with report_path.open(encoding='utf-8') as handle:
        report = json.load(handle)
    event_index, event, binding = _find_event(report)
    adapted = report_document(report)
    rows = _row_map(adapted, event_index)
    fields = {}
    for field in FIELDS:
        row = rows.get(field)
        provenance = event.get('direct_gain_provenance', {}).get(field)
        fields[field] = {
            'producer_amount': event.get('deltas', {}).get(field),
            'producer_direct_basis': (provenance or {}).get('basis'),
            'adapter_row_present': row is not None,
            'adapter_amount': (row or {}).get('payload', {}).get('amount'),
            'adapter_interval_ms': ([row['start_ms'], row['end_ms']]
                                    if row is not None else None),
            'amount_evidence': (row or {}).get('amount_evidence', []),
            'phase_evidence': (row or {}).get('phase_evidence', []),
            'result_path_in_phase_evidence': RESULT_PATH in (row or {}).get(
                'phase_evidence', []),
            'amount_preserved': row is not None and row.get('payload', {}).get(
                'amount') == event.get('deltas', {}).get(field),
        }
    group = event.get('result_group')
    group_summary = None
    if isinstance(group, dict):
        group_summary = {
            'interval_ms': group.get('interval_ms'),
            'training_option': group.get('training_option'),
            'observations': [
                {
                    'source_timestamp_ms': member.get('source_timestamp_ms'),
                    'evidence': evidence_ids(member.get('evidence')),
                    'training_option': member.get('training_option'),
                    'screen': member.get('screen'),
                }
                for member in group.get('observations', [])
                if isinstance(member, dict)
            ],
        }
    return {
        'report_sha256': _sha256(report_path),
        'source_sha256': report.get('source', {}).get('sha256'),
        'event_index': event_index,
        'event_id': event.get('id'),
        'event_training_option': event.get('training_option'),
        'event_binding': binding,
        'result_group': group_summary,
        'source_result': {
            'evidence': RESULT_PATH,
            'timestamp_ms': RESULT_TIMESTAMP_MS,
        },
        'fields': fields,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    candidate = _summarize(args.candidate)
    baseline = _summarize(args.baseline)
    result = {
        'diagnostic': 'independent-02-t004-adapter-amount-vs-phase-v1',
        'binding': {
            'evidence': RESULT_PATH,
            'timestamp_ms': RESULT_TIMESTAMP_MS,
            'fields': list(FIELDS),
            'amount_source': 'producer direct_gain_provenance',
            'phase_source': 'report-owned result_group after direct gain',
        },
        'baseline': baseline,
        'candidate': candidate,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n',
                           encoding='utf-8')
    print(json.dumps({
        'output': str(args.output),
        'candidate_event': candidate['event_id'],
        'candidate_phase_bound_fields': [
            field for field, details in candidate['fields'].items()
            if details['result_path_in_phase_evidence']],
    }, sort_keys=True))


if __name__ == '__main__':
    main()
