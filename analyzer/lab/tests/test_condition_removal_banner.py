"""Tests of ``tests.test_condition_removal_banner`` that need locally preserved evidence; they run only where it is."""
import json
from pathlib import Path
import unittest
from tests import localdata
from tracen_replay.condition_removal_banner import (
    merge_condition_removal_effects,
    read_condition_cured_banner,
)
from tracen_replay.gameplay import effects_from_lines


class ConditionRemovalBannerTests(unittest.TestCase):
    def test_independent_source_banner_recovers_clipped_receipt(self):
        path = localdata.root("development_third_recording_baseline",
                              "neural", "part-001-frame-000445.json")
        if not path.is_file():
            self.skipTest("local frozen source sidecars are not part of a clean checkout")
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = read_condition_cured_banner(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Night Owl")
        self.assertEqual(result["source_timestamp_ms"], 231000)
        self.assertEqual(result["evidence"], "gameplay/part-001-frame-000445.png")
        self.assertEqual(result["source_proof"]["heading"]["text"], "CONDITIONCURED!")
        self.assertEqual(result["source_proof"]["condition_name"]["text"], "Night Owl")
        self.assertEqual(result["confidence"], 99.93)
        # The receipt line is clipped in this source, so it must not be
        # silently completed or treated as the banner's name proof.
        self.assertNotIn("complete_receipt", result["source_proof"])
