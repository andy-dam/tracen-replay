"""Tests of ``tests.test_occluded_receipt_field_projection`` that need locally preserved evidence; they run only where it is."""
import copy
import json
from pathlib import Path
import unittest
from tests import localdata
from tracen_replay.evaluation_adapters import report_document, source_document
from tracen_replay.observation_evaluate import evaluate
from tests.test_occluded_receipt_field_projection import ACTUAL_REPORT


class OccludedReceiptFieldProjectionTests(unittest.TestCase):
    def _rows(self, report):
        return {
            row["payload"].get("name"): row
            for row in report_document(report)["observations"]
            if row.get("payload", {}).get("kind") == "friendship_change"
        }




    @unittest.skipUnless(ACTUAL_REPORT.is_file(), "preserved third-recording report is unavailable")
    def test_actual_air_groove_source_frame_matches_reference_field(self):
        report = json.loads(ACTUAL_REPORT.read_text(encoding="utf-8"))
        data = report["gameplay_tracking"]
        event = copy.deepcopy(data["events"][12])
        wanted = {
            event["evidence"],
            "occluded-receipt-recovery/receipt-inspection/155000-156001-16/"
            "frame-000013.png",
            "occluded-receipt-recovery/receipt-inspection/155000-156001-16/"
            "frame-000014.png",
            "occluded-receipt-recovery/receipt-inspection/155000-156001-16/"
            "frame-000015.png",
        }
        readings = [
            copy.deepcopy(row) for row in data["readings"]
            if row.get("evidence") in wanted
        ]
        self.assertEqual(len(readings), 4)
        mini = {
            "source": copy.deepcopy(report["source"]),
            "gameplay_tracking": {
                "auxiliary_log_used": False,
                "readings": readings,
                "events": [event],
                "turn_action_receipts": [],
                "dialogue_choices": [],
                "races": [],
                "checkpoints": [],
                "performance_accounting": {"checkpoints": []},
                "skill_purchases": [],
            },
        }
        projected = report_document(mini)
        row = next(
            item for item in projected["observations"]
            if item["source_ref"] == "/gameplay_tracking/events/0/effects/1"
        )
        self.assertIn(event["evidence"], row["evidence"])
        self.assertEqual(row["observation_basis"], "source_bound_occluded_receipt_line")
        self.assertEqual((row["start_ms"], row["end_ms"]), (155750, 155933))

        reference = source_document({
            "source_sha256": report["source"]["sha256"],
            "start_ms": 155000,
            "end_ms": 156001,
            "labels": [{
                "id": "air-groove",
                "category": "effect",
                "first_seen_ms": 155750,
                "last_seen_ms": 155750,
                "evidence": [event["evidence"]],
                "expected": {
                    "kind": "friendship_change",
                    "name": "Air Groove",
                    "amount": 7,
                },
            }],
        })
        scored = evaluate(reference, projected)
        result = scored["results"][0]
        self.assertEqual(result["status"], "correct")
        self.assertEqual(result["matching_basis"], "source_identity")
