"""Tests of ``tests.test_turn_explanation_inventory`` that need locally preserved evidence; they run only where it is."""
import copy
import json
from pathlib import Path
import sys
import unittest
from tests import localdata
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the lab scripts beside this suite

from inventory_turn_explanations import inventory  # noqa: E402
from audit_turn_explanations import (  # noqa: E402
    audit_openings,
    diff_accepted_actions,
    diff_canonical_effects,
    diff_numeric_events,
)


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
