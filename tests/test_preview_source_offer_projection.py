import copy
import unittest

from tracen_replay.preview_observations import build_preview_observations


def _reading(timestamp, effects, *, screen="lesson_selection"):
    return {
        "source_timestamp_ms": timestamp,
        "evidence": f"source/frame-{timestamp}.png",
        "screen": screen,
        "completed_action": None,
        "facts": {"preview_effects": copy.deepcopy(effects)},
        "stats": {"preview_option": "speed"},
    }


class PreviewSourceOfferProjectionTests(unittest.TestCase):
    def test_explicit_offer_identity_survives_typed_effect_projection(self):
        effects = [
            {
                "kind": "stat_change",
                "field": "power",
                "amount": 22,
                "source_semantics": "lesson_offer",
                "offer_id": "lesson-offer:present-march",
                "source_offer": "Present March ♪",
            },
            {
                "kind": "skill_hint_change",
                "amount": 1,
                "category": "skill_hint",
                "level": "sprint",
                "name": "Sprint",
                "name_visible": True,
                "raw_label": "Skill Hint Lvl +1 (Sprint)",
                "source_semantics": "lesson_offer",
                "offer_id": "lesson-offer:present-march",
                "source_offer": "Present March ♪",
            },
        ]
        result = build_preview_observations([_reading(100, effects)])
        self.assertEqual(len(result["observations"]), 2)
        self.assertTrue(all(row["category"] == "effect" for row in result["observations"]))
        self.assertTrue(all(row["phase"] == "preview" for row in result["observations"]))
        self.assertEqual(
            {row["payload"]["source_offer"] for row in result["observations"]},
            {"Present March ♪"},
        )
        hints = [row["payload"] for row in result["observations"]
                 if row["payload"]["kind"] == "skill_hint_change"]
        self.assertEqual(hints[0]["name"], "Sprint")

    def test_offer_identity_requires_parser_owned_context(self):
        row = _reading(100, [{
            "kind": "stat_change",
            "field": "power",
            "amount": 22,
            "source_offer": "Copied context text",
        }])
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["rejected_counts"], {"unproven_typed_source_offer": 1})

    def test_offer_id_keeps_same_field_cards_as_distinct_preview_occurrences(self):
        rows = [
            _reading(100, [{
                "kind": "stat_change",
                "field": "power",
                "amount": 22,
                "source_semantics": "lesson_offer",
                "offer_id": "lesson-offer:one",
                "source_offer": "Present March ♪",
            }]),
            _reading(200, [{
                "kind": "stat_change",
                "field": "power",
                "amount": 22,
                "source_semantics": "lesson_offer",
                "offer_id": "lesson-offer:two",
                "source_offer": "Ring Ring Diary",
            }]),
        ]
        result = build_preview_observations(rows)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(
            {row["offer_id"] for row in result["observations"]},
            {"lesson-offer:one", "lesson-offer:two"},
        )
        self.assertEqual(result["ambiguities"], [])

    def test_malformed_offer_identity_is_rejected(self):
        row = _reading(100, [{
            "kind": "stat_change",
            "field": "power",
            "amount": 22,
            "source_semantics": "lesson_offer",
            "offer_id": "lesson-offer:present-march",
            "source_offer": "Present\x00March",
        }])
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["rejected_counts"], {"invalid_typed_source_offer": 1})


if __name__ == "__main__":
    unittest.main()
