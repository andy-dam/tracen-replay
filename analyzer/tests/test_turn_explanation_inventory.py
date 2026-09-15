import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analyzer" / "lab"))

from tests import localdata

from inventory_turn_explanations import inventory  # noqa: E402
from audit_turn_explanations import (  # noqa: E402
    audit_openings,
    diff_accepted_actions,
    diff_canonical_effects,
    diff_numeric_events,
)


STATS = ("speed", "stamina", "power", "guts", "wit", "skill_points")
PERFORMANCE = ("dance", "passion", "vocal", "visual", "composure")


def _opening(values, channel, status="observed"):
    collection = "readings/0/stats/values" if channel == "stats" else "readings/0/facts/performance_points"
    return {
        "values_ref": f"/gameplay_tracking/{collection}",
        "observed_at_ms": 100,
        "values": copy.deepcopy(values),
        "evidence": ["frame-0001.png"],
        "basis": "source_snapshot",
    }, status


def _report(*, stats_opening=None, performance_opening=None, readings=None, turns=None):
    stats_state = {"opening": stats_opening, "closing": None, "opening_status": "observed"}
    performance_state = {"opening": performance_opening, "closing": None, "opening_status": "observed"}
    if stats_opening is None:
        stats_state["opening_status"] = "not_observed_before_action"
    if performance_opening is None:
        performance_state["opening_status"] = "not_observed_before_action"
    turn = {
        "id": "turn-001",
        "start_ms": 0,
        "end_ms": 1000,
        "window_kind": "calendar_turn",
        "action_status": "one_action",
        "states": {"stats": stats_state, "performance": performance_state},
    }
    if turns is not None:
        turn = turns[0]
    return {
        "source": {"sha256": "a" * 64, "duration_ms": 1000},
        "gameplay_tracking": {"readings": readings or []},
        "turn_ledger": {
            "turns": [turn] if turns is None else turns,
            "timeline": [],
        },
        "causal_accounting": {"contributions": [], "turn_transitions": []},
    }


