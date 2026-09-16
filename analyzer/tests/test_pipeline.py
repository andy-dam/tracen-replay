import hashlib
import json
import shutil
import subprocess
import unittest
import uuid

from pathlib import Path
from unittest.mock import patch

from tests import localdata
from tracen_replay.pipeline import PipelineError, analyze, decode_frames


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class PipelineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = localdata.scratch(uuid.uuid4().hex)
        cls.source = cls.root / "synthetic.mkv"
        # Uneven original PTS plus a nonzero media origin. Frame-index/fps
        # timestamp reconstruction would fail this fixture.
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=10:duration=2", "-vf",
                        "select='eq(n,0)+eq(n,1)+eq(n,4)+eq(n,9)+eq(n,13)+eq(n,19)',setpts=PTS+5/TB",
                        "-fps_mode", "vfr", "-c:v", "ffv1", str(cls.source)], check=True, capture_output=True)
        cls.digest = hashlib.sha256(cls.source.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def destination(self):
        return self.root / uuid.uuid4().hex

    def annotations(self, rows, digest=None):
        path = self.root / f"{uuid.uuid4().hex}.json"
        path.write_text(json.dumps({"source_sha256": digest or self.digest, "observations": rows}), encoding="utf-8")
        return path

    def test_variable_pts_and_nonzero_origin_with_offset(self):
        output = self.destination()
        result = analyze(self.source, output, start=0.2, duration=1.4, fps=8)
        report = json.loads(Path(result["report"]).read_text())
        self.assertEqual(report["source"]["timeline_origin_seconds"], 5)
        self.assertEqual([f["source_timestamp_ms"] for f in report["frames"]], [400, 900, 1300])
        self.assertEqual([f["clip_timestamp_ms"] for f in report["frames"]], [200, 700, 1100])
        self.assertFalse(report["recognition"]["enabled"])
        for frame in report["frames"]:
            self.assertTrue((output / frame["evidence"]).is_file())
            self.assertIsNone(frame["screen_label"])
        self.assertTrue((output / "index.html").is_file())

    def test_annotations_do_not_silently_label_nearby_frames(self):
        path = self.annotations([
            {"timestamp_seconds": 0.4, "screen_label": "example", "title": "<script>alert(1)</script>"},
            {"timestamp_seconds": 0.5, "screen_label": "different_screen"},
            {"timestamp_seconds": 1.9, "screen_label": "outside_clip"},
        ])
        output = self.destination()
        analyze(self.source, output, 0.2, 1.4, 8, path)
        report = json.loads((output / "report.json").read_text())
        first, second = report["observations"]
        self.assertEqual(first["evidence_status"], "matched_sample")
        self.assertIsNone(second["evidence"])
        self.assertEqual(second["evidence_status"], "needs_exact_frame")
        self.assertIn("&lt;script&gt;", (output / "index.html").read_text())
        self.assertNotIn("<script>alert", (output / "index.html").read_text())

    def test_wrong_recording_annotations_do_not_publish_partial_output(self):
        output = self.destination()
        before = set(self.root.glob(".tracen-*"))
        with self.assertRaisesRegex(PipelineError, "source_sha256"):
            analyze(self.source, output, 0.2, 1.4, 8, self.annotations([], "0"*64))
        self.assertFalse(output.exists())
        self.assertEqual(set(self.root.glob(".tracen-*")), before)

    def test_existing_output_is_preserved(self):
        output = self.destination()
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("existing data")
        with self.assertRaisesRegex(PipelineError, "already exists"):
            analyze(self.source, output, 0.2, 1.4, 8)
        self.assertEqual(marker.read_text(), "existing data")

    def test_transient_file_lock_retries_without_redecoding(self):
        output = self.destination()
        rename = Path.rename
        attempts = []
        def temporarily_locked(path, target):
            attempts.append(path)
            if len(attempts)==1:
                raise PermissionError('temporary scanner lock')
            return rename(path,target)
        with patch.object(Path,'rename',temporarily_locked), patch('tracen_replay.pipeline.time.sleep'), \
                patch('tracen_replay.pipeline.decode_frames',wraps=decode_frames) as decode:
            analyze(self.source,output,0.2,1.4,8)
        # The real filesystem can add a scanner lock after our injected one.
        # Every retry must publish the same decoded bundle, never decode again.
        self.assertGreaterEqual(len(attempts),2)
        self.assertEqual(len(set(attempts)),1)
        self.assertEqual(decode.call_count,1)
        self.assertTrue((output/'report.json').exists())

    def test_persistent_file_lock_leaves_no_published_report(self):
        output = self.destination()
        before = set(self.root.glob('.tracen-*'))
        with patch.object(Path,'rename',side_effect=PermissionError('locked')) as rename, patch('tracen_replay.pipeline.time.sleep'):
            with self.assertRaises(PermissionError):
                analyze(self.source,output,0.2,1.4,8)
        self.assertEqual(rename.call_count,5)
        self.assertFalse(output.exists())
        self.assertEqual(set(self.root.glob('.tracen-*')),before)

    def test_bad_media_and_invalid_intervals(self):
        bad = self.root / "invalid.mp4"
        bad.write_text("not a video")
        with self.assertRaisesRegex(PipelineError, "ffprobe failed"):
            analyze(bad, self.destination())
        for start, duration, fps in [(float("nan"),1,4), (0,float("inf"),4), (-1,1,4), (0,121,4), (0,1,0), (100,1,4)]:
            with self.subTest(start=start, duration=duration, fps=fps):
                with self.assertRaises(PipelineError):
                    analyze(self.source, self.destination(), start, duration, fps)

    def test_an_inconsistent_showinfo_sequence_is_decoded_once_more(self):
        # The decoder's log came out with a duplicated showinfo line on the
        # first attempt; the same part decodes cleanly on the second.
        from tracen_replay import pipeline
        directory = self.root / "retry-frames"
        directory.mkdir()
        calls = []
        def fake_run(command):
            calls.append(command)
            for path in directory.glob("*.jpg"):
                path.unlink()
            for i in range(3):
                (directory / f"{i + 1:06d}.jpg").write_bytes(b"jpg")
            lines = ["[Parsed_showinfo_1 @ 0] config in time_base: 1/15360, frame_rate: 60/1"]
            order = [0, 0, 1, 2] if len(calls) == 1 else [0, 1, 2]
            for n in order:
                lines.append(f"[Parsed_showinfo_1 @ 0] n: {n} pts: {3840 * n} pts_time:{0.25 * n:.6f} pos: 1")
            return subprocess.CompletedProcess(command, 0, "", "\n".join(lines))
        with patch("tracen_replay.pipeline.run", side_effect=fake_run):
            frames = pipeline.decode_frames(self.source, directory, 0, 1, 4, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual([f["source_pts"] for f in frames], [0, 3840, 7680])
        with patch("tracen_replay.pipeline.run", side_effect=fake_run):
            calls.clear()
            with self.assertRaisesRegex(PipelineError, "inconsistent .*showinfo n=0 for image 1"):
                pipeline.decode_frames(self.source, directory, 0, 1, 4, 0, attempts=1)

    def test_decoder_failure_cleans_its_temporary_directory(self):
        output = self.destination()
        before = set(self.root.glob(".tracen-*"))
        with patch("tracen_replay.pipeline.decode_frames", side_effect=PipelineError("interrupted decoder")):
            with self.assertRaisesRegex(PipelineError, "interrupted"):
                analyze(self.source, output, 0.2, 1.4, 8)
        self.assertFalse(output.exists())
        self.assertEqual(set(self.root.glob(".tracen-*")), before)


if __name__ == "__main__":
    unittest.main()
