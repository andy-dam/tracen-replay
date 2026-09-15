import unittest
from tracen_replay.gameplay_view import render_gameplay


class AcquisitionConflictViewTests(unittest.TestCase):
    def test_linked_purchase_warns_and_alternatives_are_not_duplicated(self):
        conflict=dict(source_ref='/gameplay_tracking/song_acquisitions/0',
                      observed_name_candidates=['A <title>','A title?'],evidence=['source.png'])
        report=dict(gameplay_tracking=dict(screens=[],intervals=[],checkpoints=[],readings=[],
            lesson_purchases=[dict(name='A title',performance_cost={},evidence=[],receipt_event_id='event'),
                              dict(name='Other',performance_cost={},evidence=[],receipt_event_id='other')]),
            turn_ledger=dict(timeline=[dict(event_id='event',acquisition_conflicts=[conflict]),
                                      dict(event_id='event',acquisition_conflicts=[conflict])]))
        html=render_gameplay(report)
        self.assertEqual(html.count('Acquisition name unresolved'),1)
        self.assertEqual(html.count('A &lt;title&gt; / A title?'),1)
        self.assertNotIn('A <title>',html)
        self.assertIn('href="source.png"',html)
        self.assertIn('<li>Other · cost:',html)


if __name__=='__main__':unittest.main()
