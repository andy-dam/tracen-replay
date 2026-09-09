import unittest

from tests.test_neural_transactions import line, raw, row
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import parse


def receipt(time, text, confidence=99, **kwargs):
    parsed = parse(raw([line(text, confidence=confidence)]))
    return row(time, parsed['screen'], parsed['facts'], parsed['effects'], **kwargs)


class ReceiptConfirmationTests(unittest.TestCase):
    def rows(self, field='Power cap', amount=11):
        return [receipt(0, f'{field} went up by 1.'),
                receipt(250, f'{field} went up by {amount}.'),
                receipt(500, f'{field} went up by {amount}')]

    def test_complete_receipt_and_later_repeat_resolve_with_raw_proof(self):
        for field, amount in [('Power cap', 11), ('Stamina', 17), ('Dance', 21)]:
            with self.subTest(field=field):
                event = outcome_events(self.rows(field, amount))[0]
                self.assertEqual(event['effects'][0]['amount'], amount)
                self.assertEqual(event['conflicting_readings'], [])
                proof = event['resolved_reading_conflicts'][0]
                self.assertEqual(proof['rejected_observations'][0]['raw_text'], f'{field} went up by 1.')
                self.assertEqual(proof['complete_receipt']['source_timestamp_ms'], 250)
                self.assertEqual(proof['later_repeat']['source_timestamp_ms'], 500)

    def test_prefix_before_complete_receipt_cannot_corroborate(self):
        rows = self.rows()
        rows[1]['source_timestamp_ms'], rows[2]['source_timestamp_ms'] = 500, 250
        self.assertTrue(outcome_events(sorted(rows, key=lambda r:r['source_timestamp_ms']))[0]['conflicting_readings'])

    def test_weak_duplicate_or_distant_repeat_cannot_corroborate(self):
        for mode in ('weak', 'same_frame', 'same_artifact', 'distant'):
            with self.subTest(mode=mode):
                rows = self.rows()
                if mode == 'weak': rows[-1]['facts']['effect_candidates'][0]['confidence'] = 96.9
                if mode == 'same_frame': rows[-1]['source_timestamp_ms'] = 250
                if mode == 'same_artifact': rows[-1]['evidence'] = rows[1]['evidence']
                if mode == 'distant': rows[-1]['source_timestamp_ms'] = 1000
                self.assertTrue(outcome_events(rows)[0]['conflicting_readings'])

    def test_disagreement_or_multiple_outliers_cannot_resolve(self):
        variants = [self.rows() + [receipt(750, 'Power cap went up by 12')],
                    self.rows() + [receipt(750, 'Power cap went up by 1.')],
                    [receipt(0, 'Power cap went up by 2.')] + self.rows()[1:],
                    [receipt(0, 'Power cap went up by 11.'),
                     receipt(250, 'Power cap went up by 1.'),
                     receipt(500, 'Power cap went up by 11')]]
        for rows in variants:
            with self.subTest(rows=rows):
                self.assertTrue(outcome_events(rows)[0]['conflicting_readings'])

    def test_boundary_and_missing_complete_receipt_remain_unresolved(self):
        rows = self.rows()
        rows.insert(2, row(375, 'training_result'))
        self.assertTrue(outcome_events(rows)[0]['conflicting_readings'])
        events = outcome_events([receipt(0, 'Power cap went up by 1.'),
                                 receipt(250, 'Power cap went up by 11'),
                                 receipt(500, 'Power cap went up by 11')])
        self.assertEqual(events[0]['effects'][0]['amount'], 1)
        self.assertNotIn('resolved_reading_conflicts', events[0])

    def test_numeric_resolution_never_selects_recipient_identity(self):
        rows = self.rows('Friendship with Somebody')
        self.assertTrue(outcome_events(rows)[0]['conflicting_readings'])


if __name__ == '__main__':
    unittest.main()
