import copy
import unittest

from tracen_replay.causal_accounting import CHANNELS, build


def fixture():
    stats = dict.fromkeys(CHANNELS['stats'], 100)
    performance = dict.fromkeys(CHANNELS['performance'], 20)
    return {'source': {'sha256': 'example'}, 'gameplay_tracking': {
        'auxiliary_log_used': False,
        'readings': [{'source_timestamp_ms': 150, 'evidence': 'receipt.png'}],
        'events': [{'id': 'event', 'kind': 'outcome', 'first_seen_ms': 150, 'last_seen_ms': 150,
                    'effects': [{'kind': 'stat_change', 'field': 'speed', 'amount': 5}],
                    'field_evidence': {'stat_change|speed|': ['receipt.png']}, 'deltas': {'speed': 5}}],
        'checkpoints': [{'first_seen_ms': 0, 'last_seen_ms': 100, 'values': stats},
                        {'first_seen_ms': 200, 'last_seen_ms': 250, 'values': dict(stats, speed=105)}],
        'performance_accounting': {'checkpoints': [
            {'first_seen_ms': 0, 'last_seen_ms': 100, 'values': performance},
            {'first_seen_ms': 200, 'last_seen_ms': 250, 'values': performance}]},
        'lesson_purchases': [], 'song_acquisitions': [], 'turn_action_receipts': []}}


def field(result, channel='stats', name='speed'):
    return next(f for c in result['comparisons'] if c['channel'] == channel for f in c['fields'] if f['field'] == name)


