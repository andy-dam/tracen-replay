import unittest

from tracen_replay.action_evaluate import evaluate


class ActionReferenceScopeTests(unittest.TestCase):
    def inputs(self):
        reference = dict(source_sha256='source', start_ms=0, end_ms=1000,
                         kinds=['training'], scope='Sampled training actions',
                         actions=[], no_completed_actions=True,
                         independently_reviewed=True, reference_complete=True)
        report = dict(source=dict(sha256='source', duration_ms=1000),
                      gameplay_tracking=dict(auxiliary_log_used=False,
                                             turn_action_receipts=[]))
        return reference, report

    def test_other_channels_cannot_pass_as_empty_turn_action_references(self):
        for kinds in (['lesson'], ['event'], ['concert'], ['training', 'lesson']):
            with self.subTest(kinds=kinds):
                reference, report = self.inputs()
                reference['kinds'] = kinds
                with self.assertRaisesRegex(ValueError, 'supported'):
                    evaluate(reference, report)

    def test_kind_scope_is_an_explicit_unique_list(self):
        for kinds in ('training', {'training': True}, [], ['training', 'training'], [None], [[]]):
            with self.subTest(kinds=kinds):
                reference, report = self.inputs()
                reference['kinds'] = kinds
                with self.assertRaises(ValueError):
                    evaluate(reference, report)

    def test_a_negative_interval_cannot_exceed_or_misstate_source_time(self):
        for start, end in ((0, 1001), (False, 1000), (0, 1000.0), (1000, 1000), (-1, 1000)):
            with self.subTest(start=start, end=end):
                reference, report = self.inputs()
                reference.update(start_ms=start, end_ms=end)
                with self.assertRaises(ValueError):
                    evaluate(reference, report)

    def test_training_only_score_retains_scope_and_does_not_score_a_race(self):
        reference, report = self.inputs()
        report['gameplay_tracking']['turn_action_receipts'] = [dict(kind='race', source_timestamp_ms=500)]
        score = evaluate(reference, report)
        self.assertTrue(score['passed'])
        self.assertEqual(score['kinds'], ['training'])
        self.assertEqual((score['start_ms'], score['end_ms']), (0, 1000))
        reference['kinds'].append('race')
        self.assertEqual(score['kinds'], ['training'])
        self.assertFalse(evaluate(reference, report)['passed'])

    def test_action_label_must_have_integral_times_within_scope(self):
        for action in (None, dict(kind='training', start_ms=False, end_ms=200),
                       dict(kind='training', start_ms=100, end_ms=200.5),
                       dict(kind='training', start_ms=100, end_ms=1001)):
            with self.subTest(action=action):
                reference, report = self.inputs()
                reference.update(actions=[action], no_completed_actions=False)
                with self.assertRaises(ValueError):
                    evaluate(reference, report)

    def test_malformed_prediction_times_cannot_pass_or_disappear_from_scoring(self):
        for value in (False, True, 50.0, '50', None, -1, 1000):
            with self.subTest(value=value):
                reference, report = self.inputs()
                report['gameplay_tracking']['turn_action_receipts'] = [dict(kind='training', source_timestamp_ms=value)]
                with self.assertRaisesRegex(ValueError, 'Invalid predicted action row 0'):
                    evaluate(reference, report)

    def test_prediction_validation_precedes_scope_filtering(self):
        for action in (None, {}, dict(kind='race'), dict(kind='event', source_timestamp_ms=500),
                       dict(kind='race', source_timestamp_ms=None)):
            with self.subTest(action=action):
                reference, report = self.inputs()
                report['gameplay_tracking']['turn_action_receipts'] = [action]
                with self.assertRaisesRegex(ValueError, 'Invalid predicted action row 0'):
                    evaluate(reference, report)

    def test_missing_prediction_list_and_malformed_duration_are_rejected(self):
        for value in (None, {}, 'missing'):
            reference, report = self.inputs()
            report['gameplay_tracking']['turn_action_receipts'] = value
            with self.assertRaisesRegex(ValueError, 'predicted action rows'):
                evaluate(reference, report)
        reference, report = self.inputs()
        del report['gameplay_tracking']['turn_action_receipts']
        with self.assertRaisesRegex(ValueError, 'predicted action rows'):
            evaluate(reference, report)
        for value in (None, False, 1000.0, '1000', 0):
            reference, report = self.inputs()
            report['source']['duration_ms'] = value
            with self.assertRaisesRegex(ValueError, 'Invalid source duration'):
                evaluate(reference, report)
