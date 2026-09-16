"""A receipt read cut short or garbled beside its parsed reading is a fragment, not a missed effect."""
import unittest
from tracen_replay.mechanics_audit import fragment_of, out_of_scope_family, unparsed_receipt_candidates
from tests.test_neural_transactions import line, row


def receipt_row(t, text, effects=()):
    return row(t, 'event_outcome', ocr={'neural': [line(text)]}, effects=list(effects))


class FragmentTests(unittest.TestCase):
    def test_prefix_and_bounded_variants_are_fragments(self):
        full = 'Friendship with Light Hello is maxed out.'
        self.assertEqual(fragment_of('Friendship with Light Hello is maxed', [full]), full)
        self.assertEqual(fragment_of('Friendship with Light Hello ls naxed oul.', [full]), full)
        self.assertEqual(fragment_of('Skill Pts went un hv 57.', ['Skill Pts went up by 57.']), 'Skill Pts went up by 57.')
        # A different receipt is not a fragment, however close in shape.
        self.assertIsNone(fragment_of('Friendship with Light Hello went up by 4.', [full]))
        self.assertIsNone(fragment_of('Speed went up by 12.', ['Speed went up by 17.']))
        self.assertIsNone(fragment_of('Guts went up by 3.', ['Wit went up by 3.']))
        self.assertIsNone(fragment_of('Learned', ['Learned the song "Hoppity Sunny Days".']))

    def test_candidates_beside_a_parsed_receipt_are_marked_not_reviewed(self):
        parsed = dict(kind='friendship_status', name='Light Hello', raw_text='Friendship with Light Hello is maxed out.')
        readings = [
            receipt_row(1000, 'Friendship with Light Hello is maxed out.', [parsed]),
            receipt_row(1500, 'Friendship with Light Hello is maxed'),
            receipt_row(1750, 'Friendship with Light Hello ls naxed oul.'),
            receipt_row(60000, 'Stamina went up by'),
        ]
        got = unparsed_receipt_candidates(readings)
        self.assertEqual([(c['raw_text'], c['status']) for c in got], [
            ('Friendship with Light Hello is maxed', 'ocr_fragment'),
            ('Friendship with Light Hello ls naxed oul.', 'ocr_fragment'),
            ('Stamina went up by', 'needs_review'),
        ])
        self.assertEqual(got[0]['fragment_of'], 'Friendship with Light Hello is maxed out.')

    def test_out_of_scope_families_are_kept_but_not_reviewed(self):
        self.assertTrue(out_of_scope_family('Smart Falcon joined your cluse!'))
        self.assertTrue(out_of_scope_family('Friendship with Matikanefukukitaru went up by'))
        self.assertTrue(out_of_scope_family('Light Hello will now appear in training'))
        self.assertFalse(out_of_scope_family('Skill Pts went up by'))
        self.assertFalse(out_of_scope_family('Learned the song "Full Speed Ahead!'))
        readings = [
            receipt_row(1000, 'Smart Falcon joined your cluse!'),
            receipt_row(2000, 'Friendship with Matikanefukukitaru went up by'),
            receipt_row(3000, 'Stamina went up by'),
        ]
        got = unparsed_receipt_candidates(readings)
        self.assertEqual([c['status'] for c in got], ['out_of_scope', 'out_of_scope', 'needs_review'])

    def test_a_fragment_of_a_longer_unparsed_line_is_a_fragment(self):
        readings = [
            receipt_row(1000, 'Learned the song "Hoppity Sunny Da'),
            receipt_row(1300, 'Learned the song "Hoppity Sun'),
        ]
        got = unparsed_receipt_candidates(readings)
        self.assertEqual([c['status'] for c in got], ['needs_review', 'ocr_fragment'])


if __name__ == '__main__':
    unittest.main()
