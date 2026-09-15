"""Regression tests for source-bound lesson cost enrichment."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tracen_replay.evaluation_adapters import report_document
from tracen_replay.full_recording import cached_readings
from tracen_replay.preview_observations import build_preview_observations


EVIDENCE = "gameplay/lesson-selection.png"
SOURCE_PROOF = {
    "evidence": EVIDENCE,
    "evidence_sha256": "e" * 64,
    "source_frame_sha256": "f" * 64,
    "source_frame_verified": True,
}


def _price(field, value, *, status="accepted"):
    return {
        "field": field,
        "value": value if status == "accepted" else None,
        "status": status,
        "unknown_reason": None if status == "accepted" else "missing_numeric_cost",
    }


def _offer(offer_id, name, cost, *, prices=None, proof=None):
    if prices is None:
        prices = [_price(field, value) for field, value in cost.items()]
    return {
        "offer_id": offer_id,
        "name": name,
        "phase": "preview",
        "preview_only": True,
        "committed": False,
        "cost": copy.deepcopy(cost),
        "prices": copy.deepcopy(prices),
        "payload": {"kind": "lesson", "name": name},
        "source_proof": copy.deepcopy(proof or SOURCE_PROOF),
    }


def _row(offers, *, effects=None, evidence=EVIDENCE, envelope=None):
    if effects is None:
        effects = [
            {
                "kind": "lesson",
                "name": offer["name"],
                "offer_id": offer["offer_id"],
                "phase": "preview",
                "source_semantics": "lesson_offer",
                "source_evidence": [evidence],
            }
            for offer in offers
        ]
    if envelope is None:
        envelope = {
            "phase": "preview",
            "preview_only": True,
            "committed": False,
            "offers": copy.deepcopy(offers),
            "source_proof": copy.deepcopy(SOURCE_PROOF),
        }
    return {
        "source_timestamp_ms": 100,
        "evidence": evidence,
        "screen": "lesson_selection",
        "completed_action": None,
        "facts": {
            "preview_effects": copy.deepcopy(effects),
            "lesson_offer_preview": copy.deepcopy(envelope),
        },
        "stats": {},
    }


class LessonOfferPreviewBridgeTests(unittest.TestCase):
    def test_three_typed_offers_are_enriched_without_duplicate_occurrences(self):
        offers = [
            _offer("offer-a", "Audience Involvement Basics", {"passion": 10}),
            _offer("offer-b", "Group Lesson Basics", {"vocal": 15}),
            _offer("offer-c", "Vocal Training Basics", {"dance": 0}),
        ]
        result = build_preview_observations([_row(offers)])
        purchases = [row for row in result["observations"] if row["category"] == "purchase"]

        self.assertEqual(len(purchases), 3)
        self.assertEqual(
            {row["payload"]["name"]: row["payload"]["cost"] for row in purchases},
            {
                "Audience Involvement Basics": {"passion": 10},
                "Group Lesson Basics": {"vocal": 15},
                "Vocal Training Basics": {"dance": 0},
            },
        )
        self.assertEqual({row["offer_id"] for row in purchases},
                         {"offer-a", "offer-b", "offer-c"})
        self.assertTrue(all("lesson_offer_preview" in row["source_fact_keys"]
                            for row in purchases))
        self.assertTrue(all(
            sum(item["offer_id"] == row["offer_id"] for item in purchases) == 1
            for row in purchases
        ))

    def test_unknown_price_slot_is_omitted_instead_of_becoming_zero(self):
        offer = _offer(
            "offer-unknown",
            "Audience Involvement Basics",
            {"passion": 10, "visual": 0},
            prices=[
                _price("passion", 10),
                _price("visual", None, status="unknown"),
            ],
        )
        result = build_preview_observations([_row([offer])])
        payload = result["observations"][0]["payload"]
        self.assertEqual(payload.get("cost"), {"passion": 10})
        self.assertNotIn("visual", payload["cost"])

    def test_conflicting_accepted_price_values_remain_unknown(self):
        offer = _offer(
            "offer-conflict",
            "Group Lesson Basics",
            {"passion": 15, "visual": 5},
            prices=[
                _price("passion", 15),
                _price("visual", 5),
                _price("visual", 7),
            ],
        )
        result = build_preview_observations([_row([offer])])
        payload = result["observations"][0]["payload"]
        self.assertEqual(payload.get("cost"), {"passion": 15})
        self.assertNotIn("visual", payload["cost"])

    def test_invalid_phase_or_unbound_proof_cannot_supply_cost(self):
        offer = _offer("offer-boundary", "Vocal Training Basics", {"vocal": 10})

        applied = _row([offer])
        applied["facts"]["lesson_offer_preview"]["phase"] = "applied"
        result = build_preview_observations([applied])
        self.assertNotIn("cost", result["observations"][0]["payload"])
        self.assertEqual(result["rejected_counts"],
                         {"non_preview_lesson_offer_envelope": 1})

        unbound = _row([offer])
        unbound["facts"]["lesson_offer_preview"]["offers"][0]["source_proof"] = {
            "evidence": "gameplay/other-frame.png",
            "evidence_sha256": "a" * 64,
        }
        result = build_preview_observations([unbound])
        self.assertNotIn("cost", result["observations"][0]["payload"])
        self.assertEqual(result["rejected_counts"],
                         {"unproven_lesson_offer_cost": 1})

    def test_actual_three_offer_envelope_survives_report_document_projection(self):
        base = Path(".local/full-recording/independent-02/initial-baseline")
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


if __name__ == "__main__":
    unittest.main()
