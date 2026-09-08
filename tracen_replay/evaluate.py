"""Evaluate saved predictions; reference values never enter OCR or accounting."""

import argparse
import json
import sys
from pathlib import Path

from .reconcile import FIELDS


def evaluate(reference, reports):
    if reference.get('schema_version') != 'tracen-replay/stat-reference-v1':
        raise ValueError('Unsupported reference schema.')
    examples = reference.get('examples')
    if not examples:
        raise ValueError('Reference must contain examples.')
    expected_times = [row['source_timestamp_ms'] for row in examples]
    if len(set(expected_times)) != len(expected_times):
        raise ValueError('Duplicate reference timestamps.')
    readings, checkpoints = {}, []
    for report in reports:
        if report['source']['sha256'].lower() != reference['source_sha256'].lower():
            raise ValueError('Report and reference source hashes do not match.')
        tracking = report.get('stat_tracking', {})
        if not tracking.get('enabled'):
            raise ValueError('Report has no stat predictions.')
        checkpoints.extend(tracking['checkpoints'])
        for row in tracking['readings']:
            timestamp = row['source_timestamp_ms']
            if timestamp in readings:
                raise ValueError('Reports contain overlapping prediction timestamps.')
            readings[timestamp] = row
    counts = dict(reference_screens=len(examples), readable_screens=0, negative_screens=0,
                  complete_correct_screens=0, expected_fields=0, accepted_fields=0, correct_fields=0,
                  abstained_fields=0, false_positive_fields=0, checkpoint_reference_matches=0,
                  countdown_expected=0, countdown_accepted=0, countdown_correct=0,
                  preview_expected=0, preview_correct=0, preview_option_expected=0, preview_option_correct=0)
    errors, details = [], []
    for example in examples:
        timestamp = example['source_timestamp_ms']
        expected = example['values']
        if expected is not None and (set(expected) != set(FIELDS) or any(type(v) is not int for v in expected.values())):
            raise ValueError('Each readable reference must contain exactly six integer fields.')
        counts['readable_screens' if expected is not None else 'negative_screens'] += 1
        if expected is not None:
            counts['expected_fields'] += len(FIELDS)
        row = readings.get(timestamp)
        if row is None:
            errors.append(dict(source_timestamp_ms=timestamp, kind='missing_prediction'))
            continue
        actual = row.get('values') or {}
        screen_errors = []
        for field in FIELDS:
            value = actual.get(field)
            if expected is None:
                if value is not None:
                    counts['false_positive_fields'] += 1
                    screen_errors.append(dict(field=field, expected=None, actual=value))
            elif value is None:
                counts['abstained_fields'] += 1
            else:
                counts['accepted_fields'] += 1
                if type(value) is int and value == expected[field]:
                    counts['correct_fields'] += 1
                else:
                    screen_errors.append(dict(field=field, expected=expected[field], actual=value))
        if expected is not None and all(actual.get(f)==expected[f] and type(actual.get(f)) is int for f in FIELDS):
            counts['complete_correct_screens'] += 1
        for key, prefix in [('turns_remaining_to_goal','countdown'),('training_preview','preview'),('preview_option','preview_option')]:
            if key not in example:
                continue
            counts[prefix+'_expected'] += 1
            value = row.get(key)
            if prefix=='countdown' and value is not None:
                counts['countdown_accepted'] += 1
            if value == example[key]:
                counts[prefix+'_correct'] += 1
            elif value is not None:
                screen_errors.append(dict(field=key,expected=example[key],actual=value))
        if row.get('completed_action') is not None:
            screen_errors.append(dict(field='completed_action',expected=None,actual=row['completed_action']))
        for checkpoint in checkpoints:
            if checkpoint['first_seen_ms'] <= timestamp <= checkpoint['last_seen_ms']:
                if expected is None or checkpoint['values'] != expected:
                    screen_errors.append(dict(field='checkpoint',expected=expected,actual=checkpoint['values']))
                else:
                    counts['checkpoint_reference_matches'] += 1
        if screen_errors:
            errors.append(dict(source_timestamp_ms=timestamp,kind='incorrect_prediction',fields=screen_errors))
        details.append(dict(source_timestamp_ms=timestamp,evidence=row['evidence'],expected=expected,actual=row.get('values'),errors=screen_errors))
    coverage = counts['complete_correct_screens']/counts['readable_screens'] if counts['readable_screens'] else 0
    accuracy = counts['correct_fields']/counts['accepted_fields'] if counts['accepted_fields'] else None
    return dict(schema_version='tracen-replay/stat-evaluation-v1',source_sha256=reference['source_sha256'],
                split=reference.get('split'),independently_reviewed=reference.get('independently_reviewed',False),
                counts=counts,accepted_field_accuracy=accuracy,complete_reading_coverage=coverage,
                passed=not errors and coverage>=0.8 and counts['negative_screens']>0,
                errors=errors,examples=details,
                limitations=['Sparse development references from one source; not an independent test.',
                             'Does not measure event recall, occurrence identity, or every emitted checkpoint.'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference',type=Path)
    parser.add_argument('reports',type=Path,nargs='+',help='Saved report.json files')
    parser.add_argument('--output',type=Path,help='New evaluation JSON; existing files are preserved')
    args=parser.parse_args(argv)
    try:
        reference=json.loads(args.reference.read_text(encoding='utf-8'))
        reports=[json.loads(p.read_text(encoding='utf-8')) for p in args.reports]
        result=evaluate(reference,reports)
        if args.output:
            with args.output.open('x',encoding='utf-8') as stream:
                json.dump(result,stream,indent=2,ensure_ascii=False)
        print(json.dumps({k:v for k,v in result.items() if k!='examples'},indent=2))
        return 0 if result['passed'] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Evaluation error: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
