import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analyzer" / "lab"))

from audit_turn_explanations import (  # noqa: E402
    _allowed_enrichment,
    audit_openings,
    compare_old_readings,
    diff_accepted_actions,
    diff_canonical_effects,
)


class TurnExplanationAuditTests(unittest.TestCase):
    def test_observed_numeric_reading_mutation_is_not_allowed_enrichment(self):
        before = {
            "gameplay_tracking": {
                "readings": [
                    {
                        "source_timestamp_ms": 100,
                        "evidence": "frame-a.png",
                        "stats": {"values": {"speed": 10}},
                    }
                ]
            }
        }
        after = copy.deepcopy(before)
        after["gameplay_tracking"]["readings"][0]["stats"]["values"]["speed"] = 11

        compared = compare_old_readings(before, after)
        enrichment = _allowed_enrichment(compared["changed"])
        self.assertEqual(len(compared["changed"]), 1)
        self.assertEqual(enrichment["allowed"], [])
        self.assertEqual(len(enrichment["unauthorized"]), 1)

    def test_changed_action_and_duplicate_effect_require_explanation(self):
        before = {
            "gameplay_tracking": {
                "turn_action_receipts": [
                    {
                        "kind": "training",
                        "training_option": "speed",
                        "source_timestamp_ms": 100,
                        "event_id": "training-1",
                    }
                ],
                "events": [
                    {
                        "id": "training-1",
                        "kind": "training",
                        "effects": [{"kind": "stat_change", "field": "speed", "amount": 1}],
                    }
                ],
            }
        }
        after = copy.deepcopy(before)
        after["gameplay_tracking"]["turn_action_receipts"][0]["training_option"] = "power"
        after["gameplay_tracking"]["events"][0]["effects"].append(
            {"kind": "stat_change", "field": "speed", "amount": 1}
        )

        actions = diff_accepted_actions(before, after)
        effects = diff_canonical_effects(before, after)
        self.assertFalse(actions["identity_unchanged"])
        self.assertFalse(actions["kind_option_unchanged"])
        self.assertTrue(actions["explanation_required"])
        self.assertEqual(len(effects["added"]), 1)
        self.assertFalse(effects["unchanged"])
        self.assertTrue(effects["explanation_required"])

    def test_opening_values_mismatch_against_source_pointer_is_rejected(self):
        values = {
            "speed": 10,
            "stamina": 11,
            "power": 12,
            "guts": 13,
            "wit": 14,
            "skill_points": 15,
        }
        report = {
            "source": {"sha256": "a" * 64},
            "gameplay_tracking": {
                "readings": [
                    {
                        "source_timestamp_ms": 100,
                        "evidence": "frame-a.png",
                        "stats": {"values": values},
                    }
                ]
            },
            "turn_ledger": {
                "turns": [
                    {
                        "id": "turn-001",
                        "states": {
                            "stats": {
                                "opening": {
                                    "values_ref": "/gameplay_tracking/readings/0/stats/values",
                                    "observed_at_ms": 100,
                                    "values": {**values, "speed": 999},
                                    "evidence": ["frame-a.png"],
                                    "basis": "source_snapshot",
                                },
                                "opening_status": "observed",
                            },
                            "performance": {
                                "opening": None,
                                "opening_status": "not_observed_before_action",
                            },
                        },
                    }
                ]
            },
        }

        result = audit_openings(report)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            any(finding["kind"] == "opening_values_ref_mismatch" for finding in result["findings"])
        )

    def test_same_timestamp_corroboration_counts_as_one_witness(self):
        readings = [
            {
                "source_timestamp_ms": 100,
                "evidence": "frame-a.png",
                "facts": {"performance_points": {"dance": 1}},
            },
            {
                "source_timestamp_ms": 100,
                "evidence": "frame-b.png",
                "facts": {"performance_points": {"dance": 1}},
            },
        ]
        report = {
            "source": {"sha256": "a" * 64},
            "gameplay_tracking": {"readings": readings},
            "turn_ledger": {
                "turns": [
                    {
                        "id": "turn-001",
                        "states": {
                            "stats": {
                                "opening": None,
                                "opening_status": "not_observed_before_action",
                            },
                            "performance": {
                                "opening": {
                                    "values_ref": "/gameplay_tracking/readings/0/facts/performance_points",
                                    "observed_at_ms": 100,
                                    "values": {"dance": 1},
                                    "evidence": ["frame-a.png"],
                                    "basis": "partial_source_snapshot_with_repeated_field_corroboration",
                                    "field_corroboration": {
                                        "dance": [
                                            {
                                                "value_ref": "/gameplay_tracking/readings/0/facts/performance_points/dance",
                                                "observed_at_ms": 100,
                                                "evidence": ["frame-a.png"],
                                            },
                                            {
                                                "value_ref": "/gameplay_tracking/readings/1/facts/performance_points/dance",
                                                "observed_at_ms": 100,
                                                "evidence": ["frame-b.png"],
                                            },
                                        ]
                                    },
                                },
                                "opening_status": "partially_observed",
                            },
                        },
                    }
                ]
            },
        }

        result = audit_openings(report)
        insufficient = [
            finding
            for finding in result["findings"]
            if finding["kind"] == "insufficient_distinct_corroboration_timestamps"
        ]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(insufficient), 1)
        self.assertEqual(insufficient[0]["count"], 1)


if __name__ == "__main__":
    unittest.main()
