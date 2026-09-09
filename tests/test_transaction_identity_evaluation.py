import copy
import unittest

from tracen_replay.transaction_evaluate import evaluate


class TransactionIdentityEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.reference = dict(
            source_sha256='source', start_ms=0, end_ms=2000, kinds=['lesson'],
            scope='Source-labeled lesson receipt and confirmation',
            transactions=[dict(kind='lesson', start_ms=900, end_ms=1100,
                               name='Makeup Basics', requested_name='Makeup Basics',
                               receipt_name='Makeup Basics', stats={'guts': 5})])
        self.purchase = dict(id='lesson-1', source_timestamp_ms=1000,
                             name='Makeup Basics', requested_name='Makeup Basics',
                             receipt_name='Makeup Basics', performance_cost={'dance': 10},
                             awarded_stats={'guts': 5})
        self.report = dict(source={'sha256': 'source', 'duration_ms': 2000},
                           gameplay_tracking=dict(auxiliary_log_used=False,
                                                  lesson_purchases=[self.purchase]))

    def test_matching_names_and_rewards_pass(self):
        result = evaluate(self.reference, self.report)
        self.assertTrue(result['passed'])
        self.assertEqual(result['evaluated_fields'],
                         ['name', 'receipt_name', 'requested_name', 'stats'])

    def test_wrong_or_missing_name_fails_despite_same_timing_and_rewards(self):
        for field in ('name', 'requested_name', 'receipt_name'):
            for value in ('Different Lesson', None):
                with self.subTest(field=field, value=value):
                    report = copy.deepcopy(self.report)
                    report['gameplay_tracking']['lesson_purchases'][0][field] = value
                    result = evaluate(self.reference, report)
                    self.assertFalse(result['passed'])
                    self.assertEqual(result['matched'], 1)
                    self.assertEqual(result['field_errors'], [dict(
                        transaction='lesson-1', field=field,
                        expected='Makeup Basics', actual=value)])

    def test_numeric_only_reference_does_not_claim_identity_evaluation(self):
        reference = copy.deepcopy(self.reference)
        for field in ('name', 'requested_name', 'receipt_name'):
            reference['transactions'][0].pop(field)
        self.purchase['name'] = 'Different Lesson'
        result = evaluate(reference, self.report)
        self.assertTrue(result['passed'])
        self.assertEqual(result['evaluated_fields'], ['stats'])

    def test_exact_names_are_not_fuzzily_repaired(self):
        self.purchase['receipt_name'] = 'Makeup Baslcs'
        result = evaluate(self.reference, self.report)
        self.assertFalse(result['passed'])
        self.assertEqual(result['field_errors'][0]['field'], 'receipt_name')

    def test_unknown_reference_name_cannot_pass_as_identity(self):
        self.reference['transactions'][0]['name'] = None
        self.purchase['name'] = None
        with self.assertRaisesRegex(ValueError, 'nonempty source-observed lesson name'):
            evaluate(self.reference, self.report)


if __name__ == '__main__':
    unittest.main()