class CausalAccountingTests(unittest.TestCase):
    def test_committed_offer_cost_is_derived_and_requires_receipt_and_linked_proofs(self):
        from tracen_replay.causal_accounting import _lesson_cost_basis
        purchase=dict(name='Song',cost_basis='receipt_request_and_observed_offer_prices',performance_cost={'dance':5},
            offer_cost_evidence=dict(cost={'dance':5},receipt_name='Song',receipt={'event_id':'receipt'},
                offer=dict(title='Song',timestamps_ms=[10,20],evidence=['10.png','20.png']),
                request=dict(title='Song',timestamps_ms=[30,40],evidence=['30.png','40.png'])))
        receipt=dict(id='receipt',first_seen_ms=50,effects=[dict(kind='song_learned',name='Song')])
        rows=[dict(evidence=f'{t}.png',source_timestamp_ms=t) for t in (10,20,30,40)]
        self.assertEqual(_lesson_cost_basis(purchase,receipt,rows),'committed_offer_cost_derived')
        for case in ('no_receipt','wrong_name','wrong_cost','missing_proof','after_receipt','unbacked_timestamp','conflicted_name'):
            p=copy.deepcopy(purchase); e=copy.deepcopy(receipt); observations=copy.deepcopy(rows)
            if case=='no_receipt':e=None
            elif case=='wrong_name':e['effects'][0]['name']='Other'
            elif case=='wrong_cost':p['performance_cost']['dance']=6
            elif case=='missing_proof':observations.pop()
            elif case=='unbacked_timestamp':p['offer_cost_evidence']['offer']['evidence']=['10.png']
            elif case=='conflicted_name':p['name_conflicted']=True
            else:e['first_seen_ms']=35
            self.assertEqual(_lesson_cost_basis(p,e,observations),'projected_debit',case)

    def test_energy_and_condition_conflicts_stay_outside_numeric_stat_channels(self):
        report = fixture(); event = report['gameplay_tracking']['events'][0]
        for key in ('energy_change||', 'mood_change||', 'fan_change||', 'condition_acquired||Practice Perfect'):
            event['conflicting_readings'] = [{'field': key}]
            self.assertEqual(field(build(report))['status'], 'balanced_observations')

    def test_separate_performance_conflict_schema_blocks_materialized_delta(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        event.update(kind='training', performance_deltas={'dance': 10},
                     performance_evidence={'dance': ['receipt.png']}, performance_reading_conflicts={'dance': [1, 10]})
        result = build(report)
        self.assertEqual(field(result, 'performance', 'dance')['status'], 'unresolved_attribution')
        self.assertEqual(field(result)['status'], 'balanced_observations')

    def test_unrelated_hint_conflict_does_not_block_readable_stat_award(self):
        report = fixture()
        event = report['gameplay_tracking']['events'][0]
        event['conflicting_readings'] = [{'field': 'skill_hint_change||Some Skill', 'reason': 'name_disagreement'}]
        original = copy.deepcopy(event)
        self.assertEqual(field(build(report))['status'], 'balanced_observations')
        self.assertEqual(event, original)

    def test_training_conflict_only_blocks_its_own_stat_field(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        event.update(kind='training', effects=[], deltas={'speed': 5, 'skill_points': 7},
                     field_evidence={'speed': ['receipt.png'], 'skill_points': ['receipt.png']},
                     performance_deltas={'dance': 10}, performance_evidence={'dance': ['receipt.png']},
                     conflicting_readings={'skill_points': [7, 72]})
        data['checkpoints'][1]['values']['skill_points'] = 107
        checkpoint = data['performance_accounting']['checkpoints'][1]
        checkpoint['values'] = dict(checkpoint['values'], dance=30)
        result = build(report)
        self.assertEqual(field(result)['status'], 'balanced_observations')
        self.assertEqual(field(result, 'performance', 'dance')['status'], 'balanced_observations')
        self.assertEqual(field(result, name='skill_points')['status'], 'unresolved_attribution')
        self.assertEqual(field(result, name='skill_points')['direct_change'], 0)

    def test_typed_resource_conflict_does_not_cross_fields_or_channels(self):
        report = fixture(); event = report['gameplay_tracking']['events'][0]
        for key, expected in [('stat_change|speed|', 'unresolved_attribution'),
                              ('stat_change|power|', 'balanced_observations'),
                              ('performance_change|dance|', 'balanced_observations'),
                              ('stat_cap_change|speed|', 'balanced_observations')]:
            with self.subTest(key=key):
                event['conflicting_readings'] = [{'field': key}]
                self.assertEqual(field(build(report))['status'], expected)

    def test_unscoped_or_malformed_conflicts_still_block_numeric_claims(self):
        report = fixture(); event = report['gameplay_tracking']['events'][0]
        for conflict in [[{}], ['bad'], [{'field': 'speed'}], {'speed': [5, 50]},
                         [{'field': 'stat_change||'}], True]:
            with self.subTest(conflict=conflict):
                event['conflicting_readings'] = conflict
                self.assertEqual(field(build(report))['status'], 'unresolved_attribution')

    def test_canonical_receipt_and_its_summaries_are_counted_once(self):
        report = fixture()
        data = report['gameplay_tracking']
        data['turn_action_receipts'] = [{'event_id': 'event', 'deltas': {'speed': 5}}]
        data['song_acquisitions'] = [{'receipt_event_id': 'event', 'awarded_stats': {'speed': 5}}]
        before = copy.deepcopy(report)
        result = build(report)
        self.assertEqual(len(result['contributions']), 1)
        self.assertEqual(field(result)['direct_change'], 5)
        self.assertEqual(field(result)['status'], 'balanced_observations')
        self.assertFalse(result['comparisons'][0]['complete_event_history'])
        self.assertEqual(report, before)

    def test_missed_receipt_remains_residual_and_is_not_invented(self):
        report = fixture(); report['gameplay_tracking']['events'] = []
        result = build(report)
        self.assertEqual(result['contributions'], [])
        self.assertEqual(field(result)['unresolved_change'], 5)

    def test_summary_disagreement_is_visible_and_not_a_second_award(self):
        report = fixture(); report['gameplay_tracking']['events'][0]['deltas']['speed'] = 10
        result = build(report)
        self.assertEqual(field(result)['direct_change'], 5)
        self.assertEqual(result['issues'][0]['kind'], 'summary_effect_disagreement')

    def test_state_derived_gain_is_not_independent_receipt_evidence(self):
        report = fixture(); event = report['gameplay_tracking']['events'][0]
        event.update(kind='training', effects=[], field_evidence={'speed': ['receipt.png']}, result_state_derived_fields=['speed'])
        result = build(report)
        self.assertEqual(field(result)['direct_change'], 0)
        self.assertEqual(field(result)['derived_or_summary_change'], 5)
        self.assertEqual(field(result)['status'], 'balanced_with_derived_changes')

    def test_state_constrained_summary_cites_selected_gain_paths_without_promoting_amount(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        event.update(kind='training', effects=[], field_evidence={'speed': ['receipt.png']},
                     last_seen_ms=180,
                     state_supported_candidate_resolutions=[{
                         'field': 'speed', 'amount': 5, 'visual_candidates': [5, 8],
                         'gain_evidence': ['gain.png', 'gain.png'],
                         'basis': 'visible_gain_candidate_and_stable_result_suffix',
                     }])
        data['readings'].extend([
            {'source_timestamp_ms': 160, 'evidence': 'gain.png'},
        ])
        result = build(report)
        contribution = next(c for c in result['contributions'] if c['field'] == 'speed')
        self.assertEqual(contribution['amount'], 5)
        self.assertEqual(contribution['basis'], 'state_constrained')
        self.assertEqual(contribution['evidence'], ['receipt.png', 'gain.png'])
        self.assertEqual(contribution['observation_start_ms'], 150)
        self.assertEqual(contribution['observation_end_ms'], 160)
        self.assertEqual(contribution['timing_basis'], 'field_observations')

    def test_state_constrained_proof_rejects_ambiguous_or_mismatched_candidates(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        event.update(kind='training', effects=[], field_evidence={'speed': ['receipt.png']},
                     state_supported_candidate_resolutions=[
                         {'field': 'speed', 'amount': 5, 'gain_evidence': ['first.png'],
                          'basis': 'visible_gain_candidate_and_stable_result_suffix'},
                         {'field': 'speed', 'amount': 8, 'gain_evidence': ['second.png'],
                          'basis': 'visible_gain_candidate_and_stable_result_suffix'},
                     ])
        data['readings'].extend([
            {'source_timestamp_ms': 160, 'evidence': 'first.png'},
            {'source_timestamp_ms': 170, 'evidence': 'second.png'},
        ])
        result = build(report)
        contribution = next(c for c in result['contributions'] if c['field'] == 'speed')
        self.assertEqual(contribution['amount'], 5)
        self.assertEqual(contribution['basis'], 'state_constrained')
        self.assertEqual(contribution['evidence'], ['receipt.png'])

        event['state_supported_candidate_resolutions'] = [{
            'field': 'speed', 'amount': 8, 'gain_evidence': ['second.png'],
            'basis': 'visible_gain_candidate_and_stable_result_suffix',
        }]
        result = build(report)
        contribution = next(c for c in result['contributions'] if c['field'] == 'speed')
        self.assertEqual(contribution['amount'], 5)
        self.assertEqual(contribution['evidence'], ['receipt.png'])

    def test_state_constrained_proof_flattens_source_paths_only(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        event.update(kind='training', effects=[], field_evidence={'speed': ['receipt.png']},
                     state_supported_candidate_resolutions=[{
                         'field': 'speed', 'amount': 5,
                         'gain_evidence': [['gain-a.png', ['gain-b.png', 'gain-a.png']],
                                           {'ignored': 'not-a-source-path'}],
                         'basis': 'visible_gain_candidate_and_stable_result_suffix',
                     }])
        data['readings'].extend([
            {'source_timestamp_ms': 160, 'evidence': 'gain-a.png'},
            {'source_timestamp_ms': 170, 'evidence': 'gain-b.png'},
        ])
        result = build(report)
        contribution = next(c for c in result['contributions'] if c['field'] == 'speed')
        self.assertEqual(contribution['evidence'], ['receipt.png', 'gain-a.png', 'gain-b.png'])

    def test_state_derived_field_does_not_borrow_candidate_gain_paths(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        event.update(kind='training', effects=[], field_evidence={'speed': ['receipt.png']},
                     result_state_derived_fields=['speed'],
                     state_supported_candidate_resolutions=[{
                         'field': 'speed', 'amount': 5, 'gain_evidence': ['gain.png'],
                         'basis': 'visible_gain_candidate_and_stable_result_suffix',
                     }])
        data['readings'].append({'source_timestamp_ms': 160, 'evidence': 'gain.png'})
        result = build(report)
        contribution = next(c for c in result['contributions'] if c['field'] == 'speed')
        self.assertEqual(contribution['basis'], 'state_derived')
        self.assertEqual(contribution['evidence'], ['receipt.png'])

    def test_crossing_an_endpoint_does_not_assign_by_convenient_amount(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        data['readings'].append({'source_timestamp_ms': 225, 'evidence': 'late.png'})
        event['last_seen_ms'] = 225
        event['field_evidence']['stat_change|speed|'].append('late.png')
        result = build(report)
        self.assertEqual(field(result)['direct_change'], 0)
        self.assertEqual(field(result)['status'], 'unresolved_attribution')
        self.assertIn('crosses_observed_endpoint', field(result)['ambiguous_contributions'][0]['reasons'])

    def test_parent_window_cannot_replace_missing_field_evidence(self):
        report = fixture(); report['gameplay_tracking']['events'][0]['field_evidence'] = {}
        result = build(report)
        self.assertEqual(field(result)['direct_change'], 0)
        self.assertEqual(result['contributions'][0]['timing_basis'], 'parent_window_only')

    def test_duplicate_effect_claims_are_exposed_not_summed(self):
        report = fixture(); event = report['gameplay_tracking']['events'][0]
        event['effects'].append(copy.deepcopy(event['effects'][0]))
        result = build(report)
        self.assertEqual(field(result)['direct_change'], 0)
        self.assertIn('multiple_effect_claims_for_one_event_field', [i['kind'] for i in result['issues']])

    def test_cloned_events_cannot_hide_a_missed_award_by_balancing_the_total(self):
        report=fixture();data=report['gameplay_tracking']
        duplicate=copy.deepcopy(data['events'][0]);duplicate['id']='different-event-id'
        data['events'].append(duplicate)
        data['checkpoints'][1]['values']['speed']=110
        result=build(report)
        self.assertEqual(field(result)['status'],'unresolved_attribution')
        self.assertEqual(field(result)['direct_change'],0)
        self.assertIn('shared_source_effect_claim',[i['kind'] for i in result['issues']])

    def test_performance_credit_and_purchase_debit_have_distinct_sources(self):
        report = fixture(); data = report['gameplay_tracking']; event = data['events'][0]
        event['effects'].append({'kind': 'performance_change', 'field': 'vocal', 'amount': 10})
        event['field_evidence']['performance_change|vocal|'] = ['receipt.png']
        data['lesson_purchases'] = [{'id': 'lesson', 'receipt_event_id': 'event', 'source_timestamp_ms': 150,
                                    'evidence': ['receipt.png'], 'performance_cost': {'vocal': 10}, 'cost_basis': 'observed_debit'}]
        result = build(report)
        vocal = field(result, 'performance', 'vocal')
        self.assertEqual((vocal['direct_change'], vocal['derived_or_summary_change']), (10, -10))
        self.assertEqual(len(vocal['contribution_refs']), 2)
        self.assertEqual(len(result['comparisons'][1]['fields']), 5)

    def test_unobserved_and_projected_debits_do_not_close_accounting(self):
        report = fixture(); data = report['gameplay_tracking']
        data['lesson_purchases'] = [{'id': 'lesson', 'receipt_event_id': 'event', 'source_timestamp_ms': 150,
                                    'evidence': ['receipt.png'], 'performance_cost': {'vocal': 10}, 'cost_basis': 'projected'}]
        result = build(report)
        self.assertEqual(field(result, 'performance', 'vocal')['derived_or_summary_change'], 0)
        self.assertEqual(field(result, 'performance', 'vocal')['status'], 'unresolved_attribution')
        data['lesson_purchases'][0]['performance_cost'] = None
        self.assertIn('unobserved_purchase_debit', [i['kind'] for i in build(report)['issues']])

    def test_unknown_endpoint_and_amount_are_not_zero(self):
        report = fixture(); data = report['gameplay_tracking']
        data['checkpoints'][1]['values']['speed'] = None
        data['events'][0]['effects'][0]['amount'] = None
        result = build(report)
        self.assertEqual(field(result)['status'], 'missing_endpoint')
        self.assertIsNone(field(result)['unresolved_change'])

    def test_hints_and_conditions_remain_resolvable_without_stat_credits(self):
        report = fixture(); event = report['gameplay_tracking']['events'][0]
        event['effects'].append({'kind': 'skill_hint_change', 'name': 'Straightaway Recovery', 'amount': 1})
        result = build(report)
        self.assertEqual(len(result['contributions']), 1)
        self.assertEqual(result['other_effects'][0]['source_ref'], '/gameplay_tracking/events/0/effects/1')

    def test_turn_openings_expose_the_same_contributions_without_double_counting(self):
        report=fixture()
        def turn(index):
            return dict(id=f'turn-{index}',states={channel:dict(opening=dict(
                values_ref=f'/gameplay_tracking/{"checkpoints" if channel=="stats" else "performance_accounting/checkpoints"}/{index}/values',
                values=report['gameplay_tracking']['checkpoints'][index]['values'] if channel=='stats' else
                       report['gameplay_tracking']['performance_accounting']['checkpoints'][index]['values'],
                observed_at_ms=100 if index==0 else 200),closing=None) for channel in CHANNELS})
        report['turn_ledger']={'turns':[turn(0),turn(1)]}
        result=build(report)
        transition=result['turn_transitions'][0]
        self.assertEqual(transition['next_turn_id'],'turn-1')
        self.assertEqual(transition['fields'][0]['direct_change'],5)
        self.assertEqual(transition['fields'][0]['contribution_refs'],field(result)['contribution_refs'])
        self.assertEqual(transition['cause_refs'],['/gameplay_tracking/events/0'])
        self.assertEqual(len(result['contributions']),1)
        self.assertTrue(all(f['status']=='missing_endpoint' for f in result['turn_transitions'][-1]['fields']))
        report['turn_ledger']['turns'][0]['states']['stats']['opening']['values']=dict(speed=999)
        with self.assertRaises(ValueError):build(report)

    def test_promoted_partial_opening_binds_to_its_source_row_without_the_unreadable_field(self):
        # A promoted opening endpoint keeps only the integer fields of its
        # source row; the row itself still carries the unreadable field as
        # None.  The summary is source-bound field by field and must not
        # abort the run; a stated value that differs from the row still does.
        report=fixture()
        row=dict(source_timestamp_ms=100,evidence='home.png',stats=dict(values=dict(report['gameplay_tracking']['checkpoints'][0]['values'],speed=None)))
        report['gameplay_tracking']['readings'].insert(0,row)
        partial={f:v for f,v in row['stats']['values'].items() if type(v) is int}
        def turn(index):
            return dict(id=f'turn-{index}',states={channel:dict(opening=dict(
                values_ref=('/gameplay_tracking/readings/0/stats/values' if (channel=='stats' and index==0) else
                            f'/gameplay_tracking/{"checkpoints" if channel=="stats" else "performance_accounting/checkpoints"}/{index}/values'),
                values=(partial if (channel=='stats' and index==0) else
                        report['gameplay_tracking']['checkpoints'][index]['values'] if channel=='stats' else
                        report['gameplay_tracking']['performance_accounting']['checkpoints'][index]['values']),
                observed_at_ms=100 if index==0 else 200),closing=None) for channel in CHANNELS})
        report['turn_ledger']={'turns':[turn(0),turn(1)]}
        result=build(report)
        speed=next(f for f in result['turn_transitions'][0]['fields'] if f['field']=='speed')
        self.assertEqual(speed['status'],'missing_endpoint')
        stamina=next(f for f in result['turn_transitions'][0]['fields'] if f['field']=='stamina')
        self.assertEqual(stamina['status'],'balanced_observations')
        report['turn_ledger']['turns'][0]['states']['stats']['opening']['values']=dict(partial,stamina=999)
        with self.assertRaisesRegex(ValueError,'readings/0/stats/values'):build(report)


if __name__ == '__main__': unittest.main()
