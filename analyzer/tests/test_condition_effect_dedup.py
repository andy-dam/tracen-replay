import json
from pathlib import Path
import unittest

from tests import localdata
from tracen_replay.condition_removal_banner import (
    merge_condition_removal_effects,
    normalize_condition_removal_event,
    read_condition_cured_banner,
)
from tracen_replay.gameplay import effects_from_lines
from tracen_replay.evaluation_adapters import report_document
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import parse


def line(text, box, confidence=99.5):
    return {"text": text, "box": list(box), "confidence": confidence}


def banner_lines(name="Night Owl"):
    return [
        line("CONDITIONCURED!", (392, 605, 711, 640)),
        line(name, (485, 650, 613, 690)),
    ]


class ConditionEffectDedupTests(unittest.TestCase):
    def test_source_event_collapses_fading_receipt_variants(self):
        paths = sorted(localdata.root(
            "development_third_recording_baseline", "neural"
        ).glob("part-001-frame-00044[5-9].json"))
        if len(paths) != 5:
            self.skipTest("independent-02 source sidecars are not available")
        rows = []
        for path in paths:
            raw = json.loads(path.read_text(encoding="utf-8"))
            parsed = parse(raw)
            parsed.update(
                source_timestamp_ms=raw["source_timestamp_ms"],
                evidence=raw["evidence"],
                context_title="At the Infirmary",
            )
            rows.append(parsed)

        event = outcome_events(rows)[0]
        before = [effect.get("name") for effect in event["effects"]
                  if effect.get("kind") == "condition_removed"]
        self.assertEqual(before, ["Night Owl"])

        normalized = normalize_condition_removal_event(
            event, {row["evidence"]: row for row in rows}
        )
        self.assertEqual(normalized, event)
        removals = [effect for effect in normalized["effects"]
                    if effect.get("kind") == "condition_removed"]
        self.assertEqual([effect["name"] for effect in removals], ["Night Owl"])
        self.assertFalse(any(effect.get("kind") == "condition_removal_conflict"
                             for effect in normalized["effects"]))
        variants = removals[0]["resolved_condition_name_variants"]
        self.assertEqual([item["observed_name"] for item in variants],
                         ["Night O", "Night O", "Night Ol"])
        self.assertIn("gameplay/part-001-frame-000448.png",
                      variants[1]["evidence"])
        self.assertIn("gameplay/part-001-frame-000449.png",
                      variants[2]["evidence"])
        document = report_document({
            "source": {"sha256": "a" * 64},
            "gameplay_tracking": {"readings": rows, "events": [event],
                                  "turn_action_receipts": []},
        })
        applied = [observation for observation in document["observations"]
                   if observation.get("phase") == "applied"
                   and observation.get("payload", {}).get("kind") == "condition_removed"]
        self.assertEqual([item["payload"]["name"] for item in applied], ["Night Owl"])

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
