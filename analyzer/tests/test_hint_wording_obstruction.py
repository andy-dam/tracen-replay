"""An obstruction over a hint receipt's fixed wording leaves its number and name readable."""
import unittest

from tests.test_neural_transactions import line, raw
from tracen_replay.receipt_occlusion import hint_data_boxes, hint_wording_obstructed
from tracen_replay.vision import parse


# The words of "Gained 4 hint levei(s) for Pace Chaser" as the recognizer
# placed them on the frame this case comes from, the name wrapping onto the
# line below.
WORDS = [('Gained', [326, 822, 386, 849]), ('4', [399, 822, 408, 849]), ('hint', [412, 822, 449, 849]),
         ('levei(s)', [453, 822, 526, 850]), ('for', [539, 823, 558, 850]),
         ('Pace', [566, 823, 607, 850]), ('Chaser', [616, 823, 681, 851])]
PARTICLES = [[461, 812, 501, 832], [517, 817, 545, 849], [544, 830, 551, 836]]


def hint_line(words=WORDS, text=None):
    spelled = ' '.join(word for word, _box in words)
    return dict(line(text or spelled, (317, 822, 681, 851), 96.589),
                word_boxes=[dict(text=word, box=list(box)) for word, box in words])


class HintWordingObstructionTests(unittest.TestCase):
    def test_the_number_and_the_name_are_the_boxes_that_matter(self):
        self.assertEqual(hint_data_boxes(hint_line()),
                         [[399, 822, 408, 849], [566, 823, 607, 850], [616, 823, 681, 851]])
        # Word boxes that do not spell the line are not a reading of it.
        self.assertIsNone(hint_data_boxes(hint_line(text='Gained 4 hint level(s) for Someone Else')))
        # Damage past the repair tolerance leaves the split untrusted.
        broken = [(w if w != 'levei(s)' else 'levs', b) for w, b in WORDS]
        self.assertIsNone(hint_data_boxes(hint_line(broken)))
        self.assertIsNone(hint_data_boxes(line('Gained 4 hint level(s) for Pace Chaser', (317, 822, 681, 851))))

    def test_an_obstruction_on_the_wording_alone_leaves_the_data_readable(self):
        self.assertTrue(hint_wording_obstructed(hint_line(), PARTICLES))
        # Touching the number, or any word of the name, is not that.
        self.assertFalse(hint_wording_obstructed(hint_line(), [[395, 830, 410, 845]]))
        self.assertFalse(hint_wording_obstructed(hint_line(), [[600, 830, 620, 845]]))
        self.assertFalse(hint_wording_obstructed(hint_line(), PARTICLES + [[670, 830, 690, 845]]))
        self.assertFalse(hint_wording_obstructed(hint_line(), []))

    def test_a_proven_obstruction_lets_the_wording_be_repaired(self):
        source = raw([hint_line(), line('Corners.', (319, 850, 426, 873), 98.3)],
                     resolved_receipt_occlusions=[dict(text=' '.join(w for w, _b in WORDS), box=[317, 822, 681, 851],
                                                       basis='overlay_covers_fixed_hint_wording')])
        effect = parse(source)['effects'][0]
        self.assertEqual((effect['kind'], effect['name'], effect['amount']),
                         ('skill_hint_change', 'Pace Chaser Corners', 4))
        self.assertEqual(effect['original_text'], 'Gained 4 hint levei(s) for Pace Chaser Corners.')
        self.assertEqual(effect['text_normalization'], 'obstructed_hint_wording')

    def test_without_that_proof_the_same_line_stays_unparsed(self):
        source = raw([hint_line(), line('Corners.', (319, 850, 426, 873), 98.3)])
        self.assertEqual(parse(source)['effects'], [])
        # Nor does a resolution of some other line speak for this one.
        source = raw([hint_line(), line('Corners.', (319, 850, 426, 873), 98.3)],
                     resolved_receipt_occlusions=[dict(text='Gained 9 hint level(s) for Another Skill',
                                                       box=[317, 900, 681, 930],
                                                       basis='overlay_covers_fixed_hint_wording')])
        self.assertEqual(parse(source)['effects'], [])


if __name__ == '__main__':
    unittest.main()
