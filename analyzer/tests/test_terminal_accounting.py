"""A late observation must not masquerade as a completed run balance."""
import copy
import unittest

from tracen_replay.causal_accounting import CHANNELS, build


def fixture():
    data = dict(auxiliary_log_used=False, events=[], readings=[], lesson_purchases=[])
    data['checkpoints'] = [dict(first_seen_ms=t, last_seen_ms=t, values=dict.fromkeys(CHANNELS['stats'], 100))
                           for t in (100, 200)]
    data['performance_accounting'] = dict(checkpoints=[
        dict(first_seen_ms=t, last_seen_ms=t, values=dict.fromkeys(CHANNELS['performance'], 20))
        for t in (100, 240)])
    states = {}
    for channel in CHANNELS:
        collection = 'checkpoints' if channel == 'stats' else 'performance_accounting/checkpoints'
        checkpoints = data['checkpoints'] if channel == 'stats' else data['performance_accounting']['checkpoints']
        states[channel] = {name: dict(values_ref=f'/gameplay_tracking/{collection}/{i}/values',
                                     values=copy.deepcopy(checkpoints[i]['values']),
                                     observed_at_ms=checkpoints[i]['first_seen_ms'])
                           for i, name in enumerate(('opening', 'closing'))}
    return dict(source=dict(sha256='a' * 64), gameplay_tracking=data,
                turn_ledger=dict(turns=[dict(id='turn-001', states=states)]))


class TerminalAccountingTests(unittest.TestCase):
    def test_last_sp_observation_precedes_purchase_and_is_not_a_final_balance(self):
        report = fixture()
        report['gameplay_tracking']['readings'] = [dict(evidence='purchase.png', source_timestamp_ms=250)]
        report['gameplay_tracking']['events'] = [dict(id='purchase', kind='skill_purchase_batch',
            first_seen_ms=250, last_seen_ms=250, deltas={'skill_points': -50}, evidence=['purchase.png'])]
        original = copy.deepcopy(report)
        result = build(report)
        stats, performance = result['turn_transitions']
        self.assertEqual(stats['terminal_observation']['observed_at_ms'], 200)
        self.assertEqual(performance['terminal_observation']['observed_at_ms'], 240)
        self.assertEqual(stats['terminal_observation']['later_numeric_contribution_refs'],
                         ['/gameplay_tracking/events/0/deltas/skill_points'])
        self.assertEqual(performance['terminal_observation']['later_numeric_contribution_refs'], [])
        self.assertFalse(stats['terminal_observation']['run_completion_verified'])
        self.assertEqual(next(f for f in stats['fields'] if f['field']=='skill_points')['after'], 100)
        self.assertTrue(all(f['next_turn']=='no_next_turn' for f in stats['endpoint_availability']))
        self.assertEqual(report, original)

    def test_missing_terminal_fields_keep_legacy_gap_count_and_do_not_claim_invisibility(self):
        report = fixture()
        for state in report['turn_ledger']['turns'][0]['states'].values():
            state['closing'] = None
        result = build(report)
        self.assertEqual(result['summary']['turn_field_status_counts'], {'career_end': 11})
        for transition in result['turn_transitions']:
            self.assertIsNone(transition['terminal_observation'])
            for field in transition['endpoint_availability']:
                self.assertEqual(field['opening'], 'observed')
                self.assertEqual(field['next_turn'], 'no_next_turn')
                self.assertEqual(field['terminal_observation'], 'not_observed')

    def test_missing_next_opening_is_distinct_from_no_next_turn(self):
        report = fixture()
        following = copy.deepcopy(report['turn_ledger']['turns'][0])
        following['id'] = 'turn-002'
        for state in following['states'].values():
            state['opening'] = None
            state['closing'] = None
        report['turn_ledger']['turns'].append(following)
        result = build(report)
        self.assertEqual(result['summary']['turn_field_status_counts'], {'missing_endpoint': 11, 'career_end': 11})
        for transition in result['turn_transitions'][:2]:
            self.assertEqual(transition['transition_kind'], 'between_turn_openings')
            for field in transition['endpoint_availability']:
                self.assertEqual(field['next_turn'], 'not_observed')
                self.assertEqual(field['terminal_observation'], 'not_applicable')

    def test_partial_terminal_observation_keeps_zero_and_unknown_distinct(self):
        report = fixture()
        values = report['gameplay_tracking']['checkpoints'][1]['values']
        values['skill_points'] = 0
        values.pop('speed')
        report['turn_ledger']['turns'][0]['states']['stats']['closing']['values'] = copy.deepcopy(values)
        result = build(report)
        availability = {f['field']: f for f in result['turn_transitions'][0]['endpoint_availability']}
        self.assertEqual(availability['skill_points']['terminal_observation'], 'observed')
        self.assertEqual(availability['speed']['terminal_observation'], 'not_observed')
        # No later screen can supply the end of the career's last turn.
        self.assertEqual(result['summary']['turn_field_status_counts']['career_end'], 1)

    def test_a_closing_read_across_the_ending_screens_is_bound_field_by_field(self):
        report = fixture()
        five = ('speed', 'stamina', 'power', 'guts', 'wit')
        report['gameplay_tracking']['readings'] = [
            dict(evidence='popup.png', source_timestamp_ms=300, screen='career_summary',
                 facts=dict(final_attributes=dict(speed=150, stamina=100, power=100, guts=100, wit=100))),
            dict(evidence='hub.png', source_timestamp_ms=320, screen='career_completion_hub', facts=dict(current_skill_points=40))]
        closing = report['turn_ledger']['turns'][0]['states']['stats']['closing']
        closing.update(values=dict(speed=150, stamina=100, power=100, guts=100, wit=100, skill_points=40), observed_at_ms=320,
                       values_ref='/gameplay_tracking/readings/1/facts',
                       field_sources={**{f: dict(values_ref=f'/gameplay_tracking/readings/0/facts/final_attributes/{f}', observed_at_ms=300) for f in five},
                                      'skill_points': dict(values_ref='/gameplay_tracking/readings/1/facts/current_skill_points', observed_at_ms=320)})
        rows = {f['field']: f for f in build(report)['turn_transitions'][0]['fields']}
        self.assertEqual((rows['speed']['after'], rows['speed']['status']), (150, 'unexplained_change'))
        self.assertEqual((rows['stamina']['after'], rows['stamina']['status']), (100, 'balanced_observations'))
        self.assertEqual((rows['skill_points']['after'], rows['skill_points']['status']), (40, 'unexplained_change'))
        # A field the screens never showed ends with the career.
        closing['values'].pop('guts'); closing['field_sources'].pop('guts')
        rows = {f['field']: f for f in build(report)['turn_transitions'][0]['fields']}
        self.assertEqual((rows['guts']['after'], rows['guts']['status']), (None, 'career_end'))
        # A value that is not what its own frame shows is refused.
        closing['values']['speed'] = 151
        with self.assertRaises(ValueError):
            build(report)


if __name__ == '__main__':
    unittest.main()
