import unittest

from tracen_replay.training_gain_resolution import resolve_occluded_prefix, resolve_prefix


def obs(sequence, start=1548583, step=17):
    rows = []
    for index, value in enumerate(sequence):
        time = start + index * step
        rows.append((dict(source_timestamp_ms=time, evidence=f'recovery/frame-{index:06d}.png', facts=dict(training_gains=dict(speed=value))), value))
    return rows


class OccludedPrefixTests(unittest.TestCase):
    def test_a_complete_badge_repeated_on_three_frames_beats_a_more_frequent_prefix(self):
        # Sparkles hid the trailing digit on seven of ten dense frames.
        sequence = [1, 1, 13, 13, 1, 1, 1, 1, 13, 1]
        self.assertIsNone(resolve_prefix(obs(sequence)))
        resolved = resolve_occluded_prefix(obs(sequence))
        self.assertEqual(resolved['accepted_amount'], 13)
        self.assertEqual((resolved['complete_frames'], resolved['prefix_frames']), (3, 7))
        self.assertEqual(resolved['basis'], 'repeated_complete_badge_with_occluded_prefix_observations')
        self.assertEqual(len(resolved['complete_observations']), 3)

    def test_fewer_complete_frames_a_non_prefix_or_a_third_value_abstain(self):
        self.assertIsNone(resolve_occluded_prefix(obs([1, 1, 13, 1, 1, 13, 1])))
        self.assertIsNone(resolve_occluded_prefix(obs([3, 3, 13, 13, 13, 3])))
        self.assertIsNone(resolve_occluded_prefix(obs([1, 13, 13, 13, 12, 1])))
        self.assertIsNone(resolve_occluded_prefix(obs([13, 13, 13, 13])))

    def test_duplicate_frames_and_wide_spans_are_rejected(self):
        rows = obs([1, 1, 13, 13, 13, 1])
        rows.append((dict(rows[2][0]), 13))
        self.assertIsNone(resolve_occluded_prefix(rows))
        self.assertIsNone(resolve_occluded_prefix(obs([1, 1, 13, 13, 13, 1], step=250)))


if __name__ == '__main__':
    unittest.main()


class PrefixResolvedFieldsStayInRereadScopeTests(unittest.TestCase):
    def test_plan_keeps_a_prefix_resolved_field_beside_the_conflicting_ones(self):
        from tracen_replay.training_gain_recovery import plan
        rows = [dict(source_timestamp_ms=t, screen='training_result', evidence=f'gameplay/{t}.png',
                     facts=dict(training_outcome='success', training_gains=dict(speed=v, skill_points=13)))
                for t, v in ((1000, 10), (1250, 16))]
        event = dict(id='training-0001', kind='training', training_option='wit', first_seen_ms=1000, last_seen_ms=1250,
                     deltas=dict(skill_points=13), conflicting_readings=dict(speed=[10, 16]),
                     gain_prefix_resolutions=dict(skill_points=dict(accepted_amount=13)))
        requests = plan(rows, [event])
        self.assertEqual([(r['owner_id'], r['fields'], r['reason']) for r in requests],
                         [('training-0001', ['skill_points', 'speed'], 'conflicting_observed_training_badge_digits')])
