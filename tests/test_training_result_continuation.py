import unittest

from tracen_replay.transactions import training_events


def result(time, option=None, gains=None, outcome=None, title=None, totals=None):
    facts = {}
    if outcome: facts['training_outcome'] = outcome
    if gains is not None: facts['training_gains'] = dict(gains)
    if totals is not None: facts['result_values'] = dict(totals)
    row = dict(source_timestamp_ms=time, screen='training_result', evidence=f'gameplay/{time}.png', stats={}, effects=[], facts=facts)
    if option: row['training_option'] = option
    if title: row['context_title'] = title
    return row


def receipt(time):
    return dict(source_timestamp_ms=time, screen='event_outcome', evidence=f'gameplay/{time}.png', stats={}, effects=[], facts={}, context_title='Exercise Bike')


REAL = dict(speed=35, power=21, skill_points=16)


class ResultContinuationTests(unittest.TestCase):
    def test_empty_optionless_frames_after_the_receipt_join_the_training(self):
        rows = [result(1217750, 'speed', REAL, 'success'), result(1217767, 'speed', REAL, 'success'),
                receipt(1219000), result(1219250), result(1219500), result(1219750)]
        events = training_events(rows)
        self.assertEqual([(e['training_option'], e['deltas'], e['first_seen_ms'], e['last_seen_ms']) for e in events],
                         [('speed', REAL, 1217750, 1217767)])
        self.assertEqual([o['source_timestamp_ms'] for o in events[0]['result_continuation_observations']], [1219250, 1219500, 1219750])

    def test_a_late_or_payload_bearing_group_stays_separate(self):
        base = [result(1217750, 'speed', REAL, 'success'), result(1217767, 'speed', REAL, 'success')]
        # More than three seconds later: not a continuation.
        events = training_events(base + [result(1221000), result(1221250)])
        self.assertEqual(len(events), 2)
        # Carries an outcome banner: kept as its own group.
        events = training_events(base + [result(1219250, outcome='success'), result(1219500, outcome='success')])
        self.assertEqual(len(events), 2)
        # Carries result totals: kept as its own group.
        events = training_events(base + [result(1219250, totals=dict(speed=334)), result(1219500, totals=dict(speed=334))])
        self.assertEqual(len(events), 2)
        # Carries gains without an option: kept.
        events = training_events(base + [result(1219250, gains=dict(power=5)), result(1219500, gains=dict(power=5))])
        self.assertEqual(len(events), 2)

    def test_unread_none_fields_are_not_a_payload(self):
        # The result parser records an unreadable card as None; such frames
        # after the receipt are still a continuation.
        rows = [result(1217750, 'speed', REAL, 'success'), result(1217767, 'speed', REAL, 'success'), receipt(1219000),
                result(1219250, gains={}, totals=dict(skill_points=None)), result(1219500, gains={}, totals=dict(skill_points=None))]
        events = training_events(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]['result_continuation_observations']), 2)

    def test_the_same_heading_on_a_trailing_frame_is_still_a_continuation(self):
        # The option heading and the training name can outlast the badges
        # by a frame; the same option within three seconds is the same result.
        rows = [result(760750, 'wit', dict(speed=15, wit=30, skill_points=14), 'success'),
                result(760767, 'wit', dict(speed=15, wit=30, skill_points=14), 'success'),
                receipt(761000), result(761500, 'wit', gains={}, title='Push-Button Quiz', totals=dict(skill_points=None)),
                result(761550, gains={}, totals=dict(skill_points=None))]
        events = training_events(rows)
        self.assertEqual([(e['training_option'], e['deltas']) for e in events], [('wit', dict(speed=15, wit=30, skill_points=14))])
        self.assertEqual([o['source_timestamp_ms'] for o in events[0]['result_continuation_observations']], [761500, 761550])

    def test_another_option_on_a_trailing_frame_starts_its_own_group(self):
        rows = [result(760750, 'wit', dict(speed=15, wit=30, skill_points=14), 'success'),
                result(760767, 'wit', dict(speed=15, wit=30, skill_points=14), 'success'),
                result(761500, 'speed', gains={}, totals=dict(skill_points=None)), result(761550, 'speed', gains={}, totals=dict(skill_points=None))]
        self.assertEqual([e['training_option'] for e in training_events(rows)], ['wit', 'speed'])

    def test_a_continuation_never_follows_an_optionless_group(self):
        rows = [result(1217750, gains=dict(power=5)), result(1217767, gains=dict(power=5)), result(1219250), result(1219500)]
        self.assertEqual(len(training_events(rows)), 2)


if __name__ == '__main__':
    unittest.main()
