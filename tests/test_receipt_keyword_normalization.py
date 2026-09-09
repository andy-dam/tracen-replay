import copy
import json
from pathlib import Path
import unittest

from tracen_replay.vision import parse


class ReceiptKeywordNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(Path('tests/fixtures/receipt-keyword-249000.json').read_text(encoding='utf-8'))

    def effects(self, text=None):
        raw = copy.deepcopy(self.raw)
        if text is not None:
            raw['lines'] = [dict(line, text=text) if line['text'].startswith('Friewdship') else line for line in raw['lines']]
        return parse(raw)['effects']

    def test_source_receipt_recovers_keyword_without_changing_recipient_or_amount(self):
        effect = next(e for e in self.effects() if e.get('name') == 'Light Hello')
        self.assertEqual(effect['amount'], 4)
        self.assertEqual(effect['original_text'], 'Friewdship with Light Hello went up by 4.')
        self.assertEqual(effect['confidence'], 97.747)

    def test_missing_keyword_letter_preserves_original_text(self):
        text = 'Frendship with Example Person went up by 6.'
        effect = next(e for e in self.effects(text) if e.get('name') == 'Example Person')
        self.assertEqual(effect['amount'], 6)
        self.assertEqual(effect['original_text'], text)

    def test_projection_incomplete_sentence_and_other_keyword_are_not_repaired(self):
        for text in ('Friewdship with Example Person will go up by 4.',
                     'Friewdship with Example Person went up by 4',
                     'Leadership with Example Person went up by 4.'):
            with self.subTest(text=text):
                self.assertFalse(any(e.get('name') == 'Example Person' for e in self.effects(text)))

    def test_low_confidence_is_not_promoted(self):
        self.raw['lines'] = [dict(line, confidence=80) if line['text'].startswith('Friewdship') else line for line in self.raw['lines']]
        self.assertFalse(any(e.get('name') == 'Light Hello' for e in self.effects()))

    def test_source_friendship_verb_preserves_recipient_and_amount(self):
        raw = json.loads(Path('tests/fixtures/receipt-keyword-499000.json').read_text(encoding='utf-8'))
        effect = next(e for e in parse(raw)['effects'] if e.get('name') == 'Kitasan Black')
        self.assertEqual(effect['amount'], 6)
        self.assertEqual(effect['original_text'], 'Friendship with Kitasan Black wert up by 6.')
        for text, confidence in (
            ('Friendship with Example Person wert up by 6', 99),
            ('Friendship with Example Person will go up by 6.', 99),
            ('Friendship with Example Person wert up by 6.', 80),
        ):
            with self.subTest(text=text, confidence=confidence):
                changed = copy.deepcopy(raw)
                changed['lines'] = [dict(l, text=text, confidence=confidence)
                    if 'Kitasan Black' in l['text'] else l for l in changed['lines']]
                self.assertFalse(any(e.get('name') == 'Example Person' for e in parse(changed)['effects']))

    def test_repaired_keyword_does_not_bypass_recipient_occlusion(self):
        import hashlib
        from unittest.mock import patch
        from PIL import Image
        from tests.test_neural_transactions import raw, line
        from tracen_replay.receipt_occlusion import annotate
        pane = Image.new('RGB', (810,1080), 'white')
        for prefix in ('Friewdship', 'Frendship'):
            with self.subTest(prefix=prefix):
                source = raw([line(prefix+' with Example Name went up by 7.', (300,820,740,850))])
                source['gameplay_sha256'] = hashlib.sha256(pane.tobytes()).hexdigest()
                source['overlay_alignment'] = [dict(line_box=[300,820,740,850],
                    words=[prefix,'with','Example','Name','went','up','by','7.'],
                    columns=[[1,2],[4,5],[7,8,9],[11,12],[14,15],[17,18],[20,21],[24,25]],
                    line_length=27, confidence=99)]
                with patch('tracen_replay.receipt_occlusion.overlay_boxes', return_value=[[445,824,458,846]]):
                    marked = annotate(source,pane)
                self.assertEqual(parse(marked)['effects'], [])
                self.assertTrue(marked['occluded_receipt_lines'][0]['recipient_name_occluded'])
