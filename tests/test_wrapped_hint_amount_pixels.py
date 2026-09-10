"""Replay actual recognizer crop outputs against a two-digit pixel control."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from PIL import Image
from tracen_replay.hint_card_identity import _wrapped_amount_ocr


FIXTURES = Path(__file__).parent / 'fixtures'


class WrappedHintAmountPixelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((FIXTURES / 'hint-amount-pixels.json').read_text(encoding='utf-8'))
        self.outputs = {entry['pixel_sha256']: entry for entry in self.fixture['observations']}
        self.seen = []

    def reader(self):
        def recognize(request):
            self.assertEqual(len(request.img), 1)
            rgb = request.img[0][:, :, ::-1].copy()
            digest = hashlib.sha256(rgb.tobytes()).hexdigest()
            self.assertIn(digest, self.outputs, 'Changed crop needs a new recorded recognition witness.')
            entry = self.outputs[digest]
            self.seen.append(entry)
            return SimpleNamespace(txts=entry['recognized_text'], scores=entry['confidence'])
        return SimpleNamespace(
            engine=SimpleNamespace(text_rec=recognize),
            TextRecInput=lambda **kwargs: SimpleNamespace(**kwargs),
            fingerprint='a' * 64,
        )

    def pane(self, name):
        path = FIXTURES / name
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.fixture['fixture_sha256'][name])
        pane = Image.new('RGB', (810, 1080), 'white')
        with Image.open(path) as image:
            pane.paste(image.convert('RGB'), tuple(self.fixture['source_crop_box'][:2]))
        return pane

    def test_source_amount_has_a_right_boundary_witness(self):
        result = _wrapped_amount_ocr(self.reader(), self.pane('hint-amount-source.png'),
                                     {'amount': 2, 'box': self.fixture['receipt_box']})
        self.assertIsNotNone(result)
        self.assertEqual(result['amount'], 2)
        self.assertGreaterEqual(len(self.seen), 2)

    def test_high_confidence_first_digit_cannot_hide_a_second_digit(self):
        # The recorded narrow crop returns 2 at 98.621% even though the
        # constructed receipt contains 22. Its adjacent field reads "2 h".
        result = _wrapped_amount_ocr(self.reader(), self.pane('hint-amount-duplicated-digit.png'),
                                     {'amount': 2, 'box': self.fixture['receipt_box']})
        self.assertIsNone(result)
        self.assertEqual(self.seen[0]['recognized_text'], ['2'])
        self.assertGreater(self.seen[0]['confidence'][0], .98)
        self.assertGreaterEqual(len(self.seen), 2)


if __name__ == '__main__':
    unittest.main()
