"""Work a run repeats on the same file is done once, and redone when the file changes."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from tracen_replay import event_choice_adapter, frame_cache


def pane(seed):
    return Image.fromarray(np.random.default_rng(seed).integers(0, 256, (1080, 810, 3), dtype=np.uint8), 'RGB')


class FrameDigestTests(unittest.TestCase):
    def setUp(self):
        frame_cache.clear()

    def test_a_fingerprint_is_decoded_once_and_again_after_the_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pane.png'
            first = pane(1)
            first.save(path)
            with mock.patch.object(frame_cache, '_decoded', wraps=frame_cache._decoded) as decoded:
                digest, size = frame_cache.rgb_digest(path)
                self.assertEqual(frame_cache.rgb_digest(path), (digest, size))
                self.assertEqual(decoded.call_count, 1)
            self.assertEqual((digest, size), (hashlib.sha256(first.tobytes()).hexdigest(), (810, 1080)))
            second = pane(2)
            second.save(path)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertEqual(frame_cache.rgb_sha256(path), hashlib.sha256(second.tobytes()).hexdigest())


class ChoiceObservationMemoTests(unittest.TestCase):
    def test_a_pane_is_verified_once_per_unchanged_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'gameplay').mkdir()
            path = root / 'gameplay' / 'frame.png'
            image = pane(3)
            image.save(path)
            reading = dict(source_timestamp_ms=1000, evidence='gameplay/frame.png')
            raw = dict(source_timestamp_ms=1000, evidence='gameplay/frame.png',
                       gameplay_sha256=hashlib.sha256(image.tobytes()).hexdigest())
            with mock.patch.object(event_choice_adapter, '_verified_pane',
                                   wraps=event_choice_adapter._verified_pane) as verified:
                first = event_choice_adapter._observe_verified(root, reading, raw, [], 1000, 'gameplay/frame.png')
                second = event_choice_adapter._observe_verified(root, reading, raw, [], 1000, 'gameplay/frame.png')
                self.assertEqual(first, second)
                self.assertEqual(verified.call_count, 1)
                stat = path.stat()
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
                event_choice_adapter._observe_verified(root, reading, raw, [], 1000, 'gameplay/frame.png')
                self.assertEqual(verified.call_count, 2)
            self.assertTrue(first[0])


if __name__ == '__main__':
    unittest.main()
