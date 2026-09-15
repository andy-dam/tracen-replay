import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import analyze_frames


class FrameQueueFailureTests(unittest.TestCase):
    def test_first_frame_failure_cancels_pending_work_and_preserves_partial_cache(self):
        calls = []
        lock = threading.Lock()

        class Reader:
            Image = Image

            def __init__(self, *args, **kwargs):
                pass

            def read(self, pane):
                marker = pane.getpixel((0, 0))[0]
                with lock:
                    calls.append(marker)
                if marker == 1:
                    raise RuntimeError('injected first-frame failure')
                # Give the consumer time to observe the failed future while
                # another real executor task is still in flight.
                time.sleep(0.02)
                return {}

        with workspace_temp() as root:
            (root / 'frames').mkdir()
            frames = []
            for index in range(24):
                relative = f'frames/{index}.png'
                Image.new('RGB', (960, 1080), (index + 1, 0, 0)).save(root / relative)
                frames.append(dict(id=f'f{index:03d}', evidence=relative,
                                   source_timestamp_ms=index * 250))
            with patch('tracen_replay.full_recording.NeuralReader', Reader), patch(
                    'tracen_replay.full_recording.parse_receipt_pixels', return_value={}):
                with self.assertRaisesRegex(RuntimeError, 'injected first-frame failure'):
                    analyze_frames(dict(frames=frames, source={}), root, workers=2)
            self.assertIn(1, calls)
            self.assertLess(len(calls), len(frames))
            cached = list((root / 'neural').glob('*.json'))
            self.assertEqual(len(cached), len(calls) - 1)
            self.assertFalse((root / 'neural/f000.json').exists())


if __name__ == '__main__':
    unittest.main()
