"""A turn whose only decision is a training with unread gains takes the turn difference, flagged as extrapolated."""
import unittest

from tracen_replay.causal_accounting import CHANNELS, build


def report(*, deltas=None, second_training=False, unparsed=None, residual_sign=1, race=False, outcome=False, lessons=(), performance_after=None, batches=(), card_only=False):
    stats = dict.fromkeys(CHANNELS['stats'], 100)
    after = dict(stats, speed=100 + 9 * residual_sign, guts=100 + 13 * residual_sign, skill_points=100 + 8 * residual_sign)
    performance = dict.fromkeys(CHANNELS['performance'], 20)
    performance_after = dict(performance, **(performance_after or {}))
    events = [dict(id='training-1', kind='training', training_option='guts', first_seen_ms=150, last_seen_ms=150,
                   evidence='banner.png', deltas=dict(deltas or {}), field_evidence={f: ['banner.png'] for f in (deltas or {})}, effects=[])]
    receipts = [dict(kind='training', training_option='guts', source_timestamp_ms=150, event_id='training-1', evidence='banner.png')]
    timeline = [dict(id='entry-1', kind='committed_action', action_kind='training', turn_id='turn-001',
                     source_ref='/gameplay_tracking/turn_action_receipts/0', event_ref='/gameplay_tracking/events/0',
                     first_seen_ms=150, last_seen_ms=150)]
    if card_only:
        # Committed from its result card alone: no action receipt.
        receipts = []
        timeline[0].update(source_ref='/gameplay_tracking/events/0', identity_basis='result_card_only')
    if second_training:
        events.append(dict(id='training-2', kind='training', training_option='wit', first_seen_ms=170, last_seen_ms=170,
                           evidence='banner2.png', deltas={}, field_evidence={}, effects=[]))
        receipts.append(dict(kind='training', training_option='wit', source_timestamp_ms=170, event_id='training-2', evidence='banner2.png'))
        timeline.append(dict(id='entry-2', kind='committed_action', action_kind='training', turn_id='turn-001',
                             source_ref='/gameplay_tracking/turn_action_receipts/1', event_ref='/gameplay_tracking/events/1',
                             first_seen_ms=170, last_seen_ms=170))
    if outcome:
        events.append(dict(id='outcome-1', kind='outcome', first_seen_ms=160, last_seen_ms=165, evidence='outcome.png',
                           effects=[], field_evidence={}, conflicting_readings=[]))
    if race:
        receipts.append(dict(kind='race', source_timestamp_ms=180, evidence='race.png', race_id='race-1'))
        timeline.append(dict(id='entry-3', kind='committed_action', action_kind='race', turn_id='turn-001',
                             source_ref='/gameplay_tracking/turn_action_receipts/' + str(len(receipts) - 1), first_seen_ms=180, last_seen_ms=180))
    for batch in batches:
        events.append(dict(batch))
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
        'races': [dict(id='race-1', first_seen_ms=180, last_seen_ms=180, evidence='race.png')] if race else [],
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


