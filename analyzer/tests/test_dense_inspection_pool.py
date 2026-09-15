import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.inspect_receipts import inspect, prepare_window


class _FakeReader:
    """Deterministic stand-in for the OCR reader (same interface as NeuralReader)."""
    fingerprint = 'fake-engine'
    models = {'model': 'hash'}
    Image = Image

    def read(self, pane):
        digest = hashlib.sha256(pane.tobytes()).hexdigest()
        return dict(lines=[dict(text=f'+{int(digest[:2], 16) % 90 + 10}', confidence=99.0, box=[300, 660, 360, 700])],
                    regions={}, header='Career', result_grid=False, current_grid=False,
                    engine_fingerprint=self.fingerprint, model_sha256=dict(self.models),
                    gameplay_sha256=digest)


def _fake_decode(source, directory, start, duration, fps, origin):
    frames = []
    for index in range(3):
        path = directory / f'{index + 1:06d}.jpg'
        Image.new('RGB', (1920, 1080), (40 + 60 * index, 80, 120)).save(path)
        frames.append(dict(id=f'frame-{index + 1:06d}', source_timestamp_ms=int(start * 1000) + index * 50,
                           evidence=f'frames/{path.name}', source_pts=index, time_base='1/1000'))
    return frames


def _fixture(root):
    video = root / 'source.mp4'
    video.write_bytes(b'source identity test')
    source = dict(sha256=hashlib.sha256(video.read_bytes()).hexdigest(), duration_ms=5000)
    (root / 'capture.json').write_text(json.dumps(dict(source=source, frames=[])), encoding='utf-8')
    return video


def _snapshot(root):
    out = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix in ('.json', '.png'):
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class DenseInspectionPoolTests(unittest.TestCase):
    def test_prepare_then_inspect_produces_the_same_caches_and_manifest_as_inspect_alone(self):
        with workspace_temp() as root, patch('tracen_replay.inspect_receipts.decode_frames', side_effect=_fake_decode), \
                patch('tracen_replay.receipt_wrapping.enrich', side_effect=lambda raw, pane, reader: raw):
            direct = root / 'direct'; direct.mkdir(); video = _fixture(direct)
            inspect(video, direct, 1000, 2000, 30, reader=_FakeReader())
            pooled = root / 'pooled'; pooled.mkdir(); video2 = _fixture(pooled)
            self.assertEqual(prepare_window(video2, pooled, 1000, 2000, 30, reader=_FakeReader()), 3)
            self.assertFalse((pooled / 'receipt-inspection.json').exists())
            inspect(video2, pooled, 1000, 2000, 30, reader=_FakeReader())
            self.assertEqual(_snapshot(direct), _snapshot(pooled))
            manifest = json.loads((pooled / 'receipt-inspection.json').read_text(encoding='utf-8'))
            self.assertEqual([w['start_ms'] for w in manifest['windows']], [1000])
            self.assertEqual(len(manifest['readings']), 3)

    def test_prepare_windows_with_one_worker_is_a_no_op(self):
        from tracen_replay.dense_inspection_pool import prepare_windows
        self.assertEqual(prepare_windows('x', 'y', [dict(start_ms=0, end_ms=10)], 30, kind='base', model_dir='m', workers=1), 0)
        self.assertEqual(prepare_windows('x', 'y', [], 30, kind='base', model_dir='m', workers=4), 0)


if __name__ == '__main__':
    unittest.main()
