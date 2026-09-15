"""Assemble section score dimensions without merging unlike denominators.

Requires the local sealed reference and grading artifacts. Missing sections
remain pending. This assembler does not certify visual review completeness.
"""
import argparse
import json
from pathlib import Path

from baseline_status import SECTIONS, digest, inspect


def build(root, repo):
    state = inspect(root, repo)
    if state['freeze_errors']:
        raise ValueError(state['freeze_errors'])
    records = []
    inputs = {}

    def read(section, filename):
        path = root / section / filename
        inputs[str(path.relative_to(root))] = digest(path)
        return json.loads(path.read_text(encoding='utf-8'))

    def pair(correct, total):
        return {'correct': correct, 'denominator': total}

    def counts(data):
        return pair(data.get('correct', 0), sum(data.values()))

    for section, start, end in SECTIONS:
        record = {'section': section, 'scope_ms': [start, end], 'metrics': {}}
        status = next(x for x in state['sections'] if x['section'] == section)
        if status['pending_artifact_checks']:
            record['pending'] = status['pending_artifact_checks']
            records.append(record)
            continue
        if section == 'source-b':
            action_data = read(section, 'states-actions-grade.json')
            effects = read(section, 'effect-grade.json')['adjudicated']
            purchases = read(section, 'purchase-grade.json')['counts']
            action = action_data['actions']
            values = action_data['numeric_states']
            metrics = record['metrics']
            metrics['action_kind_and_option'] = pair(action['action_identity']['status'].get('correct', 0), action['normal_committed_action_denominator'])
            metrics['training_success_field'] = pair(action['training_success']['status'].get('correct', 0), action['training_success']['denominator'])
            metrics['accepted_numeric_snapshots'] = pair(values['accepted_checkpoint']['snapshot_correct'], values['accepted_checkpoint']['snapshot_denominator'])
            metrics['exact_frame_numeric_fields'] = pair(values['exact_reading']['field_correct'], values['exact_reading']['field_denominator'])
            metrics['consumer_opening_numeric_fields'] = pair(values['consumer_opening']['field_correct'], values['consumer_opening']['field_denominator'])
            metrics['purchase_aggregate_cost'] = pair(purchases['report_cost_exact'], purchases['source_transactions'])
            metrics['effect_scalar_core_identity'] = pair(effects['scalar_core_correct'], effects['scalar_core_denominator'])
            metrics['effect_numeric_amount'] = pair(effects['amount_exact_correct'], effects['amount_denominator'])
            record['status'] = 'section dimensions assembled; semantic limits remain in section scorecard'
            records.append(record)
            continue
        metrics = record['metrics']
        if section in ('source-a', 'source-c'):
            filename = 'adjudicated-grade.json' if section == 'source-a' else 'adjudications.json'
            data = read(section, filename)['adjudicated']
            action = data['action']
            metrics['action_kind_and_option'] = pair(action['core_occurrence_identity_correct'], action['source_labels'])
            if section == 'source-a':
                success = action['training_success']
                metrics['training_success_field'] = pair(success['correct_prediction_rows'], success['source_rows'])
                values = data['numeric_state']
                metrics['accepted_numeric_snapshots'] = pair(values['accepted_checkpoint_correct'], values['accepted_checkpoint_denominator'])
                metrics['exact_frame_numeric_snapshots'] = pair(values['exact_timestamp_correct'], values['exact_timestamp_denominator'])
                metrics['purchase_aggregate_cost'] = pair(data['purchase']['cost_exact_correct'], data['purchase']['cost_denominator'])
            else:
                metrics['training_success_field'] = pair(action['result_success_prediction_rows'], action['result_success_source_rows'])
                values = data['state']
                metrics['accepted_numeric_snapshots'] = pair(values['accepted_checkpoint_correct'], values['normalized_numeric_rows'])
                metrics['exact_frame_numeric_snapshots'] = pair(values['literal_frame_exact'], values['normalized_numeric_rows'])
                purchases = data['purchase']
                metrics['purchase_aggregate_cost'] = pair(purchases['transaction_occurrence_and_aggregate_cost_correct'], purchases['source_purchase_labels'])
            effects = data['effect']
            metrics['effect_core_identity'] = pair(effects['core_occurrence_identity_correct'], effects['core_occurrence_identity_denominator'])
            metrics['effect_numeric_amount'] = pair(effects['amount_exact_correct'], effects['amount_bearing_source_rows'])
        else:
            filename = 'adjudicated-grade.json' if section in ('middle-a', 'tail-a') else 'draft-grade.json'
            data = read(section, filename)
            if section == 'middle-a':
                actions = data['actions']
                metrics['action_kind_and_option'] = pair(sum(x['identity_status'] == 'correct' for x in actions), len(actions))
                result_rows = [x for x in actions if x.get('result_expected') == 'success']
                metrics['training_success_field'] = pair(sum(x['result_status'] == 'correct' for x in result_rows), len(result_rows))
            else:
                actions = [x for x in data['results'] if x['category'] == 'action']
                def core_correct(row):
                    # Participant and training-result metadata have their own grades.
                    fields = {'kind', 'training_option'}
                    if row['expected']['kind'] == 'race':
                        fields |= {'name', 'placing'}
                    checks = [x for x in row['comparisons'] if x['field'] in fields]
                    return bool(checks) and all(x['status'] == 'correct' for x in checks)
                metrics['action_kind_and_option'] = pair(sum(core_correct(x) for x in actions), len(actions))
                success = [c for x in actions for c in x['comparisons'] if c['field'] == 'result']
                metrics['training_success_field'] = pair(sum(c['status'] == 'correct' for c in success), len(success))
            effects = [x for x in data['results'] if x['category'] == 'effect']
            metrics['effect_fully_supplied'] = pair(sum(x['status'] == 'correct' for x in effects), len(effects))
            purchases = [x for x in data['results'] if x['category'] == 'purchase']
            def cost_correct(row):
                fields = set(row['expected'].get('cost', {}))
                checks = {c['field']: c['status'] for c in row['comparisons']}
                return bool(row.get('matched_prediction')) and bool(fields) and all(checks.get(f) == 'correct' for f in fields)
            metrics['purchase_aggregate_cost'] = pair(sum(cost_correct(x) for x in purchases), len(purchases))
            if section in ('middle-a', 'tail-a'):
                metrics['accepted_numeric_snapshots'] = counts(data['accepted_checkpoint_snapshots'])
                metrics['exact_frame_numeric_fields'] = counts(data['literal_numeric_fields'])
                metrics['consumer_opening_numeric_fields'] = counts(data['turn_opening_numeric_fields'])
            else:
                states = [x for x in data['results'] if x['category'] == 'state' and x['expected'].get('channel') in ('stats', 'performance')]
                metrics['accepted_numeric_snapshots'] = pair(sum(x.get('accepted_state_status') == 'correct' for x in states), len(states))
                checks = [c for x in states for c in x['comparisons']]
                metrics['exact_frame_numeric_fields'] = pair(sum(c['status'] == 'correct' for c in checks), len(checks))
                if section == 'tail-c':
                    opening = read(section, 'turn-state-grade.json')
                    metrics['consumer_opening_numeric_fields'] = counts(opening['numeric_field_counts'])
        record['status'] = 'section dimensions assembled; semantic limits remain in section scorecard'
        records.append(record)
    return {'report_sha256': state['report_sha256'], 'inputs_sha256': inputs,
            'sections': records, 'pending_sections': [x['section'] for x in records if 'pending' in x],
            'review_completion': 'Not determined by this assembler; see the local evaluation records.',
            'definitions': {
                'action_kind_and_option': 'Committed action kind and selected training option; race name/placing where labeled. Does not require participant identity, training success metadata or exact calendar boundary.',
                'training_success_field': 'Source-observed successful result supplied as success. Unknown predictions are abstentions, not assertions of failure.',
                'accepted_numeric_snapshots': 'Stats/SP or performance snapshot recovered within the source state interval. Each channel is one snapshot; missing exact-frame OCR can still recover here.',
                'exact_frame_numeric_snapshots': 'Entire numeric channel snapshot correct at its labeled frame timestamp; a partial frame does not count as a full snapshot.',
                'exact_frame_numeric_fields': 'Individual numeric stat/SP/performance fields correct at their labeled frame timestamps; these are fields, not snapshots.',
                'consumer_opening_numeric_fields': 'Individual numeric fields recovered from the calendar-anchored consumer ledger opening state, which may be observed after the exact turn boundary.',
                'effect_core_identity': 'Typed occurrence and base identity. Incomplete tier/glyph details remain separately disclosed.',
                'effect_scalar_core_identity': 'Core occurrence of labeled scalar stat/performance/friendship/hint/condition effects; composite rewards, modifiers and context metadata have separate grades.',
                'effect_fully_supplied': 'Complete scored effect row including structured details required by that section. Do not add this to core-identity scores.',
                'effect_numeric_amount': 'Exact scalar amount on source-labeled effects, including non-stat types such as friendship and hints.',
                'purchase_aggregate_cost': 'One completed transaction and its aggregate debit. Bundled skill components are not separate transactions.'},
            'limitations': [
                'This is a development baseline on footage already used during analyzer development.',
                'No overall accuracy, precision or exhaustive native-frame recall is computed.',
                'Exact-frame snapshots and fields use different units and must not be summed.',
                'Ungraded reference gaps, ambiguous details and unobservable states are listed in section scorecards.',
                'Full completion requires the source review, gap register, boundary audit and documented limits.']}


