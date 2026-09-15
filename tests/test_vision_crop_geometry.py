import hashlib
import math
import unittest
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageOps

import tracen_replay.vision as vision


class _RecognitionInput:
    def __init__(self, *, img):
        self.img = img


class _FakeEngine:
    def __init__(self):
        self.recognition_images = None

    def __call__(self, _image):
        # The full-pane detector adds the 148px gameplay origin to x values.
        box = np.asarray([
            [362.0, 693.0], [402.0, 693.0],
            [402.0, 724.0], [362.0, 724.0],
        ])
        return SimpleNamespace(boxes=[box], txts=['Power'], scores=[0.99])

    def text_rec(self, input_data):
        self.recognition_images = list(input_data.img)
        return SimpleNamespace(txts=['/1300'], scores=[0.99])


def _fake_reader():
    reader = object.__new__(vision.NeuralReader)
    reader.np = np
    reader.Image = Image
    reader.ImageOps = ImageOps
    reader.TextRecInput = _RecognitionInput
    reader.engine = _FakeEngine()
    reader.models = {'test.onnx': 'model'}
    reader.fingerprint = 'test-engine'
    return reader


class VisionCropGeometryTests(unittest.TestCase):
    def test_fractional_box_expands_to_integer_pixel_boundaries(self):
        self.assertEqual(
            vision._normalize_crop_box((491, 728.5, 572, 808.5)),
            [491, 728, 572, 809],
        )
        self.assertEqual(
            vision._normalize_crop_box((148.1, 0.1, 957.9, 1079.9)),
            [148, 0, 958, 1080],
        )

    def test_invalid_crop_geometry_is_rejected_without_clamping(self):
        invalid = (
            (148, 0, 148, 20),
            (148, 20, 200, 19),
            (148, 0, 200),
            (148, 0, 200, '20'),
            (148, 0, 200, math.nan),
            (148, 0, 200, math.inf),
            (147.9, 0, 200, 20),
            (148, -0.1, 200, 20),
            (148, 0, 958.1, 20),
            (148, 0, 200, 1080.1),
        )
        for box in invalid:
            with self.subTest(box=box), self.assertRaises(ValueError):
                vision._normalize_crop_box(box)

    def test_reader_uses_canonical_box_for_actual_fractional_stat_cap_request(self):
        reader = _fake_reader()
        pane = Image.new('RGB', (810, 1080), (17, 33, 49))

        raw = reader.read(pane)

        region = raw['regions']['stats_cap.power']
        self.assertEqual(region['box'], [491, 728, 572, 809])
        self.assertEqual(reader.engine.recognition_images[0].shape, (81, 81, 3))

        source = np.asarray(pane.convert('RGB'))
        source_roi = source[728:809, 491 - vision.PANE[0]:572 - vision.PANE[0], :]
        gray = np.asarray(ImageOps.autocontrast(ImageOps.grayscale(Image.fromarray(source_roi))))
        expected = np.repeat(gray[:, :, None], 3, axis=2)[:, :, ::-1]
        self.assertTrue(np.array_equal(reader.engine.recognition_images[0], expected))

        # A downstream source proof using the published region box hashes the
        # same integer ROI that recognition consumed.  The recognizer's
        # grayscale view has a separate transform, so its bytes intentionally
        # have a different hash from the source RGB crop.
        roi_rgb = source_roi
        self.assertEqual(
            hashlib.sha256(roi_rgb.tobytes()).hexdigest(),
            hashlib.sha256(pane.crop((491 - vision.PANE[0], 728, 572 - vision.PANE[0], 809)).tobytes()).hexdigest(),
        )
        self.assertEqual(
            hashlib.sha256(expected.tobytes()).hexdigest(),
            hashlib.sha256(reader.engine.recognition_images[0].tobytes()).hexdigest(),
        )


if __name__ == '__main__':
    unittest.main()
