import tempfile
import unittest
from pathlib import Path

from tracen_replay.analysis_job import prune_frames, prune_working_data


class PruneWorkingDataTests(unittest.TestCase):
    def test_directories_go_and_top_level_files_stay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.json").write_text("{}", encoding="utf-8")
            (root / "timeline.json").write_text("{}", encoding="utf-8")
            (root / "index.html").write_text("<p>viewer</p>", encoding="utf-8")
            (root / "capture.json").write_text("{}", encoding="utf-8")
            (root / "neural").mkdir()
            (root / "neural" / "part-000-frame-000001.json").write_text("{}" * 500, encoding="utf-8")
            (root / "part-000" / "frames").mkdir(parents=True)
            (root / "part-000" / "frames" / "000001.jpg").write_bytes(b"x" * 1000)
            (root / "training-gain-recovery" / "window").mkdir(parents=True)
            (root / "training-gain-recovery" / "window" / "frame-000001.png").write_bytes(b"y" * 100)
            (root / "training-gain-recovery" / "window" / "frame-000001.json").write_text("{}", encoding="utf-8")
            removed = prune_working_data(root)
            self.assertEqual(removed, {"directories": 3, "files": 4, "bytes": 1000 + 1000 + 100 + 2})
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["capture.json", "index.html", "report.json", "timeline.json"])

    def test_frame_pruning_first_then_working_data_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.json").write_text("{}", encoding="utf-8")
            (root / "part-000" / "frames").mkdir(parents=True)
            (root / "part-000" / "frames" / "000001.jpg").write_bytes(b"x" * 10)
            (root / "part-000" / "frames.json").write_text("[]", encoding="utf-8")
            self.assertEqual(prune_frames(root)["files"], 1)
            first = prune_working_data(root)
            self.assertEqual((first["directories"], first["files"]), (1, 1))
            self.assertEqual(prune_working_data(root), {"directories": 0, "files": 0, "bytes": 0})
            self.assertTrue((root / "report.json").is_file())


if __name__ == "__main__":
    unittest.main()
