import copy
import unittest

from tracen_replay.transaction_evaluate import evaluate


class TransactionNumericTypesTests(unittest.TestCase):
    def setUp(self):
        self.ref = dict(source_sha256='s', start_ms=0, end_ms=1000,
                        kinds=['lesson'], scope='Numeric transaction fields',
                        transactions=[dict(kind='lesson', start_ms=100, end_ms=200,
                                           stats={'speed': 1})])
        self.report = dict(source={'sha256': 's'}, gameplay_tracking=dict(
            auxiliary_log_used=False, lesson_purchases=[dict(id='l',
                source_timestamp_ms=150, performance_cost={}, awarded_stats={'speed': 1})]))

    def test_bad_reference_values_and_unknown_field_cannot_pass(self):
        for stats in ({'speed': True}, {'speed': 1.0}, {'speeed': 1}, None):
            with self.subTest(stats=stats):
                ref = copy.deepcopy(self.ref)
                ref['transactions'][0]['stats'] = stats
                with self.assertRaisesRegex(ValueError, 'Numeric labels'):
                    evaluate(ref, self.report)

    def test_bad_prediction_values_are_reported_not_equal_to_integer(self):
        for stats in ({'speed': True}, {'speed': 1.0}, {'speed': '1'}, {'speeed': 1}, None):
            with self.subTest(stats=stats):
                report = copy.deepcopy(self.report)
                report['gameplay_tracking']['lesson_purchases'][0]['awarded_stats'] = stats
                result = evaluate(self.ref, report)
                self.assertFalse(result['passed'])
                self.assertEqual(result['field_errors'][0]['reason'],
                                 'unknown_or_malformed_numeric_prediction')

    def test_omitted_zero_fields_in_valid_sparse_maps_still_match(self):
        self.report['gameplay_tracking']['lesson_purchases'][0]['awarded_stats']['wit'] = 0
        self.assertTrue(evaluate(self.ref, self.report)['passed'])

    def test_malformed_concert_receipt_is_not_coerced_by_summing(self):
        ref = copy.deepcopy(self.ref)
        ref['kinds'] = ['concert']
        ref['transactions'][0]['kind'] = 'concert'
        for effect in (dict(kind='stat_change', field='speed', amount=True),
                       dict(kind='stat_change', field='speeed', amount=1)):
            with self.subTest(effect=effect):
                report = dict(source={'sha256': 's'}, gameplay_tracking=dict(
                    auxiliary_log_used=False, concerts=[dict(id='c', first_seen_ms=150,
                                                             observed_rewards=[[effect]])]))
                self.assertFalse(evaluate(ref, report)['passed'])


if __name__ == '__main__':
    unittest.main()
