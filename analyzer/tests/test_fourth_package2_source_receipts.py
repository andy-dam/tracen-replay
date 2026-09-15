import copy
import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image

from tests import localdata
from tracen_replay.vision import parse


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = localdata.root("fourth_recording_retest_newer", "report.json")
SOURCE_FRAME_PATH = localdata.root(
    "fourth_recording_source_controls_middle",
    "extensions/window-03-1526000-1564000/frames/000141.jpg",
)
REPORT_GAMEPLAY_PATH = localdata.root(
    "fourth_recording_retest_newer", "gameplay/part-013-frame-000005.png"
)


@unittest.skipUnless(
    REPORT_PATH.is_file() and SOURCE_FRAME_PATH.is_file()
    and REPORT_GAMEPLAY_PATH.is_file(),
    "preserved fourth package-2 source bundle is unavailable",
)
class FourthPackage2SourceReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        cls.row = copy.deepcopy(
            next(
                row
                for row in report["gameplay_tracking"]["readings"]
                if row.get("source_timestamp_ms") == 1_561_000
            )
        )

    def test_actual_fendship_source_line_reaches_normal_effect_parser(self):
        self.assertEqual(
            hashlib.sha256(SOURCE_FRAME_PATH.read_bytes()).hexdigest(),
            self.row["source_frame_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(
                Image.open(REPORT_GAMEPLAY_PATH).convert("RGB").tobytes()
            ).hexdigest(),
            self.row["gameplay_sha256"],
        )

        raw = dict(
            self.row,
            lines=self.row["ocr"]["neural"],
            regions={},
            header="Training",
            current_grid=False,
            result_grid=False,
        )
        effects = parse(raw)["effects"]
        matches = [
            effect
            for effect in effects
            if effect.get("kind") == "friendship_status"
            and effect.get("name") == "Matikanefukukitaru"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["value"], "maximum")
        self.assertEqual(
            matches[0]["original_text"],
            "Fendship with Matikanefukukitaru is maxed out.",
        )
        self.assertEqual(
            matches[0]["text_normalization"],
            "source_wrapped_friendship_status",
        )

    def test_actual_source_keeps_neighboring_receipts_separate(self):
        raw = dict(
            self.row,
            lines=self.row["ocr"]["neural"],
            regions={},
            header="Training",
            current_grid=False,
            result_grid=False,
        )
        effects = parse(raw)["effects"]
        statuses = [
            (effect.get("name"), effect.get("value"))
            for effect in effects
            if effect.get("kind") == "friendship_status"
        ]
        self.assertEqual(
            statuses,
            [
                ("Light Hello", "maximum"),
                ("Matikanefukukitaru", "maximum"),
                ("Agnes Tachyon", "maximum"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
