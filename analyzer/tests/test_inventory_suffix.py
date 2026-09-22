import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image

from tracen_replay.inventory_suffix import detect


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
MANIFEST_PATH = FIXTURE_ROOT / "inventory-suffix-real.json"
TERMINAL_CONTROLS_PATH = FIXTURE_ROOT / "inventory-suffix-terminal-controls.json"


class InventorySuffixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def fixture(self, entry):
        path = FIXTURE_ROOT / entry["fixture"]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["fixture_sha256"])
        image = Image.open(path).convert("RGB")
        self.assertEqual(image.size, (810, 1080))
        return image

    def test_real_source_fixture_hashes_are_pinned(self):
        recordings = {entry["source_recording"] for entry in self.manifest["fixtures"]}
        self.assertEqual(len(recordings), 2)
        for entry in self.manifest["fixtures"]:
            self.assertRegex(entry["source_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(entry["source_frame_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(entry["crop_regions_pane"])

    def test_real_final_summary_markers_and_unmarked_text(self):
        for entry in self.manifest["fixtures"][:2]:
            image = self.fixture(entry)
            for observation in entry["observations"]:
                with self.subTest(label=observation["label"]):
                    self.assertEqual(detect(image, observation["box"]), observation["expected"])

    def test_real_double_circle_is_distinguished_from_single_circle(self):
        entry = self.manifest["fixtures"][2]
        image = self.fixture(entry)
        observation = entry["observations"][0]
        self.assertEqual(detect(image, observation["box"]), "double_circle")

    def test_ordinary_terminal_letters_are_not_circle_suffixes(self):
        # These saved controls use the same full-frame box convention as the
        # OCR observations.  They ensure an O/o at the end of ordinary text
        # does not become a marker merely because the search window is broad.
        metadata = json.loads(TERMINAL_CONTROLS_PATH.read_text(encoding="utf-8"))
        path = FIXTURE_ROOT / metadata["fixture"]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata["fixture_sha256"])
        image = Image.open(path).convert("RGB")
        self.assertEqual(image.size, tuple(metadata["canvas_size"]))
        for control in metadata["controls"]:
            with self.subTest(word=control["word"], size=control["size"]):
                self.assertIsNone(detect(image, control["box"]))

    def test_clipped_marker_abstains(self):
        entry = self.manifest["fixtures"][0]
        image = self.fixture(entry)
        # Corner Recovery's marker begins at pane x=295 and continues right.
        # Remove its right side to model a clipped source crop.
        image.paste((255, 255, 255), (296, 637, 334, 684))
        self.assertIsNone(detect(image, [317, 646, 443, 675]))

    def test_multiple_marker_candidates_are_ambiguous(self):
        entry = self.manifest["fixtures"][0]
        image = self.fixture(entry)
        # Duplicate the real marker into the same row.  The detector must not
        # choose one candidate by position or by the OCR text.
        marker = image.crop((291, 648, 312, 674))
        image.paste(marker, (311, 648))
        self.assertIsNone(detect(image, [317, 646, 443, 675]))

    def test_malformed_boxes_and_blank_panes_abstain(self):
        image = Image.new("RGB", (810, 1080), "white")
        for box in (None, [], [1, 2, 3], [1, 2, 3, 4, 5], [4, 2, 1, 6], [1, 2, 3, float("nan")]):
            with self.subTest(box=box):
                self.assertIsNone(detect(image, box))
        self.assertIsNone(detect(image, [317, 646, 443, 675]))


if __name__ == "__main__":
    unittest.main()
