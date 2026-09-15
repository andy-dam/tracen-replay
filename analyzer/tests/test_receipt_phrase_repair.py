"""A receipt phrase corrupted by the cursor is restored; names and amounts never change."""
import unittest

from tracen_replay.gameplay import effects_from_lines as parse_effects, repair_receipt_phrase


def line(text, confidence=98):
    return dict(text=text, confidence=confidence, box=[316, 820, 530, 853])


class ReceiptPhraseRepairTests(unittest.TestCase):
    def test_cursor_over_up_by(self):
        effects = parse_effects([line('Speed went uply 30.')])
        self.assertEqual([(e['kind'], e['field'], e['amount']) for e in effects], [('stat_change', 'speed', 30)])
        self.assertEqual(effects[0]['raw_text'], 'Speed went uply 30.')
        self.assertEqual(effects[0]['normalized_text'], 'Speed went up by 30.')
        self.assertEqual(effects[0]['text_normalization'], 'verb_phrase_repair')

    def test_merged_up_by(self):
        effects = parse_effects([line('Wit went upby 10.')])
        self.assertEqual([(e['field'], e['amount']) for e in effects], [('wit', 10)])

    def test_down_is_kept_negative(self):
        effects = parse_effects([line('Power went dovn by 5.')])
        self.assertEqual([(e['field'], e['amount']) for e in effects], [('power', -5)])

    def test_clean_lines_are_not_marked_repaired(self):
        effects = parse_effects([line('Speed went up by 30.')])
        self.assertEqual(effects[0]['amount'], 30)
        self.assertNotIn('text_normalization', effects[0])

    def test_missing_amount_or_low_confidence_is_not_repaired(self):
        self.assertEqual(parse_effects([line('Power went up by.')]), [])
        self.assertEqual(parse_effects([line('Guts went up')]), [])
        self.assertEqual(parse_effects([line('Speed went uply 30.', confidence=85)]), [])

    def test_maxed_out_within_two_edits(self):
        for text in ('Friendship with Kitasan Black is naxed out.', 'Friendship with Kitasan Black is maxd out.',
                     'Friendship with Marvelous Sunday is Tnaxed out.'):
            effects = parse_effects([line(text)])
            self.assertEqual([(e['kind'], e['value']) for e in effects], [('friendship_status', 'maximum')], text)
        self.assertEqual(parse_effects([line('Friendship with Kitasan Black is worn out.')]), [])

    def test_repair_never_touches_subject_or_amount(self):
        self.assertIsNone(repair_receipt_phrase('Speedy went uply 30.'))
        self.assertEqual(repair_receipt_phrase('Stamina went uply 12.')['text'], 'Stamina went up by 12.')


if __name__ == '__main__':
    unittest.main()
