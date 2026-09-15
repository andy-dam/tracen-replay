"""Evaluate immutable action references with explicit, disjoint per-kind scopes.

An index lists source-relative reference paths, SHA-256 hashes and optionally
``selected_kinds`` (a subset of each reference's declared kinds). This selects
which channels contribute to the corpus without changing source labels or
inventing negative references. Different kinds may cover the same interval;
two selected scopes for the same kind may not overlap.

File hashes establish reference integrity, not visual annotation correctness.
Sample completeness and agreement never establish native-frame semantic recall.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .action_evaluate import ACTION_KINDS, evaluate

SCHEMA = 'tracen-replay/action-corpus-v1'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path.name}')
    return value


def _merge(spans: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def score_corpus(index_path: Path, report_path: Path, evidence_root: Path) -> dict[str, Any]:
    index_path, report_path = Path(index_path), Path(report_path)
    root = Path(evidence_root).resolve()
    index, report = _read(index_path), _read(report_path)
    if index.get('schema') != SCHEMA:
        raise ValueError('Unsupported action corpus schema.')
    source = report.get('source')
    if not isinstance(source, dict) or index.get('source_sha256') != source.get('sha256'):
        raise ValueError('Different source recordings.')
    if not isinstance(index.get('source_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', index['source_sha256']):
        raise ValueError('A source SHA-256 hash is required.')
    duration = source.get('duration_ms')
    if (type(duration) is not int or duration <= 0
            or type(index.get('source_duration_ms')) is not int
            or index['source_duration_ms'] != duration):
        raise ValueError('Corpus and report must declare the same positive source duration.')
    entries = index.get('references')
    if not isinstance(entries, list) or not entries:
        raise ValueError('A nonempty reference list is required.')
    scored = []
    scopes: dict[str, list[dict[str, Any]]] = {kind: [] for kind in ACTION_KINDS}
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not entry['path']:
            raise ValueError(f'Invalid reference entry {entry_index}.')
        relative = Path(entry['path'])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            raise ValueError('Reference path must stay relative to the evidence root.')
        if not path.is_file() or _sha(path) != entry.get('sha256'):
            raise ValueError(f'Reference file hash mismatch: {entry["path"]}')
        reference = _read(path)
        # Validate and evaluate the original reference, retaining its entire
        # result. Channel selection below never rewrites its source labels.
        result = evaluate(reference, report)
        kinds = entry.get('selected_kinds', reference['kinds'])
        if (not isinstance(kinds, list) or not kinds
                or any(kind not in reference['kinds'] for kind in kinds)
                or len(set(kinds)) != len(kinds)):
            raise ValueError('Selected kinds must be a nonempty unique subset of the source reference scope.')
        scored.append({'path': entry['path'], 'sha256': entry['sha256'],
                       'selected_kinds': list(kinds), 'original_reference_score': result})
        for kind in kinds:
            scopes[kind].append({'path': entry['path'], 'start_ms': result['start_ms'],
                                 'end_ms': result['end_ms'],
                                 'reference_complete': result['reference_complete']})
    for kind, selected in scopes.items():
        selected.sort(key=lambda row: (row['start_ms'], row['end_ms']))
        for previous, current in zip(selected, selected[1:]):
            if current['start_ms'] < previous['end_ms']:
                raise ValueError(f'Overlapping {kind} scopes: {previous["path"]} and {current["path"]}')

    receipts = report['gameplay_tracking']['turn_action_receipts']
    channels = {}
    for kind, selected in scopes.items():
        union = _merge([[row['start_ms'], row['end_ms']] for row in selected])
        gaps, cursor = [], 0
        for start, end in union:
            if cursor < start:
                gaps.append([cursor, start])
            cursor = end
        if cursor < duration:
            gaps.append([cursor, duration])
        results = [row['original_reference_score'] for row in scored if kind in row['selected_kinds']]
        matches = [match for result in results for match in result['matches'] if match['expected']['kind'] == kind]
        missed = [label for result in results for label in result['missed'] if label['kind'] == kind]
        extras = [actual for result in results for actual in result['extra'] if actual['kind'] == kind]
        outside = [actual for actual in receipts if actual['kind'] == kind
                   and not any(start <= actual['source_timestamp_ms'] < end for start, end in union)]
        if len(matches) + len(extras) + len(outside) != sum(actual['kind'] == kind for actual in receipts):
            raise ValueError(f'Prediction accounting does not partition the {kind} channel.')
        incomplete = [row['path'] for row in selected if row['reference_complete'] is False]
        unspecified = [row['path'] for row in selected if row['reference_complete'] is None]
        channels[kind] = {
            'reference_label_count': len(matches) + len(missed), 'matched': len(matches),
            'scoped_prediction_count': len(matches) + len(extras),
            'reference_label_recall': len(matches) / (len(matches) + len(missed)) if matches or missed else None,
            'scoped_prediction_precision': len(matches) / (len(matches) + len(extras)) if matches or extras else None,
            'matches': matches, 'missed_labels': missed, 'extra_scoped_predictions': extras,
            'predictions_outside_declared_scope': outside,
            'declared_scope_union_ms': union, 'uncovered_kind_intervals_ms': gaps,
            'explicit_incomplete_references': incomplete,
            'undeclared_completeness_references': unspecified,
            'scoped_agreement_passed': bool(selected) and not missed and not extras and not incomplete,
            'full_recording_recall_established': False,
        }
    return {
        'schema': 'tracen-replay/action-corpus-score-v2',
        'source_sha256': source['sha256'], 'source_duration_ms': duration,
        'index_sha256': _sha(index_path), 'report_sha256': _sha(report_path),
        'reference_entries_verified': len(scored),
        'unique_reference_files_verified': len({row['path'] for row in scored}), 'channels': channels,
        'per_reference_scores': scored, 'full_recording_action_recall_measured': False,
        'interpretation': ('Agreement is measured only inside each selected kind scope. Outside predictions are '
                          'unassessed, not false positives. Reference hashes do not validate source pixels or '
                          'annotation completeness. Sampled agreement is not full semantic recall.'),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = score_corpus(args.index, args.report, args.evidence_root)
    # Scores are versioned artifacts; do not silently replace prior results.
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({kind: {'matched': value['matched'], 'expected': value['reference_label_count'],
                            'outside_scope': len(value['predictions_outside_declared_scope'])}
                      for kind, value in result['channels'].items()}))


if __name__ == '__main__':
    main()
