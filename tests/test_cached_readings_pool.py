import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_full_recording import FakeReader
from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import analyze_frames, cached_readings
from tracen_replay.pipeline import PipelineError


def _fixture(root, count=5):
    (root / 'frames').mkdir()
    frames = []
    for index in range(count):
        image = Image.new('RGB', (1920, 1080), (10 * index, 20, 30))
        image.paste(Image.new('RGB', (810, 1080), (255, 255 - 10 * index, 255)), (148, 0))
        image.save(root / 'frames' / f'f{index}.png')
        frames.append(dict(id=f'frame-{index}', evidence=f'frames/f{index}.png', source_timestamp_ms=250 * index))
    report = dict(source=dict(sha256='a' * 64), frames=frames)
    FakeReader.inputs = []
    with patch('tracen_replay.full_recording.NeuralReader', FakeReader):
        analyze_frames(report, root, workers=1)
    return report


class CachedReadingsPoolTests(unittest.TestCase):
    def test_pooled_reload_matches_the_sequential_pass(self):
        with workspace_temp() as root:
            report = _fixture(root)
            sequential = cached_readings(report, root)
            self.assertEqual(len(sequential), 5)
            self.assertEqual(cached_readings(report, root, workers=2), sequential)

    def test_pooled_reload_raises_the_first_failure_in_frame_order(self):
        with workspace_temp() as root:
            report = _fixture(root)
            (root / 'frames' / 'f3.png').write_bytes(b'changed')
            (root / 'frames' / 'f4.png').write_bytes(b'changed too')
            with self.assertRaisesRegex(PipelineError, 'source evidence mismatch: frame-3'):
                cached_readings(report, root, workers=2)

    def test_pooled_reload_honours_partial_loads(self):
        with workspace_temp() as root:
            report = _fixture(root)
            (root / 'neural' / 'frame-1.json').unlink()
            rows = cached_readings(report, root, allow_partial=True, workers=2)
            self.assertEqual([row['source_timestamp_ms'] for row in rows], [0, 500, 750, 1000])
            with self.assertRaisesRegex(PipelineError, 'Missing OCR observation: frame-1'):
                cached_readings(report, root, workers=2)


if __name__ == '__main__':
    unittest.main()
