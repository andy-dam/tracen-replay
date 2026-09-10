import unittest

from tracen_replay.gameplay import effects_from_lines
from tracen_replay.transactions import outcome_events


def line(text='Stamina spark activated!', top=800):
    return dict(text=text, confidence=99, box=[315,top,600,top+28])


def row(time, lines):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='event_outcome',
                stats={}, facts={}, effects=effects_from_lines(lines), ocr={'neural':lines})


class InheritanceOccurrenceIntegrationTests(unittest.TestCase):
    def test_parallel_receipts_are_multiplicity_not_contradictory_values(self):
        lines=[line(),line(top=880)]
        event=outcome_events([row(1000,lines),row(1250,lines)])[0]
        self.assertEqual(len(event['effects']),1)
        self.assertEqual(event['conflicting_readings'],[])
        entry=event['inheritance_occurrence_evidence']['by_key']['inheritance_spark||Stamina']
        self.assertEqual(entry['minimum_observed_count'],2)
        self.assertIsNone(entry['total_count'])
        self.assertFalse(entry['count_complete'])
        self.assertEqual(event['deltas'],{})
        self.assertTrue(event['resolved_occurrence_conflicts'])

    def test_overlapping_ocr_duplicates_remain_unresolved(self):
        event=outcome_events([row(1000,[line(),line(top=810)])])[0]
        self.assertTrue(event['conflicting_readings'])
        self.assertNotIn('resolved_occurrence_conflicts',event)
        entry=event['inheritance_occurrence_evidence']['by_key']['inheritance_spark||Stamina']
        self.assertEqual(entry['minimum_observed_count'],1)

    def test_numeric_receipt_conflicts_are_not_reinterpreted_as_inheritance(self):
        event=outcome_events([row(1000,[line('Stamina went up by 5.'),line('Stamina went up by 5.',880)])])[0]
        self.assertTrue(event['conflicting_readings'])
        self.assertNotIn('inheritance_occurrence_evidence',event)
        self.assertEqual(event['deltas'],{})


if __name__=='__main__':
    unittest.main()
