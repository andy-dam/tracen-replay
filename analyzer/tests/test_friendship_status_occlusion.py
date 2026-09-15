import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

from tracen_replay.receipt_occlusion import (
    annotate,
    annotate_path,
    friendship_name_bounds,
    friendship_status_line,
)
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.vision import parse
from tests.test_gameplay import workspace_temp


def line(text, box=(300, 820, 740, 850), confidence=99):
    return dict(text=text, box=list(box), confidence=confidence)


def raw(lines):
    return dict(
        lines=list(lines),
        regions={},
        header="",
        current_grid=False,
        result_grid=False,
    )


def status_alignment(text="maxed"):
    suffix = ["is", text, "out."]
    words = ["Friendship", "with", "Example", "Name", *suffix]
    columns = [
        [1, 2],
        [4, 5],
        [7, 8, 9],
        [11, 12],
        [14, 15],
        list(range(17, 17 + len(text))),
        [23, 24, 25, 26],
    ]
    return dict(
        line_box=[300, 820, 740, 850],
        words=words,
        columns=columns,
        line_length=27,
        confidence=99,
    )


class FriendshipStatusOcclusionTests(unittest.TestCase):
    def source(self, text="Friendship with Example Name is maxed out."):
        pane = Image.new("RGB", (810, 1080), "white")
        result = raw([line(text)])
        result["gameplay_sha256"] = hashlib.sha256(pane.tobytes()).hexdigest()
        return result, pane

    def test_status_shapes_include_nonnumeric_and_raw_ocr_variants_without_repair(self):
        for text in (
            "Friendship with Example Name didn't go up.",
            "Friendship with Example Name is maxed out.",
            "Friendship with Example Name is mad out.",
            "Friendship with Example Name is maed out.",
        ):
            with self.subTest(text=text):
                self.assertTrue(friendship_status_line(line(text)))
        for text in (
            "Friendship with Example Name will go up by 4.",
            "Friendship with Example Name is maximum.",
            "Friendship with Example Name is maxed",
        ):
            with self.subTest(text=text):
                self.assertFalse(friendship_status_line(line(text)))

    def test_name_bounds_cover_wert_and_complete_status_receipts(self):
        box = [300, 820, 740, 850]
        completed = status_alignment()
        bounds = friendship_name_bounds(
            box, completed["words"], completed["columns"], completed["line_length"]
        )
        self.assertIsNotNone(bounds)
        self.assertLess(bounds[0], bounds[2])
        for verb_words in (
            ["Friendship", "with", "Example", "Name", "went", "up", "by", "7."],
            ["Friendship", "with", "Example", "Name", "wert", "up", "by", "7."],
        ):
            columns = [
                [1, 2],
                [4, 5],
                [7, 8, 9],
                [11, 12],
                [14, 15],
                [17, 18],
                [20, 21],
                [24, 25],
            ]
            with self.subTest(verb=verb_words[4]):
                self.assertIsNotNone(friendship_name_bounds(box, verb_words, columns, 27))

    def test_cursor_over_status_recipient_abstains_and_marks_name_occlusion(self):
        source, pane = self.source()
        source["overlay_alignment"] = [status_alignment()]
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[445, 824, 458, 846]],
        ):
            marked = annotate(source, pane)
        self.assertEqual(parse(marked)["effects"], [])
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertTrue(marked["lines"][0]["overlay_occluded"])
        self.assertTrue(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])
        self.assertEqual(source["lines"][0]["confidence"], 99)

    def test_cursor_on_fixed_status_grammar_does_not_claim_name_occlusion(self):
        source, pane = self.source()
        source["overlay_alignment"] = [status_alignment()]
        # This box overlaps the fixed "is maxed out" grammar after the name.
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[545, 824, 560, 846]],
        ):
            marked = annotate(source, pane)
        self.assertEqual(parse(marked)["effects"], [])
        self.assertTrue(marked["occluded_receipt_lines"])
        self.assertFalse(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])

    def test_missing_alignment_abstains_on_full_overlapping_status_line(self):
        source, pane = self.source("Friendship with Example Name didn't go up.")
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[445, 824, 458, 846]],
        ):
            marked = annotate(source, pane)
        self.assertEqual(parse(marked)["effects"], [])
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertFalse(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])

    def test_cached_status_path_uses_the_same_alignment_guard(self):
        source, pane = self.source()
        alignment = status_alignment()
        with workspace_temp() as directory:
            source_path = directory / "status.png"
            pane.save(source_path)
            sidecar = dict(
                raw_sha256=fingerprint(source),
                evidence_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                lines=[alignment],
            )
            source_path.with_suffix(".overlay.json").write_text(
                json.dumps(sidecar), encoding="utf-8"
            )
            with patch(
                "tracen_replay.receipt_occlusion.overlay_boxes",
                return_value=[[445, 824, 458, 846]],
            ):
                marked = annotate_path(source, source_path)
            self.assertTrue(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])

    @unittest.skipUnless(
        Path(".local/full-recording/independent-01/report.json").is_file(),
        "independent source diagnostics are not available",
    )
    def test_independent_cursor_frames_cover_fixed_status_grammar_not_name(self):
        # These are existing OCR/raw diagnostics paired with the source PNGs;
        # this test does not run OCR or provide a catalog/expected label.
        root = Path(".local/full-recording/independent-01")
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        expected = {
            474500: (
                "part-003-frame-000459.png",
                "3e5bb7647074d507d46778db485c8ba57681a67c91b790dc8f2fa0925d0c6734",
                "mad",
            ),
            474750: (
                "part-003-frame-000460.png",
                "597bae3c32739625573522bb6004342cff4e56aeed6793765b33806b5a7bf978",
                "maed",
            ),
        }
        readings = {
            r["source_timestamp_ms"]: r
            for r in report["gameplay_tracking"]["readings"]
            if r.get("source_timestamp_ms") in expected
        }
        self.assertEqual(set(readings), set(expected))
        for timestamp, (filename, image_sha, variant) in expected.items():
            reading = copy.deepcopy(readings[timestamp])
            path = root / "gameplay" / filename
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), image_sha)
            pane = Image.open(path).convert("RGB")
            status = next(
                item
                for item in reading["ocr"]["neural"]
                if item["text"].startswith("Friendship with Nishino Flower is")
            )
            self.assertIn(f"is {variant} out", status["text"])
            reading["lines"] = reading["ocr"]["neural"]
            reading["gameplay_sha256"] = hashlib.sha256(pane.tobytes()).hexdigest()
            marked = annotate(reading, pane)
            marked_status = next(
                item
                for item in marked["occluded_receipt_lines"]
                if item["text"].startswith("Friendship with Nishino Flower is")
            )
            # The real cursor is over "mad/maed out", part of the fixed
            # grammar. Without an alignment proof, the full line is rejected,
            # but it is not misclassified as a hidden recipient name.
            self.assertFalse(marked_status["recipient_name_occluded"])
            self.assertEqual(
                next(item["text"] for item in marked["lines"] if item["box"] == status["box"]),
                status["text"],
            )


if __name__ == "__main__":
    unittest.main()
