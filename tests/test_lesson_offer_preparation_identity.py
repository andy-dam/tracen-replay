import unittest

from tracen_replay.lesson_offer_preparation import _effect_signature, _offer_summary


class LessonOfferPreparationIdentityTests(unittest.TestCase):
    def test_summary_preserves_typed_effect_identity(self):
        result = {
            "offers": [{
                "offer_id": "lesson-offer:present-march",
                "card_index": 0,
                "name": "Present March ♪",
                "status": "complete",
                "effects": [{
                    "kind": "skill_hint_change",
                    "category": "skill_hint",
                    "amount": 1,
                    "level": "sprint",
                    "name": "Sprint",
                    "name_visible": True,
                    "raw_label": "Skill Hint Lvl +1 (Sprint)",
                    "source_offer": "Present March ♪",
                    "source_semantics": "lesson_offer",
                    "ignored": "candidate text",
                }],
            }],
        }
        summary = _offer_summary(result, {})
        self.assertEqual(summary[0]["effects"], [{
            "kind": "skill_hint_change",
            "category": "skill_hint",
            "amount": 1,
            "level": "sprint",
            "name": "Sprint",
            "name_visible": True,
            "raw_label": "Skill Hint Lvl +1 (Sprint)",
            "source_offer": "Present March ♪",
            "source_semantics": "lesson_offer",
        }])

    def test_effect_signature_keeps_offer_identity_but_ignores_untyped_text(self):
        left = {
            "kind": "stat_change",
            "field": "power",
            "amount": 22,
            "source_offer": "Present March ♪",
            "source_semantics": "lesson_offer",
            "raw_text": "Power +22",
        }
        right = dict(left, source_offer="Ring Ring Diary")
        self.assertNotEqual(_effect_signature(left), _effect_signature(right))
        self.assertNotIn("raw_text", dict(_effect_signature(left)))


if __name__ == "__main__":
    unittest.main()
