"""The learned reader cuts, decodes and attaches its reads the way it was trained."""
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tracen_replay.learned_reader import (
    BADGE_BOXES,
    CHARS,
    HEIGHT,
    PANE_LEFT,
    READER_MARGIN,
    RESULT_BOXES,
    WIDTH,
    annotate,
    box_array,
    ctc_decode,
    pane_box,
    read_shape,
)


class FakeReader:
    """Stands in for the ONNX session: every stat box reads '+12', skill points '+5'."""
    model_sha256 = 'f' * 64

    def transcribe(self, arrays):
        out = []
        for index in range(len(arrays)):
            text = '+5' if index % len(RESULT_BOXES) == len(RESULT_BOXES) - 1 else '+12'
            out.append((text, [0.99] * len(text)))
        return out


class LearnedReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='learned-reader-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_boxes_are_cut_wider_and_sized_for_the_network(self):
        box = BADGE_BOXES['speed']
        left, top, right, bottom = READER_MARGIN
        self.assertEqual(pane_box(box, READER_MARGIN), (box[0] - PANE_LEFT - left, box[1] - top, box[2] - PANE_LEFT + right, box[3] + bottom))
        pane = Image.new('RGB', (810, 1080), (10, 20, 30))
        array = box_array(pane, 'speed')
        self.assertEqual(array.shape, (3, HEIGHT, WIDTH))
        self.assertEqual(array.dtype, np.uint8)
        self.assertEqual(tuple(array[:, 0, 0]), (10, 20, 30))

    def test_decoding_and_shapes(self):
        plus = CHARS.index('+') + 1
        slash = CHARS.index('/') + 1
        self.assertEqual(ctc_decode([0, plus, 2, 2, 0, 3]), '+12')
        self.assertEqual(ctc_decode([2, 0, 10, 1, slash], [0.9, 0.5, 0.8, 0.7, 0.95]), ('190/', [0.9, 0.8, 0.7, 0.95]))
        self.assertEqual(read_shape('speed', '190/', [0.95] * 4), (190, None))
        self.assertEqual(read_shape('speed', '190/1341', [0.95] * 4 + [0.2] * 4), (190, None))
        self.assertEqual(read_shape('speed', '01/', [0.99] * 3), (None, None))
        self.assertEqual(read_shape('speed', '+15', [0.99, 0.99, 0.5]), (None, None))
        self.assertEqual(read_shape('skill_points', '2097', [0.99] * 4), (2097, None))
        self.assertEqual(read_shape('wit', '+15', [0.99] * 3), (None, 15))

    def test_reads_are_attached_to_result_readings_with_the_model(self):
        (self.tmp / 'gameplay').mkdir()
        Image.new('RGB', (810, 1080), 'white').save(self.tmp / 'gameplay/frame-1.png')
        Image.new('RGB', (100, 100), 'white').save(self.tmp / 'gameplay/small.png')
        readings = [
            dict(screen='training_result', evidence='gameplay/frame-1.png', source_timestamp_ms=1000, facts={}),
            dict(screen='training_result', evidence='gameplay/small.png', source_timestamp_ms=1100, facts={}),
            dict(screen='training_result', evidence='gameplay/missing.png', source_timestamp_ms=1200, facts={}),
            dict(screen='event_outcome', evidence='gameplay/frame-1.png', source_timestamp_ms=1000, facts={}),
        ]
        reader = FakeReader()
        self.assertEqual(annotate(readings, self.tmp, reader), 1)
        reads = readings[0]['facts']['learned_result_reads']
        self.assertEqual(reads['model_sha256'], reader.model_sha256)
        self.assertEqual(reads['fields']['speed'], dict(text='+12', confidence=0.99, value=None, gain=12))
        self.assertEqual(reads['fields']['skill_points']['gain'], 5)
        self.assertNotIn('learned_result_reads', readings[1]['facts'])
        self.assertNotIn('learned_result_reads', readings[3]['facts'])
        # The same model does not read a frame twice.
        self.assertEqual(annotate(readings, self.tmp, reader), 0)


if __name__ == '__main__':
    unittest.main()
