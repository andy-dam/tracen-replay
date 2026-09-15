"""A turn whose only decision is a training with unread gains takes the turn difference, flagged as extrapolated."""
import unittest

from tracen_replay.causal_accounting import CHANNELS, build


def report(*, deltas=None, second_training=False, unparsed=None, residual_sign=1, race=False, outcome=False, lessons=(), performance_after=None):
    stats = dict.fromkeys(CHANNELS['stats'], 100)
    after = dict(stats, speed=100 + 9 * residual_sign, guts=100 + 13 * residual_sign, skill_points=100 + 8 * residual_sign)
    performance = dict.fromkeys(CHANNELS['performance'], 20)
    performance_after = dict(performance, **(performance_after or {}))
    events = [dict(id='training-1', kind='training', training_option='guts', first_seen_ms=150, last_seen_ms=150,
                   evidence='banner.png', deltas=dict(deltas or {}), field_evidence={f: ['banner.png'] for f in (deltas or {})}, effects=[])]
    receipts = [dict(kind='training', training_option='guts', source_timestamp_ms=150, event_id='training-1', evidence='banner.png')]
    timeline = [dict(id='entry-1', kind='committed_action', action_kind='training', turn_id='turn-001',
                     source_ref='/gameplay_tracking/turn_action_receipts/0', first_seen_ms=150, last_seen_ms=150)]
    if second_training:
        events.append(dict(id='training-2', kind='training', training_option='wit', first_seen_ms=170, last_seen_ms=170,
                           evidence='banner2.png', deltas={}, field_evidence={}, effects=[]))
        receipts.append(dict(kind='training', training_option='wit', source_timestamp_ms=170, event_id='training-2', evidence='banner2.png'))
        timeline.append(dict(id='entry-2', kind='committed_action', action_kind='training', turn_id='turn-001',
                             source_ref='/gameplay_tracking/turn_action_receipts/1', first_seen_ms=170, last_seen_ms=170))
    if outcome:
        events.append(dict(id='outcome-1', kind='outcome', first_seen_ms=160, last_seen_ms=165, evidence='outcome.png',
                           effects=[], field_evidence={}, conflicting_readings=[]))
    if race:
        receipts.append(dict(kind='race', source_timestamp_ms=180, evidence='race.png'))
        timeline.append(dict(id='entry-3', kind='committed_action', action_kind='race', turn_id='turn-001',
                             source_ref='/gameplay_tracking/turn_action_receipts/' + str(len(receipts) - 1), first_seen_ms=180, last_seen_ms=180))
    doc = {'source': {'sha256': 'example'}, 'gameplay_tracking': {
        'auxiliary_log_used': False,
        'readings': [dict(source_timestamp_ms=100, evidence='before.png'), dict(source_timestamp_ms=150, evidence='banner.png'),
                     dict(source_timestamp_ms=160, evidence='outcome.png'), dict(source_timestamp_ms=162, evidence='cut.png'),
                     dict(source_timestamp_ms=170, evidence='banner2.png'), dict(source_timestamp_ms=170, evidence='lesson.png'),
                     dict(source_timestamp_ms=180, evidence='race.png'),
                     dict(source_timestamp_ms=200, evidence='after.png')],
        'events': events,
        'checkpoints': [dict(first_seen_ms=0, last_seen_ms=100, values=stats, evidence='before.png'),
                        dict(first_seen_ms=200, last_seen_ms=250, values=after, evidence='after.png')],
        'performance_accounting': {'checkpoints': [dict(first_seen_ms=0, last_seen_ms=100, values=performance),
                                                   dict(first_seen_ms=200, last_seen_ms=250, values=performance_after)]},
        'lesson_purchases': list(lessons), 'song_acquisitions': [], 'turn_action_receipts': receipts,
        'unparsed_receipt_candidates': list(unparsed or [])},
        'turn_ledger': {'turns': [
            dict(id='turn-001', states=dict(
                stats=dict(opening=dict(values_ref='/gameplay_tracking/checkpoints/0/values', values=dict(stats), observed_at_ms=100), closing=None),
                performance=dict(opening=dict(values_ref='/gameplay_tracking/performance_accounting/checkpoints/0/values',
                                              values=dict(performance), observed_at_ms=100), closing=None))),
            dict(id='turn-002', states=dict(
                stats=dict(opening=dict(values_ref='/gameplay_tracking/checkpoints/1/values', values=dict(after), observed_at_ms=200), closing=None),
                performance=dict(opening=dict(values_ref='/gameplay_tracking/performance_accounting/checkpoints/1/values',
                                              values=dict(performance_after), observed_at_ms=200), closing=None)))],
            'timeline': timeline}}
    return doc


def turn_field(result, name, turn='turn-001', channel='stats'):
    tr = next(t for t in result['turn_transitions'] if t['turn_id'] == turn and t['channel'] == channel)
    return next(f for f in tr['fields'] if f['field'] == name)


