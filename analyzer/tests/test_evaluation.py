import copy
import unittest

from tracen_replay.evaluate import evaluate
from tracen_replay.reconcile import FIELDS


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.values={f:100 for f in FIELDS}
        self.reference=dict(schema_version='tracen-replay/stat-reference-v1',source_sha256='abc',
                            examples=[dict(source_timestamp_ms=0,values=self.values),dict(source_timestamp_ms=250,values=None)])
        self.report=dict(source=dict(sha256='abc'),stat_tracking=dict(enabled=True,checkpoints=[],readings=[
            dict(source_timestamp_ms=0,values=self.values.copy(),evidence='frames/1.jpg'),
            dict(source_timestamp_ms=250,values=None,evidence='frames/2.jpg')]))

    def test_exact_fields_and_negative_abstention_pass(self):
        result=evaluate(self.reference,[self.report])
        self.assertTrue(result['passed'])
        self.assertEqual(result['accepted_field_accuracy'],1)
        self.assertEqual(result['complete_reading_coverage'],1)

    def test_wrong_value_fails_without_modifying_prediction(self):
        self.report['stat_tracking']['readings'][0]['values']['speed']=999
        original=copy.deepcopy(self.report)
        result=evaluate(self.reference,[self.report])
        self.assertFalse(result['passed'])
        self.assertEqual(result['counts']['correct_fields'],5)
        self.assertEqual(self.report,original)

    def test_abstention_is_not_counted_as_correct(self):
        self.report['stat_tracking']['readings'][0]['values']['speed']=None
        result=evaluate(self.reference,[self.report])
        self.assertEqual(result['accepted_field_accuracy'],1)
        self.assertEqual(result['counts']['abstained_fields'],1)
        self.assertEqual(result['complete_reading_coverage'],0)
        self.assertFalse(result['passed'])

    def test_projection_on_hidden_stats_screen_fails(self):
        self.report['stat_tracking']['readings'][1]['values']={'guts':5}
        result=evaluate(self.reference,[self.report])
        self.assertEqual(result['counts']['false_positive_fields'],1)
        self.assertFalse(result['passed'])

    def test_missing_samples_cannot_silently_pass(self):
        self.report['stat_tracking']['readings'].pop()
        result=evaluate(self.reference,[self.report])
        self.assertFalse(result['passed'])
        self.assertEqual(result['errors'][0]['kind'],'missing_prediction')

    def test_source_mismatch_and_overlaps_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'overlapping'):
            evaluate(self.reference,[self.report,self.report])
        self.report['source']['sha256']='different'
        with self.assertRaisesRegex(ValueError,'hashes'):
            evaluate(self.reference,[self.report])

    def test_checkpoint_cannot_bridge_negative_reference(self):
        self.report['stat_tracking']['checkpoints']=[dict(first_seen_ms=0,last_seen_ms=500,values=self.values)]
        result=evaluate(self.reference,[self.report])
        self.assertFalse(result['passed'])
        self.assertEqual(result['errors'][0]['fields'][0]['field'],'checkpoint')


if __name__=='__main__':
    unittest.main()
