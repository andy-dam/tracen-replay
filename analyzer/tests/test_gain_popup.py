import unittest

from tracen_replay.gameplay import cut_receipt_lines, effects_from_lines, gain_popup


def line(text, box, confidence=97):
    return dict(text=text, box=list(box), confidence=confidence)


RECEIPT = line('Power went up by', (313, 827, 515, 867), 93)
POPUP = line('+5', (317, 444, 426, 521), 94)
LABEL = line('Power', (330, 518, 456, 566), 100)


def stat_changes(lines):
    return [e for e in effects_from_lines(lines) if e['kind'] == 'stat_change']


class GainPopupTests(unittest.TestCase):
    def test_a_cut_number_is_read_from_the_labelled_popup(self):
        effects = stat_changes([RECEIPT, POPUP, LABEL])
        self.assertEqual(len(effects), 1)
        effect = effects[0]
        self.assertEqual((effect['field'], effect['amount']), ('power', 5))
        self.assertEqual(effect['amount_basis'], 'gain_popup_on_same_frame')
        self.assertEqual(effect['raw_text'], 'Power went up by')
        self.assertEqual(effect['gain_popup']['box'], [317, 444, 426, 521])
        self.assertEqual(effect['gain_popup_label']['text'], 'Power')

    def test_a_garbled_number_is_read_the_same_way(self):
        lines = [line('Guts went up by T0.', (314, 828, 516, 863), 92),
                 line('+10', (291, 406, 447, 500), 98), line('Guts', (345, 535, 444, 580), 100),
                 line('Power went up by.', (316, 806, 512, 836), 94),
                 line('+5', (491, 558, 603, 643), 100), line('Power', (504, 636, 631, 686), 100)]
        effects = {e['field']: e['amount'] for e in stat_changes(lines)}
        self.assertEqual(effects, {'guts': 10, 'power': 5})

    def test_without_a_popup_the_line_stays_unparsed(self):
        self.assertEqual(stat_changes([RECEIPT]), [])
        self.assertEqual(stat_changes([RECEIPT, POPUP]), [])
        self.assertEqual(stat_changes([RECEIPT, LABEL]), [])

    def test_two_popups_for_the_stat_or_a_popup_for_another_stat_do_not_count(self):
        second = line('+7', (517, 444, 626, 521), 96)
        second_label = line('Power', (530, 518, 656, 566), 100)
        self.assertEqual(stat_changes([RECEIPT, POPUP, LABEL, second, second_label]), [])
        self.assertEqual(stat_changes([RECEIPT, POPUP, line('Speed', (330, 518, 456, 566), 100)]), [])

    def test_the_sign_and_the_geometry_must_agree(self):
        self.assertIsNone(gain_popup([line('-5', (317, 444, 426, 521), 94), LABEL], 'power', 'up'))
        self.assertIsNone(gain_popup([line('+5', (317, 444, 426, 490), 94), LABEL], 'power', 'up'))
        self.assertIsNone(gain_popup([POPUP, line('Power', (330, 600, 456, 640), 100)], 'power', 'up'))
        self.assertIsNone(gain_popup([POPUP, line('Power', (600, 518, 720, 566), 100)], 'power', 'up'))
        self.assertEqual(gain_popup([POPUP, LABEL], 'power', 'up')['amount'], 5)

    def test_a_number_read_on_the_line_wins_over_the_popup(self):
        effects = stat_changes([line('Power went up by 8.', (313, 827, 515, 867), 97), POPUP, LABEL])
        self.assertEqual([(e['amount'], e.get('amount_basis')) for e in effects], [(8, None)])

    def test_a_cut_receipt_under_the_band_gate_joins_the_band_when_a_popup_vouches(self):
        band = (250, 770, 850, 1000)
        self.assertEqual(cut_receipt_lines([RECEIPT, POPUP, LABEL], band), [RECEIPT])
        # No popup, a clean number, a line outside the band, or a confident
        # line that is already in the band: nothing to add.
        self.assertEqual(cut_receipt_lines([RECEIPT], band), [])
        self.assertEqual(cut_receipt_lines([line('Power went up by 8.', (313, 827, 515, 867), 93), POPUP, LABEL], band), [])
        self.assertEqual(cut_receipt_lines([line('Power went up by', (313, 600, 515, 640), 93), POPUP, LABEL], band), [])
        self.assertEqual(cut_receipt_lines([line('Power went up by', (313, 827, 515, 867), 97), POPUP, LABEL], band), [])

    def test_skill_points_use_their_own_label(self):
        lines = [line('Skill Pts went up by', (313, 827, 515, 867), 93),
                 line('+30', (317, 444, 426, 521), 98), line('Skill Pts', (330, 518, 456, 566), 100)]
        self.assertEqual([(e['field'], e['amount']) for e in stat_changes(lines)], [('skill_points', 30)])


if __name__ == '__main__':
    unittest.main()
