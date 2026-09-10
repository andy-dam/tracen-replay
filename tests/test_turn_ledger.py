import copy
import unittest

from tracen_replay.reconcile import FIELDS
from tracen_replay.turn_ledger import build


def reading(time, date='Junior Year Early Jul', remaining=None):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png',
                stats=dict(calendar_text=date, turns_remaining_to_goal=remaining))


def checkpoint(identity, time, value):
    return dict(id=identity, first_seen_ms=time, last_seen_ms=time + 100,
                values={f: value for f in FIELDS}, evidence=f'{time}.png')


def report():
    return dict(source=dict(sha256='a' * 64, duration_ms=5000), gameplay_tracking=dict(
        auxiliary_log_used=False,
        readings=[reading(100), reading(200), reading(500), reading(1000),
                  reading(2000, 'Junior Year Late Jul'), reading(2200, 'Junior Year Late Jul')],
        events=[], turn_action_receipts=[], checkpoints=[], intervals=[],
        performance_accounting=dict(checkpoints=[], intervals=[])))


class TurnLedgerTests(unittest.TestCase):
    def test_ambiguous_hint_names_remain_visible_without_counting_as_awards(self):
        source = report()
        source['gameplay_tracking']['events'] = [dict(id='hint-event', kind='outcome',
            first_seen_ms=1000, last_seen_ms=2200, effects=[], evidence='1000.png',
            ambiguous_effect_candidates=[dict(effect=dict(kind='skill_hint_change', name='Unreadable candidate', amount=1),
                reason='unresolved_identity', evidence=['2000.png', '2200.png'])])]
        ledger = build(source)
        candidate = next(e for e in ledger['timeline'] if e['kind'] == 'ambiguous_effect')
        self.assertEqual(candidate['source_ref'], '/gameplay_tracking/events/0/ambiguous_effect_candidates/0')
        self.assertFalse(candidate['accepted_award'])
        self.assertIsNone(candidate['occurrence_count'])
        self.assertEqual(candidate['turn_id'], 'turn-002')
        self.assertEqual(candidate['candidate']['effect']['amount'], 1)
        self.assertFalse(any(e['kind'] == 'skill_hint_change' for e in ledger['timeline']))

    def test_links_action_and_hint_without_turning_preview_into_an_action(self):
        source = report()
        data = source['gameplay_tracking']
        data['training_previews'] = [dict(first_seen_ms=500, training_option='speed')]
        data['events'] = [dict(id='event', kind='outcome', first_seen_ms=500, last_seen_ms=1000,
                              evidence='500.png', deltas={'speed': 5},
                              effects=[dict(kind='skill_hint_change', name='Test ○', amount=1)],
                              field_evidence={'skill_hint_change||Test ○': ['1000.png']})]
        data['turn_action_receipts'] = [dict(kind='training', training_option='power',
            source_timestamp_ms=500, evidence='500.png', event_id='event')]
        data['lesson_purchases'] = [dict(id='lesson', source_timestamp_ms=1000,
            receipt_event_id='event', performance_cost={'dance': 10}, evidence=['1000.png'])]
        before = copy.deepcopy(source)
        ledger = build(source)
        self.assertEqual(source, before)
        actions = [e for e in ledger['timeline'] if e['kind'] == 'committed_action']
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['training_option'], 'power')
        self.assertEqual(actions[0]['event_ref'], '/gameplay_tracking/events/0')
        hint = next(e for e in ledger['timeline'] if e['kind'] == 'skill_hint_change')
        self.assertEqual(hint['first_seen_ms'], 1000)
        self.assertEqual(hint['turn_id'], 'turn-001')
        lesson = next(e for e in ledger['timeline'] if e['kind'] == 'lesson_purchases')
        self.assertEqual(lesson['accounting_role'], 'reference_only_not_an_additional_award')
        self.assertNotIn('performance_changes', lesson)

    def test_cross_boundary_event_keeps_ambiguity_and_hint_can_have_exact_time(self):
        source = report()
        source['gameplay_tracking']['events'] = [dict(id='event', kind='outcome',
            first_seen_ms=1000, last_seen_ms=2200, evidence='1000.png',
            effects=[dict(kind='skill_hint_change', name='Test', amount=2)],
            field_evidence={'skill_hint_change||Test': ['2200.png']})]
        ledger = build(source)
        event, hint = ledger['timeline']
        self.assertIsNone(event['turn_id'])
        self.assertEqual(event['candidate_turn_ids'], ['turn-001', 'turn-002'])
        self.assertEqual(hint['turn_id'], 'turn-002')
        self.assertIn(event['id'], ledger['unassigned_entry_refs'])

    def test_missing_states_stay_null_and_shared_comparison_is_not_split(self):
        source = report()
        data = source['gameplay_tracking']
        data['checkpoints'] = [checkpoint('before', 500, 10), checkpoint('after', 2200, 15)]
        observed = {f: 5 for f in FIELDS}
        supported = {f: 0 for f in FIELDS}
        data['intervals'] = [dict(start_ms=600, end_ms=2200, observed_change=observed,
            supported_change=supported, unexplained_change=observed, status='unresolved')]
        ledger = build(source)
        self.assertEqual(len(ledger['comparisons']['stats']), 1)
        comparison = ledger['comparisons']['stats'][0]
        self.assertTrue(comparison['spans_multiple_turns'])
        self.assertEqual(comparison['unexplained_change'], observed)
        for turn in ledger['turns']:
            self.assertEqual(turn['comparisons']['stats'], ['/gameplay_tracking/intervals/0'])
            self.assertIsNone(turn['states']['performance']['opening'])
        opening = ledger['turns'][0]['states']['stats']['opening']
        self.assertEqual(opening['observed_at_ms'], 500)
        self.assertFalse(opening['exact_turn_boundary'])
        self.assertEqual(ledger['turns'][0]['states']['stats']['closing']['values'], {f: 15 for f in FIELDS})

    def test_state_observed_only_after_action_cannot_be_opening_state(self):
        source = report()
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=500, evidence='500.png')]
        source['gameplay_tracking']['checkpoints'] = [checkpoint('late', 1000, 10)]
        ledger = build(source)
        self.assertIsNone(ledger['turns'][0]['states']['stats']['opening'])

    def test_unchanged_state_crossing_date_needs_actual_same_value_source_observation(self):
        source = report()
        state = checkpoint('stable', 500, 10)
        state.update(last_seen_ms=2200, supporting_frames=['500.png', '2000.png', '2200.png'])
        source['gameplay_tracking']['checkpoints'] = [state]
        # A checkpoint time span alone cannot populate the next turn's state.
        self.assertIsNone(build(source)['turns'][1]['states']['stats']['opening'])
        source['gameplay_tracking']['readings'][-2]['stats']['values'] = state['values']
        opening = build(source)['turns'][1]['states']['stats']['opening']
        self.assertEqual(opening['observed_at_ms'], 2000)
        self.assertEqual(opening['evidence'], ['2000.png'])
        self.assertTrue(opening['exact_turn_boundary'])

    def test_comparison_must_match_adjacent_states_and_preserve_arithmetic(self):
        source = report()
        data = source['gameplay_tracking']
        data['checkpoints'] = [checkpoint('before', 500, 10), checkpoint('after', 2200, 15)]
        data['intervals'] = [dict(before_id='before', after_id='after', start_ms=600, end_ms=2200,
            observed_change={f: 5 for f in FIELDS}, supported_change={f: 0 for f in FIELDS},
            unexplained_change={f: 5 for f in FIELDS}, status='unresolved')]
        self.assertEqual(build(source)['comparisons']['stats'][0]['after_state_ref'], '/gameplay_tracking/checkpoints/1')
        for mutation in ('wrong_id', 'wrong_delta', 'bool_delta'):
            broken = copy.deepcopy(source)
            interval = broken['gameplay_tracking']['intervals'][0]
            if mutation == 'wrong_id':
                interval['before_id'] = 'absent'
            else:
                interval['unexplained_change']['speed'] = True if mutation == 'bool_delta' else 0
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build(broken)

    def test_countdowns_are_segments_not_claimed_unique_turns_and_noise_stays_visible(self):
        source = report()
        source['gameplay_tracking']['readings'] = [reading(100, 'Junior Year Pre-Debut', 3),
            reading(200, 'Junior Year Pre-Debut', 3), reading(500, 'Junior Year Pre-Debut', 2),
            reading(1000, 'Junior Year Pre-Debut', 1), reading(1200, 'Junior Year Pre-Debut', 1)]
        ledger = build(source)
        self.assertEqual(len(ledger['turns']), 2)
        self.assertEqual(ledger['turns'][0]['window_kind'], 'countdown_segment')
        self.assertTrue(any(i['reason'] == 'single_calendar_observation' for i in ledger['calendar_issues']))
        self.assertIn('calendar_gap_or_reset', ledger['turns'][1]['issues'])
        self.assertIn('unconfirmed_calendar_transition', ledger['turns'][0]['issues'])

    def test_unparsed_receipts_remain_non_awards_and_outside_turns_are_not_lost(self):
        source = report()
        source['gameplay_tracking']['unparsed_receipt_candidates'] = [dict(first_seen_ms=0,
            last_seen_ms=50, evidence='0.png', raw_text='Speed went up by')]
        ledger = build(source)
        entry = ledger['timeline'][0]
        self.assertFalse(entry['accepted_award'])
        self.assertIsNone(entry['turn_id'])
        self.assertEqual(ledger['unassigned_entry_refs'], [entry['id']])

    def test_unconfirmed_calendar_transition_cannot_assign_changes_to_previous_turn(self):
        source = report()
        source['gameplay_tracking']['readings'] = [reading(100), reading(200),
            reading(500, 'Junior Year Late Jul'), reading(1000, 'Junior Year Early Aug'),
            reading(1100, 'Junior Year Early Aug')]
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='training', source_timestamp_ms=500)]
        source['gameplay_tracking']['lesson_purchases'] = [dict(source_timestamp_ms=750, evidence='750.png')]
        source['gameplay_tracking']['checkpoints'] = [checkpoint('uncertain', 750, 10)]
        ledger = build(source)
        for entry in ledger['timeline']:
            self.assertIsNone(entry['turn_id'])
            self.assertEqual(entry['assignment_basis'], 'unconfirmed_calendar_transition')
            self.assertIn('Junior Year Late Jul', entry['unconfirmed_calendar_labels'])
        self.assertIsNone(ledger['turns'][0]['states']['stats']['opening'])
        self.assertEqual(len(ledger['turns'][0]['ambiguous_timeline_refs']), 2)

    def test_new_phase_closes_calendar_turn_before_countdown_is_readable(self):
        source = report()
        source['gameplay_tracking']['readings'] = [reading(100, 'Senior Year Late Dec'),
            reading(200, 'Senior Year Late Dec'), reading(1000, 'Finale Underway'),
            reading(1200, 'Finale Underway'), reading(2000, 'Finale Underway', 1),
            reading(2200, 'Finale Underway', 1), reading(2300, 'Finale Underway')]
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=1500)]
        turns = build(source)['turns']
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[0]['end_ms'], 1000)
        self.assertEqual(turns[1]['action_status'], 'one_action')
        self.assertEqual(turns[1]['window_kind'], 'unresolved_phase')
        self.assertEqual(turns[2]['calendar_value'], 1)

    def test_skipped_calendar_dates_do_not_assign_gap_action_to_earlier_date(self):
        source = report()
        source['gameplay_tracking']['readings'] = [reading(100), reading(200),
            reading(1000, 'Junior Year Late Aug'), reading(1100, 'Junior Year Late Aug')]
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='rest', source_timestamp_ms=500)]
        ledger = build(source)
        self.assertIsNone(ledger['timeline'][0]['turn_id'])
        self.assertEqual(ledger['calendar_issues'][0]['reason'], 'calendar_gap_or_reset')

    def test_final_unconfirmed_calendar_span_cannot_supply_closing_state(self):
        source = report()
        data = source['gameplay_tracking']
        data['readings'] = [reading(100), reading(200), reading(4000, 'Junior Year Late Jul')]
        data['turn_action_receipts'] = [dict(kind='rest', source_timestamp_ms=500)]
        data['checkpoints'] = [checkpoint('uncertain_tail', 4500, 10)]
        state = build(source)['turns'][0]['states']['stats']
        self.assertIsNone(state['closing'])
        self.assertEqual(state['closing_basis'], 'unavailable')

    def test_invalid_core_references_and_non_numeric_states_fail(self):
        for case in ('event_link', 'action_kind', 'time', 'duplicate_event', 'state', 'evidence_time'):
            with self.subTest(case=case):
                source = report()
                data = source['gameplay_tracking']
                if case == 'event_link':
                    data['turn_action_receipts'] = [dict(kind='training', source_timestamp_ms=500, event_id='missing')]
                elif case == 'action_kind':
                    data['turn_action_receipts'] = [dict(kind='preview', source_timestamp_ms=500)]
                elif case == 'time':
                    data['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=True)]
                elif case == 'duplicate_event':
                    data['events'] = [dict(id='same', kind='outcome', first_seen_ms=500, last_seen_ms=500)] * 2
                elif case == 'state':
                    data['checkpoints'] = [checkpoint('bad', 500, True)]
                else:
                    data['readings'][1]['evidence'] = data['readings'][0]['evidence']
                with self.assertRaises(ValueError):
                    build(source)

    def test_report_validator_rejects_stale_projection_and_malformed_core_action(self):
        from tests.test_report_contract import valid_report
        from tracen_replay.report_contract import ReportContractError, validate
        source = valid_report()
        source['source']['duration_ms'] = 5000
        source['gameplay_tracking'].update(report()['gameplay_tracking'])
        source['turn_ledger'] = build(source)
        validate(source, require_gameplay=True)
        stale = copy.deepcopy(source)
        stale['turn_ledger']['turns'][0]['start_ms'] = 0
        with self.assertRaisesRegex(ReportContractError, 'does not match'):
            validate(stale, require_gameplay=True)
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='preview', source_timestamp_ms=500)]
        with self.assertRaisesRegex(ReportContractError, 'Unknown committed action kind'):
            validate(source, require_gameplay=True)


if __name__ == '__main__':
    unittest.main()
