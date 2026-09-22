import shutil
import subprocess
import unittest
import uuid

from pathlib import Path
from unittest.mock import patch

from tests import localdata
from tracen_replay.pipeline import PipelineError, decode_frames, probe


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

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def test_variable_pts_and_nonzero_origin_with_offset(self):
        _, _, _, origin = probe(self.source)
        self.assertEqual(origin, 5)
        directory = self.root / uuid.uuid4().hex
        directory.mkdir()
        frames = decode_frames(self.source, directory, 0.2, 1.4, 8, origin)
        self.assertEqual([f["source_timestamp_ms"] for f in frames], [400, 900, 1300])
        self.assertEqual([f["clip_timestamp_ms"] for f in frames], [200, 700, 1100])
        for frame in frames:
            self.assertTrue((directory / Path(frame["evidence"]).name).is_file())

    def test_bad_media_is_refused(self):
        bad = self.root / "invalid.mp4"
        bad.write_text("not a video")
        with self.assertRaisesRegex(PipelineError, "ffprobe failed"):
            probe(bad)

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


if __name__ == "__main__":
    unittest.main()
