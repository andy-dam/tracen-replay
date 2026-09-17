"""The OCR engine is always handed one compact array, whatever view a caller passes."""
import unittest

import numpy as np

from tracen_replay.vision import CompactInputEngine


class FakeEngine:
    def __init__(self):
        self.seen = None

    def __call__(self, img, *args, **kwargs):
        self.seen = img
        return 'result'

    def text_rec(self, request):
        return 'read', request


class CompactInputEngineTests(unittest.TestCase):
    def test_a_flipped_view_reaches_the_engine_as_one_compact_array_of_the_same_pixels(self):
        rgb = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
        view = rgb[:, :, ::-1]
        self.assertFalse(view.flags['C_CONTIGUOUS'])
        fake = FakeEngine()
        self.assertEqual(CompactInputEngine(fake)(view), 'result')
        self.assertTrue(fake.seen.flags['C_CONTIGUOUS'])
        np.testing.assert_array_equal(fake.seen, view)

    def test_everything_else_passes_through_to_the_engine(self):
        fake = FakeEngine()
        self.assertEqual(CompactInputEngine(fake).text_rec('crops'), ('read', 'crops'))
        self.assertEqual(CompactInputEngine(fake)('a path'), 'result')
        self.assertEqual(fake.seen, 'a path')


if __name__ == '__main__':
    unittest.main()
