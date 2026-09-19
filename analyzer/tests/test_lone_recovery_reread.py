import unittest

from tracen_replay.receipt_recovery import plan, scoped_observations


def recovery_event(**overrides):
    event = dict(id='outcome-0125', kind='outcome', first_seen_ms=981867, last_seen_ms=981867,
                 evidence='gameplay/part-008-frame-000083.png', context_title=None,
                 effects=[dict(kind='energy_change', amount=40, raw_text='Energy recovered by 40.'),
                          dict(kind='mood_status', value='great', amount=None, raw_text='Mood remains Great.')])
    event.update(overrides)
    return event


class LoneRecoveryRereadTests(unittest.TestCase):
    def test_a_recovery_receipt_seen_on_one_frame_is_reread_around_that_frame(self):
        windows = plan([], [recovery_event()], 2_400_000)
        self.assertEqual([(w['start_ms'], w['end_ms'], w['reason']) for w in windows],
                         [(981367, 982367, 'lone_recovery_receipt')])
        trigger = windows[0]['triggers'][0]
        self.assertEqual((trigger['kind'], trigger['field'], trigger['owner_ref']), ('energy_change', None, 'outcome-0125'))
        self.assertEqual((trigger['owner_start_ms'], trigger['owner_end_ms']), (981367, 982367))

    def test_a_repeated_receipt_or_one_under_an_event_title_is_not_reread(self):
        self.assertEqual(plan([], [recovery_event(last_seen_ms=982133)], 2_400_000), [])
        self.assertEqual(plan([], [recovery_event(context_title='A Day at the Beach')], 2_400_000), [])
        self.assertEqual(plan([], [recovery_event(effects=[dict(kind='mood_status', value='great', amount=None)])], 2_400_000), [])

    def test_the_reread_frames_with_the_recovery_are_promoted_as_readings(self):
        windows = plan([], [recovery_event()], 2_400_000)
        fresh = [dict(source_timestamp_ms=981700, evidence='dense/frame-000001.png', screen='event_outcome', facts={},
                      effects=[dict(kind='energy_change', amount=40, raw_text='Energy recovered by 40.'),
                               dict(kind='mood_status', value='great', amount=None, raw_text='Mood remains Great.')]),
                 dict(source_timestamp_ms=981733, evidence='dense/frame-000002.png', screen='unknown', facts={}, effects=[])]
        promoted = scoped_observations([], fresh, windows)
        self.assertEqual([(row['source_timestamp_ms'], [e['kind'] for e in row['effects']]) for row in promoted],
                         [(981700, ['energy_change'])])
        self.assertEqual(promoted[0]['facts']['numeric_receipt_recovery']['requested_owner_ref'], 'outcome-0125')


if __name__ == '__main__':
    unittest.main()
