import unittest
from tracen_replay.transaction_evaluate import evaluate


class TransactionReferenceScopeTests(unittest.TestCase):
    def fixture(self):
        return (dict(source_sha256='source',start_ms=0,end_ms=1000,kinds=['lesson'],transactions=[],scope='Source-reviewed interval'),
                dict(source=dict(sha256='source',duration_ms=1000),gameplay_tracking=dict(auxiliary_log_used=False)))

    def test_empty_labels_cannot_silently_pass(self):
        ref,report=self.fixture()
        with self.assertRaises(ValueError):evaluate(ref,report)
        ref['no_transactions']=True
        with self.assertRaises(ValueError):evaluate(ref,report)
        ref['independently_reviewed']=True
        result=evaluate(ref,report)
        self.assertTrue(result['passed']);self.assertIsNone(result['recall'])
        self.assertTrue(result['explicitly_reviewed_negative'])

    def test_reviewed_negative_still_catches_false_positive(self):
        ref,report=self.fixture();ref.update(no_transactions=True,independently_reviewed=True)
        report['gameplay_tracking']['lesson_purchases']=[dict(id='lesson',source_timestamp_ms=500,
            performance_cost={'dance':10},awarded_stats={'speed':5})]
        result=evaluate(ref,report)
        self.assertFalse(result['passed']);self.assertEqual(len(result['extra_predictions']),1)

    def test_unsupported_contradictory_and_out_of_source_references_rejected(self):
        ref,report=self.fixture();ref.update(no_transactions=True,independently_reviewed=True)
        for kinds in ([],['skill'],['lesson','unsupported']):
            with self.assertRaises(ValueError):evaluate(dict(ref,kinds=kinds),report)
        with self.assertRaises(ValueError):evaluate(dict(ref,end_ms=1001),report)
        with self.assertRaises(ValueError):evaluate(dict(ref,transactions=[dict(kind='lesson',start_ms=100,end_ms=500)]),report)


if __name__=='__main__':unittest.main()
