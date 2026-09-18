"""Regressions for typed previews on frames the screen classifier cannot name."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tests import localdata
from tracen_replay.lesson_offer_adapter import adapt_lesson_offer_frame
from tracen_replay.preview_observations import build_preview_observations


FIXTURE = Path(__file__).parent / "fixtures" / "preview-unknown-screen-regression-v1.json"


def _typed_effect(*, field: str = "dance", amount: int = 14) -> dict:
    return {
        "kind": "performance_change" if field == "dance" else "stat_change",
        "field": field,
        "amount": amount,
        "phase": "preview",
        "preview": True,
        "awarded": False,
        "source_evidence": ["gameplay/source-frame.png"],
    }


def _unknown_row(*, facts: dict | None = None, completed_action=None) -> dict:
    return {
        "source_timestamp_ms": 100,
        "evidence": "gameplay/source-frame.png",
        "screen": "unknown",
        "completed_action": completed_action,
        "facts": facts if facts is not None else {
            "preview_overlay_effects": [_typed_effect()],
            "preview_overlay_proven": True,
            "preview_phase_proof": {
                "phase": "preview",
                "menu_proven": True,
                "result_proven": False,
                "basis": "source_menu_geometry",
            },
        },
        "stats": {"preview_option": "speed"},
    }


class UnknownScreenPreviewTests(unittest.TestCase):
    def test_actual_v5_unknown_screen_rows_reach_preview_adapter(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                row = {
                    "source_timestamp_ms": case["source_timestamp_ms"],
                    "evidence": case["evidence"],
                    "screen": case["screen"],
                    "completed_action": case["completed_action"],
                    "facts": case["facts"],
                    "stats": {"preview_option": case["facts"].get("preview_option")},
                }
                result = build_preview_observations([row])
                actual = {
                    (item["payload"]["kind"], item["payload"]["field"],
                     item["payload"]["amount"])
                    for item in result["observations"]
                }
                expected = {
                    (item["kind"], item["field"], item["amount"])
                    for item in case["expected_effects"]
                }
                self.assertEqual(actual, expected)
                self.assertEqual(result["rejected_counts"], {})
                self.assertFalse(result["committed_actions_inferred"])
                self.assertTrue(all(
                    item["evidence"] == [case["evidence"]]
                    for item in result["observations"]
                ))

    def test_unknown_screen_needs_typed_menu_phase_proof(self):
        row = _unknown_row()
        row["facts"].pop("preview_phase_proof")
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])

    def test_unknown_screen_needs_menu_geometry_or_basis(self):
        row = _unknown_row()
        row["facts"]["preview_phase_proof"] = {
            "phase": "preview",
            "menu_proven": True,
            "result_proven": False,
        }
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])

    def test_unknown_screen_result_marker_abstains(self):
        row = _unknown_row()
        row["facts"]["preview_phase_proof"]["result_proven"] = True
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])

        row = _unknown_row()
        row["facts"]["result_marker_visible"] = True
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])

    def test_unknown_screen_completed_action_abstains(self):
        result = build_preview_observations([_unknown_row(completed_action="training")])
        self.assertEqual(result["observations"], [])

    def test_unknown_screen_applied_fact_abstains(self):
        row = _unknown_row()
        row["facts"]["applied"] = True
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])

    def test_other_unrecognized_screen_does_not_use_preview_proof(self):
        row = _unknown_row()
        row["screen"] = "training_transition"
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])

    def test_input_is_not_mutated_when_unknown_screen_is_accepted(self):
        row = _unknown_row()
        original = copy.deepcopy(row)
        build_preview_observations([row])
        self.assertEqual(row, original)

    def test_typed_hint_metadata_is_preserved(self):
        effect = _typed_effect(field="skill_points", amount=1)
        effect.update({
            "kind": "skill_hint_change",
            "category": "skill_hint",
            "level": "Medium",
            "name_visible": False,
            "raw_label": "Skill Hint Lvl +1 (Medium)",
        })
        row = _unknown_row(facts={"preview_effects": [effect]})
        row["screen"] = "lesson_selection"
        result = build_preview_observations([row])
        self.assertEqual(result["observations"][0]["payload"], {
            "kind": "skill_hint_change",
            "amount": 1,
            "category": "skill_hint",
            "level": "medium",
            "name_visible": False,
            "raw_label": "Skill Hint Lvl +1 (Medium)",
        })

    def test_malformed_optional_hint_metadata_is_rejected(self):
        for key, value, reason in (
            ("category", "lesson", "invalid_typed_hint_category"),
            ("level", 2, "invalid_typed_hint_level"),
            ("name_visible", 0, "invalid_typed_hint_name_visibility"),
            ("raw_label", ["Skill Hint"], "invalid_typed_hint_label"),
        ):
            with self.subTest(key=key):
                effect = _typed_effect(field="skill_points", amount=1)
                effect["kind"] = "skill_hint_change"
                effect[key] = value
                row = _unknown_row(facts={"preview_effects": [effect]})
                row["screen"] = "lesson_selection"
                result = build_preview_observations([row])
                self.assertEqual(result["observations"], [])
                self.assertEqual(result["rejected_counts"], {reason: 1})


if __name__ == "__main__":
    unittest.main()
