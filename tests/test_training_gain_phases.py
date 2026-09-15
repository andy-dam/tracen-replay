import unittest

from tracen_replay.transactions import training_actions, training_events


FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points')


def result_row(timestamp, gains, option='wit'):
    return dict(
        source_timestamp_ms=timestamp,
        evidence=f'{timestamp}.png',
        screen='training_result',
        training_option=option,
        facts={
            'training_gains': dict(gains),
            'training_gain_candidates': {field: [amount] for field, amount in gains.items()},
        },
        effects=[],
        stats={},
    )


def before_state(skill_points=606):
    return dict(
        first_seen_ms=0,
        last_seen_ms=0,
        values=dict(speed=100, stamina=100, power=100, guts=100, wit=100, skill_points=skill_points),
        evidence='before.png',
    )


class TrainingGainPhaseTests(unittest.TestCase):
    def test_result_group_preserves_result_membership_separately_from_identity(self):
        rows = [
            result_row(1000, {'speed': 8}),
            result_row(1033, {'speed': 8}),
            result_row(1066, {'speed': 8}, option=None),
        ]

        event = training_events(rows)[0]
        group = event['result_group']
        self.assertEqual(group['interval_ms'], [1000, 1066])
        self.assertEqual(group['training_option'], 'wit')
        self.assertEqual(
            [(row['source_timestamp_ms'], row['evidence']) for row in group['observations']],
            [(row['source_timestamp_ms'], row['evidence']) for row in rows],
        )
        self.assertEqual(event['action_identity_evidence'], ['1000.png', '1033.png'])

        action = training_actions([event])[0]
        self.assertEqual(action['result_group'], group)

    def test_unknown_option_does_not_count_as_observed_action_identity(self):
        rows = [result_row(t, {'speed': 8}, option=None) for t in (1000, 1033)]
        event = training_events(rows)[0]
        self.assertIsNone(event['training_option'])
        self.assertEqual(event['action_identity_evidence'], [])
        self.assertEqual(event['action_identity_observations'], 0)
        self.assertEqual(event['deltas']['speed'], 8)

    def test_repeated_full_result_records_strict_component_candidate_without_accounting(self):
        rows = [
            result_row(1000, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1033, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1066, {'speed': 8, 'skill_points': 1}, option=None),
            result_row(1099, {'speed': 8, 'skill_points': 1}),
        ]

        event = training_events(rows)[0]

        self.assertNotIn('skill_points', event['deltas'])
        self.assertEqual(event['conflicting_readings']['skill_points'], [1, 11])
        candidate = event['gain_phase_candidates']['skill_points']
        self.assertEqual(candidate['basis'], 'repeated_full_gain_shape_strictly_contains_all_component_shapes')
        self.assertEqual(candidate['shape'], ['skill_points', 'speed', 'wit'])
        self.assertEqual(candidate['component_shapes'], {'1': [['skill_points', 'speed']]})
        self.assertEqual(candidate['phase_order'], 'full_before_component')
        self.assertEqual(candidate['full_last_seen_ms'], 1033)
        self.assertEqual(candidate['component_first_seen_ms'], 1066)

    def test_equal_shape_alternatives_remain_ambiguous_even_when_larger(self):
        rows = [
            result_row(1000, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1033, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1066, {'speed': 8, 'wit': 21, 'skill_points': 14}),
            result_row(1099, {'speed': 8, 'wit': 21, 'skill_points': 14}),
        ]

        event = training_events(rows, [before_state()])[0]

        self.assertNotIn('skill_points', event['deltas'])
        self.assertEqual(event['conflicting_readings']['skill_points'], [11, 14])
        self.assertNotIn('gain_phase_candidates', event)

    def test_two_full_gain_candidates_stay_ambiguous_with_a_component_present(self):
        rows = [
            result_row(1000, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1033, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1066, {'speed': 8, 'skill_points': 1}, option=None),
            result_row(1099, {'speed': 8, 'wit': 21, 'skill_points': 14}),
            result_row(1132, {'speed': 8, 'wit': 21, 'skill_points': 14}),
        ]

        event = training_events(rows, [before_state()])[0]

        self.assertNotIn('skill_points', event['deltas'])
        self.assertEqual(event['conflicting_readings']['skill_points'], [1, 11, 14])
        self.assertNotIn('gain_phase_candidates', event)

    def test_single_component_observation_is_not_enough_phase_evidence(self):
        rows = [
            result_row(1000, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1033, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1066, {'speed': 8, 'skill_points': 1}, option=None),
        ]

        event = training_events(rows)[0]

        self.assertNotIn('skill_points', event['deltas'])
        self.assertEqual(event['conflicting_readings']['skill_points'], [1, 11])
        self.assertNotIn('gain_phase_candidates', event)

    def test_reversed_full_and_component_phases_remain_ambiguous(self):
        rows = [
            result_row(1000, {'speed': 8, 'skill_points': 1}, option=None),
            result_row(1033, {'speed': 8, 'skill_points': 1}, option=None),
            result_row(1066, {'speed': 8, 'wit': 21, 'skill_points': 11}),
            result_row(1099, {'speed': 8, 'wit': 21, 'skill_points': 11}),
        ]

        event = training_events(rows)[0]

        self.assertNotIn('skill_points', event['deltas'])
        self.assertEqual(event['conflicting_readings']['skill_points'], [1, 11])
        self.assertNotIn('gain_phase_candidates', event)


if __name__ == '__main__':
    unittest.main()