def batch(**overrides):
    """A committed skill purchase whose charge was never read."""
    return dict(dict(id='skills-1', kind='skill_purchase_batch', first_seen_ms=170, last_seen_ms=175,
                     evidence='lesson.png', deltas={}, field_evidence={}, effects=[],
                     spent_skill_points=None, cost_basis='unresolved'), **overrides)


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

    def test_a_training_committed_from_its_result_card_alone_takes_the_turn_difference(self):
        doc = report(card_only=True)
        result = build(doc)
        self.assertEqual(doc['gameplay_tracking']['events'][0]['turn_difference_gains'], dict(speed=9, guts=13, skill_points=8))
        self.assertEqual(turn_field(result, 'speed')['status'], 'balanced_with_derived_changes')

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

    def test_a_clipped_badge_is_completed_by_the_turn_difference(self):
        # The panel read speed 1; the turn rose by 10 more: the badge was the leading digit of 11.
        doc = report(deltas=dict(speed=1, guts=13, skill_points=8))
        doc['gameplay_tracking']['checkpoints'][1]['values']['speed'] = 111
        doc['turn_ledger']['turns'][1]['states']['stats']['opening']['values']['speed'] = 111
        result = build(doc)
        event = doc['gameplay_tracking']['events'][0]
        self.assertEqual(event['turn_difference_gains'], dict(speed=10))
        self.assertEqual(event['turn_difference_basis'], 'sole_training_clipped_badge_prefix_completed_by_turn_residual')
        self.assertEqual(event['turn_difference_completions']['speed'], dict(mode='clipped_badge_prefix', read=1, completed=11))
        row = turn_field(result, 'speed')
        self.assertEqual((row['status'], row['direct_change'], row['derived_or_summary_change']), ('balanced_with_derived_changes', 1, 10))
        self.assertFalse(any(i['kind'] == 'multiple_effect_claims_for_one_event_field' for i in result['issues']))
        completion = next(c for c in result['contributions'] if c['basis'] == 'turn_difference')
        self.assertEqual(completion['completes'], '/gameplay_tracking/events/0/deltas/speed')

    def test_a_read_value_that_is_not_a_prefix_of_the_total_stays_open(self):
        doc = report(deltas=dict(speed=5, guts=13, skill_points=8))
        doc['gameplay_tracking']['checkpoints'][1]['values']['speed'] = 115
        doc['turn_ledger']['turns'][1]['states']['stats']['opening']['values']['speed'] = 115
        result = build(doc)
        self.assertEqual(turn_field(result, 'speed')['status'], 'unexplained_change')
        self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['events'][0])

    def test_a_visible_candidate_equal_to_the_difference_is_confirmed(self):
        # The panel read guts and skill points; a result frame showed speed 9 that a conflicting frame dropped.
        doc = report(deltas=dict(guts=13, skill_points=8))
        doc['gameplay_tracking']['readings'][1].update(screen='training_result', facts=dict(training_gain_candidates=dict(speed=[9])))
        result = build(doc)
        event = doc['gameplay_tracking']['events'][0]
        self.assertEqual(event['turn_difference_gains'], dict(speed=9))
        self.assertEqual(event['turn_difference_basis'], 'sole_training_visible_candidate_completed_by_turn_residual')
        self.assertEqual(turn_field(result, 'speed')['status'], 'balanced_with_derived_changes')

    def learned(self, doc, **gains):
        """The banner frame as a result frame on which the learned reader read these gains."""
        fields = {field: dict(text=f'+{gain}', confidence=0.99, value=None, gain=gain) for field, gain in gains.items()}
        doc['gameplay_tracking']['readings'][1].update(screen='training_result', facts=dict(
            learned_result_reads=dict(model_sha256='f' * 64, threshold=0.9, fields=fields)))

    def test_a_learned_gain_equal_to_the_difference_is_observed(self):
        doc = report()
        self.learned(doc, speed=9)
        result = build(doc)
        event = doc['gameplay_tracking']['events'][0]
        self.assertEqual(event['learned_reader_gains'], dict(speed=9))
        self.assertEqual(event['learned_reader_frames'], dict(speed=['banner.png']))
        self.assertEqual(event['turn_difference_gains'], dict(guts=13, skill_points=8))
        row = turn_field(result, 'speed')
        self.assertEqual((row['status'], row['direct_change'], row['derived_or_summary_change']), ('balanced_observations', 9, 0))
        self.assertEqual(turn_field(result, 'guts')['status'], 'balanced_with_derived_changes')
        learned = next(c for c in result['contributions'] if c['field'] == 'speed')
        self.assertEqual((learned['basis'], learned['evidence']), ('observed_learned_training_gain', ['banner.png']))

    def test_a_learned_gain_that_differs_from_the_difference_changes_nothing(self):
        doc = report()
        self.learned(doc, speed=8)
        result = build(doc)
        self.assertNotIn('learned_reader_gains', doc['gameplay_tracking']['events'][0])
        self.assertEqual(turn_field(result, 'speed')['status'], 'balanced_with_derived_changes')

    def test_a_panel_read_without_the_field_can_own_what_the_learned_reader_saw(self):
        # The recognizer read guts only; speed stayed unexplained until the learned reader saw +9.
        doc = report(deltas=dict(guts=13))
        self.learned(doc, speed=9)
        result = build(doc)
        self.assertEqual(doc['gameplay_tracking']['events'][0]['learned_reader_gains'], dict(speed=9))
        self.assertEqual(turn_field(result, 'speed')['status'], 'balanced_observations')
        self.assertEqual(turn_field(result, 'skill_points')['status'], 'unexplained_change')

    def test_skill_points_the_learned_reader_saw_on_the_card_go_to_the_training_not_the_race(self):
        doc = report(deltas=dict(speed=9, guts=13), race=True)
        without = build(doc)
        self.assertEqual(doc['gameplay_tracking']['races'][0].get('turn_difference_gains'), dict(skill_points=8))
        doc = report(deltas=dict(speed=9, guts=13), race=True)
        self.learned(doc, skill_points=8)
        result = build(doc)
        self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['races'][0])
        self.assertEqual(doc['gameplay_tracking']['events'][0]['learned_reader_gains'], dict(skill_points=8))
        self.assertEqual(turn_field(result, 'skill_points')['status'], 'balanced_observations')
        self.assertEqual(turn_field(without, 'skill_points')['status'], 'balanced_with_derived_changes')

    def test_a_clipped_badge_completed_by_the_learned_reader_is_observed(self):
        doc = report(deltas=dict(speed=1, guts=13, skill_points=8))
        doc['gameplay_tracking']['checkpoints'][1]['values']['speed'] = 111
        doc['turn_ledger']['turns'][1]['states']['stats']['opening']['values']['speed'] = 111
        self.learned(doc, speed=11)
        result = build(doc)
        self.assertEqual(doc['gameplay_tracking']['events'][0]['learned_reader_gains'], dict(speed=10))
        row = turn_field(result, 'speed')
        self.assertEqual((row['status'], row['direct_change']), ('balanced_observations', 11))
        completion = next(c for c in result['contributions'] if c['basis'] == 'observed_learned_training_gain')
        self.assertEqual(completion['completes'], '/gameplay_tracking/events/0/deltas/speed')

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

    def test_the_one_race_takes_the_skill_points_a_read_training_did_not_show(self):
        doc = report(race=True, deltas=dict(guts=13))
        result = build(doc)
        race = doc['gameplay_tracking']['races'][0]
        self.assertEqual(race['turn_difference_gains'], dict(skill_points=8))
        self.assertEqual(race['turn_difference_basis'], 'sole_race_takes_skill_point_residual')
        self.assertEqual(turn_field(result, 'skill_points')['status'], 'balanced_with_derived_changes')
        owner = next(c for c in result['contributions'] if c['basis'] == 'turn_difference' and c['field'] == 'skill_points')
        self.assertEqual(owner['event_ref'], '/gameplay_tracking/races/0')
        # A training with nothing read could have paid them too: both stay possible, so the field stays open.
        self.assertEqual(turn_field(build(report(race=True)), 'skill_points')['status'], 'unexplained_change')

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

    def test_the_one_skill_batch_without_an_observed_charge_takes_a_negative_skill_point_difference(self):
        doc = report(residual_sign=-1, batches=[batch()])
        result = build(doc)
        committed = doc['gameplay_tracking']['events'][-1]
        self.assertEqual(committed['turn_difference_cost'], dict(skill_points=8))
        self.assertEqual(committed['turn_difference_basis'], 'sole_unpriced_skill_batch_takes_turn_residual')
        row = turn_field(result, 'skill_points')
        self.assertEqual((row['status'], row['unresolved_change'], row['derived_or_summary_change']),
                         ('balanced_with_derived_changes', 0, -8))
        cost = next(c for c in result['contributions'] if c['basis'] == 'turn_difference' and c['field'] == 'skill_points')
        self.assertEqual((cost['amount'], cost['channel']), (-8, 'stats'))
        # The batch is named as unpriced whether or not it took the difference.
        self.assertIn(dict(kind='unobserved_purchase_debit', source_ref=cost['event_ref']), result['issues'])

    def test_two_unpriced_batches_or_a_charged_one_do_not_take_the_difference(self):
        second = dict(batch(), id='skills-2', first_seen_ms=176, last_seen_ms=178)
        # A batch whose charge was read owns that charge and nothing more; the
        # rest of the difference stays open rather than being added to it.
        charged = dict(batch(), spent_skill_points=3, deltas={'skill_points': -3},
                       cost_basis='observed_states_without_other_sp_transactions')
        for batches in ([batch(), second], [charged]):
            doc = report(residual_sign=-1, batches=batches)
            result = build(doc)
            self.assertEqual(turn_field(result, 'skill_points')['status'], 'unexplained_change')
            self.assertFalse(any('turn_difference_cost' in e for e in doc['gameplay_tracking']['events']))

    def test_a_race_in_the_turn_does_not_swallow_a_negative_skill_point_difference(self):
        # A race can own a positive skill-point difference, never a charge.
        doc = report(residual_sign=-1, race=True, batches=[batch()])
        result = build(doc)
        self.assertEqual(doc['gameplay_tracking']['events'][-1]['turn_difference_cost'], dict(skill_points=8))
        self.assertEqual(turn_field(result, 'skill_points')['status'], 'balanced_with_derived_changes')
        self.assertNotIn('turn_difference_gains', doc['gameplay_tracking']['races'][0])


if __name__ == '__main__':
    unittest.main()
