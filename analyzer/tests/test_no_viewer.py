"""--no-viewer leaves the standalone viewer page out and reaches the producer."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tracen_replay import analysis_job, full_recording


class NoViewer(unittest.TestCase):
    def test_the_job_passes_the_flag_to_the_producer(self):
        parser = analysis_job._parser()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"video")
            args = parser.parse_args([str(source), "--output", str(Path(tmp) / "run"), "--no-viewer"])
            argv = analysis_job._producer_argv(source, Path(tmp) / "run", Path(tmp) / "models", args)
            self.assertIn("--no-viewer", argv)
            plain = parser.parse_args([str(source), "--output", str(Path(tmp) / "run")])
            self.assertNotIn("--no-viewer", analysis_job._producer_argv(source, Path(tmp) / "run", Path(tmp) / "models", plain))

    def test_the_producer_skips_the_page(self):
        report = {"schema_version": full_recording.FULL_RECORDING_SCHEMA}
        with tempfile.TemporaryDirectory() as tmp, \
                patch("tracen_replay.timeline_document.write", return_value=12), \
                patch.object(full_recording, "render", return_value="<html>") as render:
            full_recording._write_timeline_and_viewer(report, tmp, viewer=False)
            self.assertFalse((Path(tmp) / "index.html").exists())
            render.assert_not_called()
            full_recording._write_timeline_and_viewer(report, tmp)
            self.assertEqual((Path(tmp) / "index.html").read_text(encoding="utf-8"), "<html>")


if __name__ == "__main__":
    unittest.main()
