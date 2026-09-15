"""A countdown misread during the transition animation is noise, not a window."""
import unittest

from tests.test_turn_ledger import reading, report
from tracen_replay.turn_ledger import build


class TransientCountdownTests(unittest.TestCase):
    def test_a_value_contradicted_by_both_neighbours_is_dropped_and_the_action_assigned(self):
        source = report()
        source['gameplay_tracking']['readings'] = [
            reading(56500, 'Junior Year Pre-Debut', 11), reading(56750, 'Junior Year Pre-Debut', 11),
            reading(64500, 'Junior Year Pre-Debut', 0), reading(64750, 'Junior Year Pre-Debut', 0), reading(65000, 'Junior Year Pre-Debut', 0),
            reading(65250, 'Junior Year Pre-Debut', 10), reading(65500, 'Junior Year Pre-Debut', 10)]
        source['source']['duration_ms'] = 70000
        source['gameplay_tracking']['turn_action_receipts'] = [dict(kind='training', source_timestamp_ms=63000)]
        ledger = build(source)
        self.assertEqual([t['calendar_value'] for t in ledger['turns']], [11, 10])
        self.assertEqual(ledger['turns'][0]['action_status'], 'one_action')
        self.assertNotIn('calendar_gap_or_reset', ledger['turns'][1]['issues'])
        self.assertEqual([i['reason'] for i in ledger['calendar_issues']], ['calendar_transient_misread'])
        self.assertEqual(ledger['timeline'][0]['turn_id'], 'turn-001')

    def test_neighbours_with_the_same_value_are_joined_again(self):
        source = report()
        source['gameplay_tracking']['readings'] = [
            reading(1000, 'Junior Year Pre-Debut', 11), reading(1250, 'Junior Year Pre-Debut', 11),
            reading(3000, 'Junior Year Pre-Debut', 0), reading(3250, 'Junior Year Pre-Debut', 0),
            reading(3500, 'Junior Year Pre-Debut', 11), reading(3750, 'Junior Year Pre-Debut', 11)]
        ledger = build(source)
        self.assertEqual([t['calendar_value'] for t in ledger['turns']], [11])

    def test_a_real_step_is_kept(self):
        source = report()
        source['gameplay_tracking']['readings'] = [
            reading(1000, 'Junior Year Pre-Debut', 11), reading(1250, 'Junior Year Pre-Debut', 11),
            reading(3000, 'Junior Year Pre-Debut', 10), reading(3250, 'Junior Year Pre-Debut', 10),
            reading(4000, 'Junior Year Pre-Debut', 9), reading(4250, 'Junior Year Pre-Debut', 9)]
        self.assertEqual([t['calendar_value'] for t in build(source)['turns']], [11, 10, 9])


if __name__ == '__main__':
    unittest.main()
