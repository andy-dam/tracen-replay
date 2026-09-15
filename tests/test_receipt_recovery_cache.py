import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from tests.test_gameplay import workspace_temp
from tracen_replay.inspect_receipts import inspect
from tracen_replay.pipeline import PipelineError
from tracen_replay.verify_evidence import verify


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


class ReceiptRecoveryCacheTests(unittest.TestCase):
    def fixture(self, root, origin_name='numeric-receipt-recovery'):
        video = root/'source.mp4'; video.write_bytes(b'source identity test')
        source = dict(sha256=hashlib.sha256(video.read_bytes()).hexdigest(), duration_ms=2000)
        save(root/'capture.json', dict(source=source, frames=[]))
        origin = root/origin_name; origin.mkdir()
        save(origin/'capture.json', dict(source=source))
        directory = origin/'receipt-inspection/0-1000-30'; (directory/'frames').mkdir(parents=True)
        frame_path = directory/'frames/frame-000001.png'
        proof = directory/'frame-000001.png'
        Image.new('RGB', (1920, 1080), 'white').save(frame_path)
        pane = Image.new('RGB', (810, 1080), 'white'); pane.save(proof)
        frame = dict(id='frame-000001', source_timestamp_ms=0, evidence='frames/frame-000001.png', source_pts=0, time_base='1/1000')
        save(directory/'frames.json', [frame])
        raw = dict(source_timestamp_ms=0, source_sha256=source['sha256'],
                   source_frame_sha256=hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                   evidence=proof.relative_to(origin).as_posix(), gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest(),
                   engine_fingerprint='engine', model_sha256={'model': 'hash'})
        save(directory/'frame-000001.v2.json', raw)
        save(origin/'receipt-inspection.json', dict(source_sha256=source['sha256'],
             windows=[dict(start_ms=0, end_ms=1000, fps=30)],
             readings=[dict(source_timestamp_ms=0, evidence=raw['evidence'])]))
        return video, origin, proof

    def test_completed_window_validates_cache_without_decode_or_ocr(self):
        with workspace_temp() as root:
            video, origin, proof = self.fixture(root)
            with patch('tracen_replay.inspect_receipts.decode_frames', side_effect=AssertionError('Unexpected decode')):
                inspect(video, origin, 0, 1000, 30, reader=SimpleNamespace(fingerprint='engine', models={'model': 'hash'}))
                with self.assertRaisesRegex(PipelineError, 'model changed'):
                    inspect(video, origin, 0, 1000, 30, reader=SimpleNamespace(fingerprint='different', models={'model': 'hash'}))
                proof.unlink()
                with self.assertRaisesRegex(PipelineError, 'missing OCR or gameplay proof'):
                    inspect(video, origin, 0, 1000, 30)

    def test_nested_recovery_proof_is_included_in_evidence_integrity(self):
        with workspace_temp() as root, patch('builtins.print'):
            video, origin, proof = self.fixture(root)
            result = verify(root, video)
            self.assertTrue(result['evidence_integrity_verified'])
            self.assertEqual(result['verified_observations'], 1)
            self.assertIn('numeric-receipt-recovery/receipt-inspection.json', result['inspection_manifest_sha256'])
            Image.new('RGB', (810, 1080), 'black').save(proof)
            result = verify(root, video)
            self.assertFalse(result['evidence_integrity_verified'])
            self.assertTrue(any('differs from source crop' in e['reason'] for e in result['errors']))

    def test_training_recovery_manifest_and_source_crop_are_verified(self):
        with workspace_temp() as root, patch('builtins.print'):
            video, origin, proof = self.fixture(root, 'training-gain-recovery')
            result = verify(root, video)
            self.assertTrue(result['evidence_integrity_verified'])
            self.assertEqual(result['verified_observations'], 1)
            self.assertIn('training-gain-recovery/receipt-inspection.json', result['inspection_manifest_sha256'])
            Image.new('RGB', (810, 1080), 'black').save(proof)
            result = verify(root, video)
            self.assertFalse(result['evidence_integrity_verified'])
            self.assertTrue(any('differs from source crop' in e['reason'] for e in result['errors']))


if __name__ == '__main__':
    unittest.main()
