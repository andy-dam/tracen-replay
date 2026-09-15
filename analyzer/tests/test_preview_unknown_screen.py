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

    def test_actual_lesson_hint_cards_keep_level_and_label_metadata(self):
        base = localdata.root("development_third_recording_baseline")
        source_sha256 = "a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313"
        expected = {
            "000094": ("Group Lesson Basics", 1),
            "000121": ("Group Lesson Intermediate", 2),
        }
        required = []
        for frame_id in expected:
            required.extend((
                base / f"neural/part-005-frame-{frame_id}.json",
                base / f"gameplay/part-005-frame-{frame_id}.png",
                base / f"part-005/frames/{frame_id}.jpg",
            ))
        if not all(path.is_file() for path in required):
            self.skipTest("frozen independent-02 lesson source is not present")

        for frame_id, (offer_name, amount) in expected.items():
            with self.subTest(frame_id=frame_id):
                raw = json.loads(
                    (base / f"neural/part-005-frame-{frame_id}.json").read_text(
                        encoding="utf-8"
                    )
                )
                adapted = adapt_lesson_offer_frame(
                    raw,
                    gameplay_path=base / f"gameplay/part-005-frame-{frame_id}.png",
                    source_frame_path=base / f"part-005/frames/{frame_id}.jpg",
                    source_sha256=source_sha256,
                )
                offer = next(item for item in adapted["offers"] if item["name"] == offer_name)
                hint = next(item for item in offer["effects"] if item["kind"] == "skill_hint_change")
                evidence = adapted["source_proof"]["evidence"]
                row = {
                    "source_timestamp_ms": int(frame_id),
                    "evidence": evidence,
                    "screen": "lesson_selection",
                    "completed_action": None,
                    "facts": {
                        "preview_effects": [
                            dict(
                                hint,
                                phase="preview",
                                preview=True,
                                awarded=False,
                                context_title=offer_name,
                                source_evidence=[evidence],
                            )
                        ]
                    },
                    "stats": {"preview_option": None},
                }
                result = build_preview_observations([row])
                self.assertEqual(len(result["observations"]), 1)
                self.assertEqual(result["observations"][0]["payload"], {
                    "kind": "skill_hint_change",
                    "amount": amount,
                    "category": "skill_hint",
                    "level": "medium",
                    "name_visible": False,
                    "raw_label": f"Skill Hint Lvl +{amount} (Medium)",
                })


if __name__ == "__main__":
    unittest.main()