class TurnDifferenceExtrapolationTests(unittest.TestCase):
    def test_the_only_training_takes_the_turn_difference_flagged(self):
        doc = report()
        result = build(doc)
        event = doc['gameplay_tracking']['events'][0]
        self.assertEqual(event['turn_difference_gains'], dict(speed=9, guts=13, skill_points=8))
        self.assertEqual(event['turn_difference_basis'], 'sole_training_takes_turn_residual')
        for name, amount in (('speed', 9), ('guts', 13), ('skill_points', 8)):
            row = turn_field(result, name)
            self.assertEqual((row['status'], row['unresolved_change'], row['derived_or_summary_change'], row['direct_change']),
                             ('balanced_with_derived_changes', 0, amount, 0), name)
        self.assertEqual({c['basis'] for c in result['contributions']}, {'turn_difference'})
        self.assertEqual(turn_field(result, 'stamina')['status'], 'balanced_observations')
        self.assertEqual(result['summary']['field_status_counts'].get('unexplained_change', 0), 0)

    def test_a_field_the_training_read_is_not_extrapolated(self):
        doc = report(deltas=dict(guts=10))
        result = build(doc)
        self.assertEqual(turn_field(result, 'guts')['status'], 'unexplained_change')
        self.assertNotIn('guts', doc['gameplay_tracking']['events'][0].get('turn_difference_gains', {}))

    def test_a_result_panel_read_without_the_field_did_not_raise_it(self):
        doc = report(deltas=dict(guts=13))
        result = build(doc)
        self.assertEqual(turn_field(result, 'guts')['status'], 'balanced_observations')
        self.assertEqual(turn_field(result, 'speed')['status'], 'unexplained_change')
        self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['events'][0])

    def test_a_conflicted_field_on_a_read_panel_still_takes_the_difference(self):
        doc = report(deltas=dict(guts=13))
        doc['gameplay_tracking']['events'][0]['conflicting_readings'] = {'speed': [9, 39]}
        result = build(doc)
        self.assertEqual(doc['gameplay_tracking']['events'][0]['turn_difference_gains'], dict(speed=9))
        self.assertEqual(turn_field(result, 'speed')['status'], 'balanced_with_derived_changes')
        self.assertEqual(turn_field(result, 'skill_points')['status'], 'unexplained_change')

    def test_an_uncommitted_training_with_nothing_read_is_still_the_turns_training(self):
        doc = report()
        doc['turn_ledger']['timeline'] = []
        doc['gameplay_tracking']['events'][0]['training_option'] = None
        result = build(doc)
        self.assertEqual(doc['gameplay_tracking']['events'][0]['turn_difference_gains'], dict(speed=9, guts=13, skill_points=8))
        self.assertEqual(turn_field(result, 'speed')['status'], 'balanced_with_derived_changes')

    def test_two_trainings_or_a_negative_difference_are_left_alone(self):
        for doc in (report(second_training=True), report(residual_sign=-1)):
            result = build(doc)
            self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['events'][0])
            self.assertEqual(turn_field(result, 'speed')['status'], 'unexplained_change')

    def test_an_unparsed_receipt_naming_the_field_blocks_it(self):
        doc = report(unparsed=[dict(first_seen_ms=160, last_seen_ms=160, raw_text='Speed went up by', evidence='x.png', status='unparsed')])
        result = build(doc)
        self.assertEqual(turn_field(result, 'speed')['status'], 'unexplained_change')
        self.assertEqual(turn_field(result, 'guts')['status'], 'balanced_with_derived_changes')

    def test_skill_points_are_not_extrapolated_when_a_race_was_run(self):
        doc = report(race=True)
        result = build(doc)
        self.assertEqual(turn_field(result, 'skill_points')['status'], 'unexplained_change')
        self.assertEqual(turn_field(result, 'guts')['status'], 'balanced_with_derived_changes')

    def test_the_one_receipt_that_lost_its_number_takes_the_turn_difference(self):
        caption = dict(first_seen_ms=161, last_seen_ms=164, raw_text='Speed went up by.', evidence=['cut.png'], status='needs_review')
        doc = report(deltas=dict(speed=3), outcome=True, unparsed=[caption])
        result = build(doc)
        outcome = doc['gameplay_tracking']['events'][1]
        self.assertEqual(outcome['turn_difference_gains'], dict(speed=6))
        self.assertEqual(outcome['turn_difference_basis'], 'sole_number_cut_receipt_takes_turn_residual')
        self.assertEqual(outcome['turn_difference_captions']['speed'][0]['raw_text'], 'Speed went up by.')
        row = turn_field(result, 'speed')
        self.assertEqual((row['status'], row['unresolved_change'], row['derived_or_summary_change']), ('balanced_with_derived_changes', 0, 6))
        owner = next(c for c in result['contributions'] if c['basis'] == 'turn_difference' and c['field'] == 'speed')
        self.assertEqual(owner['event_ref'], '/gameplay_tracking/events/1')
        # The training read its panel without a speed row, so it is not a possible owner.
        self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['events'][0])

    def test_remaining_digits_on_the_cut_receipt_must_agree_with_the_difference(self):
        for raw, expected in (('Speed went up by 6', 'balanced_with_derived_changes'), ('Speed went up by T0.', 'unexplained_change'),
                              ('Speed went up by 5', 'unexplained_change')):
            caption = dict(first_seen_ms=161, last_seen_ms=164, raw_text=raw, evidence=['cut.png'], status='needs_review')
            doc = report(deltas=dict(speed=3), outcome=True, unparsed=[caption])
            self.assertEqual(turn_field(build(doc), 'speed')['status'], expected, raw)

    def test_only_the_fields_own_caption_counts(self):
        for raw in ('Friendship with Kitasan Black is axed out.', 'Speed Bonus went up by', 'Speed went down by.'):
            caption = dict(first_seen_ms=161, last_seen_ms=164, raw_text=raw, evidence=['cut.png'], status='needs_review')
            doc = report(deltas=dict(speed=3), outcome=True, unparsed=[caption])
            result = build(doc)
            self.assertEqual(turn_field(result, 'speed')['status'], 'unexplained_change', raw)
            self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['events'][1], raw)

    def test_a_training_and_a_cut_receipt_for_the_same_field_leave_it_open(self):
        caption = dict(first_seen_ms=161, last_seen_ms=164, raw_text='Speed went up by.', evidence=['cut.png'], status='needs_review')
        doc = report(outcome=True, unparsed=[caption])
        result = build(doc)
        self.assertEqual(turn_field(result, 'speed')['status'], 'unexplained_change')
        self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['events'][1])
        self.assertEqual(doc['gameplay_tracking']['events'][0]['turn_difference_gains'], dict(guts=13, skill_points=8))

    def test_a_cut_receipt_whose_event_already_read_the_field_is_a_duplicate_line(self):
        caption = dict(first_seen_ms=161, last_seen_ms=164, raw_text='Speed went up by.', evidence=['cut.png'], status='needs_review')
        doc = report(outcome=True, unparsed=[caption])
        doc['gameplay_tracking']['events'][1]['effects'] = [dict(kind='stat_change', field='speed', amount=2)]
        doc['gameplay_tracking']['events'][1]['field_evidence'] = {'stat_change|speed|': ['outcome.png']}
        result = build(doc)
        # The training is then the only possible owner of what is left.
        self.assertEqual(doc['gameplay_tracking']['events'][0]['turn_difference_gains']['speed'], 7)
        self.assertEqual(turn_field(result, 'speed')['status'], 'balanced_with_derived_changes')

    def test_the_one_lesson_without_an_observed_cost_takes_a_negative_performance_difference(self):
        lesson = dict(id='lesson-1', kind='lesson', name='Audience Involvement', source_timestamp_ms=170, receipt_event_id=None,
                      performance_cost=None, cost_basis='unresolved', evidence='lesson.png')
        doc = report(lessons=[lesson], performance_after=dict(passion=4))
        result = build(doc)
        self.assertEqual(doc['gameplay_tracking']['lesson_purchases'][0]['turn_difference_cost'], dict(passion=16))
        self.assertEqual(doc['gameplay_tracking']['lesson_purchases'][0]['turn_difference_basis'], 'sole_unpriced_lesson_takes_turn_residual')
        row = turn_field(result, 'passion', channel='performance')
        self.assertEqual((row['status'], row['unresolved_change'], row['derived_or_summary_change']), ('balanced_with_derived_changes', 0, -16))
        cost = next(c for c in result['contributions'] if c['event_ref'] == '/gameplay_tracking/lesson_purchases/0')
        self.assertEqual((cost['basis'], cost['amount'], cost['channel']), ('turn_difference', -16, 'performance'))
        self.assertNotIn('turn_difference_performance_gains', doc['gameplay_tracking']['events'][0])

    def test_two_unpriced_lessons_or_a_priced_one_do_not_take_the_difference(self):
        unpriced = dict(id='lesson-1', kind='lesson', name='A', source_timestamp_ms=170, receipt_event_id=None,
                        performance_cost=None, cost_basis='unresolved', evidence='lesson.png')
        second = dict(unpriced, id='lesson-2', name='B', source_timestamp_ms=175)
        priced = dict(unpriced, id='lesson-3', name='C', performance_cost=dict(passion=10), cost_basis='observed_debit', receipt_event_id='outcome-1')
        for lessons in ([unpriced, second], [priced]):
            doc = report(lessons=lessons, performance_after=dict(passion=4), outcome=True)
            result = build(doc)
            self.assertEqual(turn_field(result, 'passion', channel='performance')['status'], 'unexplained_change')
            self.assertFalse(any('turn_difference_cost' in l for l in doc['gameplay_tracking']['lesson_purchases']))


if __name__ == '__main__':
    unittest.main()
