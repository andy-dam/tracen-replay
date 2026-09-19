import unittest

from tracen_replay.vision import withhold_projection_without_badge


class TrainingBadgeGateTests(unittest.TestCase):
    def test_a_result_frame_without_a_badge_keeps_its_projection_aside(self):
        facts = dict(training_gains={}, awarded_performance_gains={'visual': 19})
        withhold_projection_without_badge(facts, 'training_result')
        self.assertNotIn('awarded_performance_gains', facts)
        self.assertEqual(facts['unawarded_performance_projection'], {'visual': 19})
        self.assertEqual(facts['unawarded_performance_basis'], 'no_stat_badge_on_frame')

    def test_a_badge_on_the_frame_lets_the_award_stand(self):
        facts = dict(training_gains={'guts': 12}, awarded_performance_gains={'visual': 19})
        withhold_projection_without_badge(facts, 'training_result')
        self.assertEqual(facts['awarded_performance_gains'], {'visual': 19})
        self.assertNotIn('unawarded_performance_projection', facts)

    def test_other_screens_and_frames_without_awards_are_untouched(self):
        for facts, screen in ((dict(training_gains={}, awarded_performance_gains={'visual': 19}), 'training_preview'),
                              (dict(training_gains={}), 'training_result'),
                              (dict(awarded_performance_gains={}), 'training_result')):
            before = dict(facts)
            withhold_projection_without_badge(facts, screen)
            self.assertEqual(facts, before)


if __name__ == '__main__':
    unittest.main()
