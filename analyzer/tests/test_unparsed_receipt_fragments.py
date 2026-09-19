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

    def test_dialogue_with_a_receipt_keyword_is_not_a_candidate(self):
        # A receipt is a sentence of its own and starts with a capital.
        readings = [receipt_row(1000, "learned the reason for Matikanefukukitaru's state"),
                    receipt_row(1250, 'energy that she normally does...'),
                    receipt_row(60000, 'Learned Makeup Advanced')]
        self.assertEqual([c['raw_text'] for c in unparsed_receipt_candidates(readings)], ['Learned Makeup Advanced'])

    def test_a_receipt_cut_beside_its_popup_is_a_fragment_of_the_popup_reading(self):
        # The cursor over the number: "Energy recovered by." on one frame, and
        # on the next the centered "+10 Energy" popup read as the effect.
        popup = dict(kind='energy_change', amount=10, raw_text='+10 Energy', observation_basis='visible_energy_recovery_popup')
        readings = [receipt_row(126500, 'Energy recovered by.'), receipt_row(126750, '+10', [popup])]
        candidate, = unparsed_receipt_candidates(readings)
        self.assertEqual((candidate['raw_text'], candidate['status']), ('Energy recovered by.', 'ocr_fragment'))

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

    def test_a_cut_line_under_another_events_title_is_not_a_fragment_of_the_receipt_before(self):
        parsed = dict(kind='stat_change', field='skill_points', amount=100, raw_text='Skill Pts went up by 100.')
        readings = [
            row(1000, 'event_outcome', ocr={'neural': [line('Skill Pts went up by 100.')]}, effects=[parsed], context_title='After the Finals'),
            row(4800, 'event_outcome', ocr={'neural': [line('Skill Pts went up by')]}, context_title='Twinkle Monthly'),
        ]
        got = unparsed_receipt_candidates(readings)
        self.assertEqual([(c['raw_text'], c['status'], c['context_title']) for c in got],
                         [('Skill Pts went up by', 'needs_review', 'Twinkle Monthly')])
        # Under the same title, or with no title on either frame, it is the fragment it looks like.
        for titles in (('After the Finals', 'After the Finals'), (None, 'Twinkle Monthly'), ('After the Finals', None)):
            readings[0]['context_title'], readings[1]['context_title'] = titles
            self.assertEqual(unparsed_receipt_candidates(readings)[0]['status'], 'ocr_fragment', titles)

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

    def test_damaged_boilerplate_around_a_number_and_a_name_is_a_fragment(self):
        hint = 'Gained 4 hint level(s) for Pace Chaser Savvy ○'
        self.assertEqual(fragment_of('Gained 4 hint leve"s) or Pace Chaser', [hint]), hint)
        self.assertEqual(fragment_of('Gained 1 hint level(s) tor Focus.', ['Gained 1 hint level(s) for Focus.']),
                         'Gained 1 hint level(s) for Focus.')
        # The number and the name are what the wording is about: neither may change.
        self.assertIsNone(fragment_of('Gained 2 hint level(s) for Focus.', ['Gained 4 hint level(s) for Focus.']))
        self.assertIsNone(fragment_of('Gained 4 hint level(s) for Focus.',
                                      ['Gained 4 hint level(s) for Corner Recovery ○']))
        # A cap receipt is not its stat's receipt, in either direction.
        self.assertIsNone(fragment_of('Speed cap went up by 5.', ['Speed went up by 5.']))
        self.assertIsNone(fragment_of('Speed went up by 5.', ['Speed cap went up by 5.']))
        # A glyph read for the circle that grades a skill is still that skill.
        self.assertEqual(fragment_of("Gained 1 hint leve'(s) for Right-Handed O.",
                                     ['Gained 1 hint level(s) for Right-Handed ○.']),
                         'Gained 1 hint level(s) for Right-Handed ○.')
        # Boilerplate the panel cut mid-word still folds.
        self.assertEqual(fragment_of('Stamina cap we.it', ['Stamina cap went up by 11.']),
                         'Stamina cap went up by 11.')

    def test_a_number_the_recognizer_split_is_the_same_number(self):
        full = 'Skill Pts went up by 110.'
        self.assertEqual(fragment_of('Skill Pts went up by 1 10.', [full]), full)
        self.assertIsNone(fragment_of('Skill Pts went up by 1 10.', ['Skill Pts went up by 10.']))
        readings = [
            receipt_row(1000, 'Skill Pts went up by 110.',
                        [dict(kind='stat_change', field='skill_points', amount=110, raw_text=full)]),
            receipt_row(1250, 'Skill Pts went up by 1 10.'),
        ]
        got = unparsed_receipt_candidates(readings)
        self.assertEqual([(c['raw_text'], c['status']) for c in got],
                         [('Skill Pts went up by 1 10.', 'ocr_fragment')])


if __name__ == '__main__':
    unittest.main()
