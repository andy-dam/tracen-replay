import unittest

from tracen_replay.condition_removal_banner import (
    merge_condition_removal_effects,
    normalize_condition_removal_event,
    read_condition_cured_banner,
)
from tracen_replay.gameplay import effects_from_lines


def line(text, box, confidence=99.5):
    return {"text": text, "box": list(box), "confidence": confidence}


def banner_lines(name="Night Owl"):
    return [
        line("CONDITIONCURED!", (392, 605, 711, 640)),
        line(name, (485, 650, 613, 690)),
    ]


class ConditionEffectDedupTests(unittest.TestCase):

    def test_same_frame_variant_is_merged_only_under_banner(self):
        receipt = effects_from_lines([
            line("Recovered from Night O.", (316, 829, 574, 860))
        ])
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence="gameplay/frame.png"
        )
        merged = merge_condition_removal_effects(receipt, banner)
        self.assertEqual([effect["kind"] for effect in merged], ["condition_removed"])
        self.assertEqual(merged[0]["name"], "Night Owl")
        self.assertEqual(merged[0]["resolved_condition_name_variants"][0]["observed_name"],
                         "Night O")

    def test_substituted_letters_remain_a_condition_conflict(self):
        receipt = effects_from_lines([
            line("Recovered from Night Oil.", (316, 829, 574, 860))
        ])
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence="gameplay/frame.png")
        merged = merge_condition_removal_effects(receipt, banner)
        self.assertEqual([effect["kind"] for effect in merged], ["condition_removal_conflict"])
        self.assertEqual(set(merged[0]["names"]), {"Night Owl", "Night Oil"})

    def test_variant_is_retained_when_another_name_makes_true_conflict(self):
        receipt = effects_from_lines([
            line("Recovered from Night O.", (316, 829, 574, 860)),
            line("Recovered from Practice Poor.", (316, 864, 650, 895)),
        ])
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence="gameplay/frame.png"
        )
        merged = merge_condition_removal_effects(receipt, banner)
        self.assertEqual([effect["kind"] for effect in merged],
                         ["condition_removal_conflict"])
        self.assertEqual(set(merged[0]["names"]),
                         {"Night Owl", "Night O", "Practice Poor"})
        self.assertEqual(
            {effect["name"] for effect in merged[0]["source_proof"]["receipt_effects"]},
            {"Night O", "Practice Poor"},
        )

    def test_distinct_condition_name_remains_a_conflict(self):
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence="gameplay/part-001-frame-000001.png"
        )
        event = {
            "first_seen_ms": 1000,
            "last_seen_ms": 1250,
            "effects": [banner, {
                "kind": "condition_removed",
                "name": "Practice Poor",
                "raw_text": "Recovered from Practice Poor.",
            }],
            "field_evidence": {
                "condition_removed||Night Owl": ["gameplay/part-001-frame-000001.png"],
                "condition_removed||Practice Poor": ["gameplay/part-001-frame-000002.png"],
            },
        }
        rows = {
            "gameplay/part-001-frame-000001.png": {"source_timestamp_ms": 1000},
            "gameplay/part-001-frame-000002.png": {"source_timestamp_ms": 1250},
        }
        normalized = normalize_condition_removal_event(event, rows)
        self.assertEqual(
            [effect.get("name") for effect in normalized["effects"]
             if effect.get("kind") == "condition_removed"],
            ["Night Owl", "Practice Poor"],
        )
        self.assertNotIn("resolved_condition_name_variants",
                         normalized["effects"][0])

    def test_far_later_variant_is_not_collapsed(self):
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence="gameplay/part-001-frame-000001.png"
        )
        variant = {
            "kind": "condition_removed",
            "name": "Night O",
            "raw_text": "Recovered from Night O.",
            "source_timestamp_ms": 3000,
        }
        event = {
            "first_seen_ms": 1000,
            "last_seen_ms": 3000,
            "effects": [banner, variant],
            "field_evidence": {
                "condition_removed||Night Owl": ["gameplay/part-001-frame-000001.png"],
                "condition_removed||Night O": ["gameplay/part-001-frame-000003.png"],
            },
        }
        rows = {
            "gameplay/part-001-frame-000001.png": {"source_timestamp_ms": 1000},
            "gameplay/part-001-frame-000003.png": {"source_timestamp_ms": 3000},
        }
        normalized = normalize_condition_removal_event(event, rows)
        self.assertEqual(
            [effect.get("name") for effect in normalized["effects"]
             if effect.get("kind") == "condition_removed"],
            ["Night Owl", "Night O"],
        )

    def test_name_similarity_without_source_binding_is_not_enough(self):
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence="gameplay/part-001-frame-000001.png"
        )
        event = {
            "first_seen_ms": 1000,
            "last_seen_ms": 1250,
            "effects": [banner, {
                "kind": "condition_removed",
                "name": "Night O",
                "raw_text": "Recovered from Night O.",
                "source_timestamp_ms": 1250,
            }, {
                "kind": "condition_removed",
                "name": "Night Out",
                "raw_text": "Recovered from Night Out.",
                "source_timestamp_ms": 1250,
            }],
            "field_evidence": {
                "condition_removed||Night Owl": ["gameplay/part-001-frame-000001.png"],
            },
        }
        rows = {
            "gameplay/part-001-frame-000001.png": {"source_timestamp_ms": 1000},
        }
        normalized = normalize_condition_removal_event(event, rows)
        self.assertEqual(
            [effect.get("name") for effect in normalized["effects"]
             if effect.get("kind") == "condition_removed"],
            ["Night Owl", "Night O", "Night Out"],
        )

    def test_nearby_but_distinct_name_is_not_a_variant(self):
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence="gameplay/part-001-frame-000001.png"
        )
        event = {
            "first_seen_ms": 1000,
            "last_seen_ms": 1250,
            "effects": [banner, {
                "kind": "condition_removed",
                "name": "Night Out",
                "raw_text": "Recovered from Night Out.",
            }],
            "field_evidence": {
                "condition_removed||Night Owl": ["gameplay/part-001-frame-000001.png"],
                "condition_removed||Night Out": ["gameplay/part-001-frame-000002.png"],
            },
        }
        rows = {
            "gameplay/part-001-frame-000001.png": {"source_timestamp_ms": 1000},
            "gameplay/part-001-frame-000002.png": {"source_timestamp_ms": 1250},
        }
        normalized = normalize_condition_removal_event(event, rows)
        self.assertEqual(
            [effect.get("name") for effect in normalized["effects"]
             if effect.get("kind") == "condition_removed"],
            ["Night Owl", "Night Out"],
        )

    def test_intermediate_name_tokens_must_match_for_variant(self):
        banner = read_condition_cured_banner(
            banner_lines("Night Quiet Owl"),
            source_timestamp_ms=1000,
            evidence="gameplay/part-001-frame-000001.png",
        )
        event = {
            "first_seen_ms": 1000,
            "last_seen_ms": 1250,
            "effects": [banner, {
                "kind": "condition_removed",
                "name": "Night Loud Ol",
                "raw_text": "Recovered from Night Loud Ol.",
            }],
            "field_evidence": {
                "condition_removed||Night Quiet Owl": ["gameplay/part-001-frame-000001.png"],
                "condition_removed||Night Loud Ol": ["gameplay/part-001-frame-000002.png"],
            },
        }
        rows = {
            "gameplay/part-001-frame-000001.png": {"source_timestamp_ms": 1000},
            "gameplay/part-001-frame-000002.png": {"source_timestamp_ms": 1250},
        }
        normalized = normalize_condition_removal_event(event, rows)
        self.assertEqual(
            [effect.get("name") for effect in normalized["effects"]
             if effect.get("kind") == "condition_removed"],
            ["Night Quiet Owl", "Night Loud Ol"],
        )

    def test_only_source_bound_variant_conflicts_are_removed(self):
        primary = "gameplay/part-001-frame-000001.png"
        variant = "gameplay/part-001-frame-000002.png"
        banner = read_condition_cured_banner(
            banner_lines(), source_timestamp_ms=1000, evidence=primary)
        for evidence in (None, "", [], "other/frame.png", variant):
            with self.subTest(evidence=evidence):
                conflict = {"field": "condition_removed||Night O",
                            "reason": "uncertain_condition", "evidence": evidence}
                event = {
                    "first_seen_ms": 1000, "last_seen_ms": 1250,
                    "effects": [banner, {"kind": "condition_removed", "name": "Night O",
                                         "raw_text": "Recovered from Night O."}],
                    "field_evidence": {"condition_removed||Night Owl": [primary],
                                       "condition_removed||Night O": [variant]},
                    "conflicting_readings": [conflict],
                }
                result = normalize_condition_removal_event(event, {
                    primary: {"source_timestamp_ms": 1000},
                    variant: {"source_timestamp_ms": 1250},
                })
                self.assertEqual([e["name"] for e in result["effects"]], ["Night Owl"])
                self.assertEqual(result["conflicting_readings"],
                                 [] if evidence == variant else [conflict])


if __name__ == "__main__":
    unittest.main()
