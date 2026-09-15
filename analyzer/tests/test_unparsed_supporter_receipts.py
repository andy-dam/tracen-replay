import unittest

from tracen_replay.mechanics_audit import unparsed_receipt_candidates


class UnparsedSupporterReceiptTests(unittest.TestCase):
    def row(self, text, timestamp=415500, confidence=98, screen='event_outcome'):
        return dict(screen=screen, source_timestamp_ms=timestamp, evidence=f'{timestamp}.png',
                    effects=[], ocr=dict(neural=[dict(text=text, confidence=confidence,
                                                     box=[317, 920, 638, 947])]))

    def test_malformed_source_phrasings_are_review_candidates_not_joins(self):
        rows = [self.row('Silence Suzuka joined your cuse!'),
                self.row('Mejiro Dober joined your', 419000),
                self.row('Mejiro Dober joined your ause!', 419500)]
        candidates = unparsed_receipt_candidates(rows)
        self.assertEqual(len(candidates), 3)
        self.assertTrue(all(c['status'] == 'needs_review' for c in candidates))
        self.assertTrue(all('name' not in c and 'amount' not in c for c in candidates))
        self.assertEqual(candidates[0]['raw_text'], rows[0]['ocr']['neural'][0]['text'])
        self.assertEqual(candidates[0]['evidence'], ['415500.png'])

    def test_recognized_join_preview_and_low_confidence_are_not_candidates(self):
        for row in [self.row('Example joined your cause!'),
                    self.row('Example will join your cause!'),
                    self.row('Example joined your cuse!', confidence=94),
                    self.row('Example joined your cuse!', screen='training_preview')]:
            self.assertEqual(unparsed_receipt_candidates([row]), [])

    def test_existing_repair_is_not_reported_again_and_repeats_keep_evidence(self):
        row = self.row('Example joined your cuse!')
        repaired = dict(row, effects=[dict(original_text='Example joined your cuse!',
                                           raw_text='Example joined your cause!')])
        self.assertEqual(unparsed_receipt_candidates([repaired]), [])
        result = unparsed_receipt_candidates([row, self.row('Example joined your cuse!', 415750)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['observations'], 2)
        self.assertEqual(result[0]['evidence'], ['415500.png', '415750.png'])
