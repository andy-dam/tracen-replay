import hashlib
import unittest

from PIL import Image

from tracen_replay.stat_state_details import read_result_card_occlusion


def _line(text, box, confidence=99.0):
    return {'text': text, 'confidence': confidence, 'box': list(box)}


def _synthetic_raw(image):
    pixels = image.convert('RGB').tobytes()
    return {
        'lines': [
            _line('Training', (150, 1, 227, 32)),
            _line('Speed', (348, 793, 416, 825)),
            _line('??/1600', (326, 832, 454, 877), 40.0),
        ],
        'regions': {
            'result.speed': _line('??/1600', (322, 834, 448, 876), 40.0),
        },
        'header': 'Training',
        'result_grid': True,
        'current_grid': False,
        'gameplay_sha256': hashlib.sha256(pixels).hexdigest(),
    }


class ResultCardOcclusionTests(unittest.TestCase):


    def test_missing_ocr_without_pixel_obstruction_stays_unknown(self):
        image = Image.new('RGB', (810, 1080), (150, 150, 150))
        raw = _synthetic_raw(image)
        self.assertEqual(read_result_card_occlusion(raw, image), {})

    def test_complete_ratio_is_not_marked_even_with_unrelated_pixels(self):
        image = Image.new('RGB', (810, 1080), (150, 150, 150))
        raw = _synthetic_raw(image)
        raw['regions']['result.speed'] = _line(
            '556/1600', (322, 834, 448, 876), 99.0)
        raw['lines'][-1] = _line('556/1600', (326, 832, 454, 877), 99.0)
        self.assertEqual(read_result_card_occlusion(raw, image), {})


if __name__ == '__main__':
    unittest.main()
