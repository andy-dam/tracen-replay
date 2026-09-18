"""Tests of ``tests.test_lesson_offer_preview_bridge`` that need locally preserved evidence; they run only where it is."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import unittest
from tests import localdata
from tracen_replay.evaluation_adapters import report_document
from tracen_replay.full_recording import cached_readings
from tracen_replay.preview_observations import build_preview_observations


class LessonOfferPreviewBridgeTests(unittest.TestCase):




    def test_actual_three_offer_envelope_survives_report_document_projection(self):
        base = localdata.root("development_third_recording_baseline")
        capture_path = base / "capture.json"
        if not capture_path.is_file():
            self.skipTest("Independent-02 lesson source fixture is unavailable")
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        frame = next(
            frame for frame in capture["frames"]
            if frame["id"] == "part-005-frame-000094"
        )
        readings = cached_readings({"frames": [frame]}, base)
        preview = build_preview_observations(readings)
        report = {
            "source": {"sha256": "lesson-source"},
            "gameplay_tracking": {
                "readings": readings,
                "events": [],
                "turn_action_receipts": [],
                "races": [],
                "checkpoints": [],
                "performance_accounting": {"checkpoints": []},
                "lesson_purchases": [],
                "skill_purchases": [],
                "preview_observations": preview,
                "auxiliary_log_used": False,
            },
        }
        document = report_document(report)
        purchases = [
            row for row in document["observations"]
            if row["category"] == "purchase" and row["phase"] == "preview"
        ]
        self.assertEqual(len(purchases), 3)
        by_name = {row["payload"]["name"]: row["payload"] for row in purchases}
        self.assertEqual(by_name["Audience Involvement Basics"]["cost"],
                         {"dance": 0, "passion": 10, "vocal": 0, "visual": 0})
        self.assertEqual(by_name["Group Lesson Basics"]["cost"],
                         {"dance": 0, "passion": 15, "vocal": 0})
        self.assertEqual(by_name["Vocal Training Basics"]["cost"],
                         {"dance": 0, "passion": 0, "vocal": 10, "visual": 0})
