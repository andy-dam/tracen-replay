from copy import deepcopy
import unittest

from tests.test_neural_transactions import line, raw
from tracen_replay.vision import parse


class HintReceiptGrammarTests(unittest.TestCase):
    def test_missing_plural_marker_characters_preserve_literal_values(self):
        for marker in ('', '(', '(s', '()', ')', 's', 's)'):
            with self.subTest(marker=marker):
                text = f'Gained 12 hint level{marker} for Literal Skill.'
                source = raw([line(text)])
                before = deepcopy(source)
                effects = parse(source)['effects']
                self.assertEqual(len(effects), 1)
                self.assertEqual((effects[0]['name'], effects[0]['amount']), ('Literal Skill', 12))
                self.assertEqual(effects[0]['original_text'], text)
                self.assertEqual(source, before)

    def test_wrapped_receipt_retains_both_original_lines(self):
        prefix = 'Gained 2 hint level( for Straightaway'
        source = raw([line(prefix, (315, 875, 688, 908), 96.509),
                      line('Recovery.', (315, 901, 418, 932), 99.988)])
        effect = parse(source)['effects'][0]
        self.assertEqual((effect['name'], effect['amount']), ('Straightaway Recovery', 2))
        self.assertEqual(effect['original_text'], prefix + ' Recovery.')
        self.assertEqual(effect['confidence'], 96.509)

    def test_missing_digit_projection_and_unfinished_receipt_stay_unparsed(self):
        for text in ('Gained ? hint level( for Literal Skill.',
                     'Gained 2O hint level( for Literal Skill.',
                     'Will gain 2 hint level( for Literal Skill.',
                     'Gained 2 hint bonus for Literal Skill.',
                     'Gained 2 hint level( for Literal Skill'):
            with self.subTest(text=text):
                self.assertFalse(parse(raw([line(text)]))['effects'])

    def test_low_confidence_or_occluded_text_is_not_promoted(self):
        for flags in ({'confidence': 80}, {'confidence': 0, 'overlay_occluded': True}):
            source = raw([dict(line('Gained 2 hint level( for Literal Skill.'), **flags)])
            self.assertFalse(parse(source)['effects'])

    def test_distant_continuation_cannot_complete_damaged_prefix(self):
        for box in ((315, 950, 418, 980), (400, 901, 500, 932)):
            source = raw([line('Gained 2 hint level( for Straightaway', (315, 875, 688, 908)),
                          line('Recovery.', box)])
            self.assertFalse(parse(source)['effects'])


if __name__ == '__main__':
    unittest.main()
