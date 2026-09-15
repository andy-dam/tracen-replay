"""A badge larger than the panels around the training allow is a conflict, not a gain."""
import unittest

from tracen_replay.transactions import training_events


BEFORE = dict(speed=297, stamina=274, power=216, guts=281, wit=281, skill_points=412)
AFTER = dict(speed=297, stamina=299, power=226, guts=281, wit=281, skill_points=416)


def home(time, values):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='unknown', facts={}, stats=dict(values=dict(values), training_preview=False, preview_option=None))


def result(time, gains):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='training_result', training_option='power', stats=dict(values=None),
                facts=dict(training_outcome='success', training_gains=dict(gains)))


class BadgeContradictionTests(unittest.TestCase):
    def test_a_badge_above_the_panel_difference_becomes_a_conflict(self):
        rows = [home(400000, BEFORE), home(400250, BEFORE), result(407100, dict(stamina=51, power=10, skill_points=4)), result(407350, dict(stamina=51, power=10, skill_points=4)),
                home(413750, AFTER), home(414000, AFTER)]
        events = training_events(rows)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertNotIn('stamina', event['deltas'])
        self.assertEqual(event['deltas'], dict(power=10, skill_points=4))
        self.assertEqual(event['conflicting_readings']['stamina'], [25, 51])
        self.assertEqual(event['gain_contradictions']['stamina']['allowed'], 25)

    def test_a_badge_within_the_difference_is_kept(self):
        rows = [home(400000, BEFORE), home(400250, BEFORE), result(407100, dict(stamina=25, power=10, skill_points=4)), result(407350, dict(stamina=25, power=10, skill_points=4)),
                home(413750, AFTER), home(414000, AFTER)]
        event = training_events(rows)[0]
        self.assertEqual(event['deltas'], dict(stamina=25, power=10, skill_points=4))
        self.assertNotIn('gain_contradictions', event)

    def test_without_both_panels_nothing_is_judged(self):
        rows = [result(407100, dict(stamina=51)), result(407350, dict(stamina=51))]
        event = training_events(rows)[0]
        self.assertEqual(event['deltas'], dict(stamina=51))


if __name__ == '__main__':
    unittest.main()
