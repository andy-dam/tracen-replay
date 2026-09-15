import copy
import json
from pathlib import Path
import unittest

from tracen_replay.evaluation_adapters import report_document, source_document
from tracen_replay.observation_evaluate import evaluate


REPO_ROOT = Path(__file__).resolve().parents[2]
ACTUAL_REPORT = (
    REPO_ROOT
    / ".local/final-reliability-v1/worker-runs/"
    / "post-recognition-g8-v11-independent-02-logs/report.json"
)


def _occluded_line(
    text="Friendship with Air Groove went up by 7.",
    *,
    confidence=98.846,
    box=None,
):
    return {
        "text": text,
        "confidence": confidence,
        "box": [314, 828, 709, 863] if box is None else list(box),
    }


def _synthetic_report(
    *,
    parent_line=None,
    event_start=150,
    event_end=160,
    event_context="Incline",
    parent_context="Incline",
):
    parent_line = copy.deepcopy(parent_line or _occluded_line())
    report = {
        "source": {"sha256": "synthetic-source"},
        "gameplay_tracking": {
            "auxiliary_log_used": False,
            "readings": [
                {
                    "source_timestamp_ms": 150,
                    "evidence": "parent.png",
                    "screen": "event_outcome",
                    "context_title": parent_context,
                    "facts": {"occluded_receipt_lines": [parent_line]},
                },
                {
                    "source_timestamp_ms": 160,
                    "evidence": "clear.png",
                    "screen": "event_outcome",
                    "context_title": event_context,
                    "facts": {},
                },
                {
                    "source_timestamp_ms": 160,
                    "evidence": "other-clear.png",
                    "screen": "event_outcome",
                    "context_title": event_context,
                    "facts": {},
                },
            ],
            "events": [
                {
                    "id": "outcome-0001",
                    "kind": "outcome",
                    "first_seen_ms": event_start,
                    "last_seen_ms": event_end,
                    "evidence": "parent.png",
                    "context_title": event_context,
                    "effects": [
                        {
                            "kind": "friendship_change",
                            "name": "Air Groove",
                            "amount": 7,
                            "raw_text": "Friendship with Air Groove went up by 7.",
                            "confidence": 98.0,
                        },
                        {
                            "kind": "friendship_change",
                            "name": "Another Support",
                            "amount": 7,
                            "raw_text": "Friendship with Another Support went up by 7.",
                            "confidence": 98.0,
                        },
                    ],
                    "field_evidence": {
                        "friendship_change||Air Groove": ["clear.png"],
                        "friendship_change||Another Support": ["other-clear.png"],
                    },
                    "conflicting_readings": [],
                }
            ],
            "turn_action_receipts": [],
            "dialogue_choices": [],
            "races": [],
            "checkpoints": [],
            "performance_accounting": {"checkpoints": []},
            "skill_purchases": [],
        },
    }
    return report


class OccludedReceiptFieldProjectionTests(unittest.TestCase):
    def _rows(self, report):
        return {
            row["payload"].get("name"): row
            for row in report_document(report)["observations"]
            if row.get("payload", {}).get("kind") == "friendship_change"
        }

    def test_parent_frame_is_proof_only_for_matching_effect_field(self):
        rows = self._rows(_synthetic_report())
        self.assertIn("parent.png", rows["Air Groove"]["evidence"])
        self.assertEqual(
            rows["Air Groove"]["observation_basis"],
            "source_bound_occluded_receipt_line",
        )
        self.assertNotIn("parent.png", rows["Another Support"]["evidence"])
        self.assertNotIn("observation_basis", rows["Another Support"])

    def test_wrong_name_geometry_context_or_owner_interval_cannot_attach_parent(self):
        cases = {
            "wrong_name": {"parent_line": _occluded_line(
                "Friendship with Other Support went up by 7."
            )},
            "wrong_geometry": {"parent_line": _occluded_line(
                box=[811, 828, 900, 863]
            )},
            "wrong_context": {"parent_context": "Different Event"},
            "unknown_context": {"parent_context": None},
            "missing_event_context": {"event_context": None},
            "wrong_owner_interval": {"event_start": 160, "event_end": 170},
        }
        for label, kwargs in cases.items():
            with self.subTest(label=label):
                rows = self._rows(_synthetic_report(**kwargs))
                self.assertNotIn("parent.png", rows["Air Groove"]["evidence"])
                self.assertNotIn("observation_basis", rows["Air Groove"])

    def test_invalid_occluded_line_confidence_cannot_attach_parent(self):
        for value in (10 ** 400, 101, True, float("nan")):
            with self.subTest(confidence=repr(value)):
                rows = self._rows(_synthetic_report(
                    parent_line=_occluded_line(confidence=value)
                ))
                self.assertNotIn("parent.png", rows["Air Groove"]["evidence"])
                self.assertNotIn("observation_basis", rows["Air Groove"])

    @unittest.skipUnless(ACTUAL_REPORT.is_file(), "preserved v11 report is unavailable")
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


if __name__ == "__main__":
    unittest.main()
