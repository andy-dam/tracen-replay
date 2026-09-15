import copy
import unittest

from tracen_replay.terminal_observations import build


def frame(time, screen, facts):
    return dict(source_timestamp_ms=time, screen=screen, facts=facts,
                evidence=f'frame-{time}.png')


def report(rows):
    return dict(gameplay_tracking=dict(readings=rows),
                turn_ledger=dict(turns=[dict(start_ms=100)]))


class TerminalObservationTests(unittest.TestCase):
    def test_separate_final_attributes_and_earlier_sp_after_later_purchase(self):
        data = report([
            frame(120, 'career_completion_hub', {'current_skill_points': 953}),
            frame(160, 'career_finish_confirmation', {
                'remaining_performance_points': {'dance': 17, 'passion': 10}}),
            frame(200, 'career_summary', {'final_attributes': {
                'speed': 931, 'stamina': 607, 'power': 822, 'guts': 586, 'wit': 1167}}),
        ])
        original = copy.deepcopy(data)
        debit = dict(id='purchase', channel='stats', field='skill_points',
                     observation_end_ms=150)
        observations = build(data, [debit])
        self.assertEqual(len(observations), 3)
        self.assertEqual(observations[0]['later_numeric_contribution_refs'],
                         {'skill_points': ['purchase']})
        self.assertNotIn('skill_points', observations[2]['values'])
        self.assertEqual(observations[1]['values'], {'dance': 17, 'passion': 10})
        self.assertTrue(all(not item['run_completion_verified'] for item in observations))
        self.assertEqual(data, original)

    def test_zero_is_observed_and_null_is_unknown_without_borrowing_fields(self):
        data = report([
            frame(120, 'career_completion_hub', {'current_skill_points': 25}),
            frame(200, 'career_summary', {'final_attributes': {'speed': 0, 'skill_points': None}}),
        ])
        observations = build(data, [])
        self.assertEqual(observations[1]['values'], {'speed': 0})
        self.assertEqual(observations[1]['value_refs']['speed'],
                         ['/gameplay_tracking/readings/1/facts/final_attributes/speed'])

    def test_conflicting_same_frame_sp_is_explicit_without_arbitrary_preference(self):
        observations = build(report([frame(200, 'career_completion_hub', {
            'current_skill_points': 25, 'final_attributes': {'skill_points': 28, 'speed': 931},
        })]), [])
        self.assertEqual(observations[0]['values'], {'speed': 931})
        self.assertEqual({row['value'] for row in observations[0]['conflicts']['skill_points']}, {25, 28})

    def test_new_readable_sp_is_not_replaced_by_older_balance(self):
        observations = build(report([
            frame(120, 'career_completion_hub', {'current_skill_points': 953}),
            frame(160, 'career_finish_confirmation', {'current_skill_points': 532}),
        ]), [dict(id='purchase', channel='stats', field='skill_points', observation_end_ms=150)])
        self.assertEqual(observations[-1]['values'], {'skill_points': 532})
        self.assertEqual(observations[-1]['later_numeric_contribution_refs']['skill_points'], [])

    def test_confirmed_purchase_with_unknown_cost_invalidates_earlier_sp(self):
        data = report([frame(120, 'career_completion_hub', {'current_skill_points': 953}),
                       frame(180, 'career_finish_confirmation', {'current_skill_points': 532})])
        data['gameplay_tracking']['events'] = [dict(kind='skill_purchase_batch',
            first_seen_ms=150, last_seen_ms=160, deltas={})]
        observations = build(data, [])
        self.assertEqual(observations[0]['later_numeric_contribution_refs']['skill_points'], [])
        self.assertEqual(observations[0]['later_unquantified_change_refs']['skill_points'],
                         ['/gameplay_tracking/events/0'])
        self.assertEqual(observations[1]['later_unquantified_change_refs']['skill_points'], [])

    def test_preview_and_earlier_career_panels_are_not_terminal_observations(self):
        rows = [frame(50, 'career_summary', {'final_attributes': {'speed': 1500}}),
                frame(150, 'training_preview', {'current_skill_points': 800}),
                frame(160, 'career_summary', {'final_attributes': {'speed': None}})]
        self.assertEqual(build(report(rows), []), [])
        self.assertEqual(build({'gameplay_tracking': {'readings': rows}}, []), [])


if __name__ == '__main__':
    unittest.main()
