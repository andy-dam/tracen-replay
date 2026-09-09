import hashlib
import unittest
from unittest.mock import patch

from PIL import Image

from tracen_replay.receipt_occlusion import annotate, friendship_name_bounds, receipt_line
from tracen_replay.vision import parse


def line(text, box=(316, 831, 767, 861), confidence=99):
    return dict(text=text, box=list(box), confidence=confidence)


def raw(lines):
    return dict(lines=list(lines), regions={}, header='Training',
                current_grid=False, result_grid=False)


class FriendshipReceiptRepairTests(unittest.TestCase):
    def test_source_keyword_typo_preserves_name_amount_and_provenance(self):
        # Source: independent-01/neural/part-008-frame-000153.json at 998000 ms.
        text = 'Frienlship with Agnes Tachyon went up by 2.'
        effect, = parse(raw([line(text, box=(316, 831, 746, 860), confidence=97.362)]))['effects']
        self.assertEqual((effect['kind'], effect['name'], effect['amount']),
                         ('friendship_change', 'Agnes Tachyon', 2))
        self.assertEqual(effect['raw_text'], 'Friendship with Agnes Tachyon went up by 2.')
        self.assertEqual(effect['original_text'], text)
        self.assertEqual(effect['confidence'], 97.362)
        self.assertEqual(effect['text_normalization'], 'fixed_receipt_verb')

    def test_source_verb_typo_preserves_name_amount_and_provenance(self):
        # Source: independent-01/neural/part-008-frame-000164.json at 1000750 ms.
        text = 'Friendship with Director Akikawa ent up by 5.'
        effect, = parse(raw([line(text, box=(316, 831, 767, 861), confidence=99.015)]))['effects']
        self.assertEqual((effect['kind'], effect['name'], effect['amount']),
                         ('friendship_change', 'Director Akikawa', 5))
        self.assertEqual(effect['raw_text'], 'Friendship with Director Akikawa went up by 5.')
        self.assertEqual(effect['original_text'], text)
        self.assertEqual(effect['confidence'], 99.015)
        self.assertEqual(effect['text_normalization'], 'fixed_receipt_verb')

    def test_repairs_require_complete_source_receipt_grammar(self):
        for text in (
            'Frienlship with Example Person went up by 2',
            'Frienlship with Example Person will go up by 2.',
            'Friendship with Example Person ent up by 5',
            'Friendship with Example Person ent down by 5.',
        ):
            with self.subTest(text=text):
                self.assertEqual(parse(raw([line(text)]))['effects'], [])

    def test_occlusion_grammar_protects_source_variants(self):
        words = ['Frienlship', 'with', 'Director', 'Akikawa', 'ent', 'up', 'by', '5.']
        columns = [[0, 1], [3, 4], [6, 7], [9, 10], [12, 13], [15], [18], [21, 22]]
        bounds = friendship_name_bounds([300, 820, 740, 850], words, columns, 55)
        self.assertIsNotNone(bounds)
        self.assertTrue(receipt_line(line('Frienlship with Director Akikawa ent up by 5.')))
        self.assertTrue(receipt_line(line('Frienlship with Director Akikawa is maxed out.')))

    def test_cursor_over_source_variant_still_abstains(self):
        text = 'Frienlship with Example Name went up by 7.'
        box = [300, 820, 740, 850]
        pane = Image.new('RGB', (810, 1080), 'white')
        source = raw([line(text, box)])
        source['gameplay_sha256'] = hashlib.sha256(pane.tobytes()).hexdigest()
        source['overlay_alignment'] = [dict(
            line_box=box,
            words=['Frienlship', 'with', 'Example', 'Name', 'went', 'up', 'by', '7.'],
            columns=[[1, 2], [4, 5], [7, 8, 9], [11, 12], [14, 15], [17], [20], [23, 24]],
            line_length=27,
            confidence=99,
        )]
        with patch('tracen_replay.receipt_occlusion.overlay_boxes',
                   return_value=[[445, 824, 458, 846]]):
            marked = annotate(source, pane)
        self.assertTrue(marked['occluded_receipt_lines'][0]['recipient_name_occluded'])
        self.assertEqual(parse(marked)['effects'], [])


if __name__ == '__main__':
    unittest.main()
