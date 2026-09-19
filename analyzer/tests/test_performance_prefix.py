import unittest
from tracen_replay.transactions import training_events
from tests.test_neural_transactions import row


class PerformancePrefixTests(unittest.TestCase):
    def rows(self,values=(17,17,1,17,17)):
        # A card that awards performance shows its stat badge too.
        return [row(i*25,'training_result',{'awarded_performance_gains':{'composure':v},'training_gains':{'wit':12}},training_option='wit') for i,v in enumerate(values)]

    def test_single_prefix_outlier_retains_evidence(self):
        event=training_events(self.rows())[0]
        self.assertEqual(event['performance_deltas'],{'composure':17})
        self.assertEqual(event['performance_reading_resolutions']['composure']['outlier_evidence'],['50.png'])

    def test_rejects_nonprefix_repeated_outliers_and_short_evidence(self):
        for values in ((17,17,7,17),(17,1,1,17,17),(17,1,17)):
            self.assertEqual(training_events(self.rows(values))[0]['performance_deltas'],{})
        rows=self.rows()
        for r in rows:r['source_timestamp_ms']=0
        self.assertEqual(training_events(rows)[0]['performance_deltas'],{})

    def test_failure_still_has_no_award(self):
        rows=self.rows();rows[-1]['facts']['training_outcome']='failure'
        self.assertEqual(training_events(rows)[0]['performance_deltas'],{})
