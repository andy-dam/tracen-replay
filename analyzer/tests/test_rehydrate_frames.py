"""Frames pruned from a run are decoded again, or the run refuses to reparse."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tracen_replay.full_recording import rehydrate_frames
from tracen_replay.pipeline import PipelineError


def _rows(count=2):
    return [{'id': f'part-000-frame-{i:06d}', 'source_timestamp_ms': 250 * i,
             'clip_timestamp_ms': 250 * i, 'source_pts': 1000 * i, 'time_base': '1/15360',
             'evidence': f'part-000/frames/{i:06d}.jpg', 'screen_label': None,
             'confidence': None, 'origin': 'automatic_frame_sampling'} for i in range(1, count + 1)]


def _run(root, *, rows=None, images=False):
    """A captured run with one part, optionally still holding its images."""
    source = root / 'source.mp4'
    source.write_bytes(b'video bytes')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (root / 'identity.json').write_text(json.dumps(dict(source_sha256=digest, fps=4)), encoding='utf-8')
    part = root / 'part-000'
    (part / 'frames').mkdir(parents=True)
    rows = _rows() if rows is None else rows
    (part / 'frames.json').write_text(json.dumps(rows), encoding='utf-8')
    # The capture lists every frame; panes are cut again for the observations
    # that name them, and there are none in these fixtures.
    (root / 'capture.json').write_text(json.dumps(dict(frames=rows)), encoding='utf-8')
    if images:
        for row in rows:
            (root / row['evidence']).write_bytes(b'image')
    return source, rows


def _decoder(rows, *, written=b'image'):
    """Stand in for ffmpeg: write the files the real decoder would and return its rows."""

    def decode(source, directory, start, duration, fps, origin):
        produced = []
        for row in rows:
            name = Path(row['evidence']).name
            (directory / name).write_bytes(written)
            produced.append({**row, 'id': row['id'].split('-', 2)[2], 'evidence': f'frames/{name}'})
        return produced

    return decode


class RehydrateFramesTests(unittest.TestCase):
    def test_a_pruned_part_is_decoded_again_and_keeps_its_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, rows = _run(root)
            before = (root / 'part-000' / 'frames.json').read_bytes()
            with patch('tracen_replay.full_recording.probe', return_value=({}, {}, 120.0, 0.0)), \
                 patch('tracen_replay.full_recording.decode_frames', _decoder(rows)):
                result = rehydrate_frames(source, root, 4)
            self.assertEqual(result, dict(parts=1, frames=2, crops=0))
            for row in rows:
                self.assertTrue((root / row['evidence']).is_file())
            # The recorded frames are evidence; rehydration reproduces them and
            # never rewrites what they were.
            self.assertEqual((root / 'part-000' / 'frames.json').read_bytes(), before)
            self.assertFalse((root / 'part-000' / 'frames.rehydrate').exists())

    def test_a_part_that_still_has_its_images_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _ = _run(root, images=True)

            def refuse(*args, **kwargs):
                raise AssertionError('a part with its images must not be decoded again')

            with patch('tracen_replay.full_recording.probe', return_value=({}, {}, 120.0, 0.0)), \
                 patch('tracen_replay.full_recording.decode_frames', refuse):
                self.assertEqual(rehydrate_frames(source, root, 4), dict(parts=0, frames=0, crops=0))

    def test_a_decode_that_does_not_reproduce_the_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, rows = _run(root)
            moved = [{**rows[0], 'source_timestamp_ms': 999}, rows[1]]
            with patch('tracen_replay.full_recording.probe', return_value=({}, {}, 120.0, 0.0)), \
                 patch('tracen_replay.full_recording.decode_frames', _decoder(moved)):
                with self.assertRaises(PipelineError):
                    rehydrate_frames(source, root, 4)
            # Nothing is published from a decode that disagrees.
            self.assertEqual(list((root / 'part-000' / 'frames').iterdir()), [])

    def test_another_source_or_sampling_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, rows = _run(root)
            with patch('tracen_replay.full_recording.probe', return_value=({}, {}, 120.0, 0.0)), \
                 patch('tracen_replay.full_recording.decode_frames', _decoder(rows)):
                with self.assertRaises(PipelineError):
                    rehydrate_frames(source, root, 2)
                source.write_bytes(b'a different recording')
                with self.assertRaises(PipelineError):
                    rehydrate_frames(source, root, 4)

    def test_a_pane_is_cut_again_only_when_its_pixels_match_the_observation(self):
        from PIL import Image

        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rows = _rows(1)
                source, _ = _run(root, rows=rows)
                frame = rows[0]
                picture = Image.new('RGB', (1920, 1080), (9, 30, 60))
                picture.putpixel((200, 200), (255, 0, 0))
                picture.save(root / frame['evidence'])
                # The observation recorded the pane of the stored frame, which
                # a lossy format is free to differ from in memory.
                with Image.open(root / frame['evidence']) as stored:
                    pane = stored.convert('RGB').crop((148, 0, 958, 1080))
                    recorded = hashlib.sha256(pane.tobytes()).hexdigest()
                if corrupt:
                    recorded = hashlib.sha256(b'another pane').hexdigest()
                crop = 'gameplay/' + frame['id'] + '.png'
                (root / 'neural').mkdir()
                (root / 'neural' / (frame['id'] + '.json')).write_text(
                    json.dumps(dict(evidence=crop, gameplay_sha256=recorded)), encoding='utf-8')
                with patch('tracen_replay.full_recording.probe', return_value=({}, {}, 120.0, 0.0)):
                    if corrupt:
                        # A pane that does not match what was read from it is a
                        # difference, not something to write out anyway.
                        with self.assertRaises(PipelineError):
                            rehydrate_frames(source, root, 4)
                        self.assertFalse((root / crop).exists())
                        continue
                    self.assertEqual(rehydrate_frames(source, root, 4), dict(parts=0, frames=0, crops=1))
                self.assertTrue((root / crop).is_file())
                with Image.open(root / crop) as written:
                    self.assertEqual(hashlib.sha256(written.convert('RGB').tobytes()).hexdigest(), recorded)

    def test_a_run_without_an_identity_cannot_be_rehydrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _ = _run(root)
            (root / 'identity.json').unlink()
            with patch('tracen_replay.full_recording.probe', return_value=({}, {}, 120.0, 0.0)):
                with self.assertRaises(PipelineError):
                    rehydrate_frames(source, root, 4)


if __name__ == '__main__':
    unittest.main()
