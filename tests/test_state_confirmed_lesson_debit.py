import unittest

from tracen_replay.state_confirmed_lesson_debit import BASIS, state_confirmed_lesson_debit


def row(time, screen, points=None, effects=(), awards=None):
    facts = {}
    if points is not None:
        facts['performance_points'] = dict(zip(('dance', 'passion', 'vocal', 'visual', 'composure'), points))
    if awards:
        facts['awarded_performance_gains'] = awards
    return dict(source_timestamp_ms=time, screen=screen, evidence=f'{screen}-{time}.png', facts=facts, effects=list(effects))


BEFORE = (29, 19, 10, 10, 10)
AFTER = (29, 19, 10, 10, 0)


class StateConfirmedLessonDebitTests(unittest.TestCase):
    def setUp(self):
        self.event = dict(id='outcome-0009', first_seen_ms=148000, last_seen_ms=149000,
                          effects=[dict(kind='named_acquisition', name='Composure Training Basics')])
        self.before = [row(145750, 'lesson_selection', BEFORE), row(146000, 'lesson_selection', BEFORE)]

    def test_next_repeated_panel_on_the_training_menu_confirms_the_cost(self):
        readings = [*self.before, row(148000, 'event_outcome', effects=self.event['effects']),
                    *[row(t, 'unknown') for t in range(149250, 154000, 250)],
                    row(154000, 'training_preview', AFTER), row(154250, 'training_preview', AFTER)]
        result = state_confirmed_lesson_debit(readings, self.event, [], self.before, 'Composure Training Basics')
        self.assertEqual(result['cost'], dict(dance=0, passion=0, vocal=0, visual=0, composure=10))
        self.assertEqual(result['basis'], BASIS)
        self.assertEqual(result['proofs'][1]['source_timestamps_ms'], [154000, 154250])

    def test_another_purchase_or_award_in_between_rejects(self):
        quiet = [row(t, 'unknown') for t in range(149250, 150000, 250)]
        for intruder in (row(150000, 'lesson_confirmation'),
                         row(150000, 'event_outcome', effects=[dict(kind='song_learned', name='Other Song')]),
                         row(150000, 'unknown', effects=[dict(kind='performance_change', field='dance', amount=5)]),
                         row(150000, 'training_result', awards=dict(dance=5))):
            readings = [*self.before, *quiet, intruder, *[row(t, 'unknown') for t in range(150250, 154000, 250)],
                        row(154000, 'unknown', AFTER), row(154250, 'unknown', AFTER)]
            self.assertIsNone(state_confirmed_lesson_debit(readings, self.event, [], self.before, 'Composure Training Basics'))

    def test_a_sampling_gap_or_projection_conflict_rejects(self):
        readings = [*self.before, *[row(t, 'unknown') for t in range(149250, 151000, 250)],
                    row(154000, 'unknown', AFTER), row(154250, 'unknown', AFTER)]   # 3 s hole
        self.assertIsNone(state_confirmed_lesson_debit(readings, self.event, [], self.before, 'Composure Training Basics'))
        readings = [*self.before, *[row(t, 'unknown') for t in range(149250, 154000, 250)],
                    row(154000, 'unknown', AFTER), row(154250, 'unknown', AFTER)]
        group = [dict(facts=dict(projected_performance_points=dict(composure=4)))]
        self.assertIsNone(state_confirmed_lesson_debit(readings, self.event, group, self.before, 'Composure Training Basics'))
        group = [dict(facts=dict(projected_performance_points=dict(composure=0)))]
        self.assertIsNotNone(state_confirmed_lesson_debit(readings, self.event, group, self.before, 'Composure Training Basics'))

    def test_a_return_to_the_menu_without_a_purchase_is_allowed_and_the_callers_balance_is_used(self):
        readings = [row(147500, 'lesson_selection', BEFORE), *[row(t, 'lesson_selection') for t in range(149250, 151750, 250)],
                    *[row(t, 'unknown') for t in range(151750, 154000, 250)], row(154000, 'training_preview', AFTER), row(154250, 'training_preview', AFTER)]
        initial = dict(zip(('dance', 'passion', 'vocal', 'visual', 'composure'), BEFORE))
        result = state_confirmed_lesson_debit(readings, self.event, [], [readings[0]], 'Composure Training Basics', initial=initial)
        self.assertEqual(result['cost']['composure'], 10)
        self.assertIsNone(state_confirmed_lesson_debit(readings, self.event, [], [readings[0]], 'Composure Training Basics'))

    def test_a_menu_balance_or_a_receipt_with_awards_is_left_to_the_observed_path(self):
        quiet = [row(t, 'unknown') for t in range(149250, 154000, 250)]
        readings = [*self.before, *quiet, row(154000, 'lesson_selection', AFTER), row(154250, 'lesson_selection', AFTER)]
        self.assertIsNone(state_confirmed_lesson_debit(readings, self.event, [], self.before, 'Composure Training Basics'))
        readings = [*self.before, *quiet, row(154000, 'unknown', AFTER), row(154250, 'unknown', AFTER)]
        event = dict(self.event, effects=[*self.event['effects'], dict(kind='named_acquisition', name='Another Lesson')])
        self.assertIsNone(state_confirmed_lesson_debit(readings, event, [], self.before, 'Composure Training Basics'))
        event = dict(self.event, effects=[*self.event['effects'], dict(kind='performance_change', field='dance', amount=3)])
        self.assertIsNone(state_confirmed_lesson_debit(readings, event, [], self.before, 'Composure Training Basics'))

    def test_a_single_frame_or_rising_balance_is_not_accepted(self):
        quiet = [row(t, 'unknown') for t in range(149250, 154000, 250)]
        readings = [*self.before, *quiet, row(154000, 'unknown', AFTER), row(154250, 'unknown', (29, 19, 10, 10, 5))]
        self.assertIsNone(state_confirmed_lesson_debit(readings, self.event, [], self.before, 'Composure Training Basics'))
        readings = [*self.before, *quiet, row(154000, 'unknown', (29, 25, 10, 10, 0)), row(154250, 'unknown', (29, 25, 10, 10, 0))]
        self.assertIsNone(state_confirmed_lesson_debit(readings, self.event, [], self.before, 'Composure Training Basics'))


if __name__ == '__main__':
    unittest.main()