class TurnExplanationInventoryTests(unittest.TestCase):
    def test_frozen_baseline_denominators_are_reproduced(self):
        if not localdata.available("turn_explanation_baseline"):
            self.skipTest("frozen turn-explanation baseline reports are not present")
        expected = {
            "missing_field_comparisons": 271,
            "unresolved_field_comparisons": 42,
            "eligible_dated_one_action_turns": 180,
            "fully_observed_endpoint_turns": 156,
            "fully_direct_numeric_accounting_turns": 28,
        }
        totals = {key: 0 for key in expected}
        for name in ("v1", "independent-01", "independent-02"):
            report = json.loads(
                localdata.root("turn_explanation_baseline", f"{name}-report.json").read_text(
                    encoding="utf-8"
                )
            )
            summary = inventory(report)["summary"]
            for key in expected:
                totals[key] += summary[key]
            self.assertIsNone(summary["independently_verified_complete_history_turns"])
            self.assertEqual(summary["eligible_dated_one_action_turns"], 60)
        self.assertEqual(totals, expected)

    def test_partial_opening_is_flattened_without_losing_zero(self):
        stats = {field: 0 for field in STATS}
        performance = {"dance": 0, "vocal": 7, "visual": 8, "composure": 9, "passion": None}
        stats_opening, _ = _opening(stats, "stats")
        performance_opening, status = _opening(performance, "performance", "partially_observed")
        report = _report(stats_opening=stats_opening, performance_opening=performance_opening)
        report["turn_ledger"]["turns"][0]["states"]["performance"]["opening_status"] = status

        result = inventory(report)
        states = result["missing_opening_states"]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["missing_kind"], "partial_missing")
        self.assertEqual(states[0]["missing_fields"], ["passion"])
        fields = result["field_level_cause_inventory"]["missing_opening_fields"]
        self.assertEqual([(row["field"], row["value_state"]) for row in fields], [("passion", "null")])
        self.assertEqual(result["summary"]["missing_opening_null_fields"], 1)
        self.assertEqual(result["summary"]["observed_opening_zero_fields"], 6 + 1)
        self.assertEqual(result["summary"]["partial_missing_opening_fields"], 1)

    def test_duplicate_views_are_counted_but_do_not_become_distinct_states(self):
        readings = [
            {
                "source_timestamp_ms": 100,
                "evidence": "frame-a.png",
                "stats": {"values": {"speed": 1}},
            },
            {
                "source_timestamp_ms": 100,
                "evidence": "frame-b.png",
                "stats": {"values": {"speed": 1}},
            },
        ]
        report = _report(readings=readings)
        result = inventory(report)
        state = next(row for row in result["missing_opening_states"] if row["channel"] == "stats")
        self.assertEqual(state["observation_count"], 2)
        self.assertEqual(state["distinct_source_timestamp_count"], 1)
        self.assertEqual(state["duplicate_view_count"], 1)
        self.assertEqual(result["summary"]["opening_observation_count"], 2)
        self.assertEqual(result["summary"]["opening_distinct_source_timestamp_count"], 1)
        self.assertEqual(result["summary"]["opening_duplicate_view_count"], 1)

    def test_opening_auditor_checks_partial_values_and_corroboration_refs(self):
        values_a = {"dance": 0, "passion": 2, "vocal": 3, "visual": 4}
        values_b = {"dance": 0, "passion": 2, "vocal": 3, "visual": 4}
        readings = [
            {
                "source_timestamp_ms": 100,
                "evidence": "frame-a.png",
                "facts": {"performance_points": values_a},
            },
            {
                "source_timestamp_ms": 200,
                "evidence": "frame-b.png",
                "facts": {"performance_points": values_b},
            },
        ]
        opening = {
            "source_ref": "/gameplay_tracking/readings/0/facts",
            "values_ref": "/gameplay_tracking/readings/0/facts/performance_points",
            "supporting_source_refs": [
                "/gameplay_tracking/readings/0/facts",
                "/gameplay_tracking/readings/1/facts",
            ],
            "observed_at_ms": 100,
            "values": values_a,
            "evidence": ["frame-a.png"],
            "basis": "partial_source_snapshot_with_repeated_field_corroboration",
            "field_corroboration": {
                field: [
                    {
                        "value_ref": f"/gameplay_tracking/readings/0/facts/performance_points/{field}",
                        "observed_at_ms": 100,
                        "evidence": ["frame-a.png"],
                    },
                    {
                        "value_ref": f"/gameplay_tracking/readings/1/facts/performance_points/{field}",
                        "observed_at_ms": 200,
                        "evidence": ["frame-b.png"],
                    },
                ]
                for field in values_a
            },
        }
        report = _report(readings=readings)
        report["turn_ledger"]["turns"][0]["states"]["performance"] = {
            "opening": opening,
            "closing": None,
            "opening_status": "partially_observed",
        }
        result = audit_openings(report)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["counts"]["partial_openings"], 1)
        self.assertEqual(result["counts"]["unobserved_openings"], 1)

    def test_change_diffs_keep_action_numeric_and_effect_categories_separate(self):
        before = {
            "gameplay_tracking": {
                "turn_action_receipts": [
                    {"kind": "training", "training_option": "speed", "event_id": "training-1", "training_outcome": "unknown"}
                ],
                "events": [
                    {"id": "training-1", "kind": "training", "first_seen_ms": 10, "last_seen_ms": 20, "deltas": {"speed": 1}, "effects": []}
                ],
            }
        }
        after = copy.deepcopy(before)
        after["gameplay_tracking"]["turn_action_receipts"][0]["training_outcome"] = "success"
        after["gameplay_tracking"]["events"][0]["deltas"]["speed"] = 2
        after["gameplay_tracking"]["events"][0]["effects"] = [{"kind": "stat_change", "field": "speed", "amount": 1}]
        actions = diff_accepted_actions(before, after)
        numeric = diff_numeric_events(before, after)
        effects = diff_canonical_effects(before, after)
        self.assertFalse(actions["success_metadata_unchanged"])
        self.assertTrue(actions["kind_option_unchanged"])
        self.assertEqual(len(numeric["amount_changes"]), 1)
        self.assertEqual(len(effects["added"]), 1)


if __name__ == "__main__":
    unittest.main()
