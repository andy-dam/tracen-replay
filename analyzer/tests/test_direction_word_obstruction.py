"""The cursor over a receipt's direction word leaves its subject and number readable."""
import unittest

from tests.test_neural_transactions import line, raw
from tracen_replay.receipt_grammar import direction_receipt
from tracen_replay.receipt_occlusion import direction_word_obstructed, direction_word_resolution
from tracen_replay.vision import parse


# "Speed went up by 5." with the mouse cursor parked over "up" for two
# seconds, as the recognizer placed its words on the frames this case comes
# from: the word gone outright on most frames, a remnant fused to "by" on one.
GONE = [('Speed', [322, 806, 377, 836]), ('went', [387, 806, 432, 836]), ('by', [467, 806, 487, 836]),
        ('5.', [497, 806, 516, 836])]
REMNANT = [('Speed', [323, 806, 379, 836]), ('went', [388, 806, 429, 836]), ('u', [433, 806, 444, 836]),
           ('by', [463, 806, 489, 836]), ('5.', [492, 806, 514, 836])]
CURSOR = [439, 797, 457, 819]
POPUP = line('+5', (492, 563, 601, 642), 99.6)
LABEL = line('Speed', (508, 634, 636, 691), 99.9)


def receipt_line(words=GONE, text='Speed went  by 5.', confidence=98.439):
    return dict(line(text, (315, 806, 516, 836), confidence),
                word_boxes=[dict(text=word, box=list(box)) for word, box in words])


class DirectionWordObstructionTests(unittest.TestCase):
    def test_the_direction_word_alone_is_supplied_around_what_was_read(self):
        self.assertEqual(direction_receipt('Speed went  by 5.', 'up'), 'Speed went up by 5.')
        self.assertEqual(direction_receipt('Speed went uby 5.', 'up'), 'Speed went up by 5.')
        self.assertEqual(direction_receipt('Guts went dwn by 12!', 'down'), 'Guts went down by 12!')
        self.assertEqual(direction_receipt('Vocals went up by 20.', 'up'), 'Vocals went up by 20.')
        # A remnant names one direction; letters out of their order name none.
        self.assertIsNone(direction_receipt('Speed went uby 5.', 'down'))
        self.assertIsNone(direction_receipt('Speed went pu by 5.', 'up'))
        # The subject must already read as a field, the number must carry its
        # full stop, and only a one-word receipt has this shape.
        for text in ('Yocals went  by 5.', 'Speed went  by 5', 'Speed went  by five.',
                     'Friendship with Speed went  by 5.', 'Speed cap went  by 5.'):
            with self.subTest(text=text):
                self.assertIsNone(direction_receipt(text, 'up'))
        self.assertIsNone(direction_receipt('Speed went  by 5.', 'sideways'))

    def test_an_obstruction_between_the_verb_and_the_number_alone_is_that(self):
        self.assertTrue(direction_word_obstructed(receipt_line(), [CURSOR]))
        self.assertTrue(direction_word_obstructed(receipt_line(REMNANT, 'Speed went uby 5.'), [[443, 797, 461, 819]]))
        # Touching the subject, the verb or the number, or nothing, is not that.
        self.assertFalse(direction_word_obstructed(receipt_line(), [CURSOR, [370, 810, 392, 830]]))
        self.assertFalse(direction_word_obstructed(receipt_line(), [[495, 810, 510, 830]]))
        self.assertFalse(direction_word_obstructed(receipt_line(), [[600, 810, 620, 830]]))
        self.assertFalse(direction_word_obstructed(receipt_line(), []))
        # Word boxes that do not spell the line are not a reading of it.
        self.assertFalse(direction_word_obstructed(receipt_line(text='Speed went  by 6.'), [CURSOR]))

    def test_the_gain_popup_on_the_frame_states_the_direction(self):
        obstructed = receipt_line(REMNANT, 'Speed went uby 5.')
        proof = direction_word_resolution(obstructed, [obstructed, POPUP, LABEL])
        self.assertEqual((proof['direction'], proof['gain_popup']['text'], proof['gain_popup_label']['text']),
                         ('up', '+5', 'Speed'))
        # No popup, another stat's popup, another amount, or a sign the
        # remnant contradicts proves nothing.
        self.assertIsNone(direction_word_resolution(obstructed, [obstructed]))
        self.assertIsNone(direction_word_resolution(obstructed, [obstructed, POPUP, line('Guts', (508, 634, 636, 691), 99.9)]))
        self.assertIsNone(direction_word_resolution(obstructed, [obstructed, line('+6', (492, 563, 601, 642), 99.6), LABEL]))
        self.assertIsNone(direction_word_resolution(obstructed, [obstructed, line('-5', (492, 563, 601, 642), 99.6), LABEL]))
        # With the word gone outright, the sign alone decides.
        gone = receipt_line()
        self.assertEqual(direction_word_resolution(gone, [gone, line('-5', (492, 563, 601, 642), 99.6), LABEL])['direction'], 'down')

    def test_a_proven_obstruction_lets_the_direction_be_supplied(self):
        source = raw([receipt_line()],
                     resolved_receipt_occlusions=[dict(text='Speed went  by 5.', box=[315, 806, 516, 836],
                                                       basis='overlay_covers_fixed_direction_word', direction='up')])
        effect = parse(source)['effects'][0]
        self.assertEqual((effect['kind'], effect['field'], effect['amount']), ('stat_change', 'speed', 5))
        self.assertEqual(effect['raw_text'], 'Speed went up by 5.')
        self.assertEqual(effect['original_text'], 'Speed went  by 5.')
        self.assertEqual(effect['text_normalization'], 'obstructed_direction_word')
        down = dict(source, resolved_receipt_occlusions=[dict(source['resolved_receipt_occlusions'][0], direction='down')])
        self.assertEqual(parse(down)['effects'][0]['amount'], -5)

    def test_without_that_proof_the_same_line_stays_unparsed(self):
        self.assertEqual(parse(raw([receipt_line()]))['effects'], [])
        source = raw([receipt_line()],
                     resolved_receipt_occlusions=[dict(text='Speed went  by 5.', box=[315, 806, 516, 836],
                                                       basis='overlay_covers_fixed_direction_word')])
        self.assertEqual(parse(source)['effects'], [])


if __name__ == '__main__':
    unittest.main()
