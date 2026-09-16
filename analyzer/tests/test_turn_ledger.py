import copy
import unittest

from tracen_replay.reconcile import FIELDS
from tracen_replay.turn_ledger import PERFORMANCE_FIELDS, build


def reading(time, date='Junior Year Early Jul', remaining=None):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png',
                stats=dict(calendar_text=date, turns_remaining_to_goal=remaining))


def checkpoint(identity, time, value):
    return dict(id=identity, first_seen_ms=time, last_seen_ms=time + 100,
                values={f: value for f in FIELDS}, evidence=f'{time}.png')


def complete_values(value, fields):
    return {field: value for field in fields}


def report():
    return dict(source=dict(sha256='a' * 64, duration_ms=5000), gameplay_tracking=dict(
        auxiliary_log_used=False,
        readings=[reading(100), reading(200), reading(500), reading(1000),
                  reading(2000, 'Junior Year Late Jul'), reading(2200, 'Junior Year Late Jul')],
        events=[], turn_action_receipts=[], checkpoints=[], intervals=[],
        performance_accounting=dict(checkpoints=[], intervals=[])))


class TurnLedgerTests(unittest.TestCase):
    def test_acquisition_conflict_propagates_to_event_and_linked_transaction_only(self):
        source=report(); data=source['gameplay_tracking']
        data['events']=[dict(id='song',kind='outcome',first_seen_ms=500,last_seen_ms=1000,evidence='500.png',effects=[])]
        data['song_acquisitions']=[dict(event_id='song',source_timestamp_ms=500,evidence='500.png',
            name_conflicted=True,observed_name_candidates=['Title','Damaged Title'])]
        data['lesson_purchases']=[dict(id='purchase',receipt_event_id='song',source_timestamp_ms=500,evidence='500.png'),
                                  dict(id='unrelated',source_timestamp_ms=1000,evidence='1000.png')]
        original=copy.deepcopy(source)
        rows=build(source)['timeline']
        linked=[r for r in rows if r.get('event_id')=='song']
        self.assertEqual(len(linked),3)
        self.assertTrue(all(r['conflicts_present'] for r in linked))
        self.assertTrue(all(r['acquisition_conflicts'][0]['source_ref']=='/gameplay_tracking/song_acquisitions/0' for r in linked))
        self.assertEqual(linked[0]['acquisition_conflicts'][0]['evidence'],['500.png'])
        self.assertFalse(next(r for r in rows if r.get('transaction_id')=='unrelated')['conflicts_present'])
        self.assertEqual(source,original)

    def corroborated_fixture(self):
        source = report(); data = source['gameplay_tracking']
        full = complete_values(10, PERFORMANCE_FIELDS)
        for row, omitted in zip(data['readings'][:4], [('dance',), (), ('passion',), ('dance', 'passion')]):
            row['facts'] = {'performance_points': {k: v for k, v in full.items() if k not in omitted}}
            row['screen'] = 'training_preview'
        data['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=1500)]
        return source

    def test_complete_snapshot_can_be_corroborated_by_partial_frames(self):
        source = self.corroborated_fixture(); original = copy.deepcopy(source)
        opening = build(source)['turns'][0]['states']['performance']['opening']
        self.assertEqual(opening['values_ref'], '/gameplay_tracking/readings/1/facts/performance_points')
        self.assertEqual(opening['observed_at_ms'], 200)
        self.assertEqual(opening['basis'], 'complete_source_snapshot_with_repeated_field_corroboration')
        self.assertEqual(set(opening['field_corroboration']), set(PERFORMANCE_FIELDS))
        self.assertTrue(all(len({p['observed_at_ms'] for p in proofs}) >= 2 for proofs in opening['field_corroboration'].values()))
        self.assertEqual(source, original)

    def test_partial_frames_never_manufacture_a_complete_snapshot(self):
        source = self.corroborated_fixture()
        del source['gameplay_tracking']['readings'][1]['facts']['performance_points']['vocal']
        state=build(source)['turns'][0]['states']['performance']
        self.assertEqual(state['opening_status'],'partially_observed')
        opening=state['opening']
        original=source
        for token in opening['values_ref'].strip('/').split('/'):
            original=original[int(token)] if isinstance(original,list) else original[token]
        self.assertEqual(opening['values'],original)
        self.assertLess(len(opening['values']),5)
        self.assertTrue(all(len({p['observed_at_ms'] for p in proofs})>=2 for proofs in opening['field_corroboration'].values()))

    def test_corroboration_rejects_conflicts_and_intervening_events(self):
        for case in ('conflict', 'effect', 'purchase', 'late', 'duplicate_timestamp'):
            with self.subTest(case=case):
                source = self.corroborated_fixture(); data = source['gameplay_tracking']
                if case == 'conflict':
                    data['readings'][2]['facts']['performance_points']['dance'] = 11
                elif case == 'effect':
                    data['events'] = [dict(id='effect', kind='outcome', first_seen_ms=300, last_seen_ms=400,
                        evidence='300.png', effects=[dict(kind='performance_change', field='dance', amount=1)])]
                elif case == 'purchase':
                    data['lesson_purchases'] = [dict(id='purchase', source_timestamp_ms=300, evidence='300.png')]
                elif case == 'late':
                    data['turn_action_receipts'][0]['source_timestamp_ms'] = 500
                else:
                    data['readings'][2]['source_timestamp_ms'] = 200
                opening=build(source)['turns'][0]['states']['performance']['opening']
                if case in ('effect','purchase'):
                    self.assertIsNone(opening)
                else:
                    # The uncorroborated Dance value stays missing; independently
                    # repeated fields may survive as one actual partial frame.
                    self.assertIsNone(opening['values'].get('dance'))
                    for proofs in opening['field_corroboration'].values():
                        self.assertGreaterEqual(len({p['observed_at_ms'] for p in proofs}),2)
                        if case=='late':self.assertTrue(all(p['observed_at_ms']<500 for p in proofs))

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

    def test_a_purchase_is_dated_at_its_debit_so_it_precedes_its_own_receipt(self):
        source = report()
        data = source['gameplay_tracking']
        # The receipt is what the report reads the purchase from, so a purchase
        # dated there ties with it and can be listed after the receipt that
        # announced it. Its debit window is when it actually happened, and the
        # accounting already charges it over that window.
        data['events'] = [dict(id='receipt', kind='outcome', first_seen_ms=1000, last_seen_ms=1200,
                               evidence='1000.png', deltas={}, effects=[], field_evidence={})]
        data['lesson_purchases'] = [dict(id='lesson', source_timestamp_ms=1000, receipt_event_id='receipt',
                                         debit_window_ms=[600, 1100], performance_cost={'dance': 10},
                                         evidence=['1000.png'])]
        ledger = build(source)
        lesson = next(e for e in ledger['timeline'] if e['kind'] == 'lesson_purchases')
        receipt = next(e for e in ledger['timeline'] if e['kind'] == 'outcome')
        self.assertEqual((lesson['first_seen_ms'], lesson['last_seen_ms']), (600, 1100))
        order = [e['kind'] for e in ledger['timeline'] if e['kind'] in ('lesson_purchases', 'outcome')]
        self.assertEqual(order, ['lesson_purchases', 'outcome'])
        self.assertEqual(lesson['turn_id'], receipt['turn_id'])

    def test_entries_sharing_an_instant_are_ordered_by_the_part_each_one_plays(self):
        # One screen is often the only evidence for several entries, so they
        # share a timestamp and the order they were built in decides nothing.
        # Whatever kinds land together, the decision comes before what it
        # produced, which comes before the records referring to it, which come
        # before what followed.
        source = report()
        data = source['gameplay_tracking']
        data['events'] = [dict(id='result', kind='training', first_seen_ms=1000, last_seen_ms=1000,
                               evidence='1000.png', deltas={}, effects=[], field_evidence={}),
                          dict(id='after', kind='outcome', first_seen_ms=1000, last_seen_ms=1000,
                               evidence='1000.png', deltas={}, effects=[], field_evidence={})]
        data['turn_action_receipts'] = [dict(kind='training', training_option='power',
                                             source_timestamp_ms=1000, evidence='1000.png', event_id='result')]
        data['lesson_purchases'] = [dict(id='lesson', source_timestamp_ms=1000, receipt_event_id='after',
                                         performance_cost={'dance': 1}, evidence=['1000.png'])]
        data['song_acquisitions'] = [dict(id='song', source_timestamp_ms=1000, evidence=['1000.png'])]
        at_the_instant = [e['kind'] for e in build(source)['timeline'] if e['first_seen_ms'] == 1000]
        self.assertEqual(at_the_instant[0], 'committed_action')
        self.assertEqual(at_the_instant[1], 'training')
        self.assertEqual(at_the_instant[-1], 'outcome')
        self.assertEqual(sorted(at_the_instant[2:-1]), ['lesson_purchases', 'song_acquisitions'])

    def test_an_observed_time_always_outranks_the_part_an_entry_plays(self):
        # The roles only break ties. An entry seen later stays later, however
        # early its part would otherwise place it.
        source = report()
        data = source['gameplay_tracking']
        data['events'] = [dict(id='early', kind='outcome', first_seen_ms=900, last_seen_ms=900,
                               evidence='900.png', deltas={}, effects=[], field_evidence={})]
        data['turn_action_receipts'] = [dict(kind='training', training_option='power',
                                             source_timestamp_ms=1000, evidence='1000.png')]
        order = [(e['first_seen_ms'], e['kind']) for e in build(source)['timeline']
                 if e['kind'] in ('outcome', 'committed_action')]
        self.assertEqual(order, [(900, 'outcome'), (1000, 'committed_action')])

    def test_a_purchase_without_a_usable_debit_window_keeps_its_receipt_time(self):
        source = report()
        data = source['gameplay_tracking']
        data['events'] = [dict(id='receipt', kind='outcome', first_seen_ms=1000, last_seen_ms=1200,
                               evidence='1000.png', deltas={}, effects=[], field_evidence={})]
        for window in (None, [1100, 600], [600], ['600', '1100']):
            data['lesson_purchases'] = [dict(id='lesson', source_timestamp_ms=1000, receipt_event_id='receipt',
                                             debit_window_ms=window, performance_cost={'dance': 10},
                                             evidence=['1000.png'])]
            lesson = next(e for e in build(source)['timeline'] if e['kind'] == 'lesson_purchases')
            self.assertEqual((lesson['first_seen_ms'], lesson['last_seen_ms']), (1000, 1000), window)

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

    def test_repeated_source_readings_supply_stats_and_performance_openings(self):
        source = report()
        data = source['gameplay_tracking']
        for row in data['readings'][:2]:
            row['stats']['values'] = complete_values(10, FIELDS)
            row['facts'] = {'performance_points': complete_values(20, PERFORMANCE_FIELDS)}
        data['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=500, evidence='500.png')]

        states = build(source)['turns'][0]['states']
        stats = states['stats']['opening']
        self.assertEqual(stats['source_ref'], '/gameplay_tracking/readings/0/stats')
        self.assertEqual(stats['values_ref'], '/gameplay_tracking/readings/0/stats/values')
        self.assertEqual(stats['supporting_source_refs'], [
            '/gameplay_tracking/readings/0/stats', '/gameplay_tracking/readings/1/stats'])
        self.assertEqual(stats['basis'], 'first_repeated_source_state_before_action')
        self.assertEqual(stats['observed_at_ms'], 100)
        self.assertTrue(stats['exact_turn_boundary'])
        performance = states['performance']['opening']
        self.assertEqual(performance['source_ref'], '/gameplay_tracking/readings/0/facts')
        self.assertEqual(performance['values_ref'], '/gameplay_tracking/readings/0/facts/performance_points')
        self.assertEqual(performance['supporting_source_refs'], [
            '/gameplay_tracking/readings/0/facts', '/gameplay_tracking/readings/1/facts'])
        self.assertEqual(performance['values'], complete_values(20, PERFORMANCE_FIELDS))

    def test_repeated_source_opening_rejects_singleton_and_disagreement(self):
        for case in ('singleton', 'disagreement'):
            with self.subTest(case=case):
                source = report()
                data = source['gameplay_tracking']
                data['readings'][0]['stats']['values'] = complete_values(10, FIELDS)
                if case == 'disagreement':
                    data['readings'][1]['stats']['values'] = complete_values(11, FIELDS)
                data['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=500)]
                opening = build(source)['turns'][0]['states']['stats']['opening']
                self.assertIsNone(opening)

    def test_repeated_source_opening_ignores_after_action_values(self):
        source = report()
        data = source['gameplay_tracking']
        for row in data['readings'][2:4]:
            row['stats']['values'] = complete_values(10, FIELDS)
        data['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=500)]
        self.assertIsNone(build(source)['turns'][0]['states']['stats']['opening'])

    def test_repeated_source_opening_rejects_uncertain_calendar_and_duplicate_pts(self):
        source = report()
        data = source['gameplay_tracking']
        data['readings'] = [
            reading(100, 'Junior Year Early Jul'), reading(200, 'Junior Year Early Jul'),
            reading(500, 'Junior Year Late Jul'), reading(600, None), reading(700, None),
            reading(1000, 'Junior Year Early Aug'), reading(1100, 'Junior Year Early Aug')]
        data['readings'][3]['stats']['values'] = complete_values(10, FIELDS)
        data['readings'][4]['stats']['values'] = complete_values(10, FIELDS)
        data['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=900)]
        self.assertIsNone(build(source)['turns'][0]['states']['stats']['opening'])

        duplicate = report()
        duplicate['gameplay_tracking']['readings'][0]['stats']['values'] = complete_values(10, FIELDS)
        duplicate['gameplay_tracking']['readings'][1]['source_timestamp_ms'] = 100
        duplicate['gameplay_tracking']['readings'][1]['stats']['values'] = complete_values(10, FIELDS)
        duplicate['gameplay_tracking']['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=500)]
        self.assertIsNone(build(duplicate)['turns'][0]['states']['stats']['opening'])

    def test_repeated_source_opening_rejects_malformed_values(self):
        source = report()
        data = source['gameplay_tracking']
        data['readings'][0]['stats']['values'] = complete_values(10, FIELDS)
        malformed = complete_values(10, FIELDS)
        malformed['speed'] = '10'
        data['readings'][1]['stats']['values'] = malformed
        data['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=500)]
        self.assertIsNone(build(source)['turns'][0]['states']['stats']['opening'])

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

    def test_the_phase_read_before_its_first_countdown_is_that_countdown_turn(self):
        # The recording opens on Pre-Debut frames whose countdown is not yet
        # legible; nothing is played before "11 turns to goal" appears.
        source = report()
        source['gameplay_tracking']['readings'] = [reading(100, 'Junior Year Pre-Debut'),
            reading(300, 'Junior Year Pre-Debut'), reading(1000, 'Junior Year Pre-Debut', 11),
            reading(1200, 'Junior Year Pre-Debut', 11), reading(3000, 'Junior Year Pre-Debut', 10),
            reading(3200, 'Junior Year Pre-Debut', 10)]
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='training', source_timestamp_ms=2000)]
        turns = build(source)['turns']
        self.assertEqual([(t['window_kind'], t['calendar_value'], t['start_ms']) for t in turns],
                         [('countdown_segment', 11, 100), ('countdown_segment', 10, 3000)])
        self.assertEqual(turns[0]['action_status'], 'one_action')
        # With something played before the countdown was read, the phase window stays its own turn.
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='training', source_timestamp_ms=500)]
        turns = build(source)['turns']
        self.assertEqual([t['window_kind'] for t in turns], ['unresolved_phase', 'countdown_segment', 'countdown_segment'])

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

    def test_the_goal_race_of_the_last_countdown_turn_is_scheduled_only_beside_a_decision(self):
        def race(time, name):
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='race_result', stats={}, facts=dict(race_name=name))
        source = report()
        source['source']['duration_ms'] = 6000
        source['gameplay_tracking']['readings'] = [
            reading(100, 'Junior Year Pre-Debut', 2), reading(200, 'Junior Year Pre-Debut', 2),
            reading(1000, 'Junior Year Pre-Debut', 1), reading(1100, 'Junior Year Pre-Debut', 1),
            race(2000, 'Junior Make Debut'), race(2100, 'Junior Make Debut'),
            reading(3000, 'Junior Year Early Jul'), reading(3100, 'Junior Year Early Jul')]
        # The training shares the last countdown window with the debut race.
        source['gameplay_tracking']['turn_action_receipts'] = [
            dict(kind='training', source_timestamp_ms=500), dict(kind='training', source_timestamp_ms=1500),
            dict(kind='race', source_timestamp_ms=2000)]
        turns = build(source)['turns']
        last = next(t for t in turns if t['start_ms'] == 1000)
        self.assertEqual((last['window_kind'], last['scheduled_race'], last['action_status'], last['scheduled_race_actions']),
                         ('phase_race_turn', 'Junior Make Debut', 'one_action', 1))
        self.assertTrue(last['label'].endswith('· Junior Make Debut'))
        # When the countdown was only confirmed after the training, the race is the window's one action.
        source['gameplay_tracking']['turn_action_receipts'] = [
            dict(kind='training', source_timestamp_ms=500), dict(kind='race', source_timestamp_ms=2000)]
        turns = build(source)['turns']
        last = next(t for t in turns if t['start_ms'] == 1000)
        self.assertEqual((last['window_kind'], last.get('scheduled_race'), last['action_status']), ('countdown_segment', None, 'one_action'))

    def test_finale_races_advance_the_phase_turn_and_the_ending_is_not_a_turn(self):
        # The finale keeps one calendar label for three turns: train, Qualifier;
        # train, Semifinal; train, Finals. Each race ends its turn; the screens
        # after the Finals carry the label but no training menu.
        def race(time, name):
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='race_result', stats={}, facts=dict(race_name=name))

        def menu(time):
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='training_preview',
                        stats=dict(calendar_text='Finale Underway', turns_remaining_to_goal=None))
        source = report()
        source['source']['duration_ms'] = 10000
        source['gameplay_tracking']['readings'] = [
            reading(100, 'Senior Year Late Dec'), reading(200, 'Senior Year Late Dec'),
            reading(1000, 'Finale Underway', 1), reading(1100, 'Finale Underway', 1), menu(1200),
            race(2000, 'URA Finale Qualifier'), race(2100, 'URA Finale Qualifier'),
            reading(3000, 'Finale Underway'), reading(3100, 'Finale Underway'), menu(3200),
            race(4000, 'URA Finale Semifinal'), race(4100, None),
            reading(5000, 'Finale Underway', 1), reading(5100, 'Finale Underway', 1), menu(5200),
            race(6000, 'URA Finale Finals'),
            reading(7000, 'Finale Underway'), reading(7100, 'Finale Underway')]
        source['gameplay_tracking']['turn_action_receipts'] = [
            dict(kind='training', source_timestamp_ms=1500), dict(kind='race', source_timestamp_ms=2000),
            dict(kind='training', source_timestamp_ms=3500), dict(kind='race', source_timestamp_ms=4000),
            dict(kind='training', source_timestamp_ms=5500), dict(kind='race', source_timestamp_ms=6000)]
        turns = build(source)['turns']
        self.assertEqual(len(turns), 4)
        self.assertEqual([t['label'] for t in turns[1:]],
                         ['Finale Underway · URA Finale Qualifier', 'Finale Underway · URA Finale Semifinal', 'Finale Underway · URA Finale Finals'])
        self.assertEqual([(t['start_ms'], t['end_ms']) for t in turns[1:]], [(1000, 3000), (3000, 5000), (5000, 10000)])
        self.assertEqual([t['window_kind'] for t in turns[1:]], ['phase_race_turn'] * 3)
        self.assertEqual([t['scheduled_race'] for t in turns[1:]], ['URA Finale Qualifier', 'URA Finale Semifinal', 'URA Finale Finals'])
        self.assertEqual([t['action_status'] for t in turns[1:]], ['one_action'] * 3)
        self.assertEqual([t['scheduled_race_actions'] for t in turns[1:]], [1, 1, 1])
        self.assertEqual(turns[2]['boundary_basis'], 'finale_race_advance')
        self.assertIn('2000.png', turns[2]['evidence'])
        self.assertTrue(all(t['expects_one_action'] for t in turns[1:]))


if __name__ == '__main__':
    unittest.main()
