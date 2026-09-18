"""Tests of ``tests.test_friendship_status_occlusion`` that need locally preserved evidence; they run only where it is."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image
from tests import localdata
from tracen_replay.receipt_occlusion import (
    annotate,
    annotate_path,
    friendship_name_bounds,
    friendship_status_line,
)
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.vision import parse
from tests.test_gameplay import workspace_temp
from tests.test_friendship_status_occlusion import line, raw


class FriendshipStatusOcclusionTests(unittest.TestCase):
    def source(self, text="Friendship with Example Name is maxed out."):
        pane = Image.new("RGB", (810, 1080), "white")
        result = raw([line(text)])
        result["gameplay_sha256"] = hashlib.sha256(pane.tobytes()).hexdigest()
        return result, pane







    @localdata.needs("development_second_recording", "report.json")
    def test_independent_cursor_frames_cover_fixed_status_grammar_not_name(self):
        # These are existing OCR/raw diagnostics paired with the source PNGs;
        # this test does not run OCR or provide a catalog/expected label.
        root = localdata.root("development_second_recording")
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