def markdown(result):
    def time(ms):
        seconds = ms // 1000
        return f'{seconds // 60:02}:{seconds % 60:02}'

    def cell(record, key):
        metric = record['metrics'].get(key)
        return f"{metric['correct']}/{metric['denominator']}" if metric else 'pending'

    lines = ['# Full-run baseline scorecard', '',
             ('**Status: all seven section grades assembled.** See [the completion audit](full-run-baseline-audit.md) for review scope and remaining evidence limits.' if not result['pending_sections'] else '**Status: section grading incomplete.** Pending sections are listed below.'), '',
             'The original recording is 31:06.333 long and already influenced analyzer development. References use visible gameplay only. The analyzer and report remain frozen. See [evaluation scope](full-run-baseline.md) and [timestamped gaps](full-run-baseline-gaps.md).', '',
             '| Source interval | Committed action kind/option | Success field supplied | Accepted numeric snapshots | Purchase aggregate costs |',
             '| --- | ---: | ---: | ---: | ---: |']
    for record in result['sections']:
        start, end = record['scope_ms']
        label = f'{time(start)}–{time(end)}' + ('.333' if end == 1866333 else '')
        label = f"[{label}](../.local/full-run-baseline-v1/{record['section']}/scorecard.md)" if 'pending' not in record else label
        lines.append('| ' + label + ' | ' + ' | '.join(cell(record, key) for key in ['action_kind_and_option', 'training_success_field', 'accepted_numeric_snapshots', 'purchase_aggregate_cost']) + ' |')
    lines += ['',
              'Unknown success fields are abstentions: the selected training can be correct while its success metadata is unavailable. Numeric snapshots count stats/SP and performance as separate channels. A missing exact-frame reading can still be recovered by an accepted checkpoint; the consumer ledger can recover additional states from repeated readings. A 0/0 purchase result means no source transaction was observed, not perfect purchase recall.', '',
              '| Section | Effect measure | Correct / labeled |', '| --- | --- | ---: |']
    for record in result['sections']:
        for key, label in [('effect_core_identity', 'Typed occurrence and base identity'), ('effect_scalar_core_identity', 'Scalar effect core occurrence and identity'), ('effect_numeric_amount', 'Scalar amount across labeled effect types'), ('effect_fully_supplied', 'Full scored effect row, including required details')]:
            if key in record['metrics']:
                lines.append(f"| {record['section']} | {label} | {cell(record, key)} |")
    lines += ['',
              'These effect measures have different requirements and are deliberately not added together. The section scorecards separately grade stat/SP, performance, hints, energy, friendship and observable conditions. Raw matcher failures resolved by documented occurrence joins or source corrections are not analyzer errors.', '',
              'Remaining evaluation limits include ungraded source-label gaps, obscured amounts, partial owned-skill inventory and absolute energy/continuous condition state. The gap register distinguishes those limits from source-confirmed report omissions. Matched totals alone do not prove complete causal attribution or absence of offsetting errors.', '',
              '## Reproduce', '',
              'Run the grading commands in the linked section scorecards first, then assemble these tables from their JSON outputs:', '',
              '```powershell',
              '.venv/Scripts/python.exe analyzer/lab/build_baseline_scorecard.py .local/full-run-baseline-v1 --output .local/full-run-baseline-v1/combined-scorecard.json --markdown docs/full-run-baseline-scorecard.md',
              '```', '',
              '`combined-scorecard.json` records the input artifact hashes and metric definitions. The assembler verifies the frozen analyzer/report and source-hash bindings. It leaves incomplete sections pending and does not certify visual review completion.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--markdown', type=Path)
    args = parser.parse_args()
    result = build(args.review_dir, Path(__file__).resolve().parents[1])
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    if args.markdown:
        args.markdown.write_text(markdown(result), encoding='utf-8')
    print(json.dumps({'pending_sections': result['pending_sections'], 'metrics': {x['section']: x['metrics'] for x in result['sections']}}))


if __name__ == '__main__':
    main()
