"""Capture when an analysis is started again on its own output, and at the end of a recording.

A paused analysis is a stopped process: a decode it was in the middle of
leaves images with no manifest. Started again on the same directory, the
capture has to clear them and decode that part again, and keep the parts
whose manifests were written.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tracen_replay import full_recording
from tracen_replay.pipeline import clear_partial_capture


def _rows(directory, start, count, fps):
    rows = []
    for index in range(count):
        name = f"{index + 1:06d}.jpg"
        (directory / name).write_bytes(b"jpg")
        rows.append({"id": f"frame-{index + 1:06d}", "source_timestamp_ms": round((start + index / fps) * 1000),
                     "clip_timestamp_ms": round(index / fps * 1000), "source_pts": index, "time_base": "1/1000",
                     "evidence": f"frames/{name}", "screen_label": None, "confidence": None,
                     "origin": "automatic_frame_sampling"})
    return rows


class CaptureRestart(unittest.TestCase):
    def capture(self, root, source, duration, decoded, video=None, scales=None):
        def decode(src, directory, start, length, fps, origin, *, scale):
            decoded.append((start, length, sorted(p.name for p in directory.iterdir())))
            if scales is not None:
                scales.append(scale)
            return _rows(directory, start, max(1, int(length * fps)), fps)

        video = video or {"width": 1920, "height": 1080, "codec_name": "h264"}
        with patch.object(full_recording, "probe", return_value=({}, video, duration, 0.0)), \
                patch.object(full_recording, "decode_frames", side_effect=decode), \
                patch.object(full_recording, "game_area", return_value=None), \
                patch.object(full_recording, "validate_output"):
            return full_recording.capture(source, root, 4.0)

    def test_clear_partial_capture_removes_only_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "000001.jpg").write_bytes(b"x")
            (directory / "kept").mkdir()
            clear_partial_capture(directory)
            self.assertEqual([p.name for p in directory.iterdir()], ["kept"])

    def test_a_part_left_without_its_manifest_is_decoded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, source = Path(tmp) / "run", Path(tmp) / "clip.mp4"
            source.write_bytes(b"video")
            first = []
            self.capture(root, source, 300.0, first)
            self.assertEqual([start for start, _, _ in first], [0, 120, 240])
            # The stop came while the third part was being decoded: its
            # manifest and the capture were never written, some images were.
            (root / "capture.json").unlink()
            (root / "part-002" / "frames.json").unlink()
            for image in sorted((root / "part-002" / "frames").iterdir())[100:]:
                image.unlink()
            again = []
            report = self.capture(root, source, 300.0, again)
            self.assertEqual(again, [(240, 60.0, [])], "only the unfinished part is decoded, on an empty directory")
            self.assertEqual(report["sampling"]["frame_count"], 480 + 480 + 240)
            rows = json.loads((root / "part-002" / "frames.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 240)

    def test_a_last_part_shorter_than_one_sampling_step_is_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, source = Path(tmp) / "run", Path(tmp) / "clip.mp4"
            source.write_bytes(b"video")
            decoded = []
            report = self.capture(root, source, 120.016667, decoded)
            self.assertEqual([start for start, _, _ in decoded], [0])
            self.assertEqual(report["sampling"]["frame_count"], 480)
            self.assertFalse((root / "part-001").exists())

    def test_a_last_part_of_one_sampling_step_or_more_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, source = Path(tmp) / "run", Path(tmp) / "clip.mp4"
            source.write_bytes(b"video")
            decoded = []
            self.capture(root, source, 120.5, decoded)
            self.assertEqual([start for start, _, _ in decoded], [0, 120])

    def test_a_720p_recording_is_decoded_to_1080p_frames_and_keeps_its_own_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, source = Path(tmp) / "run", Path(tmp) / "clip.mp4"
            source.write_bytes(b"video")
            video = {"width": 1280, "height": 720, "codec_name": "h264", "avg_frame_rate": "30/1"}
            scales = []
            report = self.capture(root, source, 200.0, [], video=video, scales=scales)
            self.assertEqual(scales, ["scale=1920:1080:flags=lanczos"] * 2)
            self.assertEqual((report["source"]["width"], report["source"]["height"]), (1280, 720))
            self.assertEqual(report["source"]["frame_rate"], 30)


if __name__ == "__main__":
    unittest.main()
