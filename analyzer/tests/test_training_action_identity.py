"""A completed training named on two frames is the turn's action."""
import unittest

from tracen_replay.transactions import training_actions


def training(deltas=None, result_frames=1, names=('Shogi', 'Shogi'), name='Shogi'):
    observations = [dict(name=n, source_timestamp_ms=2042750 + 500 * i, evidence=f'{i}.png') for i, n in enumerate(names)]
    return dict(id='training-0052', kind='training', training_option='wit', first_seen_ms=2042750, last_seen_ms=2042750,
                evidence='2042750.png', deltas=deltas or {}, action_identity_observations=result_frames,
                action_identity_evidence=['2042750.png'], training_outcome='success', effect_coverage_verified=False,
                training_name=name, training_name_observations=observations)


class TrainingActionIdentityTests(unittest.TestCase):
    def test_two_frames_naming_the_training_commit_the_action(self):
        actions = training_actions([training()])
        self.assertEqual(len(actions), 1)
        self.assertEqual((actions[0]['training_option'], actions[0]['source_timestamp_ms'], actions[0]['identity_basis']),
                         ('wit', 2042750, 'repeated_training_name'))

    def test_one_frame_with_one_name_is_not_enough(self):
        self.assertEqual(training_actions([training(names=('Shogi',))]), [])

    def test_two_frames_with_different_names_are_not_an_identity(self):
        self.assertEqual(training_actions([training(names=('Shogi', 'Floor Cleaning'))]), [])

    def test_names_on_the_same_frame_count_once(self):
        event = training(names=('Shogi', 'Shogi'))
        for o in event['training_name_observations']:
            o['source_timestamp_ms'] = 2042750
        self.assertEqual(training_actions([event]), [])

    def test_read_gains_and_repeated_result_frames_keep_their_bases(self):
        by_gains = training_actions([training(deltas={'wit': 12}, names=('Shogi',))])
        by_frames = training_actions([training(result_frames=3, names=('Shogi',))])
        self.assertEqual(by_gains[0]['identity_basis'], 'observed_gains')
        self.assertEqual(by_frames[0]['identity_basis'], 'repeated_result_frames')


if __name__ == '__main__':
    unittest.main()
