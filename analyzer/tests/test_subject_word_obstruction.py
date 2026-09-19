"""The cursor over a receipt's subject word leaves its direction and number readable."""
import unittest

from tests.test_neural_transactions import line, raw
from tracen_replay.receipt_grammar import subject_receipt
from tracen_replay.receipt_occlusion import subject_word_obstructed
from tracen_replay.vision import parse


# "Vocals went up by 20." with the mouse cursor parked over the V, as the
# recognizer placed its words on the frame this case comes from.
WORDS = [('Yocals', [323, 857, 386, 883]), ('went', [388, 857, 433, 883]), ('up', [440, 857, 459, 883]),
         ('by', [466, 857, 486, 883]), ('20.', [493, 857, 530, 883])]
CURSOR = [318, 862, 336, 896]


def receipt_line(words=WORDS, text=None, confidence=96.443):
    spelled = ' '.join(word for word, _box in words)
    return dict(line(text or spelled, (314, 857, 530, 883), confidence),
                word_boxes=[dict(text=word, box=list(box)) for word, box in words])


class SubjectWordObstructionTests(unittest.TestCase):
    def test_one_damaged_glyph_in_a_fixed_subject_word_names_one_field(self):
        self.assertEqual(subject_receipt('Yocals went up by 20.'), 'Vocals went up by 20.')
        self.assertEqual(subject_receipt('Nocals went down by 5!'), 'Vocals went down by 5!')
        self.assertEqual(subject_receipt('Composre went up by 8.'), 'Composure went up by 8.')
        # The cursor covers about two letters of a long word.
        self.assertEqual(subject_receipt('Slumina went up by 5.'), 'Stamina went up by 5.')
        self.assertEqual(subject_receipt('Slsmina went up by 5.'), 'Stamina went up by 5.')
        self.assertIsNone(subject_receipt('Gvtz went up by 5.'))
        # A subject that already reads as a field needs nothing; one damaged
        # past a glyph, or a sentence of another shape, is left alone.
        for text in ('Vocals went up by 20.', 'Vocal went up by 20.', 'Yocls went up by 20.',
                     'Yocals went up by 20', 'Yocals cap went up by 20.', 'Friendship with Yocals went up by 2.'):
            with self.subTest(text=text):
                self.assertIsNone(subject_receipt(text))

    def test_an_obstruction_on_the_subject_alone_leaves_the_data_readable(self):
        self.assertTrue(subject_word_obstructed(receipt_line(), [CURSOR]))
        # A line that reads cleanly needs no repair.
        whole = [(w if w != 'Yocals' else 'Vocals', b) for w, b in WORDS]
        self.assertFalse(subject_word_obstructed(receipt_line(whole), [CURSOR]))
        # Touching any other word, or none, is not that.
        self.assertFalse(subject_word_obstructed(receipt_line(), [CURSOR, [500, 860, 520, 880]]))
        self.assertFalse(subject_word_obstructed(receipt_line(), [[600, 860, 620, 880]]))
        self.assertFalse(subject_word_obstructed(receipt_line(), []))
        # Word boxes that do not spell the line are not a reading of it.
        self.assertFalse(subject_word_obstructed(receipt_line(text='Yocals went up by 2.'), [CURSOR]))

    def test_a_proven_obstruction_lets_the_subject_be_repaired(self):
        source = raw([receipt_line()],
                     resolved_receipt_occlusions=[dict(text=receipt_line()['text'], box=[314, 857, 530, 883],
                                                       basis='overlay_covers_fixed_subject_word')])
        effect = parse(source)['effects'][0]
        self.assertEqual((effect['kind'], effect['field'], effect['amount']), ('performance_change', 'vocal', 20))
        self.assertEqual(effect['raw_text'], 'Vocals went up by 20.')
        self.assertEqual(effect['original_text'], 'Yocals went up by 20.')
        self.assertEqual(effect['text_normalization'], 'obstructed_subject_word')

    def test_without_that_proof_the_same_line_stays_unparsed(self):
        self.assertEqual(parse(raw([receipt_line()]))['effects'], [])
        source = raw([receipt_line()],
                     resolved_receipt_occlusions=[dict(text='Yocals went up by 20.', box=[314, 900, 530, 926],
                                                       basis='overlay_covers_fixed_subject_word')])
        self.assertEqual(parse(source)['effects'], [])


if __name__ == '__main__':
    unittest.main()
